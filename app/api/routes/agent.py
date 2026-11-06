from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.agent.conversation_store import ConversationStore
from app.agent.registry import ToolRegistry
from app.agent.service import AgentService
from app.api.dependencies import get_agent_service, get_conversation_store, get_tool_registry
from app.schemas.agent import AgentChatRequest, AgentChatResponse, AgentStepOut, ConversationOut

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    request: AgentChatRequest, service: Annotated[AgentService, Depends(get_agent_service)]
) -> AgentChatResponse:
    """Ask the agent about a repository. It calls tools as needed and returns the answer plus every step."""
    reply = await service.ask(request.repository_id, request.message, request.conversation_id)
    result = reply.result
    return AgentChatResponse(
        conversation_id=reply.conversation_id,
        answer=result.answer,
        stopped_reason=result.stopped_reason,
        model=result.model,
        llm_calls=result.llm_calls,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        steps=[AgentStepOut(**asdict(step)) for step in result.steps],
    )


@router.get("/tools")
def list_tools(registry: Annotated[ToolRegistry, Depends(get_tool_registry)]) -> list[dict[str, Any]]:
    """The exact tool definitions sent to the model."""
    return registry.schemas()


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: int, store: Annotated[ConversationStore, Depends(get_conversation_store)]
) -> ConversationOut:
    return ConversationOut.model_validate(store.get(conversation_id))
