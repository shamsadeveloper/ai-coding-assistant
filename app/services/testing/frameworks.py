"""Test framework detection, test-file recognition and test-file naming conventions."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class TestFramework:
    __test__ = False  # not a pytest test class

    name: str  # key into Settings.test_commands
    language: str
    evidence: str


def _read(root: Path, relative: str, limit: int = 200_000) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit] if path.stat().st_size <= limit else ""
    except OSError:
        return ""


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    name, parts = p.name.lower(), [part.lower() for part in p.parts[:-1]]
    suffix = p.suffix.lower()
    if suffix == ".py":
        return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        return ".test." in name or ".spec." in name or "__tests__" in parts
    if suffix == ".go":
        return name.endswith("_test.go")
    if suffix in {".java", ".kt", ".cs", ".php"}:
        stem = p.stem.lower()
        return stem.endswith(("test", "tests")) or "test" in parts or "tests" in parts
    if suffix == ".rb":
        return name.endswith(("_spec.rb", "_test.rb"))
    if suffix == ".rs":
        return "tests" in parts
    return False


def detect_test_frameworks(root: Path, indexed_paths: Iterable[str]) -> list[TestFramework]:
    paths = list(indexed_paths)
    names = {PurePosixPath(p).name.lower(): p for p in paths}
    found: dict[str, TestFramework] = {}

    def add(name: str, language: str, evidence: str) -> None:
        found.setdefault(name, TestFramework(name, language, evidence))

    # Python
    if "pytest.ini" in names or "conftest.py" in names:
        add("pytest", "Python", "pytest.ini / conftest.py")
    for manifest in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "tox.ini"):
        if manifest in names and re.search(r"\bpytest\b", _read(root, names[manifest])):
            add("pytest", "Python", manifest)
    if any(is_test_file(p) and p.endswith(".py") for p in paths):
        add("pytest", "Python", "test_*.py files (pytest also runs unittest tests)")

    # JavaScript / TypeScript
    for path in paths:
        if PurePosixPath(path).name == "package.json":
            content = _read(root, path)
            for tool in ("vitest", "jest", "mocha"):
                if re.search(rf'"{tool}"\s*:', content):
                    add(tool, "JavaScript/TypeScript", path)

    # Other ecosystems
    rules: list[tuple[str, str, Callable[[], bool], str]] = [
        ("go", "Go", lambda: "go.mod" in names and any(p.endswith("_test.go") for p in paths), "go.mod + *_test.go"),
        ("cargo", "Rust", lambda: "cargo.toml" in names, "Cargo.toml"),
        ("phpunit", "PHP", lambda: "phpunit.xml" in names or "phpunit.xml.dist" in names
         or ("composer.json" in names and "phpunit/phpunit" in _read(root, names["composer.json"])), "phpunit config"),
        ("maven", "Java", lambda: "pom.xml" in names, "pom.xml"),
        ("gradle", "Java/Kotlin", lambda: "build.gradle" in names or "build.gradle.kts" in names, "build.gradle"),
        ("dotnet", "C#", lambda: any(
            p.endswith(".csproj") and re.search(r"xunit|nunit|MSTest", _read(root, p), re.IGNORECASE) for p in paths
        ), "test project (.csproj)"),
    ]
    for name, language, check, evidence in rules:
        if check():
            add(name, language, evidence)
    return list(found.values())


def proposed_test_path(framework: str, source_path: str) -> str:
    """Conventional location for a new test file covering ``source_path``."""
    p = PurePosixPath(source_path)
    stem, suffix, parent = p.stem, p.suffix, p.parent
    if framework in {"pytest", "unittest"}:
        return f"tests/test_{stem}.py"
    if framework in {"jest", "vitest", "mocha"}:
        return str(parent / f"{stem}.test{suffix}")
    if framework == "go":
        return str(parent / f"{stem}_test.go")
    if framework == "phpunit":
        return f"tests/{stem}Test.php"
    if framework in {"maven", "gradle"}:
        test_parent = str(parent).replace("src/main/", "src/test/", 1)
        return f"{test_parent}/{stem}Test{suffix}"
    if framework == "dotnet":
        return f"tests/{stem}Tests.cs"
    if framework == "cargo":
        return f"tests/{stem}_test.rs"
    return f"tests/test_{stem}{suffix}"


def import_hint(framework: str, source_path: str) -> str:
    """How the generated test should import the code under test."""
    p = PurePosixPath(source_path)
    if framework in {"pytest", "unittest"}:
        parts = list(p.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return f"Import it as a Python module: `from {'.'.join(parts)} import ...` (tests run from the repository root)."
    if framework in {"jest", "vitest", "mocha"}:
        return f"The test file sits next to the source: import from './{p.stem}'."
    if framework == "go":
        return "The test file is in the same package as the source."
    return f"The code under test is in {source_path}."
