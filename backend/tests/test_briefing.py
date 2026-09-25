from app.answering.briefing import build_briefing
from app.retrieval.hybrid import RetrievedChunk


def _chunk(source_type, content, matched_by, metadata=None, **kw) -> RetrievedChunk:
    base = dict(id=1, path=None, language=None, symbol_kind=None, symbol_name=None,
                start_line=None, end_line=None, score=1.0)
    return RetrievedChunk(**{**base, **kw}, source_type=source_type, content=content,
                          matched_by=matched_by, metadata=metadata or {})


def test_briefing_cites_code_history_and_graph():
    chunks = [
        _chunk("code", 'def fetch(self):\n    """Load the job from Redis."""\n', ["focus"],
               path="rq/job.py", symbol_kind="method", symbol_name="Job.fetch",
               start_line=10, end_line=12),
        _chunk("commit", "Add fetch\n\nFiles changed: rq/job.py", ["history", "introduced"],
               {"role": "introduced", "sha": "abc1234567890", "author": "Ann",
                "date": "2015-03-01T00:00:00", "pr": 12}),
        _chunk("pull_request", "PR #12: Fetch jobs\n\n- Jobs can now be loaded by id.",
               ["history", "merged abc"], {"number": 12, "title": "Fetch jobs"}),
        _chunk("code", "def run(): job.fetch()", ["graph", "caller"],
               {"graph": "calls Job.fetch"}, path="rq/worker.py", symbol_name="run"),
    ]
    text = build_briefing("why does this exist?", chunks)
    assert "`Job.fetch` (method) is defined in `rq/job.py` lines 10–12 [S1]" in text
    assert "*Load the job from Redis.*" in text
    assert "**Introduced** in `abc1234567` “Add fetch” by Ann on 2015-03-01 [S2]" in text
    assert "merged in PR #12 “Fetch jobs” [S3]" in text
    assert "PR #12 explains: *Jobs can now be loaded by id.* [S3]" in text
    assert "Called from `run` in `rq/worker.py` [S4]" in text
