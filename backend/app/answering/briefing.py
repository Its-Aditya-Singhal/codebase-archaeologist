"""Evidence briefing: a cited answer assembled without a language model.

It cannot reason about the question, but it can lay out what the retrieved
evidence establishes (what the code is, where it came from, what it connects
to, what a change would reach) with the same [S#] citations a model answer
uses. It is the free default when no model is configured.
"""

import re

from app.retrieval.hybrid import RetrievedChunk

_DOC_COMMENT = re.compile(
    r'^\s*(?:"""|\'\'\'|/\*\*?|//+|#+(?!!)|\*)\s?(.*?)\s*(?:"""|\'\'\'|\*/)?$')


def _summary_line(code: str) -> str | None:
    """First line of the docstring or leading comment in a definition."""
    lines = code.splitlines()[:12]
    for i, line in enumerate(lines):
        if i == 0 and not line.lstrip().startswith(("#", "//", "/*", '"""', "'''")):
            continue  # the signature
        stripped = line.strip()
        if not stripped or stripped.startswith("@"):
            continue
        m = _DOC_COMMENT.match(line)
        if m and m.group(1) and len(m.group(1)) > 8:
            return m.group(1).rstrip(".") + "."
        if not m:
            return None
    return None


def _date(value) -> str:
    return str(value or "")[:10]


def build_briefing(question: str, chunks: list[RetrievedChunk]) -> str:
    ref = {id(c): f"[S{i}]" for i, c in enumerate(chunks, start=1)}
    by = lambda pred: [c for c in chunks if pred(c)]  # noqa: E731
    out: list[str] = []

    # What the code is.
    focus = by(lambda c: "focus" in c.matched_by and c.source_type == "code")
    named = by(lambda c: c.source_type == "code" and "symbol" in c.matched_by)
    subject = (focus or named or by(lambda c: c.source_type == "code"))[:1]
    for c in subject:
        what = f"`{c.symbol_name}` ({c.symbol_kind})" if c.symbol_name else "This code"
        line = f"{what} is defined in `{c.path}` lines {c.start_line}–{c.end_line} {ref[id(c)]}."
        if summary := _summary_line(c.content):
            line += f" Its own description: *{summary}*"
        out.append(line)

    # Where it came from.
    origin = by(lambda c: c.source_type == "commit" and c.metadata.get("role") == "introduced")
    recent = by(lambda c: c.source_type == "commit" and c.metadata.get("role") == "modified")
    prs = {str(c.metadata.get("number")): c for c in chunks if c.source_type == "pull_request"}
    issues = by(lambda c: c.source_type == "issue" and "history" in c.matched_by)
    if origin or recent:
        out.append("\n**History**")
    for c in origin:
        m = c.metadata
        line = (f"- **Introduced** in `{m.get('sha', '')[:10]}` “{c.content.splitlines()[0]}” "
                f"by {m.get('author')} on {_date(m.get('date'))} {ref[id(c)]}")
        if (pr := prs.get(str(m.get("pr")))) is not None:
            line += f", merged in PR #{pr.metadata.get('number')} “{pr.metadata.get('title')}” "
            line += ref[id(pr)]
        out.append(line + ".")
    for c in recent:
        m = c.metadata
        line = (f"- Changed on {_date(m.get('date'))} by {m.get('author')}: "
                f"“{c.content.splitlines()[0]}” {ref[id(c)]}")
        if (pr := prs.get(str(m.get("pr")))) is not None:
            line += f" (PR #{pr.metadata.get('number')} {ref[id(pr)]})"
        out.append(line)
    for c in issues:
        m = c.metadata
        out.append(f"- Linked issue #{m.get('number')} “{m.get('title')}” "
                   f"({m.get('relation') or 'referenced'}) {ref[id(c)]}")
    for c in by(lambda c: c.source_type == "pull_request" and "history" in c.matched_by):
        body = c.content.split("\n", 2)[-1].strip() if c.content.count("\n") >= 2 else ""
        first = next((ln.strip().lstrip("-*#> ").strip() for ln in body.splitlines()
                      if len(ln.strip()) > 30), "")
        if first:
            out.append(f"- PR #{c.metadata.get('number')} explains: *{first[:240]}* {ref[id(c)]}")

    # What it connects to.
    callers = by(lambda c: str(c.metadata.get("graph", "")).startswith("calls "))
    callees = by(lambda c: str(c.metadata.get("graph", "")).startswith("called by "))
    family = by(lambda c: str(c.metadata.get("graph", "")).startswith(("base class", "subclass")))
    if callers or callees or family:
        out.append("\n**Connections** (static dependency graph)")
    if callers:
        out.append("- Called from " + ", ".join(
            f"`{c.symbol_name}` in `{c.path}` {ref[id(c)]}" for c in callers) + ".")
    if callees:
        out.append("- Calls " + ", ".join(f"`{c.symbol_name}` {ref[id(c)]}" for c in callees) + ".")
    for c in family:
        out.append(f"- `{c.symbol_name}` is the {c.metadata['graph']} {ref[id(c)]}.")

    # What a change would reach.
    for c in by(lambda c: c.source_type == "graph"):
        lines = c.content.splitlines()
        out.append("\n**Impact of a change**")
        out += [f"- {ln} {ref[id(c)]}" for ln in lines[1:3]]

    # Other matches: docs, and code/history found by search rather than by relation.
    seen = {id(c) for c in subject + callers + callees + family}
    docs = by(lambda c: c.source_type == "doc")[:3]
    other_code = [c for c in chunks if c.source_type == "code" and id(c) not in seen
                  and not c.metadata.get("graph")][:4]
    discussion = by(lambda c: c.source_type in ("pull_request", "issue", "commit")
                    and "history" not in c.matched_by)[:4]
    if docs or other_code or discussion:
        out.append("\n**Also relevant**")
    for c in docs:
        out.append(f"- Docs: “{c.symbol_name or c.path}” in `{c.path}` {ref[id(c)]}")
    for c in other_code:
        out.append(f"- Code: `{c.symbol_name or c.path}` in `{c.path}` {ref[id(c)]}")
    for c in discussion:
        title = c.metadata.get("title") or c.content.splitlines()[0]
        label = {"pull_request": f"PR #{c.metadata.get('number')}",
                 "issue": f"Issue #{c.metadata.get('number')}",
                 "commit": f"Commit `{str(c.metadata.get('sha', ''))[:10]}`"}[c.source_type]
        out.append(f"- {label}: “{title}” {ref[id(c)]}")

    if not out:
        out.append("The retrieved sources don't directly describe this; open them below.")
    out.append("\n---\n*Evidence briefing, assembled without a language model: it lays out what "
               "the sources show but doesn't interpret them for your question. For written "
               "answers, run a free local model with Ollama (see the README).*")
    return "\n".join(out)
