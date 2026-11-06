"""Repository Manager: registers folders, keeps the file index, and serves reads and searches."""

import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Repository, RepositoryFile
from app.services.repository.errors import (
    DuplicateRepositoryError,
    FileAccessDeniedError,
    FileNotIndexedError,
    InvalidRepositoryPathError,
    RepositoryError,
    RepositoryNotFoundError,
)
from app.services.repository.file_reader import FileContent, read_file_lines
from app.services.repository.ignore_rules import is_sensitive_file
from app.services.repository.path_guard import resolve_inside, resolve_repository_root
from app.services.repository.scanner import RepositoryScanner
from app.services.repository.search import CodeSearcher, SearchResult, compile_query


class RepositoryManager:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._scanner = RepositoryScanner(max_file_bytes=settings.max_file_bytes)
        self._searcher = CodeSearcher(max_file_bytes=settings.max_file_bytes)

    def register(self, path: str, name: str | None = None) -> Repository:
        """Validate a local folder, store it, and build its first file index."""
        root = resolve_repository_root(path, self._settings.allowed_repository_roots)
        existing = self._session.scalar(select(Repository).where(Repository.root_path == str(root)))
        if existing:
            raise DuplicateRepositoryError(f"Folder is already registered as repository {existing.id}")

        repository = Repository(name=name or root.name, root_path=str(root))
        self._session.add(repository)
        self._session.flush()  # assigns repository.id
        self._rebuild_index(repository, root)
        self._session.commit()
        return repository

    def list(self) -> Sequence[Repository]:
        return self._session.scalars(select(Repository).order_by(Repository.id)).all()

    def get(self, repository_id: int) -> Repository:
        repository = self._session.get(Repository, repository_id)
        if repository is None:
            raise RepositoryNotFoundError(f"Repository {repository_id} not found")
        return repository

    def remove(self, repository_id: int) -> None:
        """Forget a repository. Files on disk are never touched."""
        self._session.delete(self.get(repository_id))
        self._session.commit()

    def rescan(self, repository_id: int) -> Repository:
        repository = self.get(repository_id)
        root = Path(repository.root_path)
        if not root.is_dir():
            raise InvalidRepositoryPathError(f"Folder no longer exists: {root}")
        self._rebuild_index(repository, root)
        self._session.commit()
        return repository

    def list_files(
        self,
        repository_id: int,
        language: str | None = None,
        path_prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[int, Sequence[RepositoryFile]]:
        self.get(repository_id)
        query = select(RepositoryFile).where(RepositoryFile.repository_id == repository_id)
        if language:
            query = query.where(func.lower(RepositoryFile.language) == language.lower())
        if path_prefix:
            query = query.where(RepositoryFile.path.startswith(path_prefix.replace("\\", "/"), autoescape=True))

        total = self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        files = self._session.scalars(query.order_by(RepositoryFile.path).limit(limit).offset(offset)).all()
        return total, files

    def read_file(
        self, repository_id: int, path: str, start_line: int = 1, end_line: int | None = None
    ) -> FileContent:
        """Read an indexed file. Only files the scanner accepted can be read."""
        repository = self.get(repository_id)
        root = Path(repository.root_path)
        full_path = resolve_inside(root, path)
        if is_sensitive_file(full_path.name):
            raise FileAccessDeniedError(f"Access to sensitive file '{full_path.name}' is blocked")

        relative_path = full_path.relative_to(root).as_posix()
        record = self._session.scalar(
            select(RepositoryFile).where(
                RepositoryFile.repository_id == repository_id, RepositoryFile.path == relative_path
            )
        )
        if record is None or not full_path.is_file():
            raise FileNotIndexedError(f"'{relative_path}' is not in the index. Rescan if it is new.")

        return read_file_lines(
            full_path, relative_path, record.language, self._settings.max_file_bytes, start_line, end_line
        )

    def iter_texts(
        self, repository_id: int, languages: set[str] | None = None
    ) -> Iterator[tuple[RepositoryFile, str]]:
        """Yield (index record, file text) for indexed files, optionally only some languages.

        Files that vanished or became unsafe since the last scan are skipped silently.
        """
        root = Path(self.get(repository_id).root_path)
        query = select(RepositoryFile).where(RepositoryFile.repository_id == repository_id)
        if languages:
            query = query.where(RepositoryFile.language.in_(languages))
        for record in self._session.scalars(query.order_by(RepositoryFile.path)).all():
            try:
                full_path = resolve_inside(root, record.path)
                if full_path.stat().st_size > self._settings.max_file_bytes:
                    continue
                yield record, full_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, RepositoryError):
                continue

    def search(
        self,
        repository_id: int,
        query: str,
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        path_glob: str | None = None,
        max_results: int | None = None,
    ) -> SearchResult:
        pattern = compile_query(query, regex=regex, case_sensitive=case_sensitive)
        return self.search_pattern(repository_id, pattern, path_glob=path_glob, max_results=max_results)

    def search_pattern(
        self,
        repository_id: int,
        pattern: re.Pattern[str],
        *,
        path_glob: str | None = None,
        max_results: int | None = None,
    ) -> SearchResult:
        """Search with a pattern built by trusted code (e.g. agent tools), not raw user input."""
        repository = self.get(repository_id)
        paths = self._session.scalars(
            select(RepositoryFile.path)
            .where(RepositoryFile.repository_id == repository_id)
            .order_by(RepositoryFile.path)
        ).all()
        if path_glob:
            paths = [p for p in paths if fnmatchcase(p, path_glob)]

        limit = min(max_results or self._settings.max_search_results, self._settings.max_search_results)
        return self._searcher.search(Path(repository.root_path), paths, pattern, limit)

    def _rebuild_index(self, repository: Repository, root: Path) -> None:
        result = self._scanner.scan(root)
        self._session.execute(delete(RepositoryFile).where(RepositoryFile.repository_id == repository.id))
        if result.files:
            self._session.execute(
                insert(RepositoryFile),
                [
                    {
                        "repository_id": repository.id,
                        "path": f.path,
                        "language": f.language,
                        "size_bytes": f.size_bytes,
                        "line_count": f.line_count,
                    }
                    for f in result.files
                ],
            )
        repository.last_scanned_at = datetime.now(UTC)
        repository.scan_summary = {
            "file_count": len(result.files),
            "languages": result.language_counts(),
            "frameworks": result.frameworks,
            "skipped": dict(result.skipped),
        }
