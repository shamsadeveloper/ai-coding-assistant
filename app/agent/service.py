"""Agent service: wires repository, conversation history, tools and the engine for one request."""

from dataclasses import dataclass

from app.agent.conversation_store import ConversationStore
from app.agent.engine import AgentEngine, AgentRunResult
from app.agent.prompts import build_agent_system_prompt
from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext
from app.core.config import Settings
from app.services.changes.service import ChangeService
from app.services.code.insight import CodeInsightService
from app.services.testing.service import TestingService
from app.services.llm_service import LLMService
from app.services.repository.manager import RepositoryManager


@dataclass(frozen=True)
class AgentReply:
    conversation_id: int
    result: AgentRunResult


class AgentService:
    def __init__(
        self,
        llm: LLMService,
        repositories: RepositoryManager,
        insight: CodeInsightService,
        testing: TestingService,
        changes: ChangeService,
        conversations: ConversationStore,
        registry: ToolRegistry,
        settings: Settings,
    ) -> None:
        self._repositories = repositories
        self._insight = insight
        self._testing = testing
        self._changes = changes
        self._conversations = conversations
        self._settings = settings
        self._engine = AgentEngine(llm, registry, max_steps=settings.agent_max_steps)

    async def ask(self, repository_id: int, message: str, conversation_id: int | None = None) -> AgentReply:
        repository = self._repositories.get(repository_id)
        conversation = self._conversations.get_or_create(conversation_id, repository.id, message)
        history = self._conversations.history(conversation.id, self._settings.agent_history_messages)

        result = await self._engine.run(
            system_prompt=build_agent_system_prompt(repository),
            history=history,
            user_message=message,
            context=ToolContext(repository.id, self._repositories, self._insight, self._testing, self._changes),
        )
        self._conversations.save_turn(conversation, message, result)
        return AgentReply(conversation_id=conversation.id, result=result)
