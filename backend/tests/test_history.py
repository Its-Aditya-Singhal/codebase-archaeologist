from app.history.git_log import RangeCommit
from app.history.links import pr_number_from_commit, references
from app.history.provenance import select_range_commits, trim_diff


def test_pr_number_from_commit_subjects():
    assert pr_number_from_commit("Add retry backoff (#1234)") == 1234
    assert pr_number_from_commit("Merge pull request #77 from foo/bar") == 77
    assert pr_number_from_commit("Fix #12 in worker") is None


def test_references_split_fixes_and_mentions():
    text = "Fixes #12. Closes: #14, related to #13 and org/repo#15; see &#16;"
    assert references(text) == [(12, "fixes"), (14, "fixes"), (13, "mentions")]


def _rc(sha):
    return RangeCommit(sha, "a", "a@x", "2024-01-01T00:00:00+00:00", "s", "")


def test_select_range_commits_keeps_origin_and_recent():
    commits = tuple(_rc(str(i)) for i in range(10))  # newest first
    chosen = select_range_commits(commits, recent=3)
    assert [(c.sha, role) for c, role in chosen] == [
        ("0", "modified"), ("1", "modified"), ("2", "modified"), ("9", "introduced")]
    short = select_range_commits(commits[:2], recent=3)
    assert [(c.sha, role) for c, role in short] == [("0", "modified"), ("1", "introduced")]


def test_trim_diff_drops_headers_and_truncates():
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n" + "\n".join(
        f"+line {i}" for i in range(100))
    out = trim_diff(diff).splitlines()
    assert out[0].startswith("@@") and out[-1].startswith("…") and len(out) == 41
