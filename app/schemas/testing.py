"""Request and response models for the testing API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TestFrameworkOut(BaseModel):
    __test__ = False

    name: str
    language: str
    evidence: str


class CoverageGapOut(BaseModel):
    path: str
    language: str | None
    test_files: list[str]
    untested_symbols: list[str]
    tested_symbols: list[str]


class GenerateTestRequest(BaseModel):
    repository_id: int
    path: str = Field(min_length=1, max_length=1000)
    symbol: str | None = Field(default=None, max_length=300)
    framework: str | None = Field(default=None, max_length=50, description="Defaults to the detected framework")


class GeneratedTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    source_path: str
    symbol: str | None
    framework: str
    target_path: str
    target_exists: bool
    syntax_ok: bool
    status: str
    test_cases: list[str]
    notes: str
    content: str
    review_path: str
    backup_path: str | None
    model: str
    created_at: datetime
    decided_at: datetime | None


class ApproveTestRequest(BaseModel):
    overwrite: bool = Field(default=False, description="Required to replace an existing file at target_path")


class RunTestsRequest(BaseModel):
    repository_id: int
    framework: str | None = Field(default=None, max_length=50)
    path: str | None = Field(default=None, max_length=1000, description="Run only this test file")


class TestRunOut(BaseModel):
    __test__ = False
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    framework: str
    command: list[str]
    target_path: str | None
    exit_code: int | None
    timed_out: bool
    passed: int | None
    failed: int | None
    errors: int | None
    skipped: int | None
    duration_ms: float
    output: str
    analysis: str | None
    created_at: datetime
