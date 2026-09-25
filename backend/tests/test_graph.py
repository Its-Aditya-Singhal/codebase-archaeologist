import textwrap

from app.graph.build import CodeGraph, Graph, symbols_from_definitions
from app.graph.extract import extract
from app.graph.manifests import import_key, parse_manifest
from app.graph.resolve import ModuleResolver
from app.ingestion.chunker import chunk_file, detect_language


def _src(text: str) -> str:
    return textwrap.dedent(text).lstrip()


# --------------------------------------------------------------------- extract


def test_python_facts():
    facts = extract(_src("""
        import os.path as p
        from .job import Job, Retry as R
        from ..pkg import *

        class Worker(Base, mixins.Logged, metaclass=Meta):
            def work(self):
                self.fetch()
                self.queue.enqueue(1)
                helper()
        """), "python")
    imports = {i.module: i for i in facts.imports}
    assert imports["os.path"].alias == "p"
    assert imports["job"].level == 1 and imports["job"].aliases == {"R": "Retry"}
    assert imports["pkg"].level == 2 and imports["pkg"].names == ["*"]
    calls = {(c.name, c.receiver) for c in facts.calls}
    assert calls == {("fetch", "self"), ("enqueue", "self.queue"), ("helper", None)}
    assert [b.name for b in facts.bases] == ["Base", "Logged"]  # metaclass= is not a base


def test_typescript_facts():
    facts = extract(_src("""
        import Default, { a as b } from './mod';
        import * as ns from '@scope/pkg/sub';
        const lib = require('lib');
        export { x } from './x';
        class A extends B { m() { this.n(); new C<T>(); return <View.Item /> } }
        """), "tsx")
    imports = {i.module: i for i in facts.imports}
    assert imports["./mod"].aliases == {"Default": "default", "b": "a"}
    assert imports["@scope/pkg/sub"].alias == "ns"
    assert "lib" in imports and imports["./x"].names == ["x"]
    calls = {(c.name, c.receiver) for c in facts.calls}
    assert {("n", "this"), ("C", None), ("Item", "View")} <= calls
    assert [b.name for b in facts.bases] == ["B"]


def test_other_languages_extract_calls():
    go = extract('package p\nimport x "github.com/a/b"\nfunc f() { x.Do(); g() }\n', "go")
    assert go.imports[0].module == "github.com/a/b" and go.imports[0].alias == "x"
    assert {(c.name, c.receiver) for c in go.calls} == {("Do", "x"), ("g", None)}
    java = extract("import a.b.C;\nclass X extends Y { void m() { o.bar(); } }\n", "java")
    assert java.imports[0].names == ["C"] and java.calls[0].name == "bar"
    assert java.bases[0].name == "Y"
    rust = extract("use crate::a::{b, c};\nfn f() { A::new(); }\n", "rust")
    assert rust.imports[0].module == "crate::a" and rust.imports[0].names == ["b", "c"]
    assert (rust.calls[0].name, rust.calls[0].receiver) == ("new", "A")


# --------------------------------------------------------------------- resolve


def test_python_module_resolution():
    paths = {"rq/__init__.py", "rq/job.py", "rq/cli/cli.py", "tests/json.py", "tests/test_job.py"}
    r = ModuleResolver(paths, {})
    [rel] = extract("from ..job import Job\n", "python").imports
    assert r.resolve(rel, "rq/cli/cli.py", "python").files == ["rq/job.py"]
    [pkg] = extract("from rq import job\n", "python").imports
    res = r.resolve(pkg, "tests/test_job.py", "python")
    assert res.files == ["rq/__init__.py"] and res.submodules == {"job": "rq/job.py"}
    # `tests/` is a source root, but a stdlib name must not resolve into it.
    [std] = extract("import json\n", "python").imports
    assert r.resolve(std, "rq/job.py", "python").external == "json"


def test_js_module_resolution():
    paths = {"src/lib/api.ts", "src/components/index.tsx", "src/app/page.tsx"}
    r = ModuleResolver(paths, {})
    imports = extract(
        "import { api } from '@/lib/api';\nimport X from '../components';\n"
        "import React from 'react';\nimport y from '@scope/pkg/deep';\n", "tsx").imports
    resolved = [r.resolve(i, "src/app/page.tsx", "tsx") for i in imports]
    assert resolved[0].files == ["src/lib/api.ts"]
    assert resolved[1].files == ["src/components/index.tsx"]
    assert resolved[2].external == "react" and resolved[3].external == "@scope/pkg"


def test_manifests():
    deps = parse_manifest("package.json", '{"dependencies": {"next": "16"}, '
                                          '"devDependencies": {"eslint": "^9"}}')
    assert {(d.name, d.scope) for d in deps} == {("next", "runtime"), ("eslint", "dev")}
    deps = parse_manifest("pyproject.toml", _src("""
        [project]
        dependencies = ["redis>=4", "Click[extra]>=8 ; python_version>'3.8'"]
        [project.optional-dependencies]
        test = ["pytest"]
        """))
    assert {(d.name, d.spec, d.scope) for d in deps} == {
        ("redis", ">=4", "runtime"), ("Click", ">=8", "runtime"), ("pytest", "", "optional")}
    assert [d.name for d in parse_manifest("requirements-dev.txt", "-r base.txt\nruff==0.5\n")] \
        == ["ruff"]
    assert [d.name for d in parse_manifest("go.mod", "module x\nrequire (\n  a.io/b v1.2.0\n)\n")] \
        == ["a.io/b"]
    assert parse_manifest("package.json", "{not json") == []
    assert import_key("pypi", "Flask-Login") == import_key("pypi", "flask_login")


# ------------------------------------------------------------------ code graph


REPO = {
    "pkg/__init__.py": "from .queue import Queue\n",
    "pkg/queue.py": _src("""
        import redis

        class Queue:
            def enqueue(self, f):
                return self.enqueue_call(f)

            def enqueue_call(self, f):
                return redis.Redis()
        """),
    "pkg/utils.py": "def helper():\n    return 1\n",
    "pkg/worker.py": _src("""
        from pkg import Queue
        import pkg.utils as u
        from .base import Base

        class Worker(Base):
            def work(self):
                q = Queue()
                q.enqueue(u.helper)
                u.helper()
                self.start()
        """),
    "pkg/base.py": _src("""
        class Base:
            def start(self):
                pass
        """),
    "pyproject.toml": '[project]\ndependencies = ["redis>=4"]\n',
}


def _graph() -> Graph:
    files, rows = {}, []
    for i, (path, text) in enumerate(REPO.items()):
        language = detect_language(path)
        files[path] = {"language": language, "line_count": text.count("\n") + 1,
                       "content": text}
        for j, c in enumerate(chunk_file(path, text, language)):
            rows.append({"id": i * 100 + j, "path": path, "language": language,
                         "symbol_kind": c.symbol_kind, "symbol_name": c.symbol_name,
                         "start_line": c.start_line, "end_line": c.end_line,
                         "metadata": c.metadata})
    g = Graph()
    CodeGraph(g, files, symbols_from_definitions(files, rows)).build()
    return g


def _edge(g: Graph, src: str, kind: str, dst: str) -> dict | None:
    for (s, k, d), e in g.edges.items():
        if k == kind and s[1].endswith(src) and d[1].endswith(dst):
            return e
    return None


def test_code_graph_edges():
    g = _graph()
    # self-call within the class
    assert _edge(g, "::Queue.enqueue", "calls", "::Queue.enqueue_call")["confidence"] == 1.0
    # imported through a package __init__ re-export
    e = _edge(g, "::Worker.work", "calls", "pkg/queue.py::Queue")
    assert e["confidence"] == 1.0 and e["data"]["via"] == "import"
    # module alias: u.helper()
    assert _edge(g, "::Worker.work", "calls", "::helper")["data"]["via"] == "module import"
    # q.enqueue(): variable receiver, only one method of that name -> low confidence
    assert _edge(g, "::Worker.work", "calls", "::Queue.enqueue")["confidence"] < 0.5
    # inherited method via self
    assert _edge(g, "::Worker.work", "calls", "::Base.start")["data"]["via"] == "inherited"
    assert _edge(g, "::Worker", "inherits", "::Base") is not None
    # structure and imports
    assert _edge(g, "pkg/queue.py", "defines", "::Queue") is not None
    assert _edge(g, "::Queue", "contains", "::Queue.enqueue") is not None
    assert _edge(g, "pkg/worker.py", "imports", "pkg/utils.py") is not None
    # `import redis` resolves to the dependency declared in pyproject.toml
    assert _edge(g, "pkg/queue.py", "imports", "pypi:redis") is not None
    assert _edge(g, "pyproject.toml", "declares", "pypi:redis") is not None


def test_small_class_methods_are_symbols_mapped_to_their_chunk():
    text = REPO["pkg/queue.py"]
    files = {"pkg/queue.py": {"language": "python", "content": text}}
    chunks = [{"id": 7, "path": "pkg/queue.py", "symbol_name": "Queue", "start_line": 3,
               "end_line": 9},
              {"id": 8, "path": "pkg/queue.py", "symbol_name": None, "start_line": 1,
               "end_line": 1}]
    syms = {s.name: s for s in symbols_from_definitions(files, chunks)}
    assert set(syms) == {"Queue", "Queue.enqueue", "Queue.enqueue_call"}
    assert syms["Queue.enqueue"].kind == "method" and syms["Queue.enqueue"].parent == "Queue"
    # The class is one retrieval chunk; its methods point into it.
    assert {s.chunk_id for s in syms.values()} == {7}
