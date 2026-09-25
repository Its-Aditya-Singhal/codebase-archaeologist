import httpx
import pytest

from app.history import github
from app.history.github import SyncState, fetch_issues


def make_api(total: int, per_page: int = 100, remaining: int = 1000):
    """Fake /issues endpoint: `total` items, newest-updated first, cursor pagination."""
    items = [{"number": total - i, "updated_at": f"2026-01-01T00:00:{59 - i % 60:02d}Z"
              if i < 60 else f"2025-01-01T00:{(i // 60) % 60:02d}:00Z"} for i in range(total)]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        start = int(request.url.params.get("after", "0"))
        page = items[start : start + per_page]
        headers = {"x-ratelimit-remaining": str(remaining)}
        if start + per_page < total:
            nxt = request.url.copy_merge_params({"after": str(start + per_page)})
            headers["link"] = f'<{nxt}>; rel="next"'
        return httpx.Response(200, json=page, headers=headers)

    return handler, calls


@pytest.fixture
def api(monkeypatch):
    def install(handler):
        monkeypatch.setattr(github, "_client", lambda: httpx.Client(
            base_url="https://api.github.test", transport=httpx.MockTransport(handler)))
    return install


@pytest.fixture(autouse=True)
def small_page_cap(monkeypatch):
    class S:
        github_max_pages = 2
        github_token = None
    monkeypatch.setattr(github, "get_settings", lambda: S())


def test_crawl_pauses_at_page_cap_and_resumes_from_next_link(api):
    handler, calls = make_api(total=450)
    api(handler)
    first = fetch_issues("o", "r", SyncState())
    assert not first.complete and len(first.items) == 200
    assert "after=200" in first.state.next_url
    newest = first.state.pending_watermark

    second = fetch_issues("o", "r", first.state)
    assert not second.complete and "after=400" in second.state.next_url
    third = fetch_issues("o", "r", second.state)
    assert third.complete and len(third.items) == 50
    # The finished crawl commits the watermark captured on its first page.
    assert third.state == SyncState(watermark=newest)
    assert [i["number"] for i in first.items + second.items + third.items] == list(
        range(450, 0, -1))


def test_new_crawl_stops_at_watermark(api):
    handler, _ = make_api(total=450)
    api(handler)
    result = fetch_issues("o", "r", SyncState(watermark="2026-01-01T00:00:55Z"))
    assert result.complete and [i["number"] for i in result.items] == [450, 449, 448, 447]


def test_rate_limit_floor_pauses_with_resume_point(api):
    handler, _ = make_api(total=450, remaining=2)
    api(handler)
    result = fetch_issues("o", "r", SyncState())
    assert not result.complete and "rate limit" in result.note
    assert "after=100" in result.state.next_url


def test_issues_listing_skips_pull_requests(api):
    def handler(request):
        return httpx.Response(200, json=[
            {"number": 2, "updated_at": "2026-01-01T00:00:00Z", "pull_request": {}},
            {"number": 1, "updated_at": "2025-01-01T00:00:00Z"}])
    api(handler)
    assert [i["number"] for i in fetch_issues("o", "r", SyncState()).items] == [1]
