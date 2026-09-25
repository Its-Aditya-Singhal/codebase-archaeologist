"""Extract references between commits, pull requests and issues from text."""

import re

# Squash merges: "Add retry backoff (#1234)"; merge commits: "Merge pull request #1234 from ..."
_SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
_MERGE_PR = re.compile(r"^Merge pull request #(\d+)\b")
# GitHub closing keywords: https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
_CLOSES = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#(\d+)\b",
    re.IGNORECASE,
)
_MENTION = re.compile(r"(?<![\w/&])#(\d+)\b")


def pr_number_from_commit(subject: str) -> int | None:
    m = _MERGE_PR.match(subject) or _SQUASH_PR.search(subject)
    return int(m.group(1)) if m else None


def references(text: str) -> list[tuple[int, str]]:
    """(number, kind) pairs where kind is `fixes` or `mentions`; a number that
    is closed by a keyword is not also reported as a mention."""
    fixes = {int(n) for n in _CLOSES.findall(text)}
    out = [(n, "fixes") for n in sorted(fixes)]
    mentioned = {int(n) for n in _MENTION.findall(text)} - fixes
    out += [(n, "mentions") for n in sorted(mentioned)]
    return out
