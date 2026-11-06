"""Deterministic evidence for a bug: the code around it, its callers, imports and related tests,
plus verification of the evidence the model cites.

Nothing here calls the LLM, so the results can be trusted and shown to the developer as facts.
"""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from app.core.secrets import redact_text
from app.services.code.insight import CodeInsightService
from app.services.code.symbols import call_pattern, definition_pattern
from app.services.code.syntax import Symbol, parse_outline
from app.services.intelligence.prompts import number_lines
from app.services.repository.errors import RepositoryError
from app.services.repository.ignore_rules import is_sensitive_file
from app.services.repository.manager import RepositoryManager
from app.services.testing.frameworks import is_test_file

CONTEXT_LINES = 12
MAX_CODE_LINES = 150
MAX_CALLERS = 12
MAX_RELATED_TESTS = 8
EVIDENCE_WINDOW = 3  # a cited line may be off by this many lines and still count


@dataclass(frozen=True)
class CodeLocation:
    path: str
    line: int
    text: str


@dataclass
class BugContext:
    """Everything known about the bug before the model is asked anything."""

    path: str
    language: str | None
    line_start: int
    line_end: int
    symbol: str | None  # qualified name of the enclosing function/class
    symbol_name: str | None
    code_start: int
    code_end: int
    code: str  # numbered and redacted
    callers: list[CodeLocation] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        where = f"{self.symbol} in " if self.symbol else ""
        parts = [
            f"Reported location: {self.path} lines {self.line_start}-{self.line_end}",
            f"Code ({where}{self.path}, lines {self.code_start}-{self.code_end}):\n{self.code}",
        ]
        if self.callers:
            parts.append(
                f"Places that call {self.symbol_name} (text matches):\n"
                + "\n".join(f"  {c.path}:{c.line}: {c.text}" for c in self.callers)
            )
        parts.append("This file imports from the repository: " + (", ".join(self.imports) or "none"))
        parts.append("Files that import this file: " + (", ".join(self.imported_by) or "none"))
        parts.append("Related test files: " + (", ".join(self.related_tests) or "none found"))
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


def _enclosing_symbol(symbols: Iterable[Symbol], start: int, end: int) -> Symbol | None:
    containing = [s for s in symbols if s.start_line <= start and s.end_line >= end]
    return min(containing, key=lambda s: s.end_line - s.start_line, default=None)


def find_callers(
    repositories: RepositoryManager, repository_id: int, name: str, limit: int = MAX_CALLERS
) -> list[CodeLocation]:
    """Lines that call ``name``, without its definition(s). A text heuristic, labelled as such."""
    definition = definition_pattern(name)
    result = repositories.search_pattern(repository_id, call_pattern(name), max_results=limit * 4)
    callers = [
        CodeLocation(m.path, m.line_number, redact_text(m.line.strip())[:200])
        for m in result.matches
        if not definition.search(m.line)
    ]
    return callers[:limit]


def find_related_tests(
    repositories: RepositoryManager, repository_id: int, path: str, symbol_name: str | None
) -> list[str]:
    """Test files named after the module, then test files that mention the module or symbol."""
    stem = PurePosixPath(path).stem.lower()
    _, files = repositories.list_files(repository_id, limit=100_000)
    tests = [f.path for f in files if is_test_file(f.path)]
    named = [t for t in tests if stem and stem in PurePosixPath(t).stem.lower()]

    words = [w for w in {stem, symbol_name} if w and len(w) > 2]
    mentioning: list[str] = []
    if words:
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
        result = repositories.search_pattern(repository_id, pattern, max_results=200)
        mentioning = [m.path for m in result.matches if is_test_file(m.path)]
    return list(dict.fromkeys(named + mentioning))[:MAX_RELATED_TESTS]


def collect_context(
    repositories: RepositoryManager,
    insight: CodeInsightService,
    repository_id: int,
    path: str,
    line_start: int | None,
    line_end: int | None,
) -> BugContext:
    file = repositories.read_file(repository_id, path)  # validates the path, the index and secrets rules
    lines = redact_text(file.content).splitlines()
    total = max(len(lines), 1)
    start = min(max(line_start or 1, 1), total)
    end = min(max(line_end or start, start), total)

    outline = parse_outline(file.path, file.language, file.content)
    symbol = _enclosing_symbol(outline.symbols, start, end)
    if symbol and symbol.end_line - symbol.start_line < MAX_CODE_LINES:
        code_start, code_end = symbol.start_line, symbol.end_line
    else:
        code_start, code_end = max(1, start - CONTEXT_LINES), min(total, end + CONTEXT_LINES)

    imports: list[str] = []
    imported_by: list[str] = []
    try:
        deps = insight.dependencies(repository_id, file.path)
        imports, imported_by = deps.imports, deps.imported_by
    except RepositoryError:
        pass

    return BugContext(
        path=file.path,
        language=file.language,
        line_start=start,
        line_end=end,
        symbol=symbol.qualified_name if symbol else None,
        symbol_name=symbol.name if symbol else None,
        code_start=code_start,
        code_end=code_end,
        code=number_lines(lines[code_start - 1:code_end], code_start),
        callers=find_callers(repositories, repository_id, symbol.name) if symbol else [],
        imports=imports,
        imported_by=imported_by,
        related_tests=find_related_tests(repositories, repository_id, file.path, symbol.name if symbol else None),
    )


# ----- evidence verification ------------------------------------------------------------------

@dataclass(frozen=True)
class VerifiedEvidence:
    path: str
    line: int
    quote: str  # the real (redacted) line from the file, not the model's version
    explanation: str


@dataclass(frozen=True)
class RejectedEvidence:
    path: str
    line: int
    quote: str
    reason: str


@dataclass
class VerificationResult:
    verified: list[VerifiedEvidence] = field(default_factory=list)
    rejected: list[RejectedEvidence] = field(default_factory=list)


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _first_code_line(quote: str) -> str:
    return next((_normalise(line) for line in quote.splitlines() if line.strip()), "")


def _matches(quote: str, line: str) -> bool:
    if not line:
        return False
    return quote == line or (len(quote) >= 8 and quote in line) or (len(line) >= 8 and line in quote)


def verify_evidence(
    repositories: RepositoryManager, repository_id: int, claims: Iterable[tuple[str, int, str, str]]
) -> VerificationResult:
    """Keep only evidence whose quoted line really exists at (or within a few lines of) the cited place.

    ``claims`` are (path, line, quote, explanation). The model saw redacted code, so quotes are
    compared with the redacted file.
    """
    result = VerificationResult()
    cache: dict[str, tuple[str, list[str]] | str] = {}
    seen: set[tuple[str, int]] = set()
    for raw_path, line, raw_quote, explanation in claims:
        path = (raw_path or "").strip().replace("\\", "/").removeprefix("./")
        quote = _first_code_line(raw_quote or "")
        if len(quote) < 3:
            result.rejected.append(RejectedEvidence(path, line, raw_quote, "the quote is empty or too short"))
            continue
        if path not in cache:
            try:
                file = repositories.read_file(repository_id, path)
                cache[path] = (file.path, redact_text(file.content).splitlines())
            except RepositoryError as exc:
                cache[path] = str(exc)
        loaded = cache[path]
        if isinstance(loaded, str):
            result.rejected.append(RejectedEvidence(path, line, raw_quote, loaded))
            continue

        real_path, lines = loaded
        window = range(max(1, line - EVIDENCE_WINDOW), min(len(lines), line + EVIDENCE_WINDOW) + 1)
        match = next(
            (n for n in sorted(window, key=lambda n: abs(n - line)) if _matches(quote, _normalise(lines[n - 1]))),
            None,
        )
        if match is None:
            result.rejected.append(
                RejectedEvidence(real_path, line, raw_quote, f"this text is not in {real_path} near line {line}")
            )
        elif (real_path, match) not in seen:
            seen.add((real_path, match))
            result.verified.append(VerifiedEvidence(real_path, match, lines[match - 1].strip(), explanation))
    return result


CONFIDENCE_LEVELS = ("low", "medium", "high")


def adjusted_confidence(model_confidence: str, verification: VerificationResult) -> str:
    """The model's own confidence, lowered when its evidence does not hold up."""
    if not verification.verified:
        return "low"
    level = CONFIDENCE_LEVELS.index(model_confidence) if model_confidence in CONFIDENCE_LEVELS else 0
    if verification.rejected:
        level -= 1
    if len(verification.verified) < 2:
        level = min(level, 1)  # one quoted line is not enough for "high"
    return CONFIDENCE_LEVELS[max(level, 0)]


def files_to_change(
    candidates: Iterable[str], indexed_paths: set[str], fallback: str, max_files: int
) -> list[str]:
    """The model's list of files to change, restricted to indexed, non-test, non-secret source files."""
    chosen: list[str] = []
    for raw in candidates:
        path = (raw or "").strip().replace("\\", "/").removeprefix("./")
        if (
            path in indexed_paths and path not in chosen and not is_test_file(path)
            and not is_sensitive_file(PurePosixPath(path).name)
        ):
            chosen.append(path)
    if fallback not in chosen and not chosen:
        chosen.append(fallback)
    return chosen[:max_files]
