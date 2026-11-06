"""Code-intelligence routes: deterministic structure (outline, dependencies, duplicates)
and LLM analysis (explain, bugs, improvements)."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_code_insight, get_intelligence_service
from app.schemas.analysis import (
    AnalysisRunOut,
    AnalysisRunSummaryOut,
    CodeTargetRequest,
    DependenciesOut,
    DuplicateBlockOut,
    ExplanationOut,
    OutlineOut,
)
from app.services.code.insight import CodeInsightService
from app.services.intelligence.service import CodeIntelligenceService

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])

InsightDep = Annotated[CodeInsightService, Depends(get_code_insight)]
IntelligenceDep = Annotated[CodeIntelligenceService, Depends(get_intelligence_service)]
PathQuery = Annotated[str, Query(min_length=1, max_length=1000)]


@router.get("/outline", response_model=OutlineOut)
def outline(repository_id: int, path: PathQuery, insight: InsightDep) -> OutlineOut:
    """Functions, classes and methods with line ranges, plus import statements (Tree-sitter)."""
    result = insight.outline(repository_id, path)
    return OutlineOut(
        path=result.path,
        language=result.language,
        supported=result.supported,
        has_syntax_errors=result.has_syntax_errors,
        symbols=[asdict(s) for s in result.symbols],
        imports=[i.text for i in result.imports],
    )


@router.get("/dependencies", response_model=DependenciesOut)
def dependencies(repository_id: int, path: PathQuery, insight: InsightDep) -> DependenciesOut:
    """Repository files this file imports, files that import it, and external modules (Python, JS/TS)."""
    return DependenciesOut(**asdict(insight.dependencies(repository_id, path)))


@router.get("/duplicates", response_model=list[DuplicateBlockOut])
def duplicates(
    repository_id: int, insight: InsightDep, min_lines: Annotated[int, Query(ge=3, le=50)] = 6
) -> list[DuplicateBlockOut]:
    """Blocks of identical code (ignoring whitespace and comments) found in two or more places."""
    return [DuplicateBlockOut(**asdict(block)) for block in insight.duplicates(repository_id, min_lines)]


@router.post("/explain", response_model=ExplanationOut)
async def explain(request: CodeTargetRequest, service: IntelligenceDep) -> ExplanationOut:
    result = await service.explain(request.repository_id, request.path, request.symbol)
    return ExplanationOut(
        path=result.path, symbol=result.symbol, start_line=result.start_line,
        end_line=result.end_line, model=result.model, explanation=result.text,
    )


@router.post("/bugs", response_model=AnalysisRunOut)
async def detect_bugs(request: CodeTargetRequest, service: IntelligenceDep) -> AnalysisRunOut:
    """Find potential bugs with severity and confirmed/possible status. The run is stored."""
    run = await service.detect_bugs(request.repository_id, request.path, request.symbol, request.mode)
    return AnalysisRunOut.model_validate(run)


@router.post("/improvements", response_model=AnalysisRunOut)
async def suggest_improvements(request: CodeTargetRequest, service: IntelligenceDep) -> AnalysisRunOut:
    """Improvement suggestions with benefits and risks, plus static duplicate-code findings."""
    run = await service.suggest_improvements(request.repository_id, request.path, request.symbol, request.mode)
    return AnalysisRunOut.model_validate(run)


@router.get("/runs", response_model=list[AnalysisRunSummaryOut])
def list_runs(repository_id: int, service: IntelligenceDep) -> list[AnalysisRunSummaryOut]:
    return [AnalysisRunSummaryOut.model_validate(r) for r in service.list_runs(repository_id)]


@router.get("/runs/{run_id}", response_model=AnalysisRunOut)
def get_run(run_id: int, service: IntelligenceDep) -> AnalysisRunOut:
    return AnalysisRunOut.model_validate(service.get_run(run_id))
