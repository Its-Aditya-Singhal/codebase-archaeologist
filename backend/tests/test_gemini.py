"""The Gemini adapter against canned REST responses (no network)."""

import json

import httpx
import pytest

from app.answering import gemini


class FakeStream:
    def __init__(self, status: int, lines: list[str] | None = None, body: dict | None = None):
        self.status_code, self.lines, self.body = status, lines or [], body or {}
        self.text = json.dumps(self.body)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text

    def json(self):
        return self.body

    def iter_lines(self):
        return iter(self.lines)


def sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


@pytest.fixture
def settings(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(s, "gemini_model", "main-model")
    monkeypatch.setattr(s, "gemini_fallback_model", "lite-model")
    return s


def test_stream_skips_thoughts_and_reports_usage(monkeypatch, settings):
    calls = []

    def fake_stream(method, url, **kw):
        calls.append((url, kw["headers"]["x-goog-api-key"], kw["json"]))
        return FakeStream(200, [
            sse({"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}]}}]}),
            "",
            sse({"candidates": [{"content": {"parts": [{"text": "It retries [S1]"}]}}]}),
            sse({"candidates": [{"content": {"parts": [{"text": "."}]}, "finishReason": "STOP"}],
                 "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 7}}),
        ])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    out = list(gemini.stream("system", [{"role": "user", "content": "q1"},
                                        {"role": "assistant", "content": "a1"},
                                        {"role": "user", "content": "q2"}]))
    assert "".join(p["text"] for p in out if "text" in p) == "It retries [S1]."
    assert out[-1]["done"] == {"model": "main-model", "finish": "STOP",
                               "usage": {"input_tokens": 120, "output_tokens": 7}}
    url, key, body = calls[0]
    assert url.endswith("/main-model:streamGenerateContent") and key == "test-key"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]


def test_rate_limit_falls_back_to_lighter_model(monkeypatch, settings):
    urls = []

    def fake_stream(method, url, **kw):
        urls.append(url)
        if "main-model" in url:
            return FakeStream(429, body={"error": {"message": "Resource exhausted"}})
        return FakeStream(200, [sse({"candidates": [{"content": {"parts": [{"text": "ok"}]},
                                                     "finishReason": "STOP"}]})])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    out = list(gemini.stream("system", [{"role": "user", "content": "q"}]))
    assert out[-1]["done"]["model"] == "lite-model"
    assert len(urls) == 2


def test_overload_falls_back_then_explains(monkeypatch, settings):
    urls = []

    def fake_stream(method, url, **kw):
        urls.append(url)
        return FakeStream(503, body={"error": {"message": "This model is experiencing high "
                                                          "demand."}})

    monkeypatch.setattr(httpx, "stream", fake_stream)
    with pytest.raises(gemini.GeminiError, match="temporarily overloaded"):
        list(gemini.stream("system", [{"role": "user", "content": "q"}]))
    assert len(urls) == 2  # main model, then the fallback


def test_invalid_key_is_explained(monkeypatch, settings):
    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: FakeStream(
        400, body={"error": {"message": "API key not valid. Please pass a valid API key."}}))
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        list(gemini.stream("system", [{"role": "user", "content": "q"}]))


def test_agent_model_echoes_calls_and_returns_results(monkeypatch, settings):
    from app.answering.agent import GeminiAgentModel, ToolCall

    model_turn = {"role": "model", "parts": [
        {"functionCall": {"id": "c1", "name": "search", "args": {"query": "retry"}},
         "thoughtSignature": "sig=="}]}
    sent = []

    def fake_post(url, **kw):
        sent.append(kw["json"])
        return httpx.Response(200, json={"candidates": [{"content": model_turn}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    m = GeminiAgentModel()
    m.start("sys", "question")
    calls = m.next_calls()
    assert calls == [ToolCall("c1", "search", {"query": "retry"})]
    m.send_results([(calls[0], "Found 3 sources")])
    assert m.contents[1] == model_turn  # signature preserved
    assert m.contents[2]["parts"][0]["functionResponse"] == {
        "name": "search", "id": "c1", "response": {"result": "Found 3 sources"}}
    decls = sent[0]["tools"][0]["functionDeclarations"]
    assert {d["name"] for d in decls} >= {"search", "read_code", "impact"}
