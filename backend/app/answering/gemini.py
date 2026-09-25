"""Gemini via the Google AI Studio API (free tier available).

Plain REST over httpx (the `generateContent` endpoints), like the Ollama
adapter, so there is no extra SDK. Free-tier keys have low per-minute and
per-day limits; when the main model is rate limited, the request is retried
once on a lighter fallback model that has its own quota.

Note: on the free tier Google may use prompts and responses to improve its
products, so indexed code is sent to Google when this provider answers.
"""

import json
import logging
from collections.abc import Iterator

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = httpx.Timeout(15.0, read=300.0)


class GeminiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _headers() -> dict:
    return {"x-goog-api-key": get_settings().gemini_api_key or "",
            "Content-Type": "application/json"}


def _models() -> list[str]:
    s = get_settings()
    return [m for m in dict.fromkeys([s.gemini_model, s.gemini_fallback_model]) if m]


def _error(resp: httpx.Response) -> GeminiError:
    try:
        err = resp.json().get("error", {})
        message = err.get("message") or resp.text[:300]
    except ValueError:
        message = resp.text[:300]
    if resp.status_code in (400, 401, 403) and "key" in message.lower():
        message = ("The Gemini API key is missing or invalid. Set GEMINI_API_KEY in "
                   "backend/.env (create one free at aistudio.google.com/apikey) and restart "
                   "the API.")
    elif resp.status_code == 429:
        message = ("Gemini free-tier rate limit reached (requests per minute or per day). "
                   "Wait a minute and retry; daily limits reset at midnight Pacific time.")
    return GeminiError(message, resp.status_code)


def to_contents(messages: list[dict]) -> list[dict]:
    """Chat messages ({role: user|assistant, content: str}) as Gemini contents."""
    return [{"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]} for m in messages if m["content"]]


def stream(system: str, messages: list[dict], temperature: float = 0.2) -> Iterator[dict]:
    """Yield `{"text": ...}` pieces, then one `{"done": {...}}` with the model,
    finish reason and token usage. Raises GeminiError."""
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": to_contents(messages),
            "generationConfig": {"temperature": temperature}}
    models = _models()
    for i, model in enumerate(models):
        final: dict = {}
        with httpx.stream("POST", f"{API}/{model}:streamGenerateContent", params={"alt": "sse"},
                          headers=_headers(), json=body, timeout=TIMEOUT) as resp:
            if resp.status_code != 200:
                resp.read()
                err = _error(resp)
                if resp.status_code == 429 and i + 1 < len(models):
                    log.info("Gemini %s rate limited; falling back to %s", model, models[i + 1])
                    continue
                raise err
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:])
                cand = (chunk.get("candidates") or [{}])[0]
                for part in (cand.get("content") or {}).get("parts", []):
                    if part.get("text") and not part.get("thought"):
                        yield {"text": part["text"]}
                if cand.get("finishReason"):
                    final["finish"] = cand["finishReason"]
                if chunk.get("usageMetadata"):
                    final["usage"] = chunk["usageMetadata"]
                if (chunk.get("promptFeedback") or {}).get("blockReason"):
                    raise GeminiError("Gemini blocked this request "
                                      f"({chunk['promptFeedback']['blockReason']}).")
        usage = final.get("usage", {})
        yield {"done": {"model": model, "finish": final.get("finish", "STOP"),
                        "usage": {"input_tokens": usage.get("promptTokenCount", 0),
                                  "output_tokens": usage.get("candidatesTokenCount", 0)}}}
        return


def generate(system: str, contents: list[dict], tools: list[dict]) -> tuple[str, dict]:
    """One non-streaming turn with function declarations: (model used, the
    candidate's content). The content is echoed back unchanged in the next turn,
    which keeps any thought signatures the model attached to its calls."""
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            "tools": [{"functionDeclarations": tools}],
            "generationConfig": {"temperature": 0.1}}
    models = _models()
    for i, model in enumerate(models):
        resp = httpx.post(f"{API}/{model}:generateContent", headers=_headers(), json=body,
                          timeout=TIMEOUT)
        if resp.status_code == 429 and i + 1 < len(models):
            continue
        if resp.status_code != 200:
            raise _error(resp)
        cand = (resp.json().get("candidates") or [{}])[0]
        return model, cand.get("content") or {"role": "model", "parts": []}
    raise GeminiError("No Gemini model configured.")
