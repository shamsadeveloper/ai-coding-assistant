"""The only code path that writes into a user's repository.

Every write is validated (inside the repository, not a secret, not in .git or a
dependency folder) and the previous content is backed up first.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.repository.errors import FileAccessDeniedError, RepositoryError
from app.services.repository.ignore_rules import DEFAULT_EXCLUDED_DIRS, is_sensitive_file
from app.services.repository.path_guard import resolve_inside


class WriteConflictError(RepositoryError):
    """The file changed on disk since the content was proposed, or the target already exists."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text_or_none(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


@dataclass(frozen=True)
class WriteResult:
    path: str
    created: bool
    backup_path: str | None


class SafeFileWriter:
    def __init__(self, root: Path, backup_dir: Path) -> None:
        self._root = root
        self._backup_dir = backup_dir

    def validate_target(self, relative_path: str) -> Path:
        """Return the absolute target path or raise if writing there is not allowed."""
        full_path = resolve_inside(self._root, relative_path)
        parts = PurePosixPath(full_path.relative_to(self._root).as_posix()).parts
        if any(part.lower() in DEFAULT_EXCLUDED_DIRS for part in parts[:-1]):
            raise FileAccessDeniedError(f"Writing inside '{'/'.join(parts[:-1])}' is not allowed")
        if is_sensitive_file(full_path.name):
            raise FileAccessDeniedError(f"Writing to sensitive file '{full_path.name}' is not allowed")
        if full_path.is_dir():
            raise FileAccessDeniedError(f"'{relative_path}' is a directory")
        return full_path

    def write(
        self, relative_path: str, content: str, expected_sha256: str | None, allow_overwrite: bool = False
    ) -> WriteResult:
        """Write ``content``.

        Modifying a file: ``expected_sha256`` is its hash when the change was proposed; if the
        file changed since then, nothing is written. Creating a file (``expected_sha256`` None):
        an existing file is only replaced when ``allow_overwrite`` is True.
        """
        full_path = self.validate_target(relative_path)
        relative = full_path.relative_to(self._root).as_posix()
        current = read_text_or_none(full_path)

        if expected_sha256 is not None:
            if current is None or sha256_text(current) != expected_sha256:
                raise WriteConflictError(f"'{relative}' changed on disk after the change was proposed")
        elif current is not None and not allow_overwrite:
            raise WriteConflictError(f"'{relative}' already exists; approve with overwrite to replace it")

        backup_path = None
        if current is not None:
            backup = self._backup_dir / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full_path, backup)
            backup_path = str(backup)

        full_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the line endings exactly as given instead of translating them.
        with open(full_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return WriteResult(relative, created=current is None, backup_path=backup_path)

    def restore(self, relative_path: str, backup_path: str | None, expected_sha256: str) -> None:
        """Undo a write: restore the backup, or delete a file that the write created."""
        full_path = self.validate_target(relative_path)
        current = read_text_or_none(full_path)
        if current is None or sha256_text(current) != expected_sha256:
            raise WriteConflictError(f"'{relative_path}' changed after it was applied; restore it manually")
        if backup_path:
            shutil.copy2(backup_path, full_path)
        else:
            full_path.unlink()
