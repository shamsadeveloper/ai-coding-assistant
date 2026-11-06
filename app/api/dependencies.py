"""FastAPI dependency providers."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation_store import ConversationStore
from app.agent.registry import ToolRegistry
from app.agent.service import AgentService
from app.agent.tools import default_tools
from app.core.config import Settings, get_settings
from app.core.database import session_scope
from app.services.changes.service import ChangeService
from app.services.code.insight import CodeInsightService
from app.services.fixing.runner import FixSessionRunner
from app.services.fixing.service import FixSessionService
from app.services.intelligence.service import CodeIntelligenceService
from app.services.llm_service import LLMService, OllamaLLMService
from app.services.repository.manager import RepositoryManager
from app.services.testing.service import TestingService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_llm_service(request: Request, settings: SettingsDep) -> LLMService:
    """Build the LLM service around the Ollama client created at startup."""
    return OllamaLLMService(
        client=request.app.state.ollama_client,
        default_model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        keep_alive=settings.ollama_keep_alive,
        num_ctx=settings.ollama_num_ctx,
        max_tokens=settings.ollama_max_tokens,
    )


def get_db(request: Request) -> Iterator[Session]:
    """One database session per request, closed when the request ends."""
    yield from session_scope(request.app.state.session_factory)


SessionDep = Annotated[Session, Depends(get_db)]


def get_repository_manager(session: SessionDep, settings: SettingsDep) -> RepositoryManager:
    return RepositoryManager(session, settings)


def get_code_insight(
    repositories: Annotated[RepositoryManager, Depends(get_repository_manager)],
) -> CodeInsightService:
    return CodeInsightService(repositories)


def get_intelligence_service(
    llm: Annotated[LLMService, Depends(get_llm_service)],
    insight: Annotated[CodeInsightService, Depends(get_code_insight)],
    session: SessionDep,
    settings: SettingsDep,
) -> CodeIntelligenceService:
    return CodeIntelligenceService(llm, insight, session, max_lines=settings.analysis_max_lines)


def get_testing_service(
    llm: Annotated[LLMService, Depends(get_llm_service)],
    repositories: Annotated[RepositoryManager, Depends(get_repository_manager)],
    insight: Annotated[CodeInsightService, Depends(get_code_insight)],
    session: SessionDep,
    settings: SettingsDep,
) -> TestingService:
    return TestingService(llm, repositories, insight, session, settings)


def get_change_service(
    llm: Annotated[LLMService, Depends(get_llm_service)],
    repositories: Annotated[RepositoryManager, Depends(get_repository_manager)],
    session: SessionDep,
    settings: SettingsDep,
) -> ChangeService:
    return ChangeService(llm, repositories, session, settings)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """For background work that outlives the request (fix sessions open their own DB session)."""
    return request.app.state.session_factory


def get_fix_runner(request: Request) -> FixSessionRunner:
    return request.app.state.fix_runner


def build_fix_service(llm: LLMService, session: Session, settings: Settings) -> FixSessionService:
    repositories = RepositoryManager(session, settings)
    insight = CodeInsightService(repositories)
    return FixSessionService(
        llm, repositories, insight, TestingService(llm, repositories, insight, session, settings),
        ChangeService(llm, repositories, session, settings), session, settings,
    )


def get_fix_service(
    llm: Annotated[LLMService, Depends(get_llm_service)], session: SessionDep, settings: SettingsDep
) -> FixSessionService:
    return build_fix_service(llm, session, settings)


def get_conversation_store(session: SessionDep) -> ConversationStore:
    return ConversationStore(session)


def get_tool_registry(settings: SettingsDep) -> ToolRegistry:
    return ToolRegistry(default_tools(), max_output_chars=settings.agent_tool_output_chars)


def get_agent_service(
    llm: Annotated[LLMService, Depends(get_llm_service)],
    repositories: Annotated[RepositoryManager, Depends(get_repository_manager)],
    insight: Annotated[CodeInsightService, Depends(get_code_insight)],
    testing: Annotated[TestingService, Depends(get_testing_service)],
    changes: Annotated[ChangeService, Depends(get_change_service)],
    conversations: Annotated[ConversationStore, Depends(get_conversation_store)],
    registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
    settings: SettingsDep,
) -> AgentService:
    return AgentService(llm, repositories, insight, testing, changes, conversations, registry, settings)
