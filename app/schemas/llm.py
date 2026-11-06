"""Request and response models for the LLM API."""

from pydantic import BaseModel, Field

from app.services.llm_service import Role

MAX_MESSAGE_CHARS = 32_000


class ChatMessageIn(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=50)
    model: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._:/-]+$",
        max_length=100,
        description="Ollama model name; the configured default is used when omitted.",
    )


class ChatResponse(BaseModel):
    model: str
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: float | None


class LLMHealthResponse(BaseModel):
    reachable: bool
    configured_model: str
    model_available: bool
    available_models: list[str]
