"""Resolve a user-supplied repository reference to a local git checkout."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

_GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com[/:](?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$"
)
_SHORTHAND = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+)$")


@dataclass
class RepoRef:
    url: str  # canonical identifier stored in the DB
    owner: str | None
    name: str
    clone_url: str | None  # None for local checkouts
    local_path: Path


class RepoSourceError(ValueError):
    pass


def parse_repo_ref(raw: str) -> RepoRef:
    raw = raw.strip()
    settings = get_settings()
    if raw.startswith("file://"):
        raw = raw.removeprefix("file://")

    local = Path(raw).expanduser()
    if raw.startswith(("/", "~", ".")) and local.is_dir():
        local = local.resolve()
        if not (local / ".git").exists():
            raise RepoSourceError(f"{local} is not a git repository")
        return RepoRef(f"file://{local}", None, local.name, None, local)

    m = _GITHUB.match(raw) or _SHORTHAND.match(raw)
    if not m:
        raise RepoSourceError(
            "Expected a GitHub URL (https://github.com/owner/repo), owner/repo, "
            "or an absolute path to a local git checkout"
        )
    owner, name = m["owner"], m["name"]
    return RepoRef(
        url=f"https://github.com/{owner}/{name}",
        owner=owner,
        name=name,
        clone_url=f"https://github.com/{owner}/{name}.git",
        local_path=settings.repos_dir / f"{owner}__{name}",
    )


def git(args: list[str], cwd: Path | None = None, timeout: int = 900, strip: bool = True) -> str:
    settings = get_settings()
    cmd = ["git"]
    if settings.github_token:
        # Pass the token as a header rather than embedding it in the remote URL,
        # so it never lands in .git/config.
        cmd += ["-c", f"http.extraHeader=Authorization: Bearer {settings.github_token}"]
    proc = subprocess.run(
        cmd + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise RepoSourceError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    # Callers parsing \x1e/\x1f-delimited output pass strip=False: Python treats
    # those separators as whitespace.
    return proc.stdout.strip() if strip else proc.stdout


def sync_checkout(ref: RepoRef) -> None:
    """Clone (or fast-forward) the repository.

    A full clone: line-level history (`git log -L`, `git blame`) needs every
    historical blob, and on a partial clone each one becomes a network fetch.
    """
    if ref.clone_url is None:
        return  # local checkout: index it as-is
    if (ref.local_path / ".git").exists() and _is_partial_clone(ref.local_path):
        shutil.rmtree(ref.local_path)  # from an earlier blobless clone; re-clone in full
    if (ref.local_path / ".git").exists():
        git(["fetch", "--prune", "origin"], cwd=ref.local_path)
        branch = git(["rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=ref.local_path)
        git(["reset", "--hard", branch], cwd=ref.local_path)
        return
    ref.local_path.parent.mkdir(parents=True, exist_ok=True)
    git(["clone", "--no-tags", ref.clone_url, str(ref.local_path)])


def _is_partial_clone(path: Path) -> bool:
    try:
        return bool(git(["config", "--get", "remote.origin.partialclonefilter"], cwd=path))
    except RepoSourceError:  # key not set
        return False


def head_info(path: Path) -> tuple[str, str]:
    sha = git(["rev-parse", "HEAD"], cwd=path)
    try:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    except RepoSourceError:
        branch = "HEAD"
    return sha, branch


def tracked_files(path: Path) -> list[tuple[str, str]]:
    """(path, blob_sha) for every file tracked at HEAD."""
    out = git(["ls-files", "-s", "-z"], cwd=path)
    files = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, file_path = entry.split("\t", 1)
        mode, blob_sha, _stage = meta.split(" ")
        if mode.startswith("160000"):  # submodule
            continue
        files.append((file_path, blob_sha))
    return files
