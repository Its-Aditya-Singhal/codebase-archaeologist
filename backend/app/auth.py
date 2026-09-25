"""Accounts: sign up, log in, log out, and the current user.

- Passwords are hashed with scrypt (standard library), with a random salt per user.
- A login issues a random session token, sent as an HttpOnly, SameSite=Lax
  cookie; the database stores only its SHA-256 hash, so a leaked table cannot
  be replayed as sessions.
- Failed logins are throttled per email address.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import connection

COOKIE = "ca_session"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_MAXMEM = 64 * 1024 * 1024
USER_COLUMNS = "u.id, u.email, u.name, u.created_at"

router = APIRouter(prefix="/api/auth", tags=["auth"])


# -------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=_MAXMEM, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        **_SCRYPT, salt=base64.b64encode(salt).decode(), digest=base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n),
                                r=int(r), p=int(p), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))


# Compared against when the email is unknown, so both cases take equally long.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# --------------------------------------------------------------------- throttle

MAX_FAILURES = 8
FAILURE_WINDOW = 15 * 60  # seconds
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _recent_failures(email: str) -> list[float]:
    cutoff = time.monotonic() - FAILURE_WINDOW
    return [t for t in _failures.get(email, []) if t > cutoff]


def _throttled(email: str) -> bool:
    with _failures_lock:
        return len(_recent_failures(email)) >= MAX_FAILURES


def _record_failure(email: str) -> None:
    with _failures_lock:
        _failures[email] = [*_recent_failures(email), time.monotonic()]


# --------------------------------------------------------------------- sessions


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _start_session(response: Response, request: Request, user_id: int) -> None:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=settings.session_days)
    with connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, user_agent) "
            "VALUES (%s, %s, %s, %s)",
            (_token_hash(token), user_id, expires, request.headers.get("user-agent", "")[:300]))
        conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))
        conn.execute("DELETE FROM sessions WHERE expires_at < now()")
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.cookie_secure, path="/")


def current_user(request: Request) -> dict:
    """The logged-in user, or 401."""
    token = request.cookies.get(COOKIE)
    if token:
        with connection() as conn:
            user = conn.execute(
                f"""SELECT {USER_COLUMNS} FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = %s AND s.expires_at > now()""",
                (_token_hash(token),)).fetchone()
        if user is not None:
            return user
    raise HTTPException(401, "Not logged in")


CurrentUser = Annotated[dict, Depends(current_user)]


def authorize(request: Request, user: CurrentUser) -> None:
    """Router-wide guard: a logged-in user, who may only reach their own
    repositories and investigations (others' look like they do not exist)."""
    params = request.path_params
    try:
        repo_id = int(params["repo_id"]) if "repo_id" in params else None
        inv_id = int(params["investigation_id"]) if "investigation_id" in params else None
    except ValueError:
        return  # the endpoint rejects the malformed id
    with connection() as conn:
        if repo_id is not None and conn.execute(
                "SELECT 1 FROM user_repositories WHERE user_id = %s AND repo_id = %s",
                (user["id"], repo_id)).fetchone() is None:
            raise HTTPException(404, "Repository not found")
        if inv_id is not None and conn.execute(
                "SELECT 1 FROM investigations WHERE id = %s AND user_id = %s",
                (inv_id, user["id"])).fetchone() is None:
            raise HTTPException(404, "Investigation not found")


# ----------------------------------------------------------------------- routes


class SignupIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=256)
    name: str = Field(max_length=100)  # required; blank is rejected with a clear message


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


@router.get("/config")
def auth_config():
    return {"signup_enabled": get_settings().allow_signup}


@router.post("/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response):
    if not get_settings().allow_signup:
        raise HTTPException(403, "Sign-up is disabled on this server")
    email = body.email.strip().lower()
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Enter your name")
    if not _EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address")
    if body.password.strip() != body.password or len(set(body.password)) < 4:
        raise HTTPException(422, "Choose a less predictable password")
    with connection() as conn, conn.transaction():
        if conn.execute("SELECT 1 FROM users WHERE lower(email) = %s",
                        (email,)).fetchone() is not None:
            raise HTTPException(409, "An account with this email already exists")
        user = conn.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) "
            "RETURNING id, email, name, created_at",
            (email, name, hash_password(body.password))).fetchone()
        _adopt_unowned(conn, user["id"])
    _start_session(response, request, user["id"])
    return user


def _adopt_unowned(conn, user_id: int) -> None:
    """The first account takes over what was created before accounts existed."""
    if conn.execute("SELECT 1 FROM users WHERE id <> %s LIMIT 1", (user_id,)).fetchone():
        return
    conn.execute("UPDATE investigations SET user_id = %s WHERE user_id IS NULL", (user_id,))
    conn.execute(
        """INSERT INTO user_repositories (user_id, repo_id)
           SELECT %s, r.id FROM repositories r
           WHERE NOT EXISTS (SELECT 1 FROM user_repositories ur WHERE ur.repo_id = r.id)""",
        (user_id,))


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    if _throttled(email):
        raise HTTPException(429, "Too many failed attempts; try again in 15 minutes")
    with connection() as conn:
        row = conn.execute(
            "SELECT id, email, name, created_at, password_hash FROM users WHERE lower(email) = %s",
            (email,)).fetchone()
    if not verify_password(body.password, row["password_hash"] if row else _DUMMY_HASH) \
            or row is None:
        _record_failure(email)
        raise HTTPException(401, "Incorrect email or password")
    _start_session(response, request, row["id"])
    return {k: row[k] for k in ("id", "email", "name", "created_at")}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    if token := request.cookies.get(COOKIE):
        with connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def me(user: CurrentUser):
    return user
