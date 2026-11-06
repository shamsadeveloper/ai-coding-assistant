from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_llm_service
from app.schemas.llm import ChatRequest, ChatResponse, LLMHealthResponse
from app.services.llm_service import ChatMessage, LLMService

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])

LLMServiceDep = Annotated[LLMService, Depends(get_llm_service)]


@router.get("/health", response_model=LLMHealthResponse)
async def llm_health(service: LLMServiceDep) -> LLMHealthResponse:
    """Report whether Ollama is reachable and the configured model is pulled."""
    return LLMHealthResponse(**asdict(await service.health()))


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, service: LLMServiceDep) -> ChatResponse:
    """Send a conversation to the local model and return its reply."""
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    result = await service.chat(messages, model=request.model)
    return ChatResponse(**asdict(result))
