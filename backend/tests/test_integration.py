"""End-to-end tests against PostgreSQL: index a small git repository through the
API, then exercise retrieval, the graph, impact, evolution, investigations,
incremental re-indexing, the explorer and job resumption.

The embedding model is replaced by a deterministic bag-of-words embedder so the
tests are fast and need no model download.
"""

import hashlib
import json
import re
import subprocess
import textwrap
import time
from pathlib import Path

import numpy as np
import psycopg
import pytest

from tests.conftest import TEST_DATABASE

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ fixtures


def _admin_url() -> str:
    from app.config import get_settings
    return get_settings().database_url.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture(scope="session", autouse=True)
def fresh_database():
    try:
        with psycopg.connect(_admin_url(), autocommit=True, connect_timeout=3) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DATABASE}")
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")


class BagOfWordsEmbedder:
    """Deterministic stand-in for the embedding model: hashed identifier tokens."""

    def __init__(self, dim: int):
        self.dim = dim

    def _embed(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v + 1e-3

    def embed_documents(self, texts):
        self.calls = getattr(self, "calls", 0) + len(texts)
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture(scope="session")
def embedder():
    from app.config import get_settings
    from app.ingestion import store
    from app.retrieval import hybrid

    fake = BagOfWordsEmbedder(get_settings().embedding_dim)
    mp = pytest.MonkeyPatch()
    mp.setattr(store, "get_embedder", lambda: fake)
    mp.setattr(hybrid, "get_embedder", lambda: fake)
    yield fake
    mp.undo()


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = {"GIT_AUTHOR_NAME": "Ann Author", "GIT_AUTHOR_EMAIL": "ann@example.com",
           "GIT_COMMITTER_NAME": "Ann Author", "GIT_COMMITTER_EMAIL": "ann@example.com",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, path: str, text: str) -> None:
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip())


QUEUE_V1 = '''
    class Queue:
        """A named queue of jobs."""

        def enqueue(self, func):
            """Enqueue a function call."""
            return self.enqueue_call(func)

        def enqueue_call(self, func):
            return Job(func)


    class Job:
        def __init__(self, func):
            self.func = func
    '''

QUEUE_V2 = QUEUE_V1.replace("            return Job(func)",
                            "            job = Job(func)\n            job.save()\n"
                            "            return job")
QUEUE_V2 = QUEUE_V2.replace("            self.func = func",
                            "            self.func = func\n\n        def save(self):\n"
                            "            return True")


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("sample") / "sample"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md", "# Sample\n\nA tiny job queue used in tests.\n")
    _write(repo, "pkg/__init__.py", "from .queue import Queue\n")
    _write(repo, "pkg/queue.py", QUEUE_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add the queue", date="2024-01-01T10:00:00")
    _write(repo, "pkg/worker.py", '''
        from pkg import Queue


        def run(func):
            """Enqueue work from the worker."""
            q = Queue()
            return q.enqueue(func)
        ''')
    _write(repo, "tests/test_queue.py", '''
        from pkg.queue import Queue


        def test_enqueue():
            assert Queue().enqueue(print)
        ''')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add a worker (#3)", date="2024-02-01T10:00:00")
    _write(repo, "pkg/queue.py", QUEUE_V2)
    _git(repo, "commit", "-q", "-am", "Persist jobs when enqueued", date="2024-03-01T10:00:00")
    return repo


@pytest.fixture(scope="session")
def client(embedder):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/repos").status_code == 401  # everything needs an account
        r = c.post("/api/auth/signup", json={"email": "Ann@Example.com", "name": "Ann",
                                              "password": "correct horse battery"})
        assert r.status_code == 201, r.text
        yield c


def _wait_ready(client, repo_id: int, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        repo = client.get(f"/api/repos/{repo_id}").json()
        if repo["status"] in ("ready", "failed"):
            assert repo["status"] == "ready", repo["error"]
            return repo
        time.sleep(0.2)
    raise AssertionError("indexing did not finish")


@pytest.fixture(scope="session")
def indexed(client, sample_repo) -> dict:
    created = client.post("/api/repos", json={"url": str(sample_repo)}).json()
    return _wait_ready(client, created["id"])


def _ask(client, repo_id: int, body: dict) -> dict[str, list]:
    events: dict[str, list] = {}
    with client.stream("POST", f"/api/repos/{repo_id}/ask", json=body) as resp:
        text = "".join(resp.iter_text())
    for block in text.strip().split("\n\n"):
        event = re.search(r"^event: (.*)$", block, re.M).group(1)
        data = json.loads(re.search(r"^data: (.*)$", block, re.M).group(1))
        events.setdefault(event, []).append(data)
    return events


def _line(repo: Path, path: str, needle: str) -> int:
    lines = (repo / path).read_text().splitlines()
    return next(i for i, ln in enumerate(lines, 1) if needle in ln)


# --------------------------------------------------------------------- tests


def test_migrations_are_recorded_and_idempotent(client):
    from app.config import get_settings
    from app.db import MIGRATIONS_DIR, migrate
    assert migrate(get_settings().database_url) == []
    with psycopg.connect(get_settings().database_url) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert sorted(versions) == sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))


def test_indexing_builds_code_history_and_graph(indexed):
    stats = indexed["stats"]
    assert stats["files"] == 5
    assert stats["history"]["commits"] == 3
    assert stats["graph"]["edge_kinds"]["calls"] >= 3
    assert stats["index"]["embedded"] > 0


def test_graph_neighbourhood(client, indexed, sample_repo):
    line = _line(sample_repo, "pkg/queue.py", "def enqueue_call")
    g = client.get(f"/api/repos/{indexed['id']}/graph",
                   params={"path": "pkg/queue.py", "start_line": line}).json()
    assert g["target"]["label"] == "Queue.enqueue_call"
    callers = {n["label"] for n in g["nodes"] if n.get("relation") == "caller"}
    callees = {n["label"] for n in g["nodes"] if n.get("relation") == "callee"}
    assert "Queue.enqueue" in callers
    assert {"Job", "Job.save"} <= callees
    # depth 2: the worker reaches enqueue_call through Queue.enqueue
    assert "run" in {n["label"] for n in g["nodes"] if n.get("depth") == 2}
    history = [n for n in g["nodes"] if n["side"] == "history"]
    assert {n["role"] for n in history if n["kind"] == "commit"} >= {"introduced"}


def test_impact(client, indexed, sample_repo):
    line = _line(sample_repo, "pkg/queue.py", "def enqueue_call")
    imp = client.get(f"/api/repos/{indexed['id']}/impact",
                     params={"path": "pkg/queue.py", "start_line": line}).json()
    labels = {d["label"]: d for d in imp["dependents"]}
    assert labels["Queue.enqueue"]["depth"] == 1
    assert labels["run"]["depth"] == 2 and labels["run"]["through"] == "Queue.enqueue"
    assert labels["test_enqueue"]["is_test"]
    assert imp["summary"]["tests"] >= 1
    assert imp["risk"]["level"] in ("low", "medium", "high") and imp["risk"]["reasons"]


def test_search_pulls_graph_neighbours_and_impact(client, indexed, sample_repo):
    line = _line(sample_repo, "pkg/queue.py", "def enqueue_call")
    hits = client.post(f"/api/repos/{indexed['id']}/search", json={
        "question": "what would break if I change this?",
        "focus": {"path": "pkg/queue.py", "start_line": line, "end_line": line + 3}}).json()
    assert hits[0]["matched_by"][0] == "focus"
    # Callees come in as graph neighbours (the caller, Queue.enqueue, shares the
    # focused chunk: the class is small enough to be one chunk).
    relations = {h["metadata"].get("graph") for h in hits}
    assert "called by Queue.enqueue_call" in relations
    impact = next(h for h in hits if h["source_type"] == "graph")
    assert impact["symbol_name"] == "Queue.enqueue_call"
    assert "- Queue.enqueue (pkg/queue.py" in impact["content"]


def test_evolution(client, indexed, sample_repo):
    line = _line(sample_repo, "pkg/queue.py", "def enqueue_call")
    evo = client.get(f"/api/repos/{indexed['id']}/evolution",
                     params={"path": "pkg/queue.py", "start_line": line}).json()
    assert evo["symbol"] == "Queue.enqueue_call"
    versions = evo["versions"]
    assert [v["subject"] for v in versions] == ["Add the queue", "Persist jobs when enqueued"]
    assert versions[0]["role"] == "introduced"
    assert "return Job(func)" in versions[0]["code"]
    assert "job.save()" in versions[1]["code"] and versions[1]["added"] >= 2


def test_investigation_follow_up(client, indexed):
    rid = indexed["id"]
    first = _ask(client, rid, {"question": "What does Queue.enqueue_call do?"})
    inv_id = first["investigation"][0]["id"]
    assert first["done"][0]["provider"] == "briefing"
    assert "[S" in "".join(d["text"] for d in first["delta"])

    follow = _ask(client, rid, {"question": "who calls it?", "investigation_id": inv_id})
    assert follow["investigation"][0] == {"id": inv_id, "follow_up": True}
    # "it" was resolved through the previous question: the callers are evidence.
    assert any("calls Queue.enqueue_call" in str(e["metadata"].get("graph"))
               for e in follow["sources"][0])

    inv = client.get(f"/api/investigations/{inv_id}").json()
    assert [t["question"] for t in inv["turns"]] == ["What does Queue.enqueue_call do?",
                                                    "who calls it?"]
    assert all(t["status"] == "done" and t["evidence"] for t in inv["turns"])
    assert client.patch(f"/api/investigations/{inv_id}", json={"title": "Enqueue"}).json()[
        "title"] == "Enqueue"
    assert any(i["id"] == inv_id for i in client.get(f"/api/repos/{rid}/investigations").json())
    assert client.delete(f"/api/investigations/{inv_id}").status_code == 204
    assert client.get(f"/api/investigations/{inv_id}").status_code == 404


def test_agent_mode_without_model_falls_back(client, indexed):
    ev = _ask(client, indexed["id"], {"question": "How are jobs saved?", "mode": "agent"})
    assert "language model" in ev["step"][0]["summary"]
    assert ev["done"]


def test_explorer(client, indexed):
    rid = indexed["id"]
    ov = client.get(f"/api/repos/{rid}/graph/overview", params={"level": "dir"}).json()
    ids = {n["id"] for n in ov["nodes"]}
    assert {"dir:pkg", "dir:tests"} <= ids
    assert any(e["src"] == "dir:tests" and e["dst"] == "dir:pkg" for e in ov["edges"])
    found = client.get(f"/api/repos/{rid}/graph/search", params={"q": "enqueue"}).json()
    assert found[0]["label"] in ("Queue.enqueue", "Queue.enqueue_call")
    node = next(n for n in found if n["label"] == "Queue.enqueue")
    detail = client.get(f"/api/repos/{rid}/graph/nodes/{node['id']}").json()
    assert detail["degree"]["calls"]["in"] >= 2
    out = client.get(f"/api/repos/{rid}/graph/nodes/{node['id']}/expand",
                     params={"kinds": "calls", "direction": "in"}).json()
    assert {n["label"] for n in out["nodes"]} >= {"run", "test_enqueue"}


def test_accounts_and_isolation(client, indexed, sample_repo):
    from fastapi.testclient import TestClient

    from app.main import app

    me = client.get("/api/auth/me").json()
    assert me["email"] == "ann@example.com"
    rid = indexed["id"]
    other = TestClient(app)  # no `with`: the app's lifespan belongs to `client`
    assert other.post("/api/auth/signup", json={
        "email": "ann@example.com", "name": "Ann 2",
        "password": "another password"}).status_code == 409
    assert other.post("/api/auth/signup", json={
        "email": "bob@example.com", "password": "bobs secret pass"}).status_code == 422
    missing = other.post("/api/auth/signup", json={"email": "bob@example.com",
                                                    "password": "bobs secret pass"})
    assert "bobs secret pass" not in missing.text  # never echo submitted values
    assert other.post("/api/auth/signup", json={
        "email": "bob@example.com", "name": "   ",
        "password": "bobs secret pass"}).status_code == 422
    assert other.post("/api/auth/signup", json={
        "email": "bob@example.com", "name": "Bob", "password": "short"}).status_code == 422
    assert other.post("/api/auth/signup", json={
        "email": "bob@example.com", "name": "Bob",
        "password": "bobs secret pass"}).status_code == 201
    # Bob cannot see or reach Ann's repository or investigations.
    assert other.get("/api/repos").json() == []
    assert other.get(f"/api/repos/{rid}").status_code == 404
    assert other.get(f"/api/repos/{rid}/files").status_code == 404
    inv = client.get(f"/api/repos/{rid}/investigations").json()
    if inv:
        assert other.get(f"/api/investigations/{inv[0]['id']}").status_code == 404
    # Adding the same repository shares the index instead of rebuilding it.
    shared = other.post("/api/repos", json={"url": str(sample_repo)}).json()
    assert shared["id"] == rid and shared["status"] == "ready"
    assert other.get(f"/api/repos/{rid}/investigations").json() == []
    # Removing it only removes it from Bob's list.
    assert other.delete(f"/api/repos/{rid}").status_code == 204
    assert other.get("/api/repos").json() == []
    assert client.get(f"/api/repos/{rid}").json()["status"] == "ready"

    other.post("/api/auth/logout")
    assert other.get("/api/auth/me").status_code == 401
    assert other.post("/api/auth/login", json={
        "email": "BOB@example.com", "password": "wrong password"}).status_code == 401
    assert other.post("/api/auth/login", json={
        "email": "BOB@example.com", "password": "bobs secret pass"}).status_code == 200
    assert other.get("/api/auth/me").json()["email"] == "bob@example.com"


def test_reindex_is_incremental(client, indexed, sample_repo, embedder):
    rid = indexed["id"]
    before = embedder.calls
    client.post(f"/api/repos/{rid}/reindex")
    repo = _wait_ready(client, rid)
    assert repo["stats"]["index"]["embedded"] == 0
    assert repo["stats"]["history"]["index"]["embedded"] == 0
    assert embedder.calls == before  # nothing re-embedded

    _write(sample_repo, "pkg/utils.py", "def helper():\n    return 42\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-q", "-m", "Add a helper", date="2024-04-01T10:00:00")
    client.post(f"/api/repos/{rid}/reindex")
    repo = _wait_ready(client, rid)
    assert 0 < repo["stats"]["index"]["embedded"] <= 2  # the new file's chunk(s)
    assert repo["stats"]["history"]["index"]["embedded"] == 1  # the new commit
    assert repo["stats"]["files"] == 6


def test_interrupted_indexing_resumes(client, indexed):
    from app.db import connection
    from app.main import _resume_interrupted

    rid = indexed["id"]
    with connection() as conn:
        conn.execute("UPDATE repositories SET status = 'embedding', progress = '{}' "
                     "WHERE id = %s", (rid,))
    _resume_interrupted()
    assert _wait_ready(client, rid)["status"] == "ready"

    with connection() as conn:
        conn.execute("UPDATE repositories SET status = 'history', "
                     "progress = '{\"resumes\": 2}' WHERE id = %s", (rid,))
    _resume_interrupted()
    repo = client.get(f"/api/repos/{rid}").json()
    assert repo["status"] == "failed" and "interrupted repeatedly" in repo["error"]
