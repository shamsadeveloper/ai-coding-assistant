"""Domain errors for the agent. Mapped to HTTP status codes in app.main."""


class AgentError(Exception):
    """Base error for agent operations."""


class ConversationNotFoundError(AgentError):
    """No conversation with the given id."""


class ConversationRepositoryMismatchError(AgentError):
    """The conversation belongs to a different repository."""
