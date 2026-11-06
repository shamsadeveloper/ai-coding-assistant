"""Duplicate-code detection by hashing windows of normalised lines (no LLM involved)."""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--")
MIN_WINDOW_CHARS = 120  # ignore windows of trivial lines like "}" or "return"


@dataclass(frozen=True)
class DuplicateLocation:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DuplicateBlock:
    line_count: int  # normalised (non-blank, non-comment) lines
    locations: tuple[DuplicateLocation, ...]
    preview: str


def _normalise(text: str) -> list[tuple[int, str]]:
    """(original line number, whitespace-collapsed line) for lines that carry code."""
    lines = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = " ".join(line.split())
        if stripped and not stripped.startswith(_COMMENT_PREFIXES):
            lines.append((number, stripped))
    return lines


def find_duplicates(files: Iterable[tuple[str, str]], min_lines: int = 6, max_blocks: int = 50) -> list[DuplicateBlock]:
    """Find blocks of at least ``min_lines`` identical normalised lines in two or more places."""
    normalised = {path: _normalise(text) for path, text in files}
    windows: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path, lines in normalised.items():
        for index in range(len(lines) - min_lines + 1):
            chunk = [text for _, text in lines[index : index + min_lines]]
            if sum(len(t) for t in chunk) < MIN_WINDOW_CHARS:
                continue
            digest = hashlib.sha1("\n".join(chunk).encode()).hexdigest()
            windows[digest].append((path, index))

    # Each repeated window is a group of (path, start index). Windows whose indexes are all
    # one higher than another group's continue the same duplicate, so merge them into runs.
    groups = {tuple(sorted(set(locs))) for locs in windows.values() if len(set(locs)) > 1}

    def shift(group: tuple[tuple[str, int], ...], by: int) -> tuple[tuple[str, int], ...]:
        return tuple((path, index + by) for path, index in group)

    runs: list[tuple[tuple[tuple[str, int], ...], int]] = []  # (first window, window count)
    for group in sorted(groups):
        if shift(group, -1) in groups:
            continue  # not the start of a run
        count, following = 1, shift(group, 1)
        while following in groups:
            count, following = count + 1, shift(following, 1)
        runs.append((group, count))

    blocks = []
    for start_locations, window_count in runs:
        span = min_lines + window_count - 1
        locations = []
        for path, index in start_locations:
            lines = normalised[path]
            locations.append(DuplicateLocation(path, lines[index][0], lines[index + span - 1][0]))
        # Overlapping windows inside one file (e.g. repeated lines) are not real duplicates.
        if len({(loc.path, loc.start_line) for loc in locations}) < 2:
            continue
        first_path, first_index = start_locations[0]
        preview = "\n".join(t for _, t in normalised[first_path][first_index : first_index + min(span, 8)])
        blocks.append(DuplicateBlock(span, tuple(locations), preview))

    blocks.sort(key=lambda b: (-b.line_count, b.locations[0].path))
    return blocks[:max_blocks]
