"""Static facts from source files: imports, call sites and base classes.

One syntax-tree walk per file, driven by small per-language tables, produces
unresolved facts ("line 42 calls `enqueue` on `self`", "this file imports
`.job`"). app/graph/resolve.py turns them into edges between real files and
symbols. This is deliberately name-based static analysis, not type inference:
cheap, language-agnostic and right most of the time, with each resolved edge
carrying a confidence so consumers can tell a certain edge from a guess.
"""

import re
from dataclasses import dataclass, field

from app.ingestion.chunker import _parser

# Call-like node -> (callee field, receiver field). A receiver field of None
# means the callee is an expression (`a.b.c`, `A::new`) that is split by
# `_split_callee`; otherwise the grammar separates name and receiver.
CALL_NODES: dict[str, dict[str, tuple[str, str | None]]] = {
    "python": {"call": ("function", None)},
    "javascript": {
        "call_expression": ("function", None),
        "new_expression": ("constructor", None),
        "jsx_opening_element": ("name", None),
        "jsx_self_closing_element": ("name", None),
    },
    "go": {"call_expression": ("function", None)},
    "rust": {"call_expression": ("function", None)},
    "java": {
        "method_invocation": ("name", "object"),
        "object_creation_expression": ("type", None),
    },
    "c": {"call_expression": ("function", None)},
    "cpp": {"call_expression": ("function", None), "new_expression": ("type", None)},
    "csharp": {
        "invocation_expression": ("function", None),
        "object_creation_expression": ("type", None),
    },
    "kotlin": {"call_expression": ("", None)},  # callee is the first named child
    "ruby": {"call": ("method", "receiver")},
    "php": {
        "function_call_expression": ("function", None),
        "member_call_expression": ("name", "object"),
        "scoped_call_expression": ("name", "scope"),
        "object_creation_expression": ("", None),
    },
    "swift": {"call_expression": ("", None)},
}
CALL_NODES["typescript"] = CALL_NODES["javascript"]
CALL_NODES["tsx"] = CALL_NODES["javascript"]

# Class-like node -> child node types that hold its base classes / interfaces.
BASE_HOLDERS = {
    "superclasses",  # python (field on class_definition)
    "class_heritage", "extends_clause", "implements_clause",  # js/ts
    "superclass", "super_interfaces", "extends_interfaces",  # java
    "base_list",  # c#
    "delegation_specifier",  # kotlin
    "base_clause", "class_interface_clause",  # php
    "base_class_clause",  # c++
}
CLASS_NODES = {
    "class_definition", "class_declaration", "abstract_class_declaration", "class",
    "interface_declaration", "class_specifier", "struct_specifier", "object_declaration",
    "record_declaration", "struct_declaration",
}

_IDENT_TYPES = {
    "identifier", "type_identifier", "property_identifier", "field_identifier",
    "simple_identifier", "constant", "name", "shorthand_property_identifier",
}
_SEPARATORS = re.compile(r"\?\.|::|->|\.|\\")
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_GENERICS = re.compile(r"<[^<>()]*>")


@dataclass
class Import:
    module: str  # as written: "os.path", "./job", "github.com/x/y", "a.b.C"
    line: int
    names: list[str] = field(default_factory=list)  # imported names; ["*"] for wildcard
    aliases: dict[str, str] = field(default_factory=dict)  # local name -> imported name
    level: int = 0  # python relative-import depth (number of leading dots)
    alias: str | None = None  # `import x as y` / `import * as ns` -> local module alias


@dataclass
class Call:
    name: str  # unqualified callee: `enqueue` for `self.queue.enqueue(...)`
    receiver: str | None  # `self.queue`; None for a bare call
    line: int


@dataclass
class Base:
    class_line: int  # first line of the class definition
    name: str  # unqualified base name
    line: int


@dataclass
class FileFacts:
    imports: list[Import] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    bases: list[Base] = field(default_factory=list)


def extract(text: str, language: str | None) -> FileFacts:
    if language not in CALL_NODES:
        return FileFacts()
    source = text.encode("utf-8")
    try:
        root = _parser(language).parse(source).root_node
    except Exception:
        return FileFacts()
    facts = FileFacts()
    call_types = CALL_NODES[language]

    def txt(node) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", "replace")

    stack = [root]
    while stack:
        node = stack.pop()
        t = node.type
        if t in call_types:
            call = _call(node, call_types[t], txt)
            if call is not None:
                # `require('x')` / dynamic `import('x')` are imports, not calls.
                imp = _require(node, call, language, txt)
                if imp is not None:
                    facts.imports.append(imp)
                else:
                    facts.calls.append(call)
        elif t in CLASS_NODES:
            facts.bases.extend(_bases(node, txt))
        imp_handler = _IMPORT_HANDLERS.get(language, {}).get(t)
        if imp_handler is not None:
            facts.imports.extend(imp_handler(node, txt))
        stack.extend(reversed(node.named_children))
    return facts


# ---------------------------------------------------------------------- calls


def _call(node, spec: tuple[str, str | None], txt) -> Call | None:
    callee_field, receiver_field = spec
    callee = node.child_by_field_name(callee_field) if callee_field else None
    if callee is None:
        callee = node.named_children[0] if node.named_children else None
    if callee is None:
        return None
    line = node.start_point[0] + 1
    if receiver_field is not None:
        receiver = node.child_by_field_name(receiver_field)
        name = txt(callee).strip()
        if not _IDENT.match(name):
            return None
        return Call(name, _clean_receiver(txt(receiver)) if receiver is not None else None, line)
    name, receiver = _split_callee(txt(callee))
    return Call(name, receiver, line) if name else None


def _split_callee(text: str) -> tuple[str | None, str | None]:
    """`self.queue.enqueue` -> ("enqueue", "self.queue"); `A::new` -> ("new", "A");
    `foo` -> ("foo", None); `f()(x)` / `a[0]` -> (None, None)."""
    text = _GENERICS.sub("", text.strip()).removeprefix("new ").strip()
    parts = _SEPARATORS.split(text)
    name = parts[-1].strip()
    if not _IDENT.match(name):
        return None, None
    receiver = text[: len(text) - len(parts[-1])].rstrip(".:->?\\").strip() or None
    return name, _clean_receiver(receiver) if receiver else None


def _clean_receiver(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 80 else text[:80]


def _require(node, call: Call, language: str, txt) -> Import | None:
    if language not in ("javascript", "typescript", "tsx", "ruby"):
        return None
    if call.receiver is not None or call.name not in ("require", "import", "require_relative"):
        return None
    arg = _first_string(node, txt)
    if arg is None:
        return None
    if call.name == "require_relative" and not arg.startswith("."):
        arg = "./" + arg
    return Import(arg, node.start_point[0] + 1)


def _first_string(node, txt) -> str | None:
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type in ("string", "template_string", "string_literal"):
            return txt(child).strip("'\"`")
        return None
    return None


# ---------------------------------------------------------------- inheritance


def _bases(node, txt) -> list[Base]:
    class_line = node.start_point[0] + 1
    holders = []
    superclasses = node.child_by_field_name("superclasses")
    if superclasses is not None:
        holders.append(superclasses)
    holders += [c for c in node.named_children if c.type in BASE_HOLDERS and c != superclasses]
    out: list[Base] = []
    for holder in holders:
        for ident in _base_names(holder, txt):
            out.append(Base(class_line, ident, holder.start_point[0] + 1))
    return out


def _base_names(holder, txt) -> list[str]:
    """Unqualified names of the types a heritage clause lists: `m.Base` -> Base.
    Keyword arguments (`metaclass=...`) and generic arguments are skipped."""
    names: list[str] = []
    for child in holder.named_children:
        if child.type in ("keyword_argument", "type_arguments", "access_specifier",
                          "value_arguments", "arguments"):
            continue
        if child.type in BASE_HOLDERS or child.type in ("constructor_invocation", "user_type",
                                                        "generic_type", "type_list"):
            names += _base_names(child, txt)
            continue
        name, _ = _split_callee(txt(child))
        if name:
            names.append(name)
    return names


# -------------------------------------------------------------------- imports


def _py_import(node, txt) -> list[Import]:
    out = []
    for child in node.children_by_field_name("name"):
        if child.type == "aliased_import":
            module = txt(child.child_by_field_name("name"))
            alias = txt(child.child_by_field_name("alias"))
            out.append(Import(module, node.start_point[0] + 1, alias=alias))
        else:
            out.append(Import(txt(child), node.start_point[0] + 1))
    return out


def _py_import_from(node, txt) -> list[Import]:
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return []
    level, module = 0, txt(module_node)
    if module_node.type == "relative_import":
        prefix = next((c for c in module_node.children if c.type == "import_prefix"), None)
        level = len(txt(prefix)) if prefix is not None else 0
        dotted = next((c for c in module_node.named_children if c.type == "dotted_name"), None)
        module = txt(dotted) if dotted is not None else ""
    imp = Import(module, node.start_point[0] + 1, level=level)
    if any(c.type == "wildcard_import" for c in node.named_children):
        imp.names.append("*")
    for child in node.children_by_field_name("name"):
        if child.type == "aliased_import":
            name = txt(child.child_by_field_name("name"))
            imp.aliases[txt(child.child_by_field_name("alias"))] = name
        else:
            name = txt(child)
        imp.names.append(name)
    return [imp]


def _js_import(node, txt) -> list[Import]:
    source = node.child_by_field_name("source")
    if source is None:
        return []
    imp = Import(txt(source).strip("'\"`"), node.start_point[0] + 1)
    clause = next((c for c in node.named_children if c.type in ("import_clause", "export_clause")),
                  None)
    if clause is not None:
        for c in clause.named_children:
            if c.type == "identifier":  # default import
                imp.aliases[txt(c)] = "default"
                imp.names.append("default")
            elif c.type == "namespace_import":
                ident = next((i for i in c.named_children if i.type == "identifier"), None)
                imp.alias = txt(ident) if ident is not None else None
            elif c.type in ("named_imports",):
                for spec in c.named_children:
                    _js_specifier(spec, imp, txt)
            elif c.type == "export_specifier":
                _js_specifier(c, imp, txt)
    return [imp]


def _js_specifier(spec, imp: Import, txt) -> None:
    name_node = spec.child_by_field_name("name")
    if name_node is None:
        return
    name = txt(name_node)
    alias = spec.child_by_field_name("alias")
    imp.names.append(name)
    if alias is not None:
        imp.aliases[txt(alias)] = name


def _go_import(node, txt) -> list[Import]:
    path = node.child_by_field_name("path")
    if path is None:
        return []
    alias = node.child_by_field_name("name")
    return [Import(txt(path).strip('"`'), node.start_point[0] + 1,
                   alias=txt(alias) if alias is not None else None)]


def _dotted_import(node, txt) -> list[Import]:
    """Java `import a.b.C;`, C# `using A.B;`, Kotlin `import a.b.C`, PHP `use A\\B\\C;`."""
    text = txt(node)
    text = re.sub(r"^(import|using|use)\s+(static\s+)?", "", text.strip()).rstrip(";").strip()
    alias = None
    if " as " in text:
        text, alias = (s.strip() for s in text.split(" as ", 1))
    text = text.replace("\\", ".").lstrip(".")
    if not text or "{" in text:
        return []
    last = text.rsplit(".", 1)[-1]
    return [Import(text, node.start_point[0] + 1, names=[last], alias=alias)]


def _rust_use(node, txt) -> list[Import]:
    arg = node.child_by_field_name("argument")
    if arg is None:
        return []
    text = "".join(txt(arg).split())
    line = node.start_point[0] + 1
    if "{" in text:
        prefix, rest = text.split("{", 1)
        names = [n.split("as")[0] for n in rest.rstrip("}").split(",") if n and "{" not in n]
        return [Import(prefix.rstrip(":"), line, names=[n.rsplit("::", 1)[-1] for n in names])]
    path, _, last = text.rpartition("::")
    return [Import(path or text, line, names=[last] if path else [])]


def _c_include(node, txt) -> list[Import]:
    path = node.child_by_field_name("path")
    if path is None:
        return []
    raw = txt(path)
    if raw.startswith("<"):
        return [Import(raw.strip("<>"), node.start_point[0] + 1, level=-1)]  # system header
    return [Import(raw.strip('"'), node.start_point[0] + 1)]


_IMPORT_HANDLERS = {
    "python": {"import_statement": _py_import, "import_from_statement": _py_import_from},
    "javascript": {"import_statement": _js_import, "export_statement": _js_import},
    "go": {"import_spec": _go_import},
    "java": {"import_declaration": _dotted_import},
    "csharp": {"using_directive": _dotted_import},
    "kotlin": {"import_header": _dotted_import},
    "php": {"namespace_use_clause": _dotted_import},
    "rust": {"use_declaration": _rust_use},
    "c": {"preproc_include": _c_include},
    "cpp": {"preproc_include": _c_include},
}
_IMPORT_HANDLERS["typescript"] = _IMPORT_HANDLERS["javascript"]
_IMPORT_HANDLERS["tsx"] = _IMPORT_HANDLERS["javascript"]
