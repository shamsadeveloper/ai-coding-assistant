"""Reads a validated text file, optionally limited to a line range."""

from dataclasses import dataclass
from pathlib import Path

from app.services.repository.errors import FileTooLargeError, InvalidLineRangeError


@dataclass(frozen=True)
class FileContent:
    path: str
    language: str | None
    start_line: int
    end_line: int
    total_lines: int
    content: str


def read_file_lines(
    full_path: Path,
    relative_path: str,
    language: str | None,
    max_bytes: int,
    start_line: int = 1,
    end_line: int | None = None,
) -> FileContent:
    """Read ``full_path`` (already validated by the path guard) and return lines ``start_line..end_line``.

    Line numbers are 1-based and inclusive. ``end_line`` is clamped to the file length.
    """
    if full_path.stat().st_size > max_bytes:
        raise FileTooLargeError(f"{relative_path} is larger than {max_bytes} bytes")

    lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    last = total if end_line is None else min(end_line, total)

    if start_line < 1 or (end_line is not None and end_line < start_line):
        raise InvalidLineRangeError("start_line must be >= 1 and end_line >= start_line")
    if total and start_line > total:
        raise InvalidLineRangeError(f"start_line {start_line} is beyond the end of the file ({total} lines)")

    return FileContent(
        path=relative_path,
        language=language,
        start_line=start_line,
        end_line=last,
        total_lines=total,
        content="\n".join(lines[start_line - 1 : last]),
    )
