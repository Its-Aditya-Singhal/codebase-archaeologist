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


def test_summary_line_completes_wrapped_first_sentence():
    from app.answering.briefing import _summary_line

    code = ('def escape(s):\n    """Replace the characters in\n    the string with safe '
            'sequences. Use this for HTML.\n    """\n')
    assert _summary_line(code) == "Replace the characters in the string with safe sequences."
    assert _summary_line('def f():\n    """Short summary."""\n') == "Short summary."
    assert _summary_line("# Parse the config and\n# return settings\ndef parse(): ...") == (
        "Parse the config and return settings.")


def test_briefing_does_not_present_an_unrelated_match_as_the_subject():
    chunks = [
        _chunk("code", "def test_format():\n    class User: ...\n", ["semantic"],
               path="tests/test_fmt.py", symbol_kind="function", symbol_name="test_format",
               start_line=1, end_line=2),
        _chunk("doc", "Formatting strings", ["semantic"], path="docs/fmt.rst",
               start_line=1, end_line=9),
    ]
    text = build_briefing("Which database does it use to store user sessions?", chunks)
    assert text.startswith("The retrieved sources don't directly address this question.")
    assert "is defined in" not in text
    assert "- Docs: `docs/fmt.rst` lines 1–9 [S2]" in text

    related = build_briefing("How does test_format handle formatting?", chunks)
    assert related.startswith("`test_format` (function) is defined in")
