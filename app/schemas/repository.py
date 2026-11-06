"""Request and response models for the repository API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterRepositoryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000, examples=["D:/llm-learning"])
    name: str | None = Field(default=None, max_length=200)


class ScanSummary(BaseModel):
    file_count: int
    languages: dict[str, int]
    frameworks: list[str]
    skipped: dict[str, int]


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    root_path: str
    created_at: datetime
    last_scanned_at: datetime | None
    scan_summary: ScanSummary | None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    language: str | None
    size_bytes: int
    line_count: int


class FileListOut(BaseModel):
    total: int
    items: list[FileOut]


class FileContentOut(BaseModel):
    path: str
    language: str | None
    start_line: int
    end_line: int
    total_lines: int
    content: str


class SearchMatchOut(BaseModel):
    path: str
    line_number: int
    line: str


class SearchOut(BaseModel):
    query: str
    files_searched: int
    truncated: bool
    matches: list[SearchMatchOut]
