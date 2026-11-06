"""Language detection by file name and framework detection from dependency manifests."""

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

EXTENSION_LANGUAGES = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".dart": "Dart",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".bat": "Batch", ".cmd": "Batch",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML",
    ".md": "Markdown", ".rst": "reStructuredText",
}

FILENAME_LANGUAGES = {"dockerfile": "Dockerfile", "makefile": "Makefile"}

# (manifest file name or suffix, pattern found in its content, framework)
_FRAMEWORK_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (manifest, re.compile(pattern, re.IGNORECASE | re.MULTILINE), framework)
    for manifest, pattern, framework in [
        ("package.json", r'"react"\s*:', "React"),
        ("package.json", r'"next"\s*:', "Next.js"),
        ("package.json", r'"vue"\s*:', "Vue"),
        ("package.json", r'"@angular/core"\s*:', "Angular"),
        ("package.json", r'"svelte"\s*:', "Svelte"),
        ("package.json", r'"express"\s*:', "Express"),
        ("package.json", r'"@nestjs/core"\s*:', "NestJS"),
        ("requirements.txt", r"^\s*django\b", "Django"),
        ("requirements.txt", r"^\s*fastapi\b", "FastAPI"),
        ("requirements.txt", r"^\s*flask\b", "Flask"),
        ("pyproject.toml", r"[\"'\s]django\b", "Django"),
        ("pyproject.toml", r"[\"'\s]fastapi\b", "FastAPI"),
        ("pyproject.toml", r"[\"'\s]flask\b", "Flask"),
        ("composer.json", r'"laravel/framework"\s*:', "Laravel"),
        ("composer.json", r'"symfony/framework-bundle"\s*:', "Symfony"),
        ("pom.xml", r"spring-boot", "Spring Boot"),
        ("build.gradle", r"spring-boot", "Spring Boot"),
        ("build.gradle.kts", r"spring-boot", "Spring Boot"),
        ("gemfile", r"^\s*gem\s+['\"]rails['\"]", "Ruby on Rails"),
        ("go.mod", r"github\.com/gin-gonic/gin", "Gin"),
        ("cargo.toml", r"^\s*actix-web\b", "Actix Web"),
        ("cargo.toml", r"^\s*axum\b", "Axum"),
        (".csproj", r"Microsoft\.NET\.Sdk\.Web|Microsoft\.AspNetCore", "ASP.NET Core"),
    ]
)

_MANIFEST_MAX_BYTES = 200_000


def detect_language(relative_path: str) -> str | None:
    """Return the language for a file name, or None when unknown."""
    path = PurePosixPath(relative_path)
    return FILENAME_LANGUAGES.get(path.name.lower()) or EXTENSION_LANGUAGES.get(path.suffix.lower())


def _manifest_key(file_name: str) -> str:
    name = file_name.lower()
    return ".csproj" if name.endswith(".csproj") else name


def detect_frameworks(root: Path, relative_paths: Iterable[str]) -> list[str]:
    """Detect frameworks by matching known dependency names inside manifest files.

    This is a heuristic: it reports what the manifests declare, not what the code imports.
    """
    rules_by_manifest: dict[str, list[tuple[re.Pattern[str], str]]] = {}
    for manifest, pattern, framework in _FRAMEWORK_RULES:
        rules_by_manifest.setdefault(manifest, []).append((pattern, framework))

    found: set[str] = set()
    for relative_path in relative_paths:
        rules = rules_by_manifest.get(_manifest_key(PurePosixPath(relative_path).name))
        if not rules:
            continue
        manifest_path = root / relative_path
        try:
            if manifest_path.stat().st_size > _MANIFEST_MAX_BYTES:
                continue
            content = manifest_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.update(framework for pattern, framework in rules if pattern.search(content))
    return sorted(found)
