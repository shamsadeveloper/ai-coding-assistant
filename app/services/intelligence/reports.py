"""Schemas the LLM must fill in. Ollama constrains generation to these JSON schemas."""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["Critical", "High", "Medium", "Low"]
SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


class BugFinding(BaseModel):
    title: str = Field(max_length=200)
    severity: Severity
    category: Literal[
        "logic", "null_handling", "security", "performance", "error_handling", "concurrency", "bad_practice"
    ]
    status: Literal["confirmed", "possible"] = Field(
        description="confirmed: the code shown proves the bug. possible: depends on context not shown."
    )
    line_start: int
    line_end: int
    explanation: str = Field(description="Why this is a problem and when it happens")
    suggestion: str = Field(description="How to fix it")


class BugReport(BaseModel):
    findings: list[BugFinding]
    summary: str = Field(description="One or two sentences about the overall quality of this code")


class Improvement(BaseModel):
    title: str = Field(max_length=200)
    category: Literal[
        "clean_code", "duplication", "design_pattern", "performance", "solid", "readability",
        "error_handling", "testability",
    ]
    impact: Literal["High", "Medium", "Low"]
    line_start: int
    line_end: int
    recommendation: str = Field(description="What to change, concretely")
    benefits: str
    risks: str = Field(description="What could go wrong or what it costs to make this change")


class ImprovementReport(BaseModel):
    suggestions: list[Improvement]
    summary: str
