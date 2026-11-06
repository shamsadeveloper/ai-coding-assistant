"""Line-based text and regex search across indexed files."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.services.repository.errors import InvalidSearchQueryError, RepositoryError
from app.services.repository.path_guard import resolve_inside

MAX_PATTERN_LENGTH = 200
# Very long lines are usually minified/generated code; skipping them also bounds regex cost.
MAX_SEARCHABLE_LINE_CHARS = 5_000
MAX_RETURNED_LINE_CHARS = 300


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_number: int
    line: str


@dataclass
class SearchResult:
    matches: list[SearchMatch] = field(default_factory=list)
    files_searched: int = 0
    truncated: bool = False


def compile_query(query: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    if not query or len(query) > MAX_PATTERN_LENGTH:
        raise InvalidSearchQueryError(f"Query must be 1-{MAX_PATTERN_LENGTH} characters")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise InvalidSearchQueryError(f"Invalid regular expression: {exc}") from exc


class CodeSearcher:
    def __init__(self, max_file_bytes: int) -> None:
        self._max_file_bytes = max_file_bytes

    def search(
        self,
        root: Path,
        relative_paths: Iterable[str],
        pattern: re.Pattern[str],
        max_results: int,
    ) -> SearchResult:
        """Search ``relative_paths`` under ``root`` and stop after ``max_results`` matches."""
        result = SearchResult()
        for relative_path in relative_paths:
            try:
                full_path = resolve_inside(root, relative_path)
                if full_path.stat().st_size > self._max_file_bytes:
                    continue
                text = full_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, RepositoryError):
                continue  # file changed or vanished since the last scan

            result.files_searched += 1
            for line_number, line in enumerate(text.splitlines(), start=1):
                if len(line) > MAX_SEARCHABLE_LINE_CHARS or not pattern.search(line):
                    continue
                result.matches.append(
                    SearchMatch(relative_path, line_number, line.strip()[:MAX_RETURNED_LINE_CHARS])
                )
                if len(result.matches) >= max_results:
                    result.truncated = True
                    return result
        return result
