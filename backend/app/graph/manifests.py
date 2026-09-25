"""Declared external dependencies, from package manifests.

Each parser returns (name, version spec, scope) triples; scope is `runtime`,
`dev` or `optional`. Parsing is forgiving: a malformed manifest yields nothing
rather than failing the index.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Dependency:
    name: str
    spec: str
    scope: str
    ecosystem: str
    manifest: str  # path of the manifest that declares it


_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


def _pep508(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http")):
        return None
    m = _PEP508.match(line)
    return (m.group(1), m.group(3).split(";", 1)[0].strip()) if m else None


def _package_json(text: str):
    data = json.loads(text)
    for section, scope in (("dependencies", "runtime"), ("devDependencies", "dev"),
                           ("peerDependencies", "runtime"), ("optionalDependencies", "optional")):
        for name, spec in (data.get(section) or {}).items():
            yield name, str(spec), scope


def _pyproject(text: str):
    data = tomllib.loads(text)
    project = data.get("project") or {}
    for req in project.get("dependencies") or []:
        if parsed := _pep508(req):
            yield *parsed, "runtime"
    for reqs in (project.get("optional-dependencies") or {}).values():
        for req in reqs:
            if parsed := _pep508(req):
                yield *parsed, "optional"
    for group in (data.get("dependency-groups") or {}).values():
        for req in group:
            if isinstance(req, str) and (parsed := _pep508(req)):
                yield *parsed, "dev"
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for section, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev")):
        for name, spec in (poetry.get(section) or {}).items():
            if name.lower() != "python":
                yield name, spec if isinstance(spec, str) else "", scope


def _requirements(text: str):
    for line in text.splitlines():
        if parsed := _pep508(line):
            yield *parsed, "runtime"


def _setup_py(text: str):
    m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
    for req in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1) if m else ""):
        if parsed := _pep508(req):
            yield *parsed, "runtime"


def _go_mod(text: str):
    for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+(v[\w.\-+]+)(.*)$",
                         text, re.MULTILINE):
        yield m.group(1), m.group(2), "dev" if "// indirect" in m.group(3) else "runtime"


def _cargo(text: str):
    data = tomllib.loads(text)
    for section, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"),
                           ("build-dependencies", "dev")):
        for name, spec in (data.get(section) or {}).items():
            version = spec if isinstance(spec, str) else (spec or {}).get("version", "")
            yield name, str(version), scope


def _gemfile(text: str):
    for m in re.finditer(r"^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?",
                         text, re.MULTILINE):
        yield m.group(1), m.group(2) or "", "runtime"


PARSERS = {
    "package.json": ("npm", _package_json),
    "pyproject.toml": ("pypi", _pyproject),
    "setup.py": ("pypi", _setup_py),
    "go.mod": ("go", _go_mod),
    "Cargo.toml": ("cargo", _cargo),
    "Gemfile": ("rubygems", _gemfile),
}
_REQUIREMENTS = re.compile(r"^requirements([-_.][\w.-]*)?\.(txt|in)$")


def is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in PARSERS or bool(_REQUIREMENTS.match(name))


def parse_manifest(path: str, text: str) -> list[Dependency]:
    name = PurePosixPath(path).name
    if name in PARSERS:
        ecosystem, parser = PARSERS[name]
    elif _REQUIREMENTS.match(name):
        ecosystem, parser = "pypi", _requirements
    else:
        return []
    try:
        return [Dependency(n, spec, scope, ecosystem, path) for n, spec, scope in parser(text)]
    except (ValueError, TypeError, AttributeError):
        return []


def import_key(ecosystem: str, name: str) -> str:
    """Normalise a package or import name so they can be matched: PyPI names are
    case-insensitive and treat - and _ alike (`Flask-Login` ~ `flask_login`)."""
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "_", name).lower()
    if ecosystem == "cargo":
        return name.replace("-", "_")
    return name
