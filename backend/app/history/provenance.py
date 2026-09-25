"""Provenance: the history behind a specific piece of code.

For a line range this walks  code -> commits that changed it (`git log -L`)
-> the pull requests those commits belong to -> the issues those PRs fix or
reference, and returns them as evidence. This is what lets "why does this use
Redis?" be answered from the PR discussion that introduced it rather than from
the code alone.
"""

from dataclasses import dataclass, field

from app.db import connection
from app.history.git_log import RangeCommit, file_history, range_history

MAX_DIFF_LINES = 40


@dataclass
class ProvenanceItem:
    """Shaped like a retrieval row so it can be merged with search results."""

    id: int
    source_type: str
    path: str | None
    language: str | None
    symbol_kind: str | None
    symbol_name: str | None
    start_line: int | None
    end_line: int | None
    content: str
    metadata: dict = field(default_factory=dict)


def trim_diff(diff: str) -> str:
    lines = [ln for ln in diff.splitlines() if not ln.startswith(("diff --git", "--- ", "+++ "))]
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"… ({len(lines) - MAX_DIFF_LINES} more diff lines)"]
    return "\n".join(lines)


def select_range_commits(commits: tuple[RangeCommit, ...], recent: int = 3
                         ) -> list[tuple[RangeCommit, str]]:
    """The introducing commit plus the most recent changes, tagged by role."""
    if not commits:
        return []
    chosen = [(c, "modified") for c in commits[:recent]]
    origin = commits[-1]
    if all(c.sha != origin.sha for c, _ in chosen):
        chosen.append((origin, "introduced"))
    else:
        chosen = [(c, "introduced" if c.sha == origin.sha else role) for c, role in chosen]
    return chosen


def range_provenance(repo_id: int, repo_path: str, head_sha: str, path: str,
                     start: int, end: int, max_items: int = 8) -> list[ProvenanceItem]:
    chosen = select_range_commits(range_history(repo_path, head_sha, path, start, end))
    commits = [(c.sha, role, trim_diff(c.diff)) for c, role in chosen]
    return _expand(repo_id, path, commits, max_items)


def file_provenance(repo_id: int, repo_path: str, path: str, max_items: int = 6
                    ) -> list[ProvenanceItem]:
    rows = file_history(repo_path, path, limit=200)
    if not rows:
        return []
    picks = [(r["sha"], "modified", "") for r in rows[:3]]
    if rows[-1]["sha"] not in {sha for sha, _, _ in picks}:
        picks.append((rows[-1]["sha"], "introduced", ""))  # the file's creation
    return _expand(repo_id, path, picks, max_items)


def _expand(repo_id: int, path: str, commits: list[tuple[str, str, str]], max_items: int
            ) -> list[ProvenanceItem]:
    """commit shas -> commit evidence (+ range diff) -> linked PRs -> linked issues.
    Introducing commit first: it answers "why does this exist"."""
    if not commits:
        return []
    commits = sorted(commits, key=lambda c: c[1] != "introduced")
    shas = [sha for sha, _, _ in commits]
    with connection() as conn:
        info = {r["sha"]: r for r in conn.execute(
            """SELECT sha, author_name, authored_at, subject, body FROM commits
               WHERE repo_id = %s AND sha = ANY(%s)""", (repo_id, shas)).fetchall()}
        chunk_ids = {r["sha"]: r["id"] for r in conn.execute(
            """SELECT id, metadata->>'sha' AS sha FROM chunks
               WHERE repo_id = %s AND source_type = 'commit' AND metadata->>'sha' = ANY(%s)""",
            (repo_id, shas)).fetchall()}
        commit_links = conn.execute(
            """SELECT src_key, dst_type, dst_key, kind FROM links
               WHERE repo_id = %s AND src_type = 'commit' AND src_key = ANY(%s)""",
            (repo_id, shas)).fetchall()
        pr_numbers = sorted(
            {lk["dst_key"] for lk in commit_links if lk["dst_type"] == "pull_request"})
        pr_links = conn.execute(
            """SELECT src_key, dst_type, dst_key, kind FROM links
               WHERE repo_id = %s AND src_type = 'pull_request' AND src_key = ANY(%s)
                 AND dst_type = 'issue'""", (repo_id, pr_numbers)).fetchall()
        issue_numbers = sorted(
            {lk["dst_key"] for lk in [*commit_links, *pr_links] if lk["dst_type"] == "issue"})
        records = {(r["source_type"], r["symbol_name"]): r for r in conn.execute(
            """SELECT id, source_type, path, language, symbol_kind, symbol_name, start_line,
                      end_line, content, metadata FROM chunks
               WHERE repo_id = %s AND (
                   (source_type = 'pull_request' AND symbol_name = ANY(%s)) OR
                   (source_type = 'issue' AND symbol_name = ANY(%s)))""",
            (repo_id, [f"#{n}" for n in pr_numbers], [f"#{n}" for n in issue_numbers]),
        ).fetchall()}

    items: list[ProvenanceItem] = []
    seen: set[tuple[str, str]] = set()

    def add_record(source_type: str, number: str, relation: str, via: str) -> None:
        key = (source_type, f"#{number}")
        if key in seen or key not in records:
            return
        seen.add(key)
        r = records[key]
        items.append(ProvenanceItem(**{**r, "metadata": {**r["metadata"], "relation": relation,
                                                          "via": via}}))

    for sha, role, diff in commits:
        c = info.get(sha)
        if c is None:
            continue
        message = f"{c['subject']}\n\n{c['body']}".strip()
        content = message + (f"\n\nChange to {path} in this commit:\n{diff}" if diff else "")
        prs = [lk["dst_key"] for lk in commit_links
               if lk["src_key"] == sha and lk["dst_type"] == "pull_request"]
        items.append(ProvenanceItem(
            id=chunk_ids.get(sha, -(int(sha[:12], 16))),
            source_type="commit", path=path, language=None, symbol_kind="commit",
            symbol_name=sha[:10], start_line=None, end_line=None, content=content,
            metadata={"sha": sha, "author": c["author_name"],
                      "date": c["authored_at"].isoformat(), "role": role,
                      "pr": int(prs[0]) if prs else None},
        ))
        for n in prs:
            add_record("pull_request", n, f"merged {sha[:10]}", sha)
            for lk in pr_links:
                if lk["src_key"] == n:
                    add_record("issue", lk["dst_key"], f"{lk['kind']} by PR #{n}", n)
        for lk in commit_links:
            if lk["src_key"] == sha and lk["dst_type"] == "issue":
                add_record("issue", lk["dst_key"], f"{lk['kind']} by commit {sha[:10]}", sha)
        if len(items) >= max_items:
            break
    return items[:max_items]


def linked_records(repo_id: int, wanted: list[tuple[int, str, str]]
                   ) -> dict[int, list[ProvenanceItem]]:
    """For search hits: commit -> its PR ("pull_request", "#n"), or
    PR "#n" -> the issues it fixes ("issue", "#n"). Keyed by the caller's index."""
    pr_refs = [ref for _, kind, ref in wanted if kind == "pull_request"]
    fixing_prs = [ref.lstrip("#") for _, kind, ref in wanted if kind == "issue"]
    with connection() as conn:
        fixes = conn.execute(
            """SELECT src_key, dst_key FROM links WHERE repo_id = %s AND src_type = 'pull_request'
                 AND src_key = ANY(%s) AND dst_type = 'issue' AND kind = 'fixes'""",
            (repo_id, fixing_prs)).fetchall()
        issue_refs = [f"#{r['dst_key']}" for r in fixes]
        rows = conn.execute(
            """SELECT id, source_type, path, language, symbol_kind, symbol_name, start_line,
                      end_line, content, metadata FROM chunks
               WHERE repo_id = %s AND (
                   (source_type = 'pull_request' AND symbol_name = ANY(%s)) OR
                   (source_type = 'issue' AND symbol_name = ANY(%s)))""",
            (repo_id, pr_refs, issue_refs)).fetchall()
    by_key = {(r["source_type"], r["symbol_name"]): r for r in rows}
    out: dict[int, list[ProvenanceItem]] = {}
    for idx, kind, ref in wanted:
        if kind == "pull_request" and (row := by_key.get(("pull_request", ref))):
            out.setdefault(idx, []).append(ProvenanceItem(
                **{**row, "metadata": {**row["metadata"], "relation": "pull request of commit"}}))
        elif kind == "issue":
            for f in fixes:
                row = by_key.get(("issue", f"#{f['dst_key']}"))
                if f"#{f['src_key']}" == ref and row:
                    meta = {**row["metadata"], "relation": f"fixed by PR {ref}"}
                    out.setdefault(idx, []).append(ProvenanceItem(**{**row, "metadata": meta}))
    return out
