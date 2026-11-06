"""Heuristic test-coverage gaps: which source files and symbols no test mentions.

A name appearing in a test is not proof it is tested, and the reverse; this is a
fast way to find obvious gaps, not a replacement for a coverage tool.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.code.syntax import parse_outline
from app.services.testing.frameworks import is_test_file


@dataclass
class CoverageGap:
    path: str
    language: str | None
    test_files: list[str] = field(default_factory=list)
    untested_symbols: list[str] = field(default_factory=list)
    tested_symbols: list[str] = field(default_factory=list)


def _is_public(name: str) -> bool:
    return not name.startswith("_") and name != "<anonymous>"


def find_coverage_gaps(files: Iterable[tuple[str, str | None, str]], limit: int = 100) -> list[CoverageGap]:
    """``files`` are (path, language, text) for all indexed code files."""
    all_files = list(files)
    tests = [(path, text) for path, _, text in all_files if is_test_file(path)]
    test_text = "\n".join(text for _, text in tests)
    gaps = []

    for path, language, text in all_files:
        if is_test_file(path):
            continue
        outline = parse_outline(path, language, text)
        names = sorted({s.name for s in outline.symbols if _is_public(s.name)})
        if not outline.supported or not names:
            continue
        stem = PurePosixPath(path).stem.lower()
        gap = CoverageGap(path, language, test_files=[t for t, _ in tests if stem in PurePosixPath(t).stem.lower()])
        for name in names:
            mentioned = re.search(rf"\b{re.escape(name)}\b", test_text) is not None
            (gap.tested_symbols if mentioned else gap.untested_symbols).append(name)
        if gap.untested_symbols:
            gaps.append(gap)

    gaps.sort(key=lambda g: (-len(g.untested_symbols), g.path))
    return gaps[:limit]
