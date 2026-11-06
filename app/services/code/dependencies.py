"""Resolve import statements to files inside the repository (Python and JS/TS).

Imports of third-party or standard-library modules do not resolve to a file
and are reported as external.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from posixpath import dirname, join, normpath

from app.services.code.syntax import FileOutline

_PY_FROM = re.compile(r"^from\s+(\.*)([\w.]*)\s+import\s+(.+)$")
_PY_IMPORT = re.compile(r"^import\s+(.+)$")
_JS_EXTENSIONS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js")


@dataclass
class FileDependencies:
    path: str
    imports: list[str] = field(default_factory=list)  # repository files this file imports
    external: list[str] = field(default_factory=list)  # modules outside the repository
    imported_by: list[str] = field(default_factory=list)


def _python_candidates(module: str) -> list[str]:
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]


def _resolve_python(statement: str, file_path: str, known: set[str]) -> tuple[list[str], list[str]]:
    text = " ".join(statement.replace("(", " ").replace(")", " ").split())
    resolved: list[str] = []
    external: list[str] = []

    if match := _PY_FROM.match(text):
        dots, module, names = match.groups()
        if dots:  # relative import: one dot = this package
            package = dirname(file_path)
            for _ in range(len(dots) - 1):
                package = dirname(package)
            module_path = join(package, module.replace(".", "/")) if module else package
            bases = [module_path]
        else:
            bases = [module.replace(".", "/")]
        imported = [n.split(" as ")[0].strip() for n in names.split(",")]
        for base in bases:
            candidates = [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]
            # "from package import module" imports a submodule file.
            candidates += [f"{base}/{name}.py" for name in imported if name != "*"]
            hits = [normpath(c).lstrip("./") for c in candidates if normpath(c).lstrip("./") in known]
            if hits:
                resolved.extend(hits)
            else:
                external.append(module or dots)
    elif match := _PY_IMPORT.match(text):
        for part in match.group(1).split(","):
            module = part.split(" as ")[0].strip()
            hits = [c for c in _python_candidates(module) if c in known]
            (resolved.extend(hits) if hits else external.append(module))
    return resolved, external


def _resolve_js(source: str, file_path: str, known: set[str]) -> str | None:
    if not source.startswith("."):
        return None
    base = normpath(join(dirname(file_path), source))
    return next((base + ext for ext in _JS_EXTENSIONS if base + ext in known), None)


def resolve_imports(outline: FileOutline, known_paths: set[str]) -> FileDependencies:
    deps = FileDependencies(outline.path)
    for statement in outline.imports:
        if outline.language == "Python":
            resolved, external = _resolve_python(statement.text, outline.path, known_paths)
            deps.imports.extend(resolved)
            deps.external.extend(external)
        elif statement.source is not None:
            target = _resolve_js(statement.source, outline.path, known_paths)
            (deps.imports.append(target) if target else deps.external.append(statement.source))
        else:
            deps.external.append(statement.text)
    deps.imports = sorted(set(deps.imports) - {outline.path})
    deps.external = sorted(set(deps.external))
    return deps


def build_dependency_graph(outlines: Iterable[FileOutline], known_paths: set[str]) -> dict[str, FileDependencies]:
    """Resolve every file's imports and fill in the reverse ``imported_by`` edges."""
    graph = {o.path: resolve_imports(o, known_paths) for o in outlines}
    for path, deps in graph.items():
        for target in deps.imports:
            if target in graph:
                graph[target].imported_by.append(path)
    for deps in graph.values():
        deps.imported_by.sort()
    return graph
