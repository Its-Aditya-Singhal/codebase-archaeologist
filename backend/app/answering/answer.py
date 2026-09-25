"""Grounded answering: retrieve evidence, then stream a cited answer from Claude."""

import logging
from collections.abc import Iterator
from functools import lru_cache

import anthropic

from app.answering.prompts import SYSTEM_PROMPT, format_evidence
from app.config import get_settings
from app.retrieval.hybrid import Focus, RetrievedChunk

log = logging.getLogger(__name__)


NO_CREDENTIALS = (
    "Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY in backend/.env and "
    "restart the API. The retrieved evidence is still shown."
)


@lru_cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


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
    return sources


def stream_answer(question: str, repo_name: str, chunks: list[RetrievedChunk],
                  focus: Focus | None, sources: list[str]) -> Iterator[dict]:
    """Yield `{"event": ..., "data": ...}` dicts: delta*, then done | error."""
    settings = get_settings()
    if not chunks:
        yield {"event": "error", "data": {
            "message": "No evidence was retrieved for this question, so there is nothing to "
                       "ground an answer in. Try naming a file, symbol or concept."}}
        return

    evidence = format_evidence(chunks, repo_name, describe_focus(focus, chunks), sources)
    user_content = f"{evidence}\n\n<question>{question}</question>"

    try:
        with _client().beta.messages.stream(
            model=settings.answer_model,
            max_tokens=settings.answer_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
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
        "usage": {"input_tokens": final.usage.input_tokens,
                  "output_tokens": final.usage.output_tokens},
    }}
