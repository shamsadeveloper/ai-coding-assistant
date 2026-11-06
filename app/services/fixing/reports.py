"""JSON schemas the model fills in during a fix session. Kept small: constrained decoding of
long schemas is slow and error-prone on 7B models. No length limits here (a validation error
would end the session); long values are trimmed after parsing instead."""

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceClaim(BaseModel):
    path: str = Field(description="File path relative to the repository root, as shown in the context")
    line: int = Field(description="Line number of the quoted line")
    quote: str = Field(description="ONE line of code copied exactly from the file")
    explanation: str = Field(description="Why this line shows the bug")


class RootCauseReport(BaseModel):
    is_real_bug: bool = Field(description="false if the code is actually correct")
    summary: str = Field(description="The root cause in one or two sentences")
    root_cause_path: str
    root_cause_line: int
    evidence: list[EvidenceClaim]
    affected_files: list[str] = Field(description="Files that must change to fix the bug, most important first")
    fix_strategy: str = Field(description="The smallest change that fixes the root cause")
    confidence: Literal["high", "medium", "low"]


class ReviewIssue(BaseModel):
    severity: Literal["High", "Medium", "Low"]
    description: str


class ReviewReport(BaseModel):
    verdict: Literal["approve", "concerns"]
    summary: str
    issues: list[ReviewIssue]
