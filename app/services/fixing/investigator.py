"""Root-cause investigation: a short, read-only agent run, then a structured report that is verified.

    deterministic evidence (BugContext)
        -> agent with READ-ONLY tools explores what the evidence does not show (bounded steps)
        -> the model turns its notes into a RootCauseReport (JSON)
        -> every quoted line is checked against the real files; unverifiable claims are dropped
"""

from dataclasses import dataclass, field

from app.agent.engine import AgentEngine, AgentStep
from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext
from app.agent.tools.repository_tools import AnalyzeFileTool, FindFunctionTool, ReadFileTool, SearchCodeTool
from app.services.code.insight import CodeInsightService
from app.services.fixing.evidence import (
    BugContext,
    VerificationResult,
    adjusted_confidence,
    files_to_change,
    verify_evidence,
)
from app.services.fixing.prompts import EXTRACT_SYSTEM, INVESTIGATE_SYSTEM
from app.services.fixing.reports import RootCauseReport
from app.services.llm_service import LLMService
from app.services.repository.manager import RepositoryManager

TOOL_OUTPUT_CHARS = 4_000
MAX_EVIDENCE_CLAIMS = 6
MAX_TEXT = 2_000


def investigation_tools() -> ToolRegistry:
    """The investigator can look, never change: no patch, test or write tools."""
    return ToolRegistry(
        [ReadFileTool(), SearchCodeTool(), FindFunctionTool(), AnalyzeFileTool()], max_output_chars=TOOL_OUTPUT_CHARS
    )


@dataclass
class Investigation:
    report: RootCauseReport
    verification: VerificationResult
    confidence: str
    files_to_change: list[str]
    notes: str
    steps: list[AgentStep] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.report.is_real_bug and bool(self.verification.verified)

    def root_cause_dict(self) -> dict:
        report = self.report
        return {
            "is_real_bug": report.is_real_bug,
            "summary": report.summary[:MAX_TEXT],
            "location": f"{report.root_cause_path}:{report.root_cause_line}",
            "fix_strategy": report.fix_strategy[:MAX_TEXT],
            "model_confidence": report.confidence,
            "confidence": self.confidence,
            "files_to_change": self.files_to_change,
            "evidence": [vars(e) for e in self.verification.verified],
            "rejected_evidence": [vars(e) for e in self.verification.rejected],
            "notes": self.notes[:MAX_TEXT * 2],
        }


class RootCauseInvestigator:
    def __init__(
        self,
        llm: LLMService,
        repositories: RepositoryManager,
        insight: CodeInsightService,
        max_steps: int,
        max_files: int,
    ) -> None:
        self._llm = llm
        self._repositories = repositories
        self._insight = insight
        self._max_steps = max_steps
        self._max_files = max_files

    async def investigate(self, repository_id: int, description: str, context: BugContext) -> Investigation:
        question = f"Reported bug: {description}\n\n{context.to_prompt()}"

        notes, steps = "", []
        if self._max_steps > 0:
            engine = AgentEngine(self._llm, investigation_tools(), max_steps=self._max_steps)
            run = await engine.run(
                INVESTIGATE_SYSTEM, [], question + "\n\nInvestigate the root cause.",
                ToolContext(repository_id, self._repositories, self._insight),
            )
            notes, steps = run.answer, run.steps

        report, _ = await self._llm.generate_structured(
            EXTRACT_SYSTEM,
            f"{question}\n\nInvestigation notes:\n{notes or '(no notes: use the context above)'}",
            RootCauseReport,
        )
        claims = [(e.path, e.line, e.quote, e.explanation[:MAX_TEXT]) for e in report.evidence[:MAX_EVIDENCE_CLAIMS]]
        verification = verify_evidence(self._repositories, repository_id, claims)

        _, files = self._repositories.list_files(repository_id, limit=100_000)
        targets = files_to_change(
            [report.root_cause_path, *report.affected_files], {f.path for f in files}, context.path, self._max_files
        )
        return Investigation(
            report=report,
            verification=verification,
            confidence=adjusted_confidence(report.confidence, verification),
            files_to_change=targets,
            notes=notes,
            steps=steps,
        )
