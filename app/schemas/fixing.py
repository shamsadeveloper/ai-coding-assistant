"""Request and response models for fix sessions."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StartFixSessionRequest(BaseModel):
    repository_id: int
    finding_id: int | None = Field(default=None, description="A bug finding from /analysis/bugs to fix")
    description: str | None = Field(
        default=None, min_length=5, max_length=4000,
        description="The bug in your words; required when no finding_id is given",
    )
    path: str | None = Field(default=None, max_length=1000, description="File where the bug shows; required without finding_id")
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    run_tests: bool = Field(default=True, description="Validate each attempt with the repository's tests in a sandbox")
    reproduction_test: bool = Field(
        default=False, description="First write a test that reproduces the bug (pytest only; sandbox only; slower)"
    )


class ApproveFixRequest(BaseModel):
    create_branch: bool = Field(default=True, description="In a Git repository, apply on a new branch")


class FixFileOut(BaseModel):
    path: str
    diff: str
    syntax_ok: bool
    notes: str


class FixAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    status: str
    explanation: str
    files: list[FixFileOut]
    checks: list[dict[str, Any]]
    test: dict[str, Any] | None
    comparison: dict[str, Any] | None
    failure_analysis: str | None
    duration_ms: float
    created_at: datetime


class FixSessionSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    finding_id: int | None
    description: str
    path: str
    status: str
    validation_status: str | None
    change_set_id: int | None
    llm_calls: int
    created_at: datetime
    finished_at: datetime | None


class FixSessionOut(FixSessionSummaryOut):
    line_start: int | None
    line_end: int | None
    options: dict[str, Any]
    validation_note: str
    context: dict[str, Any] | None
    root_cause: dict[str, Any] | None
    baseline: dict[str, Any] | None
    reproduction: dict[str, Any] | None
    review: dict[str, Any] | None
    final_attempt: int | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    attempts: list[FixAttemptOut]


class FixEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage: str
    message: str
    data: dict[str, Any] | None
    duration_ms: float | None
    llm_calls: int
    created_at: datetime
