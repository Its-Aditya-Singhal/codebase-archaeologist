"""History ingestion: commits, pull requests, issues and the links between them.

Everything becomes a retrieval chunk (source_type commit | pull_request | issue),
so "why"-style questions can match a commit message or PR description directly,
and `links` lets retrieval walk from a line of code to the PR and issue behind it.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db import connection
from app.history.git_log import CommitInfo, commits_in_merge, read_commits
from app.history.github import (
    CommentTarget,
    FetchResult,
    SyncState,
    fetch_comments,
    fetch_issues,
    fetch_pull_requests,
)
from app.history.links import pr_number_from_commit, references
from app.ingestion.repo_source import RepoSourceError, git
from app.ingestion.store import ChunkRow, sync_chunks

log = logging.getLogger(__name__)

HISTORY_TYPES = ("commit", "pull_request", "issue")
MAX_BODY_CHARS = 4000
MAX_DISCUSSION_CHARS = 3000
MAX_COMMENT_CHARS = 600
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_GITHUB_REMOTE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?$")

Progress = Callable[[str, int | None, int | None], None]


def ingest_history(repo_id: int, repo_path: Path, owner: str | None, name: str | None,
                   progress: Progress) -> dict:
    settings = get_settings()
    stats: dict = {}

    progress("Reading commit history", None, None)
    commits = read_commits(repo_path, settings.max_commits)
    _store_commits(repo_id, commits)
    stats["commits"] = len(commits)

    if owner is None:
        owner, name = _github_remote(repo_path)
    if owner and name:
        sync = _load_sync(repo_id)
        progress("Fetching pull requests from GitHub", None, None)
        prs = fetch_pull_requests(owner, name, SyncState.from_dict(sync.get("pull_requests")))
        _upsert_pull_requests(repo_id, prs)
        progress("Fetching issues from GitHub", None, None)
        issues = fetch_issues(owner, name, SyncState.from_dict(sync.get("issues")))
        _upsert_issues(repo_id, issues)
        _update_pr_comment_counts(repo_id, issues.pr_comment_counts)
        _save_sync(repo_id, {"pull_requests": asdict(prs.state), "issues": asdict(issues.state)})
        stats["github"] = {
            "complete": prs.complete and issues.complete,
            "note": "; ".join(f"{label}: {r.note}" for label, r in
                              (("pull requests", prs), ("issues", issues)) if r.note) or None,
            "fetched": {"pull_requests": len(prs.items), "issues": len(issues.items)},
        }
    else:
        stats["github"] = {"complete": False, "note": "not a GitHub repository"}

    progress("Linking commits, pull requests and issues", None, None)
    stats["links"] = _rebuild_links(repo_id, repo_path, commits)

    if owner and name:
        # After linking: the threads behind code (PRs merged from commits, the
        # issues they fix) are fetched first.
        progress("Fetching pull request and issue discussions", None, None)
        stats["github"]["discussions"] = _sync_comments(repo_id, owner, name)

    rows = _history_chunks(repo_id, commits)
    sync = sync_chunks(repo_id, HISTORY_TYPES, rows,
                       lambda done, total: progress("Embedding new history", done, total))
    stats["index"] = {"reused": sync.reused, "embedded": sync.embedded, "deleted": sync.deleted}

    with connection() as conn:
        counts = conn.execute(
            """SELECT (SELECT count(*) FROM pull_requests WHERE repo_id = %(r)s) AS prs,
                      (SELECT count(*) FROM issues WHERE repo_id = %(r)s) AS issues""",
            {"r": repo_id}).fetchone()
    stats["pull_requests"] = counts["prs"]
    stats["issues"] = counts["issues"]
    return stats


# ------------------------------------------------------------------- commits


def _store_commits(repo_id: int, commits: list[CommitInfo]) -> None:
    """Commits are derived from the clone, so they are rebuilt wholesale."""
    with connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM commits WHERE repo_id = %s", (repo_id,))
        with cur.copy(
            """COPY commits (repo_id, sha, parent_shas, author_name, author_email, authored_at,
                             committed_at, subject, body, files_changed, insertions, deletions)
               FROM STDIN"""
        ) as copy:
            for c in commits:
                copy.write_row((
                    repo_id, c.sha, c.parents, c.author_name, c.author_email, c.authored_at,
                    c.committed_at, c.subject, c.body, len(c.files),
                    sum(f.insertions or 0 for f in c.files),
                    sum(f.deletions or 0 for f in c.files),
                ))
        ids = {r["sha"]: r["id"] for r in cur.execute(
            "SELECT sha, id FROM commits WHERE repo_id = %s", (repo_id,)).fetchall()}
        with cur.copy(
            "COPY commit_files (commit_id, repo_id, path, insertions, deletions) FROM STDIN"
        ) as copy:
            for c in commits:
                for f in c.files:
                    copy.write_row((ids[c.sha], repo_id, f.path, f.insertions, f.deletions))


# -------------------------------------------------------------------- github


def _github_remote(repo_path: Path) -> tuple[str | None, str | None]:
    try:
        url = git(["remote", "get-url", "origin"], cwd=repo_path)
    except RepoSourceError:
        return None, None
    m = _GITHUB_REMOTE.search(url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _load_sync(repo_id: int) -> dict:
    with connection() as conn:
        return conn.execute("SELECT sync FROM repositories WHERE id = %s",
                            (repo_id,)).fetchone()["sync"]


def _save_sync(repo_id: int, sync: dict) -> None:
    """Merge top-level keys into the stored sync state."""
    with connection() as conn:
        conn.execute("UPDATE repositories SET sync = sync || %s WHERE id = %s",
                     (Jsonb(sync), repo_id))


def _clean_body(body: str | None) -> str:
    return _HTML_COMMENT.sub("", body or "").strip()


def _labels(item: dict) -> list[str]:
    return [label["name"] for label in item.get("labels", [])]


def _upsert_pull_requests(repo_id: int, result: FetchResult) -> None:
    if not result.items:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO pull_requests (repo_id, number, title, body, state, author, labels,
                   created_at, updated_at, closed_at, merged_at, merge_commit_sha, url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (repo_id, number) DO UPDATE SET
                   title = EXCLUDED.title, body = EXCLUDED.body, state = EXCLUDED.state,
                   labels = EXCLUDED.labels, updated_at = EXCLUDED.updated_at,
                   closed_at = EXCLUDED.closed_at, merged_at = EXCLUDED.merged_at,
                   merge_commit_sha = EXCLUDED.merge_commit_sha""",
            [(repo_id, p["number"], p["title"], _clean_body(p.get("body")),
              "merged" if p.get("merged_at") else p["state"], (p.get("user") or {}).get("login"),
              _labels(p), p["created_at"], p["updated_at"], p.get("closed_at"),
              p.get("merged_at"), p.get("merge_commit_sha"), p["html_url"])
             for p in result.items],
        )


def _upsert_issues(repo_id: int, result: FetchResult) -> None:
    if not result.items:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO issues (repo_id, number, title, body, state, author, labels, comments,
                   created_at, updated_at, closed_at, url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (repo_id, number) DO UPDATE SET
                   title = EXCLUDED.title, body = EXCLUDED.body, state = EXCLUDED.state,
                   labels = EXCLUDED.labels, comments = EXCLUDED.comments,
                   updated_at = EXCLUDED.updated_at, closed_at = EXCLUDED.closed_at""",
            [(repo_id, i["number"], i["title"], _clean_body(i.get("body")), i["state"],
              (i.get("user") or {}).get("login"), _labels(i), i.get("comments", 0),
              i["created_at"], i["updated_at"], i.get("closed_at"), i["html_url"])
             for i in result.items],
        )


def _update_pr_comment_counts(repo_id: int, counts: dict[int, int]) -> None:
    if not counts:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.executemany("UPDATE pull_requests SET comments = %s WHERE repo_id = %s AND number = %s",
                        [(c, repo_id, n) for n, c in counts.items()])


# --------------------------------------------------------------- discussions


def _sync_comments(repo_id: int, owner: str, name: str) -> dict:
    settings = get_settings()
    done: dict[str, str] = _load_sync(repo_id).get("comments") or {}
    with connection() as conn:
        linked = conn.execute(
            """SELECT DISTINCT dst_type, dst_key FROM links
               WHERE repo_id = %s AND dst_type IN ('pull_request', 'issue')""",
            (repo_id,)).fetchall()
        prs = conn.execute(
            "SELECT number, comments, updated_at FROM pull_requests WHERE repo_id = %s",
            (repo_id,)).fetchall()
        issues = conn.execute(
            """SELECT number, comments, updated_at FROM issues
               WHERE repo_id = %s AND comments > 0""", (repo_id,)).fetchall()
    linked_prs = {int(r["dst_key"]) for r in linked if r["dst_type"] == "pull_request"}
    linked_issues = {int(r["dst_key"]) for r in linked if r["dst_type"] == "issue"}
    stamp = {f"pull_request:{p['number']}": _iso(p["updated_at"]) for p in prs}
    stamp |= {f"issue:{i['number']}": _iso(i["updated_at"]) for i in issues}

    def stale(t: CommentTarget) -> bool:
        return done.get(t.key) != stamp.get(t.key)

    newest = lambda rows: sorted(rows, key=lambda r: -r["number"])  # noqa: E731
    tiers = [
        # Threads behind code first: PRs merged from commits (with their review
        # comments on diff lines) and issues they reference.
        [CommentTarget("pull_request", p["number"], conversation=p["comments"] != 0,
                       review=True) for p in newest(prs) if p["number"] in linked_prs],
        [CommentTarget("issue", i["number"]) for i in newest(issues)
         if i["number"] in linked_issues],
        [CommentTarget("issue", i["number"]) for i in newest(issues)
         if i["number"] not in linked_issues],
        [CommentTarget("pull_request", p["number"]) for p in newest(prs)
         if p["number"] not in linked_prs and (p["comments"] or 0) > 0],
    ]
    targets = [t for tier in tiers for t in tier if stale(t)]
    if not targets:
        return {"threads": 0, "comments": 0, "pending": 0, "note": None}
    result = fetch_comments(owner, name, targets, settings.github_max_comment_requests)
    if result.items:
        with connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO comments (repo_id, comment_id, parent_type, parent_number, kind,
                       author, body, path, created_at, url)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (repo_id, comment_id, kind) DO UPDATE SET
                       body = EXCLUDED.body, path = EXCLUDED.path""",
                [(repo_id, c["comment_id"], c["parent_type"], c["parent_number"], c["kind"],
                  c["author"], _clean_body(c["body"]), c["path"], c["created_at"], c["url"])
                 for c in result.items])
    done.update({key: stamp.get(key) for key in result.done})
    _save_sync(repo_id, {"comments": done})
    return {"threads": len(result.done), "comments": len(result.items),
            "pending": len(targets) - len(result.done), "note": result.note}


def _discussions(repo_id: int) -> dict[tuple[str, int], list[dict]]:
    with connection() as conn:
        rows = conn.execute(
            """SELECT parent_type, parent_number, kind, author, body, path FROM comments
               WHERE repo_id = %s ORDER BY created_at""", (repo_id,)).fetchall()
    out: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        out.setdefault((r["parent_type"], r["parent_number"]), []).append(r)
    return out


def _discussion_text(comments: list[dict]) -> str:
    """A thread as plain text, oldest first, capped so long threads don't
    drown the description."""
    lines, used = [], 0
    for c in comments:
        body = " ".join(c["body"].split())
        if not body:
            continue
        if len(body) > MAX_COMMENT_CHARS:
            body = body[:MAX_COMMENT_CHARS] + "…"
        where = f" on {c['path']}" if c["path"] else ""
        line = f"- {c['author'] or 'someone'}{where}: {body}"
        if used + len(line) > MAX_DISCUSSION_CHARS:
            lines.append(f"- … ({len(comments) - len(lines)} more comments)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# --------------------------------------------------------------------- links


def _rebuild_links(repo_id: int, repo_path: Path, commits: list[CommitInfo]) -> int:
    with connection() as conn:
        prs = conn.execute(
            "SELECT number, title, body, merge_commit_sha FROM pull_requests WHERE repo_id = %s",
            (repo_id,)).fetchall()
        issues = conn.execute("SELECT number, title, body FROM issues WHERE repo_id = %s",
                              (repo_id,)).fetchall()
    pr_numbers = {p["number"] for p in prs}
    issue_numbers = {i["number"] for i in issues}
    known_shas = {c.sha for c in commits}

    def kind_of(n: int) -> str | None:
        return "pull_request" if n in pr_numbers else "issue" if n in issue_numbers else None

    links: set[tuple[str, str, str, str, str]] = set()
    for c in commits:
        pr = pr_number_from_commit(c.subject)
        if pr is not None:
            # By convention "(#N)" / "Merge pull request #N" name a PR, even one
            # the (possibly rate-limited) GitHub fetch hasn't returned yet.
            links.add(("commit", c.sha, "pull_request", str(pr), "merged_in"))
            if c.is_merge:
                for sha in commits_in_merge(repo_path, c):
                    links.add(("commit", sha, "pull_request", str(pr), "part_of"))
        for n, kind in references(f"{c.subject}\n{c.body}"):
            if n != pr and (target := kind_of(n)):
                links.add(("commit", c.sha, target, str(n), kind))
    for p in prs:
        if p["merge_commit_sha"] in known_shas:
            links.add(("commit", p["merge_commit_sha"], "pull_request", str(p["number"]),
                       "merged_in"))
        for n, kind in references(f"{p['title']}\n{p['body']}"):
            if n != p["number"] and (target := kind_of(n)):
                links.add(("pull_request", str(p["number"]), target, str(n), kind))
    for i in issues:
        for n, _ in references(f"{i['title']}\n{i['body']}"):
            if n != i["number"] and (target := kind_of(n)):
                links.add(("issue", str(i["number"]), target, str(n), "mentions"))

    with connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM links WHERE repo_id = %s", (repo_id,))
        with cur.copy(
            "COPY links (repo_id, src_type, src_key, dst_type, dst_key, kind) FROM STDIN"
        ) as copy:
            for link in links:
                copy.write_row((repo_id, *link))
    return len(links)


# -------------------------------------------------------------------- chunks


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _history_chunks(repo_id: int, commits: list[CommitInfo]) -> list[ChunkRow]:
    with connection() as conn:
        commit_prs = {
            r["src_key"]: int(r["dst_key"]) for r in conn.execute(
                """SELECT src_key, dst_key FROM links WHERE repo_id = %s AND src_type = 'commit'
                   AND dst_type = 'pull_request' AND kind IN ('merged_in', 'part_of')""",
                (repo_id,)).fetchall()
        }
        prs = conn.execute("SELECT * FROM pull_requests WHERE repo_id = %s",
                           (repo_id,)).fetchall()
        issues = conn.execute("SELECT * FROM issues WHERE repo_id = %s", (repo_id,)).fetchall()
    threads = _discussions(repo_id)

    rows: list[ChunkRow] = []
    for c in commits:
        if c.is_merge:
            continue  # merge commits carry no content of their own; the PR chunk covers them
        files = [f.path for f in c.files]
        listed = ", ".join(files[:25]) + (f" (+{len(files) - 25} more)" if len(files) > 25 else "")
        content = f"{c.subject}\n\n{c.body}".strip() + f"\n\nFiles changed: {listed}"
        date = c.authored_at.date().isoformat()
        rows.append(ChunkRow(
            source_type="commit", content=content,
            embed_text=f"Commit by {c.author_name} on {date}\n{content}",
            search_text=f"{content}\n{c.author_name}",
            symbol_kind="commit", symbol_name=c.sha[:10],
            metadata={"sha": c.sha, "author": c.author_name, "date": c.authored_at.isoformat(),
                      "pr": commit_prs.get(c.sha),
                      "insertions": sum(f.insertions or 0 for f in c.files),
                      "deletions": sum(f.deletions or 0 for f in c.files)},
        ))
    for p in prs:
        content = f"PR #{p['number']}: {p['title']}\n\n{p['body'][:MAX_BODY_CHARS]}".strip()
        if p["labels"]:
            content += f"\n\nLabels: {', '.join(p['labels'])}"
        if thread := threads.get(("pull_request", p["number"])):
            content += f"\n\nDiscussion:\n{_discussion_text(thread)}"
        rows.append(ChunkRow(
            source_type="pull_request", content=content,
            embed_text=f"Pull request by {p['author']}\n{content}",
            search_text=f"{content}\n{p['author'] or ''}",
            symbol_kind="pull_request", symbol_name=f"#{p['number']}",
            metadata={"number": p["number"], "title": p["title"], "state": p["state"],
                      "author": p["author"], "created_at": _iso(p["created_at"]),
                      "merged_at": _iso(p["merged_at"]), "url": p["url"]},
        ))
    for i in issues:
        content = f"Issue #{i['number']}: {i['title']}\n\n{i['body'][:MAX_BODY_CHARS]}".strip()
        if i["labels"]:
            content += f"\n\nLabels: {', '.join(i['labels'])}"
        if thread := threads.get(("issue", i["number"])):
            content += f"\n\nDiscussion:\n{_discussion_text(thread)}"
        rows.append(ChunkRow(
            source_type="issue", content=content,
            embed_text=f"Issue reported by {i['author']}\n{content}",
            search_text=f"{content}\n{i['author'] or ''}",
            symbol_kind="issue", symbol_name=f"#{i['number']}",
            metadata={"number": i["number"], "title": i["title"], "state": i["state"],
                      "author": i["author"], "created_at": _iso(i["created_at"]),
                      "closed_at": _iso(i["closed_at"]), "url": i["url"]},
        ))
    return rows

