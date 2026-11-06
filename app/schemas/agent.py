"""Request and response models for the agent API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentChatRequest(BaseModel):
    repository_id: int
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = Field(default=None, description="Continue an existing conversation")


class AgentStepOut(BaseModel):
    iteration: int
    tool_name: str
    arguments: dict[str, Any]
    is_error: bool
    output: str
    output_chars: int
    duration_ms: float


class AgentChatResponse(BaseModel):
    conversation_id: int
    answer: str
    stopped_reason: str
    model: str
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    steps: list[AgentStepOut]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    created_at: datetime


class ToolCallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tool_name: str
    arguments: dict[str, Any]
    is_error: bool
    output_chars: int
    duration_ms: float
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    title: str
    created_at: datetime
    messages: list[MessageOut]
    tool_calls: list[ToolCallLogOut]
