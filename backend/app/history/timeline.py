"""History views for the UI: a code range's timeline and a commit's details."""

from app.db import connection
from app.history.git_log import commit_diff, file_history, range_history
from app.history.provenance import trim_diff

TIMELINE_DIFF_LINES = 60
FILE_HISTORY_LIMIT = 300


def _linked(repo_id: int, shas: list[str]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """sha -> PRs, and PR number -> issues, for the given commits."""
    with connection() as conn:
        commit_prs = conn.execute(
            """SELECT l.src_key AS sha, l.dst_key AS number, p.title, p.state, p.author,
                      p.merged_at,
                      -- a PR known only from a merge commit still links to GitHub
                      coalesce(p.url, CASE WHEN r.url LIKE 'https://github.com/%%'
                                           THEN r.url || '/pull/' || l.dst_key END) AS url
               FROM links l JOIN repositories r ON r.id = l.repo_id
               LEFT JOIN pull_requests p
                 ON p.repo_id = l.repo_id AND p.number = l.dst_key::int
               WHERE l.repo_id = %s AND l.src_type = 'commit' AND l.src_key = ANY(%s)
                 AND l.dst_type = 'pull_request'""", (repo_id, shas)).fetchall()
        numbers = sorted({r["number"] for r in commit_prs})
        pr_issues = conn.execute(
            """SELECT l.src_key AS pr, l.kind, i.number, i.title, i.state, i.url
               FROM links l JOIN issues i ON i.repo_id = l.repo_id AND i.number = l.dst_key::int
               WHERE l.repo_id = %s AND l.src_type = 'pull_request' AND l.src_key = ANY(%s)
                 AND l.dst_type = 'issue'
               ORDER BY (l.kind = 'fixes') DESC""", (repo_id, numbers)).fetchall()
    by_sha: dict[str, list[dict]] = {}
    for r in commit_prs:
        pr = {"number": int(r["number"]), "title": r["title"], "state": r["state"],
              "url": r["url"], "author": r["author"], "merged_at": r["merged_at"]}
        if pr not in by_sha.setdefault(r["sha"], []):
            by_sha[r["sha"]].append(pr)
    by_pr: dict[str, list[dict]] = {}
    for r in pr_issues:
        by_pr.setdefault(r["pr"], []).append(
            {"number": r["number"], "title": r["title"], "state": r["state"], "url": r["url"],
             "kind": r["kind"]})
    return by_sha, by_pr


def timeline(repo: dict, path: str, start: int | None, end: int | None) -> dict:
    if start is not None:
        commits = [
            {"sha": c.sha, "author": c.author_name, "email": c.author_email,
             "date": c.authored_at, "subject": c.subject,
             "diff": _trim_diff_lines(c.diff)}
            for c in range_history(repo["local_path"], repo["head_sha"], path, start,
                                   end or start)
        ]
        scope = "range"
    else:
        commits = [
            {"sha": r["sha"], "author": r["author_name"], "email": r["author_email"],
             "date": r["authored_at"], "subject": r["subject"], "diff": None}
            for r in file_history(repo["local_path"], path, limit=FILE_HISTORY_LIMIT)
        ]
        scope = "file"
    # The oldest entry is the origin only if the history wasn't truncated.
    if commits and (scope == "range" or len(commits) < FILE_HISTORY_LIMIT):
        commits[-1]["role"] = "introduced"
    by_sha, by_pr = _linked(repo["id"], [c["sha"] for c in commits])
    for c in commits:
        c["pull_requests"] = by_sha.get(c["sha"], [])
        c["issues"] = [i for pr in c["pull_requests"] for i in by_pr.get(str(pr["number"]), [])]
    authors = sorted({c["author"] for c in commits})
    return {"scope": scope, "path": path, "start_line": start, "end_line": end,
            "commits": commits, "authors": authors}


def _trim_diff_lines(diff: str) -> str:
    lines = trim_diff(diff).splitlines()
    if len(lines) > TIMELINE_DIFF_LINES:
        lines = lines[:TIMELINE_DIFF_LINES] + ["…"]
    return "\n".join(lines)


def commit_detail(repo: dict, sha: str, path: str | None) -> dict | None:
    with connection() as conn:
        c = conn.execute(
            """SELECT id, sha, parent_shas, author_name, author_email, authored_at, subject, body,
                      files_changed, insertions, deletions
               FROM commits WHERE repo_id = %s AND sha LIKE %s ORDER BY sha LIMIT 1""",
            (repo["id"], f"{sha}%")).fetchone()
        if c is None:
            return None
        files = conn.execute(
            "SELECT path, insertions, deletions FROM commit_files WHERE commit_id = %s "
            "ORDER BY path", (c["id"],)).fetchall()
    by_sha, by_pr = _linked(repo["id"], [c["sha"]])
    prs = by_sha.get(c["sha"], [])
    return {
        **{k: v for k, v in c.items() if k != "id"},
        "files": files,
        "diff": commit_diff(repo["local_path"], c["sha"], path),
        "diff_path": path,
        "pull_requests": prs,
        "issues": [i for pr in prs for i in by_pr.get(str(pr["number"]), [])],
    }
