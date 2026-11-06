"""Domain errors for repository access. Mapped to HTTP status codes in app.main."""


class RepositoryError(Exception):
    """Base error for repository operations."""


class InvalidRepositoryPathError(RepositoryError):
    """The folder cannot be registered or no longer exists."""


class DuplicateRepositoryError(RepositoryError):
    """The folder is already registered."""


class RepositoryNotFoundError(RepositoryError):
    """No repository with the given id."""


class PathOutsideRepositoryError(RepositoryError):
    """A requested path is absolute, contains '..', or resolves outside the repository."""


class FileAccessDeniedError(RepositoryError):
    """The file is classified as sensitive (secrets, keys, credentials)."""


class FileNotIndexedError(RepositoryError):
    """The file is not in the repository index (ignored, binary, missing, or index is stale)."""


class FileTooLargeError(RepositoryError):
    """The file exceeds the configured size limit."""


class InvalidLineRangeError(RepositoryError):
    """The requested line range is not valid for the file."""


class InvalidSearchQueryError(RepositoryError):
    """The search pattern is empty, too long, or not a valid regular expression."""
