import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.answering.answer import indexed_sources, stream_answer
from app.config import get_settings
from app.db import close_db, connection, init_db
from app.history.timeline import commit_detail, timeline
from app.ingestion.pipeline import ingest_repository
from app.ingestion.repo_source import RepoSourceError, parse_repo_ref
from app.retrieval.hybrid import Focus, search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Ingestion is CPU/IO heavy and long-running; keep it off the request threads.
_ingest_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest")

IN_PROGRESS = ("queued", "cloning", "parsing", "embedding", "history")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with connection() as conn:
        conn.execute(
            "UPDATE repositories SET status = 'failed', error = 'Interrupted by server restart' "
            "WHERE status = ANY(%s)", (list(IN_PROGRESS),))
    yield
    _ingest_pool.shutdown(wait=False, cancel_futures=True)
    close_db()


app = FastAPI(title="Codebase Archaeologist", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPO_COLUMNS = """id, url, owner, name, default_branch, head_sha, status, progress, stats, error,
                  created_at, indexed_at"""


def _get_repo(repo_id: int) -> dict:
    with connection() as conn:
        row = conn.execute(f"SELECT {REPO_COLUMNS} FROM repositories WHERE id = %s",
                           (repo_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Repository not found")
    return row


# ---------------------------------------------------------------- repositories


class CreateRepo(BaseModel):
    url: str = Field(description="GitHub URL, owner/repo, or absolute path to a local checkout")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/repos", status_code=202)
def create_repo(body: CreateRepo):
    try:
        ref = parse_repo_ref(body.url)
    except RepoSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connection() as conn:
        row = conn.execute(
            f"""INSERT INTO repositories (url, owner, name, status) VALUES (%s, %s, %s, 'queued')
                ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url
                RETURNING {REPO_COLUMNS}""",
            (ref.url, ref.owner, ref.name),
        ).fetchone()
    # New (or previously failed) repos start indexing; an already-indexed repo is
    # returned as-is and can be refreshed explicitly via /reindex.
    if row["status"] in ("queued", "failed"):
        _start_ingest(row["id"])
    return _get_repo(row["id"])


def _start_ingest(repo_id: int) -> None:
    with connection() as conn:
        conn.execute("UPDATE repositories SET status = 'queued', error = NULL WHERE id = %s",
                     (repo_id,))
    _ingest_pool.submit(ingest_repository, repo_id)


@app.get("/api/repos")
def list_repos():
    with connection() as conn:
        return conn.execute(
            f"SELECT {REPO_COLUMNS} FROM repositories ORDER BY created_at DESC").fetchall()


@app.get("/api/repos/{repo_id}")
def get_repo(repo_id: int):
    return _get_repo(repo_id)


@app.post("/api/repos/{repo_id}/reindex", status_code=202)
def reindex_repo(repo_id: int):
    repo = _get_repo(repo_id)
    if repo["status"] in IN_PROGRESS:
        raise HTTPException(409, "Indexing already in progress")
    _start_ingest(repo_id)
    return _get_repo(repo_id)


@app.delete("/api/repos/{repo_id}", status_code=204)
def delete_repo(repo_id: int):
    with connection() as conn:
        conn.execute("DELETE FROM repositories WHERE id = %s", (repo_id,))


# ----------------------------------------------------------------- exploration


@app.get("/api/repos/{repo_id}/files")
def list_files(repo_id: int):
    _get_repo(repo_id)
    with connection() as conn:
        return conn.execute(
            """SELECT f.path, f.language, f.line_count,
                      count(c.id) FILTER (WHERE c.symbol_name IS NOT NULL) AS symbols
               FROM files f LEFT JOIN chunks c ON c.file_id = f.id
               WHERE f.repo_id = %s GROUP BY f.id ORDER BY f.path""",
            (repo_id,),
        ).fetchall()


@app.get("/api/repos/{repo_id}/file")
def get_file(repo_id: int, path: str = Query(...)):
    with connection() as conn:
        f = conn.execute(
            "SELECT id, path, language, line_count, content FROM files "
            "WHERE repo_id = %s AND path = %s", (repo_id, path)).fetchone()
        if f is None:
            raise HTTPException(404, "File not indexed")
        symbols = conn.execute(
            """SELECT id, symbol_kind AS kind, symbol_name AS name, start_line, end_line
               FROM chunks WHERE file_id = %s AND symbol_name IS NOT NULL
               ORDER BY start_line, end_line DESC""", (f["id"],)).fetchall()
    return {**f, "symbols": symbols}


# ----------------------------------------------------------------------- history


@app.get("/api/repos/{repo_id}/history")
def get_history(repo_id: int, path: str = Query(...), start_line: int | None = None,
                end_line: int | None = None):
    """Every commit that changed a line range (or file), newest first, with the
    range diff and the pull requests / issues behind each commit."""
    repo = _repo_with_checkout(repo_id)
    return timeline(repo, path, start_line, end_line)


@app.get("/api/repos/{repo_id}/commits/{sha}")
def get_commit(repo_id: int, sha: str, path: str | None = None):
    if not re.fullmatch(r"[0-9a-f]{4,40}", sha):
        raise HTTPException(422, "Expected a (short) hex commit SHA")
    repo = _repo_with_checkout(repo_id)
    detail = commit_detail(repo, sha, path)
    if detail is None:
        raise HTTPException(404, "Commit not found")
    return detail


def _repo_with_checkout(repo_id: int) -> dict:
    with connection() as conn:
        repo = conn.execute("SELECT id, local_path, head_sha FROM repositories WHERE id = %s",
                            (repo_id,)).fetchone()
    if repo is None:
        raise HTTPException(404, "Repository not found")
    if not repo["local_path"] or not repo["head_sha"]:
        raise HTTPException(409, "Repository has not been indexed yet")
    return repo


# ------------------------------------------------------------------ investigation


class FocusIn(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    focus: FocusIn | None = None
    limit: int = Field(default=12, ge=1, le=30)


def _ready_repo(repo_id: int) -> dict:
    repo = _get_repo(repo_id)
    if repo["status"] != "ready":
        raise HTTPException(409, f"Repository is not ready (status: {repo['status']})")
    return repo


@app.post("/api/repos/{repo_id}/search")
def search_repo(repo_id: int, body: AskIn):
    _ready_repo(repo_id)
    focus = Focus(**body.focus.model_dump()) if body.focus else None
    return [c.to_dict() for c in search(repo_id, body.question, body.limit, focus)]


@app.post("/api/repos/{repo_id}/ask")
def ask(repo_id: int, body: AskIn):
    """Server-sent events: `sources` (the evidence, S1..Sn), `delta`* (answer text),
    then `done` or `error`."""
    repo = _ready_repo(repo_id)
    focus = Focus(**body.focus.model_dump()) if body.focus else None

    def events():
        chunks = search(repo_id, body.question, body.limit, focus)
        yield _sse("sources", [dict(c.to_dict(), ref=f"S{i}") for i, c in enumerate(chunks, 1)])
        for ev in stream_answer(body.question, repo["name"], chunks, focus,
                                indexed_sources(repo["stats"])):
            yield _sse(ev["event"], ev["data"])

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
