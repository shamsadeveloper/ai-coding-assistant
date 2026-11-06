"""Errors raised by fix sessions. All are RepositoryError subclasses so the API maps them to HTTP codes."""

from app.services.repository.errors import RepositoryError


class FixSessionNotFoundError(RepositoryError):
    """No fix session with this id."""


class FixSessionStateError(RepositoryError):
    """The fix session is not in a state that allows this action."""


class FixSessionBusyError(RepositoryError):
    """The maximum number of concurrently running fix sessions is reached."""


class SandboxError(RepositoryError):
    """The sandbox copy could not be created (too large, unreadable, ...)."""


class SessionLimitError(RepositoryError):
    """A backend-enforced limit (LLM calls) was reached; the session stops."""


class SessionStoppedError(RepositoryError):
    """The developer stopped the session."""
