"""Deterministic code insight for one repository: outlines, dependencies, duplicates.

Used by the API, the agent tools, and as context for the LLM-based analysis.
"""

from app.services.code.cache import cached
from app.services.code.dependencies import FileDependencies, build_dependency_graph, resolve_imports
from app.services.code.duplicates import DuplicateBlock, find_duplicates
from app.services.code.syntax import GRAMMARS, FileOutline, Symbol, parse_outline
from app.services.repository.errors import RepositoryError
from app.services.repository.manager import RepositoryManager

DEPENDENCY_LANGUAGES = {"Python", "JavaScript", "TypeScript"}


class SymbolNotFoundError(RepositoryError):
    """The requested function or class does not exist in the file."""


class CodeInsightService:
    def __init__(self, repositories: RepositoryManager) -> None:
        self._repositories = repositories

    def read_source(self, repository_id: int, path: str) -> tuple[str, str | None, str]:
        """(normalised path, language, full text) of an indexed file."""
        file = self._repositories.read_file(repository_id, path)
        return file.path, file.language, file.content

    def outline(self, repository_id: int, path: str) -> FileOutline:
        relative_path, language, text = self.read_source(repository_id, path)
        return parse_outline(relative_path, language, text)

    def find_symbol(self, repository_id: int, path: str, name: str) -> tuple[FileOutline, Symbol]:
        outline = self.outline(repository_id, path)
        symbol = outline.find(name)
        if symbol is None:
            available = ", ".join(s.qualified_name for s in outline.symbols[:30]) or "none found"
            raise SymbolNotFoundError(f"'{name}' not found in {outline.path}. Symbols: {available}")
        return outline, symbol

    def dependencies(self, repository_id: int, path: str) -> FileDependencies:
        """What a file imports from the repository, and which files import it."""
        target = self.outline(repository_id, path)
        if target.language not in DEPENDENCY_LANGUAGES:
            return resolve_imports(target, set())
        graph = self.dependency_graph(repository_id)
        return graph.get(target.path) or resolve_imports(target, set(graph))

    def dependency_graph(self, repository_id: int) -> dict[str, FileDependencies]:
        def compute() -> dict[str, FileDependencies]:
            files = list(self._repositories.iter_texts(repository_id, DEPENDENCY_LANGUAGES))
            known = {record.path for record, _ in files}
            outlines = [parse_outline(record.path, record.language, text) for record, text in files]
            return build_dependency_graph(outlines, known)

        return cached(self._cache_key(repository_id, "dependencies"), compute)

    def duplicates(self, repository_id: int, min_lines: int = 6) -> list[DuplicateBlock]:
        def compute() -> list[DuplicateBlock]:
            code_languages = set(GRAMMARS) | {"C", "C++", "Kotlin", "Swift", "Dart", "Scala"}
            files = ((r.path, text) for r, text in self._repositories.iter_texts(repository_id, code_languages))
            return find_duplicates(files, min_lines=min_lines)

        return cached(self._cache_key(repository_id, "duplicates", min_lines), compute)

    def _cache_key(self, repository_id: int, kind: str, *extra: object) -> tuple:
        repository = self._repositories.get(repository_id)
        scanned = repository.last_scanned_at.isoformat() if repository.last_scanned_at else ""
        return (kind, repository.root_path, scanned, *extra)
