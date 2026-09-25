"""Resolve import strings to repository files (or name the external package).

Per-language module systems, approximated with the repository's own file
list: no build tool, interpreter or type checker is invoked.
"""

import posixpath
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from app.graph.extract import Import

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")
JS_LANGS = {"javascript", "typescript", "tsx"}
PY_STDLIB = set(sys.stdlib_module_names)
_GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


@dataclass
class Resolution:
    files: list[str] = field(default_factory=list)  # repository files the import points at
    external: str | None = None  # package name when the import leaves the repository
    # For `from pkg import name`: imported names that are themselves modules/files.
    submodules: dict[str, str] = field(default_factory=dict)


class ModuleResolver:
    def __init__(self, paths: set[str], contents: dict[str, str]):
        self.paths = paths
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            self.by_name[posixpath.basename(p)].append(p)
        # Python: every dotted suffix of every module path -> files.
        self.py_modules: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            if not p.endswith(".py"):
                continue
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                self.py_modules[".".join(parts[i:])].append(p)
        # Go: directory of each go.mod -> module path.
        self.go_modules = {
            posixpath.dirname(p): m.group(1)
            for p, text in contents.items()
            if posixpath.basename(p) == "go.mod" and (m := _GO_MODULE.search(text))
        }
        self.go_dirs: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            if p.endswith(".go") and not p.endswith("_test.go"):
                self.go_dirs[posixpath.dirname(p)].append(p)

    def resolve(self, imp: Import, from_path: str, language: str) -> Resolution:
        try:
            if language == "python":
                return self._python(imp, from_path)
            if language in JS_LANGS:
                return self._js(imp, from_path)
            if language == "go":
                return self._go(imp)
            if language in ("java", "kotlin"):
                return self._dotted(imp, (".java", ".kt"))
            if language == "php":
                return self._dotted(imp, (".php",))
            if language == "rust":
                return self._rust(imp, from_path)
            if language == "ruby":
                return self._ruby(imp, from_path)
            if language in ("c", "cpp"):
                return self._c(imp, from_path)
        except (ValueError, IndexError):
            pass
        return Resolution()

    # ------------------------------------------------------------------ python

    def _python(self, imp: Import, from_path: str) -> Resolution:
        if imp.level > 0:
            base = posixpath.dirname(from_path)
            for _ in range(imp.level - 1):
                base = posixpath.dirname(base)
            rel = imp.module.replace(".", "/")
            target = posixpath.join(base, rel) if rel else base
            files = self._py_file(target)
            res = Resolution(files=files)
            for name in imp.names:
                sub = self._py_file(posixpath.join(target, name))
                if sub:
                    res.submodules[name] = sub[0]
            if not files and res.submodules:
                res.files = sorted(set(res.submodules.values()))
            return res

        top = imp.module.split(".")[0]
        candidates = [p for p in self.py_modules.get(imp.module, [])
                      if self._py_root_ok(p, imp.module)]
        if candidates:
            best = min(candidates, key=len)
            res = Resolution(files=[best])
            pkg_dir = best[: -len("/__init__.py")] if best.endswith("__init__.py") else None
            for name in imp.names:
                if pkg_dir is not None and (sub := self._py_file(f"{pkg_dir}/{name}")):
                    res.submodules[name] = sub[0]
            return res
        return Resolution(external=top)

    def _py_file(self, target: str) -> list[str]:
        target = posixpath.normpath(target) if target else ""
        return [p for p in (f"{target}.py", f"{target}/__init__.py") if p in self.paths]

    def _py_root_ok(self, path: str, module: str) -> bool:
        """An absolute import `a.b` matches `<root>/a/b.py` only if <root> is a
        source root (not itself a package); stdlib names need a top-level match."""
        depth = module.count(".") + (2 if path.endswith("__init__.py") else 1)
        prefix = "/".join(path.split("/")[:-depth])
        if module.split(".")[0] in PY_STDLIB and prefix not in ("", "src", "lib"):
            return False
        return prefix == "" or f"{prefix}/__init__.py" not in self.paths

    # -------------------------------------------------------------- javascript

    def _js(self, imp: Import, from_path: str) -> Resolution:
        spec = imp.module.split("?")[0]
        if spec.startswith("."):
            found = self._js_file(posixpath.join(posixpath.dirname(from_path), spec))
            return Resolution(files=[found] if found else [])
        if spec.startswith(("@/", "~/")):  # common tsconfig path alias for the source root
            rest = spec[2:]
            d = posixpath.dirname(from_path)
            while True:
                for root in (posixpath.join(d, "src"), d):
                    if found := self._js_file(posixpath.join(root, rest)):
                        return Resolution(files=[found])
                if not d:
                    break
                d = posixpath.dirname(d)
            return Resolution()
        if spec.startswith("node:"):
            return Resolution(external=spec[5:].split("/")[0])
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        return Resolution(external=name)

    def _js_file(self, target: str) -> str | None:
        target = posixpath.normpath(target)
        if target in self.paths:
            return target
        stem = re.sub(r"\.(m|c)?js$", "", target)  # TS allows importing `./x.js` for x.ts
        for ext in JS_EXTENSIONS:
            if stem + ext in self.paths:
                return stem + ext
        for ext in JS_EXTENSIONS:
            if f"{target}/index{ext}" in self.paths:
                return f"{target}/index{ext}"
        return None

    # --------------------------------------------------------------------- go

    def _go(self, imp: Import) -> Resolution:
        for mod_dir, module in self.go_modules.items():
            if imp.module == module or imp.module.startswith(module + "/"):
                rel = imp.module[len(module):].lstrip("/")
                pkg_dir = posixpath.join(mod_dir, rel) if rel else mod_dir
                return Resolution(files=sorted(self.go_dirs.get(pkg_dir, [])))
        return Resolution(external=imp.module)

    # ---------------------------------------------------------- java / php ...

    def _dotted(self, imp: Import, extensions: tuple[str, ...]) -> Resolution:
        parts = imp.module.split(".")
        if parts[-1] == "*":
            return Resolution()
        # Static imports name a member: try the full path, then drop the member.
        for cut in (len(parts), len(parts) - 1):
            if cut < 1:
                break
            # PSR-4 style roots drop leading namespace segments; keep at least 2.
            for start in range(0, max(1, cut - 1)):
                rel = "/".join(parts[start:cut])
                for ext in extensions:
                    matches = [p for p in self.by_name.get(parts[cut - 1] + ext, [])
                               if p == rel + ext or p.endswith("/" + rel + ext)]
                    if matches:
                        return Resolution(files=[min(matches, key=len)])
        return Resolution(external=".".join(parts[:2]))

    # ------------------------------------------------------------------- rust

    def _rust(self, imp: Import, from_path: str) -> Resolution:
        segs = [s for s in imp.module.split("::") if s]
        if not segs:
            return Resolution()
        head = segs[0]
        if head in ("crate", "self", "super"):
            if head == "crate":
                root = from_path.split("/src/")[0] + "/src" if "/src/" in from_path else "src"
                base = root
            else:
                base = posixpath.dirname(from_path)
                if head == "super":
                    base = posixpath.dirname(base)
            segs = segs[1:]
            for n in range(len(segs), 0, -1):
                rel = posixpath.join(base, *segs[:n])
                for cand in (f"{rel}.rs", f"{rel}/mod.rs"):
                    if cand in self.paths:
                        return Resolution(files=[cand])
            return Resolution()
        if head in ("std", "core", "alloc"):
            return Resolution()
        return Resolution(external=head)

    # ------------------------------------------------------------------- ruby

    def _ruby(self, imp: Import, from_path: str) -> Resolution:
        spec = imp.module
        if spec.startswith("."):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
            target = target if target.endswith(".rb") else target + ".rb"
            return Resolution(files=[target] if target in self.paths else [])
        rel = spec if spec.endswith(".rb") else spec + ".rb"
        matches = [p for p in self.by_name.get(posixpath.basename(rel), [])
                   if p == rel or p.endswith("/lib/" + rel) or p == "lib/" + rel]
        if matches:
            return Resolution(files=[min(matches, key=len)])
        return Resolution(external=spec.split("/")[0])

    # ---------------------------------------------------------------------- c

    def _c(self, imp: Import, from_path: str) -> Resolution:
        if imp.level < 0:
            return Resolution()  # <system> header
        local = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), imp.module))
        if local in self.paths:
            return Resolution(files=[local])
        matches = [p for p in self.by_name.get(posixpath.basename(imp.module), [])
                   if p == imp.module or p.endswith("/" + imp.module)]
        return Resolution(files=[min(matches, key=len)] if matches else [])
