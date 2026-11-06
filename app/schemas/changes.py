"""Request and response models for the code-change API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProposeChangeRequest(BaseModel):
    repository_id: int
    request: str = Field(min_length=5, max_length=4000, examples=["Return 0 instead of failing when the order list is empty"])
    paths: list[str] | None = Field(
        default=None, max_length=10, description="Files to change; chosen automatically when omitted"
    )


class ApproveChangeRequest(BaseModel):
    create_branch: bool = Field(default=True, description="In a Git repository, apply on a new branch")


class FileChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    is_new: bool
    diff: str
    syntax_ok: bool
    notes: str
    backup_path: str | None


class ChangeSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    request: str
    status: str
    summary: str
    model: str
    base_branch: str | None
    branch_name: str | None
    created_at: datetime
    decided_at: datetime | None
    files: list[FileChangeOut]


class GitStatusOut(BaseModel):
    is_repository: bool
    branch: str | None
    changes: list[str]
