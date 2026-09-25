"""Evolution: a piece of code's versions over time.

`git log -L` yields, for every commit that changed a line range, the diff of
that range, whose hunk header gives the range's position at that commit
(`@@ -a,b +c,d @@`). Reading the file at the commit and slicing those lines
reconstructs the code as it was, so the UI can step through versions with
the change and the pull request behind each one.
"""

import re
from functools import lru_cache
from pathlib import Path

from app.graph.query import locate
from app.history.git_log import range_history
from app.history.timeline import _linked
from app.ingestion.chunker import detect_language
from app.ingestion.repo_source import RepoSourceError, git

MAX_VERSIONS = 40
MAX_VERSION_LINES = 400
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_NEW_PATH = re.compile(r"^\+\+\+ (?:b/)?(.+)$", re.MULTILINE)


@lru_cache(maxsize=256)
def file_at(repo_path: str, sha: str, path: str) -> str | None:
    try:
        return git(["show", f"{sha}:{path}"], cwd=Path(repo_path), strip=False)
    except RepoSourceError:
        return None


def range_at(diff: str) -> tuple[int, int] | None:
    """The tracked range's lines after the commit: the span of its hunks."""
    spans = []
    for m in _HUNK.finditer(diff):
        start, length = int(m.group(1)), int(m.group(2) or 1)
        if length:
            spans.append((start, start + length - 1))
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines()
                  if ln.startswith("-") and not ln.startswith("---"))
    return added, removed


def evolution(repo: dict, path: str, start: int, end: int) -> dict:
    target = locate(repo["id"], path, start, end)
    if target is not None and target["kind"] == "symbol":
        start, end = target["start_line"], target["end_line"]
    # Merges that only re-state a range already changed on a branch have no hunk.
    commits = [c for c in range_history(repo["local_path"], repo["head_sha"], path, start, end)
               if _HUNK.search(c.diff)]
    total = len(commits)
    if total > MAX_VERSIONS:  # keep the origin and the most recent versions
        commits = commits[: MAX_VERSIONS - 1] + commits[-1:]
    commits.reverse()  # oldest first: read it like a story

    by_sha, by_pr = _linked(repo["id"], [c.sha for c in commits])
    versions = []
    for i, c in enumerate(commits):
        at_path = (m.group(1).strip() if (m := _NEW_PATH.search(c.diff)) else path)
        span = range_at(c.diff)
        code = None
        if span is not None and (text := file_at(repo["local_path"], c.sha, at_path)):
            lines = text.splitlines()[span[0] - 1: span[1]]
            if len(lines) > MAX_VERSION_LINES:
                lines = lines[:MAX_VERSION_LINES] + ["…"]
            code = "\n".join(lines)
        added, removed = diff_stats(c.diff)
        prs = by_sha.get(c.sha, [])
        versions.append({
            "sha": c.sha, "author": c.author_name, "date": c.authored_at, "subject": c.subject,
            "role": "introduced" if i == 0 else None,  # the oldest is always kept
            "path": at_path, "start_line": span[0] if span else None,
            "end_line": span[1] if span else None, "code": code,
            "diff": "\n".join(ln for ln in c.diff.splitlines()
                              if not ln.startswith(("diff --git", "--- ", "+++ "))),
            "added": added, "removed": removed,
            "pull_requests": prs,
            "issues": [iss for pr in prs for iss in by_pr.get(str(pr["number"]), [])],
        })
    return {
        "path": path, "start_line": start, "end_line": end,
        "symbol": target["label"] if target is not None and target["kind"] == "symbol" else None,
        "language": detect_language(path),
        "total_versions": total, "truncated": total > len(commits),
        "authors": sorted({v["author"] for v in versions}),
        "versions": versions,
    }
