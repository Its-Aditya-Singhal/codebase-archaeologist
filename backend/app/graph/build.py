"""Build a repository's knowledge graph from what ingestion stored.

Structure    file -defines-> symbol -contains-> symbol (class -> method)
Code         symbol -calls-> symbol, symbol -inherits-> symbol,
             file -imports-> file | module | dependency
Packages     manifest file -declares-> dependency
History      author -authored-> commit -modifies-> file
             commit -merged_in|part_of-> pull_request -fixes|mentions-> issue

The builder reads files, chunks and history from the database, not the
checkout, so the graph can be rebuilt on its own (`rebuild_graph`).

Calls are resolved by name with scope rules (see `_resolve_call`): explicit
imports, same file/class/package, inheritance for `self.x()`, then a unique
repository-wide definition as a last resort. Every edge records how it was
resolved (`via`) and a confidence, so a consumer can show "certainly calls"
apart from "probably calls".
"""

import json
import logging
import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.db import connection
from app.graph.extract import Call, FileFacts, extract
from app.graph.manifests import import_key, is_manifest, parse_manifest
from app.graph.resolve import PY_STDLIB, ModuleResolver
from app.ingestion.chunker import definitions

log = logging.getLogger(__name__)

TARGET_KINDS = {"function", "method", "class", "struct", "interface", "trait", "type", "enum",
                "protocol"}
CLASS_KINDS = {"class", "struct", "interface", "trait", "protocol", "enum", "type", "impl"}
SELF_WORDS = {"self", "this", "cls", "$this", "@", "Self", "me", "super"}
# Method names so common on built-in types (dicts, lists, strings, promises,
# loggers) that a name-only match to a repository method is usually wrong.
GENERIC_NAMES = {
    "get", "set", "put", "add", "pop", "push", "append", "extend", "insert", "remove", "delete",
    "update", "clear", "copy", "keys", "values", "items", "format", "join", "split", "strip",
    "replace", "find", "index", "count", "sort", "reverse", "encode", "decode", "lower", "upper",
    "startswith", "endswith", "read", "write", "close", "open", "flush", "send", "recv",
    "then", "catch", "finally", "map", "filter", "reduce", "forEach", "some", "every", "slice",
    "splice", "concat", "includes", "indexOf", "toString", "valueOf", "equals", "hashCode",
    "log", "debug", "info", "warn", "warning", "error", "exception", "critical", "print",
    "next", "iter", "len", "str", "repr", "run", "start", "stop", "call", "apply", "bind",
    "emit", "on", "off", "once", "has", "exists", "match", "test", "exec", "execute", "load",
    "dump", "dumps", "loads", "parse", "render", "init", "setup", "main", "wait", "sleep",
}
ECOSYSTEM = {"python": "pypi", "javascript": "npm", "typescript": "npm", "tsx": "npm",
             "go": "go", "rust": "cargo", "ruby": "rubygems"}
SAME_PACKAGE_LANGS = {"go", "java", "kotlin", "csharp", "php", "swift"}
NODE_BUILTINS = {"fs", "path", "os", "http", "https", "url", "crypto", "util", "events", "stream",
                 "child_process", "zlib", "net", "assert", "buffer", "readline", "worker_threads",
                 "process", "querystring", "timers", "tty", "dns", "cluster", "perf_hooks"}
_NON_ALNUM = re.compile(r"[^a-z0-9]")

Progress = Callable[[str], None]


@dataclass
class Sym:
    key: str  # "path::Qualified.name"
    path: str
    name: str  # qualified, e.g. Queue.enqueue_call
    kind: str
    parent: str | None  # qualified name of the containing class
    start: int
    end: int
    chunk_id: int | None
    language: str | None
    ranges: list[tuple[int, int]] = field(default_factory=list)

    @property
    def short(self) -> str:
        return re.split(r"\.|::", self.name)[-1]

    @property
    def parent_short(self) -> str | None:
        return re.split(r"\.|::", self.parent)[-1] if self.parent else None


@dataclass
class Bindings:
    """What a file's imports bring into scope."""

    names: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    modules: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    wildcard: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


@dataclass
class Graph:
    nodes: dict[tuple[str, str], dict] = field(default_factory=dict)
    edges: dict[tuple[tuple[str, str], str, tuple[str, str]], dict] = field(default_factory=dict)

    def node(self, kind: str, key: str, label: str, **attrs) -> tuple[str, str]:
        nk = (kind, key)
        if nk not in self.nodes:
            self.nodes[nk] = {"label": label, "path": None, "start_line": None, "end_line": None,
                              "chunk_id": None, "data": {}, **attrs}
        return nk

    def edge(self, src, kind: str, dst, weight: float = 1.0, confidence: float = 1.0,
             **data) -> None:
        if src == dst:
            return
        e = self.edges.get((src, kind, dst))
        if e is None:
            self.edges[(src, kind, dst)] = {"weight": weight, "confidence": confidence,
                                            "data": data}
            return
        e["weight"] += weight
        if confidence > e["confidence"]:
            e["confidence"] = confidence
            e["data"].update(via=data.get("via", e["data"].get("via")))
        if "lines" in data:
            e["data"]["lines"] = sorted(set(e["data"].get("lines", []) + data["lines"]))[:8]
        if "bound" in data:
            e["data"]["bound"] = sorted(set(e["data"].get("bound", []) + data["bound"]))[:12]


def rebuild_graph(repo_id: int, progress: Progress = lambda step: None) -> dict:
    progress("Loading files and symbols")
    files, syms = _load_code(repo_id)
    g = Graph()
    builder = CodeGraph(g, files, syms)
    progress("Extracting imports, calls and inheritance")
    code_stats = builder.build()
    progress("Linking history into the graph")
    _add_history(repo_id, g)
    progress("Saving the knowledge graph")
    _persist(repo_id, g)
    kinds = Counter(k for (_, k, _) in g.edges)
    return {
        "nodes": len(g.nodes),
        "edges": len(g.edges),
        "node_kinds": dict(Counter(k for k, _ in g.nodes)),
        "edge_kinds": dict(kinds),
        **code_stats,
    }


# ------------------------------------------------------------------------ code


def _load_code(repo_id: int) -> tuple[dict[str, dict], list[Sym]]:
    with connection() as conn:
        files = {r["path"]: r for r in conn.execute(
            "SELECT path, language, line_count, content FROM files WHERE repo_id = %s",
            (repo_id,)).fetchall()}
        chunks = conn.execute(
            """SELECT id, path, symbol_name, start_line, end_line FROM chunks
               WHERE repo_id = %s AND source_type = 'code'""", (repo_id,)).fetchall()
    return files, symbols_from_definitions(files, chunks)


def symbols_from_definitions(files: dict[str, dict], chunks: list[dict]) -> list[Sym]:
    """One node per named definition (methods of small classes included), each
    pointing at the retrieval chunk that holds it: the chunk named after it,
    else the smallest chunk containing its first line."""
    by_path: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        by_path[c["path"]].append(c)
    syms: dict[str, Sym] = {}
    for path, f in files.items():
        file_chunks = by_path.get(path, [])
        for d in definitions(f["content"], f["language"]):
            named = [c for c in file_chunks if c["symbol_name"] == d.name]
            holding = named or [c for c in file_chunks
                                if c["start_line"] <= d.start_line <= c["end_line"]]
            chunk = min(holding, key=lambda c: (c["start_line"] != d.start_line,
                                                c["end_line"] - c["start_line"]), default=None)
            key = f"{path}::{d.name}"
            s = syms.get(key)
            if s is None:
                syms[key] = Sym(key, path, d.name, d.kind, d.parent, d.start_line, d.end_line,
                                chunk["id"] if chunk else None, f["language"],
                                [(d.start_line, d.end_line)])
                continue
            # Same name twice in a file: a Rust `impl Foo` next to `struct Foo`, or a
            # conditional redefinition. One node; the type's kind wins over `impl`.
            s.start, s.end = min(s.start, d.start_line), max(s.end, d.end_line)
            s.ranges.append((d.start_line, d.end_line))
            if s.kind == "impl":
                s.kind = d.kind
    return list(syms.values())


class CodeGraph:
    def __init__(self, g: Graph, files: dict[str, dict], syms: list[Sym]):
        self.g = g
        self.files = files
        self.syms = syms
        self.by_short: dict[str, list[Sym]] = defaultdict(list)
        self.by_file_short: dict[tuple[str, str], list[Sym]] = defaultdict(list)
        self.by_file: dict[str, list[Sym]] = defaultdict(list)
        self.by_qualified: dict[tuple[str, str], Sym] = {}
        for s in syms:
            self.by_short[s.short].append(s)
            self.by_file_short[(s.path, s.short)].append(s)
            self.by_file[s.path].append(s)
            self.by_qualified[(s.path, s.name)] = s
        self.bindings: dict[str, Bindings] = defaultdict(Bindings)
        self.bases: dict[str, list[Sym]] = defaultdict(list)  # class key -> base classes
        self.deps: dict[tuple[str, str], tuple[str, str]] = {}  # (ecosystem, key) -> node
        self.go_deps: list[tuple[str, tuple[str, str]]] = []

    def build(self) -> dict:
        g = self.g
        for path, f in self.files.items():
            g.node("file", path, posixpath.basename(path), path=path, start_line=1,
                   end_line=f["line_count"], data={"language": f["language"]})
        self._structure()
        self._manifests()
        facts: dict[str, FileFacts] = {}
        for path, f in self.files.items():
            facts[path] = extract(f["content"], f["language"])
        resolver = ModuleResolver(set(self.files), {p: f["content"] for p, f in self.files.items()
                                                    if posixpath.basename(p) == "go.mod"})
        for path, ff in facts.items():
            self._imports(path, ff, resolver)
        for path, ff in facts.items():
            self._inheritance(path, ff)
        sites = resolved = 0
        for path, ff in facts.items():
            s, r = self._calls(path, ff)
            sites, resolved = sites + s, resolved + r
        return {"call_sites": sites, "resolved_call_sites": resolved}

    # ---------------------------------------------------------------- structure

    def _structure(self) -> None:
        g = self.g
        for s in self.syms:
            g.node("symbol", s.key, s.name, path=s.path, start_line=s.start, end_line=s.end,
                   chunk_id=s.chunk_id, data={"kind": s.kind, "language": s.language})
            parent = self.by_qualified.get((s.path, s.parent)) if s.parent else None
            if parent is not None:
                g.edge(("symbol", parent.key), "contains", ("symbol", s.key))
            else:
                g.edge(("file", s.path), "defines", ("symbol", s.key))

    def _manifests(self) -> None:
        for path, f in self.files.items():
            if not is_manifest(path):
                continue
            for dep in parse_manifest(path, f["content"]):
                nk = self.g.node("dependency", f"{dep.ecosystem}:{dep.name}", dep.name,
                                 data={"ecosystem": dep.ecosystem})
                self.g.edge(("file", path), "declares", nk, spec=dep.spec, scope=dep.scope)
                self.deps[(dep.ecosystem, import_key(dep.ecosystem, dep.name))] = nk
                if dep.ecosystem == "go":
                    self.go_deps.append((dep.name, nk))

    # ------------------------------------------------------------------ imports

    def _imports(self, path: str, ff: FileFacts, resolver: ModuleResolver) -> None:
        language = self.files[path]["language"]
        b = self.bindings[path]
        for imp in ff.imports:
            res = resolver.resolve(imp, path, language)
            if res.files:
                for target in res.files:
                    if target in self.files:
                        self.g.edge(("file", path), "imports", ("file", target),
                                    lines=[imp.line], via="import")
                        b.files.add(target)
            elif res.external:
                self._external(path, language, res.external, imp.line, _bound_names(imp))
            for sub in res.submodules.values():
                self.g.edge(("file", path), "imports", ("file", sub), lines=[imp.line],
                            via="import")
                b.files.add(sub)
            self._bind(b, imp, res.files, res.submodules, language)

    def _bind(self, b: Bindings, imp, files: list[str], submodules: dict[str, str],
              language: str) -> None:
        if not files and not submodules:
            return
        if language == "python" and not imp.names:  # import a.b [as c]
            b.modules[imp.alias or imp.module].extend(files)
            return
        if language == "go":
            b.modules[imp.alias or imp.module.rsplit("/", 1)[-1]].extend(files)
            return
        if imp.alias:  # import * as ns / static-import alias
            b.modules[imp.alias].extend(files)
        if "*" in imp.names or (language in ("ruby", "c", "cpp") and not imp.names):
            b.wildcard.update(files)
        local_for = {orig: local for local, orig in imp.aliases.items()}
        for name in imp.names:
            if name == "*":
                continue
            local = local_for.get(name, name)
            if name in submodules:
                b.modules[local].append(submodules[name])
            else:
                b.names[local].extend((f, name) for f in files)

    def _external(self, path: str, language: str, name: str, line: int, bound: list[str]
                  ) -> None:
        eco = ECOSYSTEM.get(language)
        target = None
        if eco == "go":
            target = next((nk for dep, nk in self.go_deps if name == dep or
                           name.startswith(dep + "/")), None)
        elif eco:
            target = self.deps.get((eco, import_key(eco, name)))
        if target is None:
            target = self.g.node("module", f"{eco or language}:{name}", name,
                                 data={"ecosystem": eco or language,
                                       "stdlib": _is_stdlib(language, name)})
        # `bound`: local names the import introduces, so a symbol's use of the
        # package can be detected in its source (see graph.query).
        self.g.edge(("file", path), "imports", target, lines=[line], via="import", bound=bound)

    # ------------------------------------------------------------- inheritance

    def _inheritance(self, path: str, ff: FileFacts) -> None:
        for base in ff.bases:
            cls = self._enclosing(path, base.class_line, kinds=CLASS_KINDS)
            if cls is None:
                continue
            targets = [t for t in self._lookup_name(path, base.name, None)
                       if t[0].kind in CLASS_KINDS and t[0].key != cls.key]
            for target, conf, via in targets[:1]:
                self.bases[cls.key].append(target)
                self.g.edge(("symbol", cls.key), "inherits", ("symbol", target.key),
                            confidence=conf, lines=[base.line], via=via)

    # -------------------------------------------------------------------- calls

    def _calls(self, path: str, ff: FileFacts) -> tuple[int, int]:
        sites = resolved = 0
        for call in ff.calls:
            if call.name not in self.by_short:
                continue  # nothing in the repo has this name: builtin / external
            sites += 1
            caller = self._enclosing(path, call.line)
            targets = self._resolve_call(path, caller, call)
            src = ("symbol", caller.key) if caller else ("file", path)
            for target, conf, via in targets:
                if caller is not None and target.key == caller.key:
                    continue  # recursion is not a relationship worth drawing
                self.g.edge(src, "calls", ("symbol", target.key), confidence=conf,
                            lines=[call.line], via=via)
            resolved += bool(targets)
        return sites, resolved

    def _enclosing(self, path: str, line: int, kinds: set[str] | None = None) -> Sym | None:
        """Innermost definition containing `line`."""
        best, best_size = None, None
        for s in self.by_file.get(path, []):
            if kinds is not None and s.kind not in kinds:
                continue
            for a, z in s.ranges:
                if a <= line <= z and (best_size is None or z - a < best_size):
                    best, best_size = s, z - a
        return best

    def _lookup(self, path: str, name: str, depth: int = 0) -> list[Sym]:
        """Definitions of `name` visible at the top level of `path`, following
        re-exports (`from .queue import Queue` in a package __init__)."""
        found = self.by_file_short.get((path, name), [])
        top = [s for s in found if s.parent is None]
        if top or found:
            return top or found
        if depth >= 3:
            return []
        b = self.bindings.get(path)
        if b is None:
            return []
        for target, orig in b.names.get(name, []):
            if hit := self._lookup(target, orig, depth + 1):
                return hit
        for target in b.wildcard:
            if hit := self._lookup(target, name, depth + 1):
                return hit
        return []

    def _lookup_name(self, path: str, name: str, caller: Sym | None
                     ) -> list[tuple[Sym, float, str]]:
        """Resolve a bare name (a call or a base class) in `path`'s scope."""
        b = self.bindings[path]
        language = self.files[path]["language"]
        if name in b.names:
            out = []
            for target, orig in b.names[name]:
                hits = self._lookup(target, name if orig == "default" else orig, 1)
                out += [(h, 1.0, "import") for h in hits]
            if out:
                return out
        local = self.by_file_short.get((path, name), [])
        if caller is not None and caller.parent and language != "python":
            same_class = [s for s in local if s.parent == caller.parent]
            if same_class:
                return [(s, 0.95, "same class") for s in same_class]
        top = [s for s in local if s.parent is None]
        if top:
            return [(s, 0.95, "same file") for s in top]
        for target in b.wildcard:
            if hits := self._lookup(target, name, 1):
                return [(h, 0.85, "wildcard import") for h in hits]
        if language in SAME_PACKAGE_LANGS:
            d = posixpath.dirname(path)
            same_pkg = [s for s in self.by_short.get(name, [])
                        if s.parent is None and posixpath.dirname(s.path) == d]
            if same_pkg:
                return [(s, 0.85, "same package") for s in same_pkg]
        cands = [s for s in self.by_short.get(name, [])
                 if s.parent is None and s.kind in TARGET_KINDS]
        if len(cands) == 1 and len(name) >= 4 and name not in GENERIC_NAMES:
            return [(cands[0], 0.5, "unique name")]
        return []

    def _resolve_call(self, path: str, caller: Sym | None, call: Call
                      ) -> list[tuple[Sym, float, str]]:
        name, recv = call.name, call.receiver
        if recv is None:
            return self._lookup_name(path, name, caller)

        b = self.bindings[path]
        if recv in SELF_WORDS:
            cls = caller.parent if caller and caller.parent else (
                caller.name if caller and caller.kind in CLASS_KINDS else None)
            if cls is None:
                return []
            own = [s for s in self.by_file_short.get((path, name), []) if s.parent == cls]
            if own and recv != "super":
                return [(s, 1.0, "self") for s in own]
            cls_sym = self.by_qualified.get((path, cls))
            for base in self._ancestors(cls_sym):
                inherited = [s for s in self.by_file_short.get((base.path, name), [])
                             if s.parent == base.name]
                if inherited:
                    return [(s, 0.9, "inherited") for s in inherited]
            return []

        if recv in b.modules:  # utils.helper(), fmt.Println()
            hits = [h for f in b.modules[recv] for h in self._lookup(f, name, 1)]
            return [(h, 1.0, "module import") for h in hits]

        # Static / class-qualified calls: Queue.from_key(), Foo::new()
        recv_last = re.split(r"\.|::|->", recv)[-1]
        if recv_last[:1].isupper():
            classes = [t for t, _, _ in self._lookup_name(path, recv_last, caller)
                       if t.kind in CLASS_KINDS]
            classes += [s for s in self.by_short.get(recv_last, [])
                        if s.kind in CLASS_KINDS and s not in classes and s.path == path]
            for cls in classes:
                members = [s for s in self.by_file_short.get((cls.path, name), [])
                           if s.parent_short == cls.short]
                if not members:  # Rust: methods live in an `impl Foo` block elsewhere
                    members = [s for s in self.by_short.get(name, [])
                               if s.parent_short == cls.short]
                if members:
                    return [(s, 0.9, "class member") for s in members[:2]]

        # A variable receiver: `job.save()`, `self.connection.pipeline()`.
        methods = [s for s in self.by_short.get(name, []) if s.parent]
        if not methods:
            return []
        # The receiver is often named after its type: `queue` / `self._queue` -> Queue.
        norm = _NON_ALNUM.sub("", recv_last.lower())
        typed = [s for s in methods if _NON_ALNUM.sub("", (s.parent_short or "").lower()) == norm]
        if typed:
            return [(s, 0.7, "receiver name") for s in typed[:2]]
        owners = {s.parent for s in methods}
        if len(owners) == 1 and len(methods) == 1 and len(name) >= 4 \
                and name not in GENERIC_NAMES:
            return [(methods[0], 0.4, "unique method name")]
        return []

    def _ancestors(self, cls: Sym | None, limit: int = 4) -> list[Sym]:
        out: list[Sym] = []
        frontier = [cls] if cls else []
        seen = {cls.key} if cls else set()
        while frontier and len(out) < 12 and limit > 0:
            nxt = []
            for c in frontier:
                for base in self.bases.get(c.key, []):
                    if base.key not in seen:
                        seen.add(base.key)
                        out.append(base)
                        nxt.append(base)
            frontier, limit = nxt, limit - 1
        return out


def _bound_names(imp) -> list[str]:
    names = [n for n in imp.names if n not in ("*", "default")]
    names = [next((a for a, o in imp.aliases.items() if o == n), n) for n in names]
    names += [a for a, o in imp.aliases.items() if o == "default"]
    if imp.alias:
        names.append(imp.alias)
    if not names:
        names.append(re.split(r"[./:]", imp.module.lstrip("@"))[-1 if "/" in imp.module else 0])
    return sorted(set(names))[:12]


def _is_stdlib(language: str, name: str) -> bool:
    if language == "python":
        return name in PY_STDLIB
    if language in ("javascript", "typescript", "tsx"):
        return name in NODE_BUILTINS
    if language == "go":
        return "." not in name.split("/")[0]
    return False


# --------------------------------------------------------------------- history


def _add_history(repo_id: int, g: Graph) -> None:
    with connection() as conn:
        commits = conn.execute(
            """SELECT id, sha, author_name, author_email, authored_at, subject, files_changed,
                      insertions, deletions
               FROM commits WHERE repo_id = %s""", (repo_id,)).fetchall()
        changes = conn.execute(
            """SELECT c.sha, cf.path, cf.insertions, cf.deletions
               FROM commit_files cf JOIN commits c ON c.id = cf.commit_id
               WHERE cf.repo_id = %s""", (repo_id,)).fetchall()
        prs = conn.execute(
            """SELECT number, title, state, author, url, merged_at FROM pull_requests
               WHERE repo_id = %s""", (repo_id,)).fetchall()
        issues = conn.execute(
            "SELECT number, title, state, author, url FROM issues WHERE repo_id = %s",
            (repo_id,)).fetchall()
        links = conn.execute(
            "SELECT src_type, src_key, dst_type, dst_key, kind FROM links WHERE repo_id = %s",
            (repo_id,)).fetchall()
        chunk_ids = {(r["source_type"], r["k"]): r["id"] for r in conn.execute(
            """SELECT id, source_type,
                      CASE WHEN source_type = 'commit' THEN metadata->>'sha'
                           ELSE metadata->>'number' END AS k
               FROM chunks WHERE repo_id = %s
                 AND source_type IN ('commit', 'pull_request', 'issue')""",
            (repo_id,)).fetchall()}

    for c in commits:
        nk = g.node("commit", c["sha"], c["subject"][:120], chunk_id=chunk_ids.get(
            ("commit", c["sha"])), data={"author": c["author_name"],
                                         "date": c["authored_at"].isoformat(),
                                         "files_changed": c["files_changed"]})
        who = (c["author_email"] or c["author_name"] or "unknown").lower()
        author = g.node("author", who, c["author_name"] or who)
        g.edge(author, "authored", nk)
    for ch in changes:
        if ("file", ch["path"]) in g.nodes:
            churn = (ch["insertions"] or 0) + (ch["deletions"] or 0)
            g.edge(("commit", ch["sha"]), "modifies", ("file", ch["path"]), weight=churn or 1)
    for p in prs:
        g.node("pull_request", str(p["number"]), f"#{p['number']} {p['title']}",
               chunk_id=chunk_ids.get(("pull_request", str(p["number"]))),
               data={"number": p["number"], "title": p["title"], "state": p["state"],
                     "author": p["author"], "url": p["url"],
                     "merged_at": p["merged_at"].isoformat() if p["merged_at"] else None})
    for i in issues:
        g.node("issue", str(i["number"]), f"#{i['number']} {i['title']}",
               chunk_id=chunk_ids.get(("issue", str(i["number"]))),
               data={"number": i["number"], "title": i["title"], "state": i["state"],
                     "author": i["author"], "url": i["url"]})
    for lk in links:
        src, dst = (lk["src_type"], lk["src_key"]), (lk["dst_type"], lk["dst_key"])
        for kind, key in (src, dst):
            if (kind, key) not in g.nodes and kind in ("pull_request", "issue"):
                # Referenced by a commit but not (yet) fetched from GitHub.
                g.node(kind, key, f"#{key}", data={"number": int(key), "fetched": False})
        if src in g.nodes and dst in g.nodes:
            g.edge(src, lk["kind"], dst)


# --------------------------------------------------------------------- persist


def _persist(repo_id: int, g: Graph) -> None:
    with connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM graph_nodes WHERE repo_id = %s", (repo_id,))
        with cur.copy("""COPY graph_nodes (repo_id, kind, key, label, path, start_line, end_line,
                                           chunk_id, data) FROM STDIN""") as copy:
            for (kind, key), n in g.nodes.items():
                copy.write_row((repo_id, kind, key, n["label"], n["path"], n["start_line"],
                                n["end_line"], n["chunk_id"], json.dumps(n["data"])))
        ids = {(r["kind"], r["key"]): r["id"] for r in cur.execute(
            "SELECT id, kind, key FROM graph_nodes WHERE repo_id = %s", (repo_id,)).fetchall()}
        with cur.copy("""COPY graph_edges (repo_id, src, dst, kind, weight, confidence, data)
                         FROM STDIN""") as copy:
            for (src, kind, dst), e in g.edges.items():
                copy.write_row((repo_id, ids[src], ids[dst], kind, e["weight"],
                                round(e["confidence"], 2), json.dumps(e["data"])))
