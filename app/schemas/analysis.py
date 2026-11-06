"""Request and response models for the code-intelligence API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CodeTargetRequest(BaseModel):
    repository_id: int
    path: str = Field(min_length=1, max_length=1000, examples=["app/main.py"])
    symbol: str | None = Field(
        default=None, max_length=300, description="Function/class name, e.g. 'create_app' or 'Repo.scan'"
    )
    mode: Literal["fast", "thorough"] = Field(
        default="fast",
        description="fast: whole file per call. thorough: one call per top-level function/class (slower, finds more).",
    )


class SymbolOut(BaseModel):
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    signature: str


class OutlineOut(BaseModel):
    path: str
    language: str | None
    supported: bool
    has_syntax_errors: bool
    symbols: list[SymbolOut]
    imports: list[str]


class DependenciesOut(BaseModel):
    path: str
    imports: list[str]
    imported_by: list[str]
    external: list[str]


class DuplicateLocationOut(BaseModel):
    path: str
    start_line: int
    end_line: int


class DuplicateBlockOut(BaseModel):
    line_count: int
    locations: list[DuplicateLocationOut]
    preview: str


class ExplanationOut(BaseModel):
    path: str
    symbol: str | None
    start_line: int
    end_line: int
    model: str
    explanation: str


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    title: str
    category: str
    severity: str | None
    status: str | None
    impact: str | None
    line_start: int
    line_end: int
    explanation: str
    suggestion: str
    benefits: str | None
    risks: str | None
    snippet: str


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    kind: str
    path: str
    symbol: str | None
    model: str
    summary: str
    discarded_findings: int
    duration_ms: float
    created_at: datetime
    findings: list[FindingOut]


class AnalysisRunSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    path: str
    symbol: str | None
    summary: str
    created_at: datetime
