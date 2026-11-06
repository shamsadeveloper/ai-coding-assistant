"""SEARCH/REPLACE edit blocks: parsing the model's reply and applying it to a file.

The model never rewrites whole files (small models drop or alter unrelated lines).
It returns blocks of "find this exact text, replace with that"; this module applies
them only when the text matches exactly one place.
"""

import difflib
import re
from dataclasses import dataclass

_BLOCK = re.compile(
    r"^<{5,9}\s*SEARCH[^\n]*\n(?P<search>.*?)^={5,9}[ \t]*\n(?P<replace>.*?)^>{5,9}\s*REPLACE",
    re.S | re.M,
)


@dataclass(frozen=True)
class Edit:
    search: str
    replace: str


@dataclass(frozen=True)
class EditOutcome:
    content: str
    applied: int
    failures: list[str]


def parse_edit_blocks(reply: str) -> tuple[list[Edit], str]:
    """Return the edit blocks and the explanation that follows 'EXPLANATION:'."""
    edits = [Edit(m.group("search"), m.group("replace")) for m in _BLOCK.finditer(reply.replace("\r\n", "\n"))]
    explanation = ""
    if (index := reply.upper().rfind("EXPLANATION:")) >= 0:
        explanation = reply[index + len("EXPLANATION:"):].strip().strip("`").strip()
    return edits, explanation


def extract_code_block(reply: str) -> str:
    """The content of the first fenced code block (or the whole reply if there is none)."""
    match = re.search(r"```[^\n]*\n(.*?)(?:```|\Z)", reply, re.S)
    code = match.group(1) if match else reply.split("EXPLANATION:")[0]
    return code.rstrip() + "\n"


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _apply_one(text: str, edit: Edit) -> tuple[str | None, str | None]:
    if not edit.search.strip():
        return None, "empty SEARCH block"
    count = text.count(edit.search)
    if count == 1:
        return text.replace(edit.search, edit.replace, 1), None
    if count > 1:
        return None, "SEARCH text matches more than one place; include more surrounding lines"

    # Tolerant match: same lines ignoring leading/trailing whitespace (models often re-indent).
    lines = text.split("\n")
    search_lines = edit.search.rstrip("\n").split("\n")
    wanted = [line.strip() for line in search_lines]
    hits = [
        i for i in range(len(lines) - len(wanted) + 1)
        if [line.strip() for line in lines[i : i + len(wanted)]] == wanted
    ]
    if len(hits) != 1:
        return None, "SEARCH text not found" if not hits else "SEARCH text matches more than one place"

    start = hits[0]
    # Shift the replacement by the indentation the model dropped (or added).
    first = next((j for j, line in enumerate(search_lines) if line.strip()), 0)
    original_indent, model_indent = _indent(lines[start + first]), _indent(search_lines[first])
    replacement = []
    for line in edit.replace.rstrip("\n").split("\n") if edit.replace.strip() else []:
        if line.strip() and line.startswith(model_indent):
            line = original_indent + line[len(model_indent):]
        replacement.append(line)
    return "\n".join(lines[:start] + replacement + lines[start + len(wanted):]), None


def apply_edits(original: str, edits: list[Edit]) -> EditOutcome:
    """Apply edits in order. Line endings of the original file are preserved."""
    crlf = "\r\n" in original
    text = original.replace("\r\n", "\n")
    failures, applied = [], 0
    for number, edit in enumerate(edits, start=1):
        updated, error = _apply_one(text, edit)
        if updated is None:
            preview = edit.search.strip().splitlines()[0][:80] if edit.search.strip() else ""
            failures.append(f"Edit {number} ({preview!r}): {error}")
        else:
            text, applied = updated, applied + 1
    return EditOutcome(text.replace("\n", "\r\n") if crlf else text, applied, failures)


def unified_diff(path: str, before: str | None, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            (before or "").replace("\r\n", "\n").splitlines(keepends=True),
            after.replace("\r\n", "\n").splitlines(keepends=True),
            fromfile="/dev/null" if before is None else f"a/{path}",
            tofile=f"b/{path}",
        )
    )
