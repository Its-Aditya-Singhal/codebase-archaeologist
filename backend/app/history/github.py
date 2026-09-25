"""Fetch pull requests and issues from the GitHub REST API.

Rate limits shape the design: unauthenticated clients get 60 requests/hour.
Listings are crawled newest-updated first and the crawl is resumable:

- a new crawl starts at page 1 and stops at the previous crawl's watermark
  (the newest `updated_at` it saw), so re-indexing only fetches what changed;
- a crawl cut short by the page cap or the rate limit records the `next` link
  GitHub returned (page- or cursor-based; /issues switches to cursors on deep
  pages), and the following run continues from there before starting a new crawl.

GITHUB_TOKEN raises the limit to 5,000/hour.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API = "https://api.github.com"
MIN_REMAINING = 3  # leave a little budget for other callers


@dataclass
class SyncState:
    watermark: str | None = None  # newest updated_at covered by the last complete crawl
    next_url: str | None = None  # GitHub's `next` link, set while a crawl is unfinished
    pending_watermark: str | None = None  # watermark the unfinished crawl will commit

    @classmethod
    def from_dict(cls, d: dict | None) -> "SyncState":
        d = dict(d or {})
        d.pop("next_page", None)  # pre-cursor state format: restart that crawl
        return cls(**d)


@dataclass
class FetchResult:
    items: list[dict] = field(default_factory=list)
    state: SyncState = field(default_factory=SyncState)
    complete: bool = True
    note: str | None = None


def _client() -> httpx.Client:
    settings = get_settings()
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return httpx.Client(base_url=API, headers=headers, timeout=30.0)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _crawl(path: str, state: SyncState, keep=lambda item: True) -> FetchResult:
    max_pages = get_settings().github_max_pages
    resuming = state.next_url is not None
    url: str = state.next_url or path
    params: dict | None = None if resuming else {
        "state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
    stop_at = _parse_ts(state.watermark) if state.watermark else None
    pending = state.pending_watermark if resuming else None
    result = FetchResult()

    def unfinished(note: str) -> FetchResult:
        result.complete = False
        result.note = note
        result.state = SyncState(state.watermark, url, pending)
        return result

    with _client() as client:
        for _ in range(max_pages):
            try:
                resp = _get_with_retry(client, url, params)
            except httpx.HTTPError as exc:
                return unfinished(f"network error: {exc}")
            remaining = int(resp.headers.get("x-ratelimit-remaining", "1000"))
            if resp.status_code in (403, 429) and remaining == 0:
                return unfinished(_rate_note(resp))
            if resp.status_code == 404:
                result.complete, result.note = False, "repository not found on GitHub (private?)"
                result.state = state
                return result
            if resp.status_code >= 400:
                return unfinished(f"GitHub API returned {resp.status_code}")

            items = resp.json()
            if not resuming and items and pending is None:
                pending = items[0]["updated_at"]  # newest item: next watermark
            for item in items:
                if stop_at and _parse_ts(item["updated_at"]) <= stop_at:
                    return _finished(result, pending or state.watermark)
                if keep(item):
                    result.items.append(item)
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                return _finished(result, pending or state.watermark)
            url, params = next_url, None  # the next link carries the query and cursor
            if remaining <= MIN_REMAINING:
                return unfinished(_rate_note(resp))
    return unfinished(f"paused after {max_pages} pages; re-index to continue")


def _get_with_retry(client: httpx.Client, url: str, params: dict | None, attempts: int = 3
                    ) -> httpx.Response:
    """Retry transient network failures (DNS hiccups, resets) with backoff."""
    for attempt in range(attempts):
        try:
            return client.get(url, params=params)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _finished(result: FetchResult, watermark: str | None) -> FetchResult:
    result.complete = True
    result.state = SyncState(watermark=watermark)
    return result


def _rate_note(resp: httpx.Response) -> str:
    reset = resp.headers.get("x-ratelimit-reset")
    when = datetime.fromtimestamp(int(reset)).strftime("%H:%M") if reset else "later"
    return (f"GitHub rate limit reached (resets at {when}); set GITHUB_TOKEN for "
            "5,000 requests/hour, then re-index to continue.")


def fetch_pull_requests(owner: str, name: str, state: SyncState) -> FetchResult:
    return _crawl(f"/repos/{owner}/{name}/pulls", state)


def fetch_issues(owner: str, name: str, state: SyncState) -> FetchResult:
    # The issues endpoint also returns pull requests; those come from /pulls.
    return _crawl(f"/repos/{owner}/{name}/issues", state,
                  keep=lambda item: "pull_request" not in item)
