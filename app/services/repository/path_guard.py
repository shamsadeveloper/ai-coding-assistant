"""Path validation: every filesystem access in the app goes through these functions."""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.services.repository.errors import InvalidRepositoryPathError, PathOutsideRepositoryError


def resolve_repository_root(raw_path: str, allowed_roots: Sequence[str]) -> Path:
    """Validate a folder the user wants to register and return its resolved path.

    Rejects relative paths, missing folders, drive/filesystem roots, and folders
    outside ``allowed_roots`` (when that list is not empty).
    """
    path = Path(raw_path.strip()).expanduser()
    if not path.is_absolute():
        raise InvalidRepositoryPathError("Repository path must be absolute")

    resolved = path.resolve()
    if not resolved.is_dir():
        raise InvalidRepositoryPathError(f"Folder does not exist: {resolved}")
    if resolved.parent == resolved:
        raise InvalidRepositoryPathError("A drive or filesystem root cannot be registered")
    if allowed_roots and not any(resolved.is_relative_to(Path(r).resolve()) for r in allowed_roots):
        raise InvalidRepositoryPathError("Folder is outside the allowed repository roots")
    return resolved


def resolve_inside(root: Path, relative_path: str) -> Path:
    """Turn a repository-relative path into an absolute path that is guaranteed to be inside ``root``.

    ``root`` must already be resolved. Symlinks are resolved before the check,
    so a link pointing outside the repository is rejected too.
    """
    if not relative_path or "\x00" in relative_path:
        raise PathOutsideRepositoryError("Path is empty or invalid")

    normalized = relative_path.replace("\\", "/")
    windows_view = PureWindowsPath(relative_path)
    posix_view = PurePosixPath(normalized)
    if posix_view.is_absolute() or windows_view.drive or windows_view.is_absolute():
        raise PathOutsideRepositoryError("Path must be relative to the repository")
    # ':' covers drive letters and NTFS alternate data streams such as "file.txt:hidden".
    if ":" in normalized or ".." in posix_view.parts:
        raise PathOutsideRepositoryError("Path must not contain '..' or ':'")

    full = (root / posix_view).resolve()
    if not full.is_relative_to(root):
        raise PathOutsideRepositoryError("Path resolves outside the repository")
    return full
