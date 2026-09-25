import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import auth, investigations
from app.answering.agent import agent_model, investigate
from app.answering.answer import indexed_sources, stream_answer
from app.config import get_settings
from app.db import close_db, connection, init_db
from app.graph import explore
from app.graph.build import rebuild_graph
from app.graph.query import impact, neighborhood
from app.history.evolution import evolution
from app.history.timeline import commit_detail, timeline
from app.ingestion.pipeline import ingest_repository
from app.ingestion.repo_source import RepoSourceError, parse_repo_ref
from app.retrieval.hybrid import Focus, search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# Ingestion is CPU/IO heavy and long-running; keep it off the request threads.
_ingest_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest")

IN_PROGRESS = ("queued", "cloning", "parsing", "embedding", "history", "graph")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _resume_interrupted()
    yield
    _ingest_pool.shutdown(wait=False, cancel_futures=True)
    close_db()


MAX_RESUMES = 2


def _resume_interrupted() -> None:
    """Indexing that a restart cut short is started again (cheaply, since
    re-indexing is incremental). A job interrupted repeatedly is marked failed
    rather than retried forever: it may be what is crashing the server."""
    with connection() as conn:
        rows = conn.execute(
            """SELECT id, coalesce((progress->>'resumes')::int, 0) AS resumes
               FROM repositories WHERE status = ANY(%s)""", (list(IN_PROGRESS),)).fetchall()
    for r in rows:
        if r["resumes"] >= MAX_RESUMES:
            with connection() as conn:
                conn.execute(
                    "UPDATE repositories SET status = 'failed', progress = '{}'::jsonb, "
                    "error = 'Indexing was interrupted repeatedly; re-index to retry' "
                    "WHERE id = %s", (r["id"],))
            continue
        log.info("Resuming interrupted indexing of repo %s", r["id"])
        _start_ingest(r["id"], resumes=r["resumes"] + 1)


app = FastAPI(title="Codebase Archaeologist", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,  # the session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Validation errors without the submitted values: FastAPI echoes the
    request body by default, which would send passwords back (and into logs)."""
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()]})

# Everything else needs a logged-in user, who only sees their own repositories
# and investigations (see auth.authorize).
api = APIRouter(dependencies=[Depends(auth.authorize)])
CurrentUser = auth.CurrentUser

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


@api.post("/api/repos", status_code=202)
def create_repo(body: CreateRepo, user: CurrentUser):
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
        conn.execute("INSERT INTO user_repositories (user_id, repo_id) VALUES (%s, %s) "
                     "ON CONFLICT DO NOTHING", (user["id"], row["id"]))
    # New (or previously failed) repos start indexing; an already-indexed repo is
    # returned as-is and can be refreshed explicitly via /reindex.
    if row["status"] in ("queued", "failed"):
        _start_ingest(row["id"])
    return _get_repo(row["id"])


def _start_ingest(repo_id: int, resumes: int = 0) -> None:
    with connection() as conn:
        conn.execute("UPDATE repositories SET status = 'queued', error = NULL, progress = %s "
                     "WHERE id = %s", (json.dumps({"resumes": resumes} if resumes else {}),
                                       repo_id))
    _ingest_pool.submit(ingest_repository, repo_id)


@api.get("/api/repos")
def list_repos(user: CurrentUser):
    with connection() as conn:
        return conn.execute(
            f"""SELECT {REPO_COLUMNS} FROM repositories
                WHERE id IN (SELECT repo_id FROM user_repositories WHERE user_id = %s)
                ORDER BY created_at DESC""", (user["id"],)).fetchall()


@api.get("/api/repos/{repo_id}")
def get_repo(repo_id: int):
    return _get_repo(repo_id)


@api.post("/api/repos/{repo_id}/reindex", status_code=202)
def reindex_repo(repo_id: int):
    repo = _get_repo(repo_id)
    if repo["status"] in IN_PROGRESS:
        raise HTTPException(409, "Indexing already in progress")
    _start_ingest(repo_id)
    return _get_repo(repo_id)


@api.delete("/api/repos/{repo_id}", status_code=204)
def delete_repo(repo_id: int, user: CurrentUser):
    """Remove the repository from this user's list (and their investigations of
    it); the index itself is deleted once no user has the repository."""
    with connection() as conn, conn.transaction():
        conn.execute("DELETE FROM investigations WHERE repo_id = %s AND user_id = %s",
                     (repo_id, user["id"]))
        conn.execute("DELETE FROM user_repositories WHERE repo_id = %s AND user_id = %s",
                     (repo_id, user["id"]))
        conn.execute("DELETE FROM repositories WHERE id = %s AND NOT EXISTS "
                     "(SELECT 1 FROM user_repositories WHERE repo_id = %s)", (repo_id, repo_id))


# ----------------------------------------------------------------- exploration


@api.get("/api/repos/{repo_id}/files")
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


@api.get("/api/repos/{repo_id}/file")
def get_file(repo_id: int, path: str = Query(...)):
    with connection() as conn:
        f = conn.execute(
            "SELECT id, path, language, line_count, content FROM files "
            "WHERE repo_id = %s AND path = %s", (repo_id, path)).fetchone()
        if f is None:
            raise HTTPException(404, "File not indexed")
        # Graph symbols are per definition (methods of small classes included) and
        # carry call degree; chunk symbols are the fallback (docs, no graph yet).
        symbols = conn.execute(
            """SELECT n.id, n.data->>'kind' AS kind, n.label AS name, n.start_line, n.end_line,
                      count(e.src) FILTER (WHERE e.dst = n.id) AS callers,
                      count(e.dst) FILTER (WHERE e.src = n.id) AS callees
               FROM graph_nodes n
               LEFT JOIN graph_edges e ON (e.dst = n.id OR e.src = n.id) AND e.kind = 'calls'
               WHERE n.repo_id = %s AND n.kind = 'symbol' AND n.path = %s
               GROUP BY n.id ORDER BY n.start_line, n.end_line DESC""",
            (repo_id, path)).fetchall()
        if not symbols:
            symbols = conn.execute(
                """SELECT DISTINCT ON (symbol_name, start_line) id, symbol_kind AS kind,
                          symbol_name AS name, start_line, end_line
                   FROM chunks WHERE file_id = %s AND symbol_name IS NOT NULL
                   ORDER BY start_line, symbol_name, end_line DESC""", (f["id"],)).fetchall()
    return {**f, "symbols": symbols}


# ----------------------------------------------------------------------- history


@api.get("/api/repos/{repo_id}/history")
def get_history(repo_id: int, path: str = Query(...), start_line: int | None = None,
                end_line: int | None = None):
    """Every commit that changed a line range (or file), newest first, with the
    range diff and the pull requests / issues behind each commit."""
    repo = _repo_with_checkout(repo_id)
    return timeline(repo, path, start_line, end_line)


@api.get("/api/repos/{repo_id}/evolution")
def get_evolution(repo_id: int, path: str = Query(...), start_line: int = Query(..., ge=1),
                  end_line: int | None = None):
    """The code's versions over time, oldest first: each commit that changed it,
    the code as it was after that commit, the diff, and the PR/issues behind it.
    A selection inside a function is widened to the whole function."""
    repo = _repo_with_checkout(repo_id)
    return evolution(repo, path, start_line, end_line or start_line)


@api.get("/api/repos/{repo_id}/commits/{sha}")
def get_commit(repo_id: int, sha: str, path: str | None = None):
    if not re.fullmatch(r"[0-9a-f]{4,40}", sha):
        raise HTTPException(422, "Expected a (short) hex commit SHA")
    repo = _repo_with_checkout(repo_id)
    detail = commit_detail(repo, sha, path)
    if detail is None:
        raise HTTPException(404, "Commit not found")
    return detail


# ------------------------------------------------------------------------- graph


@api.get("/api/repos/{repo_id}/graph")
def get_graph(repo_id: int, path: str = Query(...), start_line: int | None = None,
              end_line: int | None = None, history: bool = True):
    """The knowledge-graph neighbourhood of a symbol (selected lines) or file:
    callers and callees two hops out, base classes, imports, dependencies used,
    and the issue -> PR -> commit chain behind it."""
    repo = _repo_with_graph(repo_id)
    result = neighborhood(repo, path, start_line, end_line, history=history)
    if result is None:
        raise HTTPException(404, "Nothing in the graph at that location")
    return result


@api.get("/api/repos/{repo_id}/impact")
def get_impact(repo_id: int, path: str = Query(...), start_line: int | None = None,
               end_line: int | None = None):
    """What could break if this code changes: everything that reaches it through
    calls, inheritance or imports (3 hops), the tests among them, files that
    change in the same commits, and a heuristic risk level with its reasons."""
    repo = _repo_with_graph(repo_id)
    result = impact(repo, path, start_line, end_line)
    if result is None:
        raise HTTPException(404, "Nothing in the graph at that location")
    return result


@api.get("/api/repos/{repo_id}/graph/overview")
def graph_overview(repo_id: int, level: str = Query("file", pattern="^(file|dir)$"),
                   depth: int = Query(2, ge=1, le=6), limit: int = Query(150, ge=10, le=1000),
                   tests: bool = True, packages: bool = True):
    """The repository's architecture: files (or directories, `depth` segments
    deep) with dependency edges aggregated from imports, calls and inheritance,
    plus the external packages they use."""
    _repo_with_graph(repo_id)
    return explore.overview(repo_id, level, depth, limit, tests, packages)


@api.get("/api/repos/{repo_id}/graph/search")
def graph_search(repo_id: int, q: str = Query(..., min_length=1),
                 kinds: Annotated[list[str] | None, Query()] = None,
                 limit: int = Query(20, ge=1, le=100)):
    _repo_with_graph(repo_id)
    return explore.search(repo_id, q, kinds, limit)


@api.get("/api/repos/{repo_id}/graph/nodes/{node_id}")
def graph_node(repo_id: int, node_id: int):
    detail = explore.node_detail(repo_id, node_id)
    if detail is None:
        raise HTTPException(404, "Node not found")
    return detail


@api.get("/api/repos/{repo_id}/graph/nodes/{node_id}/expand")
def graph_expand(repo_id: int, node_id: int,
                 kinds: Annotated[list[str] | None, Query()] = None,
                 direction: str = Query("both", pattern="^(in|out|both)$"),
                 since: str | None = None, until: str | None = None,
                 limit: int = Query(40, ge=1, le=500)):
    """A node's neighbours along the given edge kinds (all by default), with
    commits / PRs / issues limited to [since, until] (ISO dates) when given."""
    result = explore.expand(repo_id, node_id, kinds, direction, since, until, limit)
    if result is None:
        raise HTTPException(404, "Node not found")
    return result


@api.post("/api/repos/{repo_id}/graph/rebuild")
def rebuild_repo_graph(repo_id: int):
    """Rebuild only the graph from the stored index (no clone, parse or embed)."""
    repo = _get_repo(repo_id)
    if repo["status"] != "ready":
        raise HTTPException(409, f"Repository is not ready (status: {repo['status']})")
    stats = rebuild_graph(repo_id)
    with connection() as conn:
        conn.execute("UPDATE repositories SET stats = stats || jsonb_build_object("
                     "'graph', %s::jsonb) WHERE id = %s", (json.dumps(stats), repo_id))
    return _get_repo(repo_id)


def _repo_with_graph(repo_id: int) -> dict:
    repo = _repo_with_checkout(repo_id)
    with connection() as conn:
        built = conn.execute("SELECT 1 FROM graph_nodes WHERE repo_id = %s LIMIT 1",
                             (repo_id,)).fetchone()
    if built is None:
        raise HTTPException(409, "The knowledge graph has not been built for this repository "
                                 "yet; re-index it or rebuild the graph.")
    return repo


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
    # Continue a saved investigation (follow-up); omitted = start a new one.
    investigation_id: int | None = None
    # "agent": a model gathers evidence over several tool-using steps first.
    mode: Literal["answer", "agent"] = "answer"


def _ready_repo(repo_id: int) -> dict:
    repo = _get_repo(repo_id)
    if repo["status"] != "ready":
        raise HTTPException(409, f"Repository is not ready (status: {repo['status']})")
    return repo


@api.post("/api/repos/{repo_id}/search")
def search_repo(repo_id: int, body: AskIn):
    _ready_repo(repo_id)
    focus = Focus(**body.focus.model_dump()) if body.focus else None
    return [c.to_dict() for c in search(repo_id, body.question, body.limit, focus)]


@api.post("/api/repos/{repo_id}/ask")
def ask(repo_id: int, body: AskIn, user: CurrentUser):
    """Server-sent events: `investigation` ({id}), in agent mode `step`* (each tool
    call and what it found), `sources` (the evidence, S1..Sn), `delta`* (answer
    text), then `done` or `error`. The turn is saved to the investigation, and
    earlier turns give a follow-up its context."""
    repo = {**_ready_repo(repo_id), **_repo_with_checkout(repo_id)}
    focus = Focus(**body.focus.model_dump()) if body.focus else None
    if body.investigation_id is not None:
        inv = investigations.get(body.investigation_id, with_turns=False)
        if inv is None or inv["repo_id"] != repo_id or inv["user_id"] != user["id"]:
            raise HTTPException(404, "Investigation not found for this repository")
        inv_id = inv["id"]
    else:
        inv_id = investigations.create(repo_id, user["id"], body.question)
    prior = investigations.prior_turns(inv_id)

    def events():
        turn = {"evidence": [], "steps": [], "answer": [], "answered_by": None,
                "status": "stopped", "error": None}
        try:
            yield _sse("investigation", {"id": inv_id, "follow_up": bool(prior)})
            query = investigations.retrieval_query(body.question, prior)
            chunks = search(repo_id, query, body.limit, focus, question_only=body.question)
            if body.mode == "agent":
                for ev in _agent_steps(repo, body.question, chunks, prior):
                    if ev["event"] == "pool":
                        chunks = ev["data"]
                    else:
                        turn["steps"].append(ev["data"])
                        yield _sse("step", ev["data"])
            turn["evidence"] = [dict(c.to_dict(), ref=f"S{i}") for i, c in enumerate(chunks, 1)]
            yield _sse("sources", turn["evidence"])
            for ev in stream_answer(body.question, repo["name"], chunks, focus,
                                    indexed_sources(repo["stats"]), prior):
                if ev["event"] == "delta":
                    turn["answer"].append(ev["data"]["text"])
                elif ev["event"] == "done":
                    turn["status"] = "done"
                    turn["answered_by"] = {"provider": ev["data"].get("provider"),
                                           "model": ev["data"].get("model")}
                elif ev["event"] == "error":
                    turn["status"], turn["error"] = "error", ev["data"]["message"]
                yield _sse(ev["event"], ev["data"])
        finally:
            # Also runs when the client disconnects mid-answer ("stopped").
            investigations.save_turn(
                inv_id, body.question, body.focus.model_dump() if body.focus else None,
                body.mode, json.loads(json.dumps(turn["steps"], default=str)),
                json.loads(json.dumps(turn["evidence"], default=str)),
                "".join(turn["answer"]), turn["answered_by"], turn["status"], turn["error"])

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _agent_steps(repo: dict, question: str, seed: list, prior: list):
    model = agent_model()
    if model is None:
        yield {"event": "step", "data": {
            "n": 1, "tool": None, "input": {}, "added": [],
            "summary": "Agent mode needs a language model (a Gemini API key, a local Ollama "
                       "model or a Claude API key); answered from single-pass retrieval "
                       "instead."}}
        return
    context = "".join(f"<earlier_question>{t.question}</earlier_question>\n" for t in prior)
    yield from investigate(repo, question, seed, model, context)


# ---------------------------------------------------------------- investigations


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@api.get("/api/repos/{repo_id}/investigations")
def list_investigations(repo_id: int, user: CurrentUser):
    _get_repo(repo_id)
    return investigations.list_for_repo(repo_id, user["id"])


@api.get("/api/investigations/{investigation_id}")
def get_investigation(investigation_id: int):
    inv = investigations.get(investigation_id)
    if inv is None:
        raise HTTPException(404, "Investigation not found")
    return inv


@api.patch("/api/investigations/{investigation_id}")
def rename_investigation(investigation_id: int, body: RenameIn):
    if not investigations.rename(investigation_id, body.title):
        raise HTTPException(404, "Investigation not found")
    return investigations.get(investigation_id, with_turns=False)


@api.delete("/api/investigations/{investigation_id}", status_code=204)
def delete_investigation(investigation_id: int):
    if not investigations.delete(investigation_id):
        raise HTTPException(404, "Investigation not found")


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


app.include_router(api)
