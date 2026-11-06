"""Walks a repository and builds the list of readable text files."""

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.services.repository.ignore_rules import GitIgnoreRules, is_excluded_dir, is_sensitive_file
from app.services.repository.language import detect_frameworks, detect_language

_BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class ScannedFile:
    path: str  # POSIX-style, relative to the repository root
    language: str | None
    size_bytes: int
    line_count: int


@dataclass
class ScanResult:
    files: list[ScannedFile] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)  # reason -> count
    frameworks: list[str] = field(default_factory=list)

    def language_counts(self) -> dict[str, int]:
        counts = Counter(f.language or "Other" for f in self.files)
        return dict(counts.most_common())


def _join(relative_dir: str, name: str) -> str:
    return f"{relative_dir}/{name}" if relative_dir else name


def _is_link(path: Path) -> bool:
    # Windows junctions are not symlinks but can also point outside the repository.
    return path.is_symlink() or path.is_junction()


class RepositoryScanner:
    """Indexes text files, skipping excluded folders, .gitignore matches, secrets and binaries."""

    def __init__(self, max_file_bytes: int, max_files: int = 50_000) -> None:
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files

    def scan(self, root: Path) -> ScanResult:
        """Scan ``root`` (already resolved and validated) and return indexed files plus skip counts."""
        result = ScanResult()
        rules = GitIgnoreRules()

        for dir_path, dir_names, file_names in os.walk(root):
            current = Path(dir_path)
            relative_dir = "" if current == root else current.relative_to(root).as_posix()
            rules.load(root, relative_dir)

            kept_dirs = []
            for name in sorted(dir_names):
                directory = current / name
                if _is_link(directory):
                    result.skipped["link"] += 1
                elif is_excluded_dir(directory):
                    result.skipped["excluded_dir"] += 1
                elif rules.is_ignored(_join(relative_dir, name), is_dir=True):
                    result.skipped["gitignored"] += 1
                else:
                    kept_dirs.append(name)
            dir_names[:] = kept_dirs  # prune: os.walk will not descend into removed folders

            for name in sorted(file_names):
                if len(result.files) >= self._max_files:
                    result.skipped["file_limit"] += 1
                    continue
                relative_path = _join(relative_dir, name)
                reason, scanned = self._inspect_file(current / name, relative_path, rules)
                if scanned:
                    result.files.append(scanned)
                else:
                    result.skipped[reason] += 1

        result.frameworks = detect_frameworks(root, (f.path for f in result.files))
        return result

    def _inspect_file(
        self, path: Path, relative_path: str, rules: GitIgnoreRules
    ) -> tuple[str, ScannedFile | None]:
        if is_sensitive_file(path.name):
            return "sensitive", None
        if _is_link(path):
            return "link", None
        if rules.is_ignored(relative_path, is_dir=False):
            return "gitignored", None
        try:
            size = path.stat().st_size
            if size > self._max_file_bytes:
                return "too_large", None
            data = path.read_bytes()
        except OSError:
            return "unreadable", None
        if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
            return "binary", None

        line_count = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        return "", ScannedFile(relative_path, detect_language(relative_path), size, line_count)
