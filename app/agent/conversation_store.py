"""Persistence for conversations: history for the agent's context, and the tool-call audit log."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import AgentRunResult
from app.agent.errors import ConversationNotFoundError, ConversationRepositoryMismatchError
from app.models import Conversation, ConversationMessage, ToolCallLog
from app.services.llm_service import AgentMessage

TITLE_CHARS = 80


class ConversationStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, conversation_id: int) -> Conversation:
        conversation = self._session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        return conversation

    def get_or_create(self, conversation_id: int | None, repository_id: int, first_message: str) -> Conversation:
        if conversation_id is None:
            conversation = Conversation(repository_id=repository_id, title=first_message[:TITLE_CHARS])
            self._session.add(conversation)
            self._session.flush()
            return conversation

        conversation = self.get(conversation_id)
        if conversation.repository_id != repository_id:
            raise ConversationRepositoryMismatchError(
                f"Conversation {conversation_id} belongs to repository {conversation.repository_id}"
            )
        return conversation

    def history(self, conversation_id: int, limit: int) -> list[AgentMessage]:
        """The last ``limit`` user/assistant messages, oldest first.

        Tool calls and tool outputs from earlier turns are deliberately left out:
        they are large, and the final answers already summarise them.
        """
        if limit == 0:
            return []
        rows = self._session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.id.desc())
            .limit(limit)
        ).all()
        return [AgentMessage(row.role, row.content) for row in reversed(rows)]

    def save_turn(self, conversation: Conversation, user_message: str, result: AgentRunResult) -> None:
        self._session.add_all([
            ConversationMessage(conversation_id=conversation.id, role="user", content=user_message),
            ConversationMessage(conversation_id=conversation.id, role="assistant", content=result.answer),
            *(
                ToolCallLog(
                    conversation_id=conversation.id,
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                    is_error=step.is_error,
                    output_chars=step.output_chars,
                    duration_ms=step.duration_ms,
                )
                for step in result.steps
            ),
        ])
        conversation.title = conversation.title or user_message[:TITLE_CHARS]
        self._session.commit()
