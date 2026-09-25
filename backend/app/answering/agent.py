"""Agentic investigations: multi-step evidence gathering with tools.

Some questions need several hops ("which issue led to the retry logic in the
worker, and what else did that PR change?"). In agent mode a model works in
two phases:

1. Investigate: starting from normal retrieval, the model calls tools (search,
   read code, line history, graph relations, impact, open a commit/PR/issue).
   Everything a tool returns joins one evidence pool, numbered S1..Sn in the
   order found, and each step is streamed to the UI.
2. Answer: the standard answer writer answers from the pool, with the same
   citation contract as a single-pass answer.

The loop is provider-agnostic (`AgentModel`): Claude via the Anthropic API or a
local model via Ollama. Without a model, agent mode falls back to single-pass.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
import httpx

from app.answering.answer import _client, choose_provider
from app.config import get_settings
from app.db import connection
from app.graph.query import describe_impact, locate, related_code
from app.history.provenance import file_provenance, range_provenance
from app.retrieval.hybrid import _COLUMNS, RELATION_TEXT, RetrievedChunk, search

log = logging.getLogger(__name__)

MAX_ROUNDS = 6
MAX_POOL = 36
PREVIEW_LINES = 30  # of each new source, shown to the model in tool results

AGENT_PROMPT = """\
You are the evidence-gathering stage of Codebase Archaeologist, which explains software \
repositories from evidence. You are given a question about the repository "{repo}" and the \
evidence found so far (sources S1..Sn). Use the tools to gather whatever further evidence the \
question needs: follow references, read the code in question, trace its history to the \
commits, pull requests and issues behind it, and follow the dependency graph. Every tool \
result adds numbered sources to the shared evidence pool.

Work efficiently: at most {rounds} rounds of tool calls, several calls per round when they are \
independent. Do not repeat a call you have made. When the pool can answer the question, or \
no tool would add anything, stop calling tools and reply with one short sentence saying what \
the evidence covers. Do not write the final answer; a later stage writes it from the pool."""

TOOLS: list[dict] = [
    {"name": "search",
     "description": "Hybrid search (semantic + keyword + symbol) over code, docs, commits, "
                    "pull requests and issues. Use for concepts, identifiers or topics.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to look for"}},
         "required": ["query"]}},
    {"name": "read_code",
     "description": "Read source code: a line range of a file, or (without lines) the file's "
                    "outline of definitions.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer"},
         "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "find_symbol",
     "description": "Locate definitions (functions, classes, methods) by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "code_history",
     "description": "History of a line range (or whole file): the commit that introduced it, "
                    "recent changes with diffs, their pull requests and linked issues.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer"},
         "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "relations",
     "description": "Dependency-graph neighbours of the definition at a location: its "
                    "callers, callees, base classes and subclasses.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer"},
         "end_line": {"type": "integer"}}, "required": ["path", "start_line"]}},
    {"name": "impact",
     "description": "What could break if the code at a location changes: dependents up to 3 "
                    "hops, tests reaching it, files that change together with it, risk.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start_line": {"type": "integer"},
         "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "open_record",
     "description": "Open a commit (by sha prefix), pull request or issue (by number) with "
                    "its description and discussion.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["commit", "pull_request", "issue"]},
         "id": {"type": "string", "description": "Commit sha (prefix) or PR/issue number"}},
         "required": ["kind", "id"]}},
]


# ------------------------------------------------------------------ evidence pool


@dataclass
class Pool:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    keys: set = field(default_factory=set)

    def add(self, items: list[RetrievedChunk]) -> list[tuple[str, RetrievedChunk]]:
        """Add new sources; returns (ref, chunk) for those that were new."""
        added = []
        for c in items:
            key = (c.source_type, c.id) if c.id > 0 else (c.source_type, c.path, c.symbol_name)
            if key in self.keys or len(self.chunks) >= MAX_POOL:
                continue
            self.keys.add(key)
            self.chunks.append(c)
            added.append((f"S{len(self.chunks)}", c))
        return added


def describe(ref: str, c: RetrievedChunk, preview: bool = True) -> str:
    if c.source_type in ("code", "doc"):
        head = f"[{ref}] {c.source_type} {c.symbol_name or ''} {c.path}:{c.start_line}-{c.end_line}"
        if c.metadata.get("graph"):
            head += f" ({c.metadata['graph']})"
    elif c.source_type == "commit":
        head = (f"[{ref}] commit {str(c.metadata.get('sha', ''))[:10]} by "
                f"{c.metadata.get('author')} {str(c.metadata.get('date', ''))[:10]}"
                + (f" role={c.metadata['role']}" if c.metadata.get("role") else ""))
    elif c.source_type in ("pull_request", "issue"):
        head = f"[{ref}] {c.source_type} {c.symbol_name} ({c.metadata.get('state')})"
    else:
        head = f"[{ref}] {c.source_type} {c.symbol_name or ''}"
    if not preview:
        return head
    lines = c.content.splitlines()
    body = "\n".join(lines[:PREVIEW_LINES])
    if len(lines) > PREVIEW_LINES:
        body += f"\n… ({len(lines) - PREVIEW_LINES} more lines in the source)"
    return f"{head}\n{body}"


# ------------------------------------------------------------------------ tools


def _chunk_rows(where: str, params: tuple) -> list[RetrievedChunk]:
    with connection() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM chunks WHERE {where}", params).fetchall()
    return [RetrievedChunk(**r, score=1.0, matched_by=["agent"]) for r in rows]


def run_tool(repo: dict, name: str, args: dict) -> tuple[list[RetrievedChunk], str]:
    """Execute a tool: (new evidence, extra text for the model)."""
    rid = repo["id"]
    path = args.get("path")
    start, end = args.get("start_line"), args.get("end_line")
    if start is not None and end is None:
        end = start
    if name == "search":
        return search(rid, str(args.get("query", "")), limit=8, history=False), ""
    if name == "read_code":
        if start is None:
            with connection() as conn:
                rows = conn.execute(
                    """SELECT label, data->>'kind' AS kind, start_line, end_line FROM graph_nodes
                       WHERE repo_id = %s AND kind = 'symbol' AND path = %s
                       ORDER BY start_line""", (rid, path)).fetchall()
            outline = "\n".join(f"{r['kind']} {r['label']} lines {r['start_line']}-"
                                f"{r['end_line']}" for r in rows[:120])
            return [], f"Outline of {path}:\n{outline or '(no definitions found)'}"
        return _chunk_rows(
            "repo_id = %s AND path = %s AND start_line <= %s AND end_line >= %s "
            "ORDER BY start_line LIMIT 4", (rid, path, end, start)), ""
    if name == "find_symbol":
        from app.graph.explore import search as graph_search
        hits = graph_search(rid, str(args.get("name", "")), ["symbol"], 12)
        return [], "\n".join(f"{h['data'].get('kind')} {h['label']} at {h['path']}:"
                             f"{h['start_line']}-{h['end_line']}" for h in hits) or "No match."
    if name == "code_history":
        if not (repo.get("local_path") and repo.get("head_sha")):
            return [], "History is not available for this repository."
        items = (range_provenance(rid, repo["local_path"], repo["head_sha"], path, start, end)
                 if start is not None else file_provenance(rid, repo["local_path"], path))
        return [RetrievedChunk(**vars(i), score=1.0, matched_by=["agent", "history"])
                for i in items], ""
    if name == "relations":
        target, related = related_code(rid, path, start, end, limit=8)
        if not related:
            return [], "The dependency graph has no relations for that location."
        chunks = {c.id: c for c in _chunk_rows(
            "id = ANY(%s)", ([r["chunk_id"] for r in related],))}
        out = []
        for r in related:
            c = chunks.get(r["chunk_id"])
            if c is not None:
                text = RELATION_TEXT[r["relation"]].format(target["label"])
                c.metadata = {**c.metadata, "graph": text}
                out.append(c)
        return out, ""
    if name == "impact":
        text = describe_impact(repo, path, start, end)
        target = locate(rid, path, start, end)
        if not text or target is None:
            return [], "No impact analysis is available for that location."
        return [RetrievedChunk(
            id=-target["id"], source_type="graph", path=target["path"], language=None,
            symbol_kind="impact", symbol_name=target["label"],
            start_line=target["start_line"], end_line=target["end_line"], content=text,
            score=1.0, matched_by=["agent", "impact"],
            metadata={"node_kind": target["kind"]})], ""
    if name == "open_record":
        kind, ident = args.get("kind"), str(args.get("id", "")).lstrip("#")
        if kind == "commit":
            return _chunk_rows("repo_id = %s AND source_type = 'commit' AND "
                               "metadata->>'sha' LIKE %s LIMIT 1", (rid, f"{ident}%")), ""
        if kind in ("pull_request", "issue") and ident.isdigit():
            return _chunk_rows("repo_id = %s AND source_type = %s AND symbol_name = %s",
                               (rid, kind, f"#{ident}")), ""
        return [], "Unknown record."
    return [], f"Unknown tool {name}."


# ----------------------------------------------------------------- model adapters


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


class AgentModel(Protocol):
    name: str

    def start(self, system: str, user: str) -> None: ...

    def next_calls(self) -> list[ToolCall]:
        """Ask the model for its next tool calls; [] means it is done."""
        ...

    def send_results(self, results: list[tuple[ToolCall, str]]) -> None: ...


class ClaudeAgentModel:
    def __init__(self) -> None:
        self.name = get_settings().answer_model
        self.system = ""
        self.messages: list[dict] = []

    def start(self, system: str, user: str) -> None:
        self.system, self.messages = system, [{"role": "user", "content": user}]

    def next_calls(self) -> list[ToolCall]:
        settings = get_settings()
        response = _client().messages.create(
            model=settings.answer_model, max_tokens=8000, system=self.system,
            tools=TOOLS, messages=self.messages, thinking={"type": "adaptive"},
            output_config={"effort": settings.answer_effort})
        self.messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return []
        return [ToolCall(b.id, b.name, dict(b.input)) for b in response.content
                if b.type == "tool_use"]

    def send_results(self, results: list[tuple[ToolCall, str]]) -> None:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": text}
            for call, text in results]})


class OllamaAgentModel:
    def __init__(self) -> None:
        self.name = get_settings().ollama_model
        self.messages: list[dict] = []

    def start(self, system: str, user: str) -> None:
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": user}]

    def next_calls(self) -> list[ToolCall]:
        settings = get_settings()
        resp = httpx.post(f"{settings.ollama_url}/api/chat", timeout=300.0, json={
            "model": settings.ollama_model, "stream": False, "messages": self.messages,
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"]}} for t in TOOLS],
            "options": {"num_ctx": settings.ollama_num_ctx, "temperature": 0.1}})
        data = resp.json()
        if resp.status_code != 200 or data.get("error"):
            raise RuntimeError(data.get("error") or resp.text[:300])
        msg = data.get("message") or {}
        self.messages.append(msg)
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append(ToolCall(uuid.uuid4().hex[:8], fn.get("name", ""), args))
        return calls

    def send_results(self, results: list[tuple[ToolCall, str]]) -> None:
        self.messages += [{"role": "tool", "tool_name": call.name, "content": text}
                          for call, text in results]


def agent_model() -> AgentModel | None:
    provider = choose_provider()
    if provider == "anthropic":
        return ClaudeAgentModel()
    if provider == "ollama":
        return OllamaAgentModel()
    return None


# ------------------------------------------------------------------------- loop


def _validate(call: ToolCall) -> str | None:
    tool = next((t for t in TOOLS if t["name"] == call.name), None)
    if tool is None:
        return f"Unknown tool {call.name!r}."
    missing = [k for k in tool["input_schema"].get("required", []) if k not in call.args]
    if missing:
        return f"Missing required arguments: {', '.join(missing)}."
    for key in ("start_line", "end_line"):
        if key in call.args and not isinstance(call.args[key], int):
            try:
                call.args[key] = int(call.args[key])
            except (TypeError, ValueError):
                return f"{key} must be an integer."
    return None


def investigate(repo: dict, question: str, seed: list[RetrievedChunk], model: AgentModel,
                context: str = "") -> Iterator[dict]:
    """Run the tool loop. Yields `step` events, and finally
    `{"event": "pool", "data": [chunks]}` with the gathered evidence."""
    pool = Pool()
    seeded = pool.add(seed)
    listing = "\n".join(describe(ref, c, preview=False) for ref, c in seeded)
    model.start(
        AGENT_PROMPT.format(repo=repo["name"], rounds=MAX_ROUNDS),
        f"{context}<question>{question}</question>\n\n<evidence_so_far>\n{listing}\n"
        "</evidence_so_far>\n\nThe sources above are only titles; read them with the tools "
        "if you need their content.")
    seen_calls: set[str] = set()
    step = 0
    for _ in range(MAX_ROUNDS):
        try:
            calls = model.next_calls()
        except (anthropic.APIError, httpx.HTTPError, RuntimeError) as exc:
            log.warning("Agent model error: %s", exc)
            yield {"event": "step", "data": {"n": step + 1, "tool": None, "input": {},
                                             "summary": f"Stopped: the model failed ({exc})",
                                             "added": []}}
            break
        if not calls:
            break
        results = []
        for call in calls:
            step += 1
            signature = json.dumps([call.name, call.args], sort_keys=True)
            error = _validate(call)
            if error is None and signature in seen_calls:
                error = "You already made this call; its sources are in the pool."
            seen_calls.add(signature)
            if error:
                results.append((call, f"Error: {error}"))
                yield {"event": "step", "data": {"n": step, "tool": call.name,
                                                 "input": call.args, "summary": error,
                                                 "added": []}}
                continue
            try:
                found, note = run_tool(repo, call.name, call.args)
            except Exception as exc:  # a tool failure is reported to the model, not fatal
                log.exception("Tool %s failed", call.name)
                found, note = [], f"Error: the tool failed ({exc})."
            added = pool.add(found)
            text = "\n\n".join(describe(ref, c) for ref, c in added)
            if found and not added:
                text = "Everything this returned is already in the pool."
            text = "\n\n".join(t for t in (note, text) if t) or "Nothing found."
            results.append((call, text))
            yield {"event": "step", "data": {
                "n": step, "tool": call.name, "input": call.args,
                "summary": _summary(call, added, note),
                "added": [ref for ref, _ in added]}}
        model.send_results(results)
        if len(pool.chunks) >= MAX_POOL:
            break
    yield {"event": "pool", "data": pool.chunks}


SOURCE_NOUN = {"code": "code source", "doc": "doc section", "graph": "impact analysis",
               "commit": "commit", "pull_request": "pull request", "issue": "issue"}


def _summary(call: ToolCall, added: list, note: str) -> str:
    if added:
        kinds: dict[str, int] = {}
        for _, c in added:
            kinds[c.source_type] = kinds.get(c.source_type, 0) + 1
        return "Found " + ", ".join(f"{n} {SOURCE_NOUN.get(k, k)}{'s' if n > 1 else ''}"
                                    for k, n in kinds.items())
    return note.splitlines()[0][:160] if note else "Nothing new"
