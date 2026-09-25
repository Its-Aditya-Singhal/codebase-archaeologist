"""Read commit history (and line-level history) from a local clone."""

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.ingestion.repo_source import RepoSourceError, git

REC, FLD = "\x1e", "\x1f"
_LOG_FORMAT = FLD.join(["%H", "%P", "%an", "%ae", "%aI", "%cI", "%s", "%b"])


@dataclass
class FileChange:
    path: str
    insertions: int | None  # None for binary files
    deletions: int | None


@dataclass
class CommitInfo:
    sha: str
    parents: list[str]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    subject: str
    body: str
    files: list[FileChange] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def read_commits(repo_path: Path, max_commits: int) -> list[CommitInfo]:
    """All commits reachable from HEAD (newest first) with per-file line stats."""
    out = git(
        ["log", f"-n{max_commits}", "--no-renames", "--numstat",
         f"--format={REC}{_LOG_FORMAT}{FLD}"],
        cwd=repo_path, timeout=600, strip=False,
    )
    commits: list[CommitInfo] = []
    for record in out.split(REC):
        if not record.strip():
            continue
        parts = record.split(FLD)
        if len(parts) < 9:
            continue
        sha, parents, an, ae, a_at, c_at, subject, body, numstat = parts[:9]
        files = []
        for line in numstat.strip().splitlines():
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            ins, dels, path = cols
            files.append(FileChange(path, None if ins == "-" else int(ins),
                                    None if dels == "-" else int(dels)))
        commits.append(CommitInfo(
            sha=sha, parents=parents.split(), author_name=an, author_email=ae,
            authored_at=datetime.fromisoformat(a_at), committed_at=datetime.fromisoformat(c_at),
            subject=subject, body=body.strip(), files=files,
        ))
    return commits


def commits_in_merge(repo_path: Path, merge: CommitInfo, cap: int = 300) -> list[str]:
    """Commits brought in by a merge commit: reachable from the merged branch
    (second parent) but not from the mainline (first parent)."""
    if len(merge.parents) < 2:
        return []
    out = git(["rev-list", f"--max-count={cap}", merge.parents[1], f"^{merge.parents[0]}"],
              cwd=repo_path)
    return out.split()


@dataclass
class RangeCommit:
    sha: str
    author_name: str
    author_email: str
    authored_at: str
    subject: str
    diff: str  # the diff restricted to the tracked line range


@lru_cache(maxsize=512)
def range_history(repo_path: str, head_sha: str, path: str, start: int, end: int
                  ) -> tuple[RangeCommit, ...]:
    """Every commit that changed lines [start, end] of `path` (as of HEAD),
    newest first, with the diff of just that range. `git log -L` follows the
    range back through edits and moves within the file, so the last entry is
    the commit that introduced the code. Cached per HEAD, so re-indexing
    invalidates it naturally."""
    marker = "\x1dCOMMIT\x1d"
    try:
        out = git(
            ["log", f"-L{start},{end}:{path}", "--no-color",
             f"--format={marker}%H{FLD}%an{FLD}%ae{FLD}%aI{FLD}%s", head_sha],
            cwd=Path(repo_path), timeout=60, strip=False,
        )
    except RepoSourceError:
        return ()
    commits: list[RangeCommit] = []
    for block in out.split(marker):
        if not block.strip():
            continue
        header, _, diff = block.partition("\n")
        fields = header.split(FLD)
        if len(fields) != 5:
            continue
        sha, an, ae, at, subject = fields
        commits.append(RangeCommit(sha, an, ae, at, subject, diff.strip()))
    return tuple(commits)


def file_history(repo_path: str, path: str, limit: int = 100) -> list[dict]:
    """Commits touching a file (following renames), newest first."""
    try:
        out = git(
            ["log", "--follow", f"-n{limit}",
             f"--format=%H{FLD}%an{FLD}%ae{FLD}%aI{FLD}%s", "--", path],
            cwd=Path(repo_path),
        )
    except RepoSourceError:
        return []
    rows = []
    for line in out.splitlines():
        f = line.split(FLD)
        if len(f) == 5:
            rows.append({"sha": f[0], "author_name": f[1], "author_email": f[2],
                         "authored_at": f[3], "subject": f[4]})
    return rows


def commit_diff(repo_path: str, sha: str, path: str | None = None, max_lines: int = 800) -> str:
    args = ["show", "--no-color", "--format=", "--patch", sha]
    if path:
        args += ["--", path]
    try:
        out = git(args, cwd=Path(repo_path))
    except RepoSourceError:
        return ""
    lines = out.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… diff truncated ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)
