"""Grounded answering: retrieved evidence in, a cited answer streamed out.

Three interchangeable writers share one contract (numbered evidence, [S#]
citations, `delta`* then `done` | `error` events):

- Claude via the Anthropic API: the strongest answers; needs a paid API key
- a local model via Ollama: free, runs on this machine
- an evidence briefing: no model at all (app/answering/briefing.py)

`answer_provider = "auto"` uses the first one that is available.
"""

import dataclasses
import json
import logging
from collections.abc import Iterator
from functools import lru_cache

import anthropic
import httpx

from app.answering.briefing import build_briefing
from app.answering.prompts import SYSTEM_PROMPT, format_evidence
from app.config import get_settings
from app.investigations import PriorTurn
from app.retrieval.hybrid import Focus, RetrievedChunk

log = logging.getLogger(__name__)

NO_CREDENTIALS = (
    "Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY in backend/.env and "
    "restart the API, or remove ANSWER_PROVIDER=anthropic to use a free local option. The "
    "retrieved evidence is still shown."
)
LOCAL_CHUNK_CHARS = 2500  # per source, for a small local context window
LOCAL_CHARS_PER_TOKEN = 3.2


@lru_cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def _ollama_ready() -> bool:
    """Is Ollama running with the configured model pulled?"""
    settings = get_settings()
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=1.0).json()
    except (httpx.HTTPError, ValueError):
        return False
    want = settings.ollama_model
    names = {m.get("name", "") for m in tags.get("models", [])}
    return want in names or f"{want}:latest" in names


def choose_provider() -> str:
    settings = get_settings()
    if settings.answer_provider != "auto":
        return settings.answer_provider
    if settings.anthropic_api_key:
        return "anthropic"
    if _ollama_ready():
        return "ollama"
    return "briefing"


def describe_focus(focus: Focus | None, chunks: list[RetrievedChunk]) -> str | None:
    if focus is None:
        return None
    pinned = next((c for c in chunks if c.source_type == "code" and c.path == focus.path
                   and c.symbol_name), None)
    where = f"`{focus.path}`"
    if focus.start_line is not None:
        where += f" lines {focus.start_line}-{focus.end_line or focus.start_line}"
    if pinned:
        where = f"{pinned.symbol_kind} `{pinned.symbol_name}` in " + where
    return where


def indexed_sources(stats: dict) -> list[str]:
    """What the index holds, so the model knows which "why" questions are answerable."""
    sources = ["code", "documentation"]
    history = stats.get("history") or {}
    if history.get("commits"):
        sources.append("commit history")
    if history.get("pull_requests"):
        sources.append("pull requests")
    if history.get("issues"):
        sources.append("issues")
    if (stats.get("graph") or {}).get("edges"):
        sources.append("dependency graph")
    return sources


FOLLOW_UP_NOTE = (
    "This is a follow-up in an ongoing investigation; the earlier questions and answers above "
    "are context. Their citations referred to earlier evidence: ground and cite this answer "
    "only in the evidence below.\n\n")


def _messages(prior: list[PriorTurn], user_content: str) -> list[dict]:
    """Earlier turns as alternating user/assistant messages, then this turn."""
    messages: list[dict] = []
    for t in prior:
        messages += [{"role": "user", "content": t.question},
                     {"role": "assistant", "content": t.answer}]
    note = FOLLOW_UP_NOTE if prior else ""
    return messages + [{"role": "user", "content": note + user_content}]


def stream_answer(question: str, repo_name: str, chunks: list[RetrievedChunk],
                  focus: Focus | None, sources: list[str],
                  prior: list[PriorTurn] | None = None) -> Iterator[dict]:
    """Yield `{"event": ..., "data": ...}` dicts: delta*, then done | error."""
    prior = prior or []
    if not chunks:
        yield {"event": "error", "data": {
            "message": "No evidence was retrieved for this question, so there is nothing to "
                       "ground an answer in. Try naming a file, symbol or concept."}}
        return
    provider = choose_provider()
    if provider == "briefing":
        yield {"event": "delta", "data": {"text": build_briefing(question, chunks)}}
        yield {"event": "done", "data": {"stop_reason": "end_turn", "model": "evidence briefing",
                                         "provider": "briefing", "usage": {}}}
        return
    focus_desc = describe_focus(focus, chunks)
    if provider == "ollama":
        yield from _stream_ollama(question, repo_name, chunks, focus_desc, sources, prior)
    else:
        yield from _stream_claude(question, repo_name, chunks, focus_desc, sources, prior)


# ------------------------------------------------------------------ local model


def _fit_local(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Trim sources to fit a small context window: cap each source, then drop
    the lowest-ranked ones (the tail, so S# numbering is unchanged)."""
    settings = get_settings()
    budget = int((settings.ollama_num_ctx - 4500) * LOCAL_CHARS_PER_TOKEN)
    fitted, used = [], 0
    for c in chunks:
        content = c.content
        if len(content) > LOCAL_CHUNK_CHARS:
            content = content[:LOCAL_CHUNK_CHARS] + "\n… (truncated)"
        if used + len(content) > budget and fitted:
            break
        used += len(content)
        fitted.append(dataclasses.replace(c, content=content))
    return fitted


def _stream_ollama(question: str, repo_name: str, chunks: list[RetrievedChunk],
                   focus_desc: str | None, sources: list[str],
                   prior: list[PriorTurn]) -> Iterator[dict]:
    settings = get_settings()
    evidence = format_evidence(_fit_local(chunks), repo_name, focus_desc, sources)
    body = {
        "model": settings.ollama_model,
        "stream": True,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *_messages(
            prior, f"{evidence}\n\n<question>{question}</question>")],
        "options": {"num_ctx": settings.ollama_num_ctx, "temperature": 0.2},
    }
    final: dict = {}
    try:
        with httpx.stream("POST", f"{settings.ollama_url}/api/chat", json=body,
                          timeout=httpx.Timeout(10.0, read=300.0)) as resp:
            if resp.status_code != 200:
                resp.read()
                raise httpx.HTTPStatusError(resp.text[:300], request=resp.request,
                                            response=resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                if text := (msg.get("message") or {}).get("content"):
                    yield {"event": "delta", "data": {"text": text}}
                if msg.get("done"):
                    final = msg
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        log.warning("Ollama error: %s", exc)
        yield {"event": "error", "data": {"message": (
            f"The local model ({settings.ollama_model}) failed: {exc}. Is Ollama running "
            "(`ollama serve`) with the model pulled?")}}
        return
    yield {"event": "done", "data": {
        "stop_reason": final.get("done_reason", "stop"),
        "model": settings.ollama_model,
        "provider": "ollama",
        "usage": {"input_tokens": final.get("prompt_eval_count", 0),
                  "output_tokens": final.get("eval_count", 0)},
    }}


# ----------------------------------------------------------------------- claude


def _stream_claude(question: str, repo_name: str, chunks: list[RetrievedChunk],
                   focus_desc: str | None, sources: list[str],
                   prior: list[PriorTurn]) -> Iterator[dict]:
    settings = get_settings()
    evidence = format_evidence(chunks, repo_name, focus_desc, sources)
    user_content = f"{evidence}\n\n<question>{question}</question>"

    try:
        with _client().beta.messages.stream(
            model=settings.answer_model,
            max_tokens=settings.answer_max_tokens,
            system=SYSTEM_PROMPT,
            messages=_messages(prior, user_content),
            thinking={"type": "adaptive"},
            output_config={"effort": settings.answer_effort},
            # On a policy decline, re-run server-side on Anthropic's recommended
            # fallback model instead of returning an empty refusal.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                yield {"event": "delta", "data": {"text": text}}
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        yield {"event": "error", "data": {"message": NO_CREDENTIALS}}
        return
    except anthropic.RateLimitError:
        yield {"event": "error",
               "data": {"message": "Rate limited by the Claude API; retry shortly."}}
        return
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error: %s", exc)
        yield {"event": "error", "data": {"message": f"Claude API error ({exc.status_code})."}}
        return
    except anthropic.APIConnectionError:
        yield {"event": "error", "data": {"message": "Could not reach the Claude API."}}
        return
    except Exception as exc:
        if "authentication method" in str(exc):  # no credentials configured at all
            yield {"event": "error", "data": {"message": NO_CREDENTIALS}}
            return
        log.exception("Answer generation failed")
        yield {"event": "error", "data": {"message": f"Answer generation failed: {exc}"}}
        return

    if final.stop_reason == "refusal":
        yield {"event": "error", "data": {"message": "The model declined to answer this question."}}
        return
    yield {"event": "done", "data": {
        "stop_reason": final.stop_reason,
        "model": final.model,
        "provider": "anthropic",
        "usage": {"input_tokens": final.usage.input_tokens,
                  "output_tokens": final.usage.output_tokens},
    }}
