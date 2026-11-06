"""LLM-based code intelligence: explanations, bug detection and improvement suggestions.

The model only sees code that the deterministic layer (index, path guard,
Tree-sitter) selected, and every finding it returns is checked against the
real file before it is stored.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.secrets import redact_text
from app.models import AnalysisFinding, AnalysisRun
from app.services.code.insight import CodeInsightService, SymbolNotFoundError
from app.services.code.syntax import FileOutline, parse_outline
from app.services.intelligence.prompts import (
    BUGS_SYSTEM,
    EXPLAIN_SYSTEM,
    IMPROVEMENTS_SYSTEM,
    code_prompt,
    number_lines,
)
from app.services.intelligence.reports import SEVERITY_ORDER, BugReport, ImprovementReport
from app.services.llm_service import ChatMessage, LLMService
from app.services.repository.errors import RepositoryError

SNIPPET_LINES = 6

AnalysisMode = Literal["fast", "thorough"]


class AnalysisRunNotFoundError(RepositoryError):
    """No analysis run with the given id."""


@dataclass(frozen=True)
class CodeTarget:
    path: str
    language: str | None
    symbol: str | None
    start_line: int
    end_line: int
    total_lines: int
    lines: list[str]  # the whole file, for snippets and validation
    context: str

    @property
    def numbered_code(self) -> str:
        return number_lines(self.lines[self.start_line - 1 : self.end_line], self.start_line)

    def prompt(self) -> str:
        return code_prompt(
            self.path, self.language, self.numbered_code, self.start_line, self.end_line,
            self.total_lines, self.symbol, self.context,
        )

    def snippet(self, start: int, end: int) -> str:
        last = min(end, start + SNIPPET_LINES - 1)
        return number_lines(self.lines[start - 1 : last], start)


@dataclass(frozen=True)
class Explanation:
    path: str
    symbol: str | None
    start_line: int
    end_line: int
    model: str
    text: str


def chunk_ranges(outline: FileOutline, total_lines: int, max_lines: int) -> list[tuple[int, int]]:
    """Split a long file into ranges of at most ``max_lines``, cutting at top-level symbol ends when possible."""
    boundaries = sorted({s.end_line for s in outline.symbols if "." not in s.qualified_name})
    ranges, start = [], 1
    while start <= total_lines:
        end = min(total_lines, start + max_lines - 1)
        if end < total_lines:
            natural = [b for b in boundaries if start + max_lines // 2 <= b <= end]
            end = natural[-1] if natural else end
        ranges.append((start, end))
        start = end + 1
    return ranges


class CodeIntelligenceService:
    def __init__(
        self, llm: LLMService, insight: CodeInsightService, session: Session, max_lines: int
    ) -> None:
        self._llm = llm
        self._insight = insight
        self._session = session
        self._max_lines = max_lines

    async def explain(self, repository_id: int, path: str, symbol: str | None = None) -> Explanation:
        target = self._targets(repository_id, path, symbol)[0]
        result = await self._llm.chat([ChatMessage("system", EXPLAIN_SYSTEM), ChatMessage("user", target.prompt())])
        return Explanation(target.path, symbol, target.start_line, target.end_line, result.model, result.content)

    async def detect_bugs(
        self, repository_id: int, path: str, symbol: str | None = None, mode: AnalysisMode = "fast"
    ) -> AnalysisRun:
        started = time.perf_counter()
        targets = self._targets(repository_id, path, symbol, mode)
        findings: list[AnalysisFinding] = []
        summaries, discarded, model = [], 0, ""

        for target in targets:
            report, model = await self._llm.generate_structured(BUGS_SYSTEM, target.prompt(), BugReport)
            summaries.append(report.summary)
            for f in report.findings:
                lines = self._valid_range(target, f.line_start, f.line_end)
                if lines is None:
                    discarded += 1
                    continue
                findings.append(AnalysisFinding(
                    source="llm", title=f.title, category=f.category, severity=f.severity, status=f.status,
                    line_start=lines[0], line_end=lines[1], explanation=f.explanation,
                    suggestion=f.suggestion, snippet=target.snippet(*lines),
                ))

        findings = _deduplicate(findings)
        findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity or "", 9), f.status != "confirmed", f.line_start))
        return self._save("bugs", repository_id, targets[0], symbol, model, summaries, findings, discarded, started)

    async def suggest_improvements(
        self, repository_id: int, path: str, symbol: str | None = None, mode: AnalysisMode = "fast"
    ) -> AnalysisRun:
        started = time.perf_counter()
        targets = self._targets(repository_id, path, symbol, mode)
        findings = self._duplicate_findings(repository_id, targets[0])
        summaries, discarded, model = [], 0, ""

        for target in targets:
            report, model = await self._llm.generate_structured(IMPROVEMENTS_SYSTEM, target.prompt(), ImprovementReport)
            summaries.append(report.summary)
            for s in report.suggestions:
                lines = self._valid_range(target, s.line_start, s.line_end)
                if lines is None:
                    discarded += 1
                    continue
                findings.append(AnalysisFinding(
                    source="llm", title=s.title, category=s.category, impact=s.impact,
                    line_start=lines[0], line_end=lines[1], explanation=s.recommendation,
                    suggestion=s.recommendation, benefits=s.benefits, risks=s.risks,
                    snippet=target.snippet(*lines),
                ))

        findings.sort(key=lambda f: ({"High": 0, "Medium": 1, "Low": 2}.get(f.impact or "", 3), f.line_start))
        return self._save("improvements", repository_id, targets[0], symbol, model, summaries, findings, discarded, started)

    def get_run(self, run_id: int) -> AnalysisRun:
        run = self._session.get(AnalysisRun, run_id)
        if run is None:
            raise AnalysisRunNotFoundError(f"Analysis run {run_id} not found")
        return run

    def list_runs(self, repository_id: int, limit: int = 50) -> Sequence[AnalysisRun]:
        return self._session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.repository_id == repository_id)
            .order_by(AnalysisRun.id.desc())
            .limit(limit)
        ).all()

    def _targets(
        self, repository_id: int, path: str, symbol: str | None, mode: AnalysisMode = "fast"
    ) -> list[CodeTarget]:
        relative_path, language, text = self._insight.read_source(repository_id, path)
        text = redact_text(text)  # line count is preserved, so line numbers stay valid
        lines = text.splitlines()
        outline = parse_outline(relative_path, language, text)
        dependencies = self._insight.dependencies(repository_id, relative_path)

        top_level = [s for s in outline.symbols if "." not in s.qualified_name]
        if symbol:
            found = outline.find(symbol)
            if found is None:
                available = ", ".join(s.qualified_name for s in outline.symbols[:30]) or "none found"
                raise SymbolNotFoundError(f"'{symbol}' not found in {relative_path}. Symbols: {available}")
            ranges = self._split(found.start_line, found.end_line)
        elif mode == "thorough" and top_level:
            # One call per top-level function/class: slower, but small models catch more.
            ranges = [r for s in top_level for r in self._split(s.start_line, s.end_line)]
        else:
            ranges = chunk_ranges(outline, max(len(lines), 1), self._max_lines)

        context_lines = []
        if dependencies.imports:
            context_lines.append("Imports from this repository: " + ", ".join(dependencies.imports[:15]))
        if dependencies.imported_by:
            context_lines.append("Used by: " + ", ".join(dependencies.imported_by[:15]))
        if outline.symbols:
            context_lines.append(
                "Symbols in file: " + ", ".join(f"{s.qualified_name} ({s.start_line}-{s.end_line})" for s in outline.symbols[:40])
            )
        context = "\n".join(context_lines)

        return [
            CodeTarget(relative_path, language, symbol, start, end, len(lines), lines, context)
            for start, end in ranges
        ]

    def _split(self, start: int, end: int) -> list[tuple[int, int]]:
        return [(s, min(end, s + self._max_lines - 1)) for s in range(start, end + 1, self._max_lines)]

    @staticmethod
    def _valid_range(target: CodeTarget, start: int, end: int) -> tuple[int, int] | None:
        """Accept a finding only if it starts inside the analysed lines; clamp its end."""
        if not target.start_line <= start <= target.end_line:
            return None
        return start, max(start, min(end, target.end_line))

    def _duplicate_findings(self, repository_id: int, target: CodeTarget) -> list[AnalysisFinding]:
        """Deterministic duplicate-code findings involving this file (no LLM)."""
        findings = []
        for block in self._insight.duplicates(repository_id):
            here = [loc for loc in block.locations if loc.path == target.path]
            if not here:
                continue
            others = [f"{loc.path}:{loc.start_line}-{loc.end_line}" for loc in block.locations if loc not in here[:1]]
            location = here[0]
            findings.append(AnalysisFinding(
                source="static", title=f"Duplicated block of {block.line_count} lines", category="duplication",
                impact="Medium", line_start=location.start_line, line_end=location.end_line,
                explanation="The same code also appears at: " + ", ".join(others),
                suggestion="Extract the shared logic into one function or module and call it from each place.",
                benefits="One place to fix bugs and change behaviour.",
                risks="The copies may have drifted on purpose; check each caller's behaviour before merging.",
                snippet=target.snippet(location.start_line, location.end_line),
            ))
        return findings

    def _save(
        self,
        kind: str,
        repository_id: int,
        target: CodeTarget,
        symbol: str | None,
        model: str,
        summaries: list[str],
        findings: list[AnalysisFinding],
        discarded: int,
        started: float,
    ) -> AnalysisRun:
        run = AnalysisRun(
            repository_id=repository_id, kind=kind, path=target.path, symbol=symbol, model=model,
            summary=" ".join(s.strip() for s in summaries if s.strip()), discarded_findings=discarded,
            duration_ms=round((time.perf_counter() - started) * 1000, 1), findings=findings,
        )
        self._session.add(run)
        self._session.commit()
        return run


def _deduplicate(findings: list[AnalysisFinding]) -> list[AnalysisFinding]:
    seen, unique = set(), []
    for finding in findings:
        key = (finding.line_start, finding.title.strip().lower())
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique
