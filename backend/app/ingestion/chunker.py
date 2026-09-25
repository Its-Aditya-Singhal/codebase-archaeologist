"""Split files into retrieval units.

Code is chunked along syntactic boundaries (functions, classes, methods) with
tree-sitter, so a retrieved chunk is a meaningful unit a developer would point
at, carries a symbol name, and has exact line numbers for citations. Code
between definitions (imports, module-level config) is kept as `module` chunks.
Markdown is split by headings. Anything else falls back to line windows.
"""

from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter_language_pack as tslp

MAX_CHUNK_LINES = 120  # a definition larger than this is split into its members/windows
MIN_GAP_LINES = 3  # ignore tiny inter-definition gaps (blank lines, a lone import)
WINDOW_LINES = 60
WINDOW_OVERLAP = 10

# Node types that start a named definition, per tree-sitter grammar.
DEFINITION_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "class",
        "method_declaration": "method",
        "constructor_declaration": "method",
    },
    "ruby": {
        "method": "method",
        "singleton_method": "method",
        "class": "class",
        "module": "module",
    },
    "c": {"function_definition": "function", "struct_specifier": "struct"},
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "namespace_definition": "namespace",
    },
    "csharp": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "method_declaration": "method",
        "constructor_declaration": "method",
    },
    "kotlin": {
        "class_declaration": "class",
        "object_declaration": "class",
        "function_declaration": "function",
    },
    "php": {
        "function_definition": "function",
        "class_declaration": "class",
        "method_declaration": "method",
    },
    "swift": {
        "class_declaration": "class",
        "protocol_declaration": "protocol",
        "function_declaration": "function",
    },
}
DEFINITION_TYPES["typescript"] = {
    **DEFINITION_TYPES["javascript"],
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "abstract_class_declaration": "class",
}
DEFINITION_TYPES["tsx"] = DEFINITION_TYPES["typescript"]

# Nodes we look *through* to find the definition they wrap.
WRAPPER_TYPES = {"export_statement", "decorated_definition", "template_declaration"}
# `const handler = async () => {}` style definitions in JS/TS.
VARIABLE_DECL_TYPES = {"lexical_declaration", "variable_declaration"}
FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression", "function", "class"}

MARKDOWN_LANGS = {"markdown"}
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".txt", ".adoc")


@dataclass
class Chunk:
    source_type: str  # code | doc
    symbol_kind: str
    symbol_name: str | None
    start_line: int  # 1-based, inclusive
    end_line: int
    content: str
    metadata: dict = field(default_factory=dict)


@lru_cache(maxsize=64)
def _parser(language: str):
    return tslp.get_parser(language)


def detect_language(path: str) -> str | None:
    if path.lower().endswith(".txt"):
        return None  # plain text; the grammar pack would call every .txt Vim help ("vimdoc")
    try:
        return tslp.detect_language_from_path(path)
    except Exception:
        return None


def chunk_file(path: str, text: str, language: str | None) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    if language in MARKDOWN_LANGS or path.lower().endswith(DOC_EXTENSIONS):
        return _chunk_markdown(lines)
    if language in DEFINITION_TYPES:
        try:
            return _chunk_code(text, lines, language)
        except Exception:
            pass  # unparseable / grammar unavailable -> windows
    return _windows(lines, 1, len(lines), "code", "window", None)


# --------------------------------------------------------------------------- code


def _node_name(node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is None:
        # C/C++ functions keep the name inside the declarator chain.
        decl = node.child_by_field_name("declarator")
        while decl is not None and decl.child_by_field_name("declarator") is not None:
            decl = decl.child_by_field_name("declarator")
        name = decl
    if name is None and node.type == "impl_item":
        name = node.child_by_field_name("type")
    if name is None and node.type == "type_declaration":
        spec = next((c for c in node.named_children if c.type == "type_spec"), None)
        name = spec.child_by_field_name("name") if spec else None
    if name is None:
        return None
    return source[name.start_byte : name.end_byte].decode("utf-8", "replace").strip() or None


def _as_definition(node, language: str, source: bytes):
    """Return (definition_node, kind, name) if `node` is or wraps a definition."""
    types = DEFINITION_TYPES[language]
    if node.type in WRAPPER_TYPES:
        inner = node.child_by_field_name("definition") or node.child_by_field_name("declaration")
        if inner is None:
            inner = next((c for c in node.named_children if c.type in types), None)
        if inner is not None:
            found = _as_definition(inner, language, source)
            if found:
                # Keep decorators / `export` in the chunk text.
                return (node, found[1], found[2], found[3])
        # `export const x = () => ...`
        for c in node.named_children:
            found = _as_definition(c, language, source)
            if found:
                return (node, found[1], found[2], found[3])
        return None
    if node.type in types:
        return (node, types[node.type], _node_name(node, source), node)
    if node.type in VARIABLE_DECL_TYPES:
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            value = decl.child_by_field_name("value")
            if value is not None and value.type in FUNCTION_VALUE_TYPES:
                kind = "class" if value.type == "class" else "function"
                return (node, kind, _node_name(decl, source), value)
    return None


def _body_children(defn_node):
    body = defn_node.child_by_field_name("body")
    if body is None:
        body = next(
            (c for c in defn_node.named_children if c.type.endswith(("body", "block", "list"))),
            None,
        )
    return list(body.named_children) if body is not None else []


def _chunk_code(text: str, lines: list[str], language: str) -> list[Chunk]:
    source = text.encode("utf-8")
    tree = _parser(language).parse(source)
    chunks: list[Chunk] = []
    covered: list[tuple[int, int]] = []

    def emit_definition(node, kind, name, defn, parent: str | None):
        start, end = node.start_point[0] + 1, node.end_point[0] + 1
        qualified = f"{parent}.{name}" if parent and name else name
        if end - start + 1 <= MAX_CHUNK_LINES:
            chunks.append(
                Chunk("code", kind, qualified, start, end, _slice(lines, start, end),
                      {"parent": parent} if parent else {})
            )
            covered.append((start, end))
            return
        # Large definition: index its members individually, keep a header chunk
        # (signature + docstring + fields) so the container is still findable.
        members = [
            d for c in _body_children(defn)
            if (d := _as_definition(c, language, source)) is not None
        ]
        if not members:
            chunks.extend(_windows(lines, start, end, "code", kind, qualified))
            covered.append((start, end))
            return
        first_member_line = members[0][0].start_point[0] + 1
        header_end = min(max(start, first_member_line - 1), start + MAX_CHUNK_LINES - 1)
        chunks.append(
            Chunk("code", kind, qualified, start, header_end, _slice(lines, start, header_end),
                  {"parent": parent, "truncated": True} if parent else {"truncated": True})
        )
        covered.append((start, header_end))
        for m_node, m_kind, m_name, m_defn in members:
            if kind in {"class", "impl", "trait", "interface"} and m_kind == "function":
                m_kind = "method"
            emit_definition(m_node, m_kind, m_name, m_defn, qualified)

    for child in tree.root_node.named_children:
        found = _as_definition(child, language, source)
        if found:
            emit_definition(*found, parent=None)

    # Module-level code between definitions.
    covered.sort()
    cursor = 1
    gaps: list[tuple[int, int]] = []
    for s, e in covered:
        if s > cursor:
            gaps.append((cursor, s - 1))
        cursor = max(cursor, e + 1)
    if cursor <= len(lines):
        gaps.append((cursor, len(lines)))
    for s, e in gaps:
        s, e = _trim_blank(lines, s, e)
        if e - s + 1 >= MIN_GAP_LINES or (not covered and e >= s):
            chunks.extend(_windows(lines, s, e, "code", "module", None))

    chunks.sort(key=lambda c: (c.start_line, -c.end_line))
    return chunks


# ---------------------------------------------------------------- definitions

# Definitions whose bodies hold further definitions worth naming (methods).
CONTAINER_KINDS = {"class", "impl", "trait", "interface", "struct", "module", "namespace",
                   "enum", "protocol"}


@dataclass
class Definition:
    kind: str
    name: str  # qualified: Queue.enqueue_call
    parent: str | None
    start_line: int
    end_line: int


def definitions(text: str, language: str | None) -> list[Definition]:
    """Every named definition in a file, classes and their members included,
    regardless of how the file is chunked for retrieval (a small class is one
    chunk, but its methods are still separate definitions)."""
    if language not in DEFINITION_TYPES:
        return []
    source = text.encode("utf-8")
    try:
        tree = _parser(language).parse(source)
    except Exception:
        return []
    out: list[Definition] = []

    def visit(node, kind, name, defn, parent: str | None, depth: int) -> None:
        if not name:
            return
        if parent and kind == "function":
            kind = "method"
        qualified = f"{parent}.{name}" if parent else name
        out.append(Definition(kind, qualified, parent, node.start_point[0] + 1,
                              node.end_point[0] + 1))
        if kind in CONTAINER_KINDS and depth < 4:
            for child in _body_children(defn):
                if (found := _as_definition(child, language, source)) is not None:
                    visit(*found, parent=qualified, depth=depth + 1)

    for child in tree.root_node.named_children:
        if (found := _as_definition(child, language, source)) is not None:
            visit(*found, parent=None, depth=0)
    return out


# ----------------------------------------------------------------------- markdown


def _chunk_markdown(lines: list[str]) -> list[Chunk]:
    sections: list[tuple[int, int, str | None]] = []
    start, title = 1, None
    in_fence = False
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and stripped.startswith("#") and i > start:
            sections.append((start, i - 1, title))
            start = i
        if not in_fence and stripped.startswith("#") and i == start:
            title = stripped.lstrip("#").strip() or None
    sections.append((start, len(lines), title))

    chunks: list[Chunk] = []
    for s, e, t in sections:
        s, e = _trim_blank(lines, s, e)
        if e < s:
            continue
        if e - s + 1 <= MAX_CHUNK_LINES:
            chunks.append(Chunk("doc", "section", t, s, e, _slice(lines, s, e)))
        else:
            chunks.extend(_windows(lines, s, e, "doc", "section", t))
    return chunks


# ------------------------------------------------------------------------ helpers


def _windows(lines, start, end, source_type, kind, name) -> list[Chunk]:
    out: list[Chunk] = []
    s = start
    while s <= end:
        e = min(end, s + WINDOW_LINES - 1)
        body = _slice(lines, s, e)
        if body.strip():
            out.append(Chunk(source_type, kind, name, s, e, body))
        if e == end:
            break
        s = e - WINDOW_OVERLAP + 1
    return out


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _trim_blank(lines, s, e):
    while s <= e and not lines[s - 1].strip():
        s += 1
    while e >= s and not lines[e - 1].strip():
        e -= 1
    return s, e
