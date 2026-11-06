"""Code modification: propose patches, show diffs, and apply them only after developer approval."""

import logging
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ChangeSet, FileChange
from app.services.changes.edits import unified_diff
from app.services.changes.git import GitError, GitService
from app.services.changes.patching import (  # noqa: F401  (re-exported: existing imports keep working)
    EDIT_SYSTEM,
    NEW_FILE_SYSTEM,
    WHOLE_FILE_MIN_SIMILARITY,
    generate_file_patch,
    whole_file_fallback,
)
from app.services.code.validation import syntax_error
from app.services.llm_service import LLMService
from app.services.repository.errors import RepositoryError
from app.services.repository.manager import RepositoryManager
from app.services.repository.writer import SafeFileWriter, read_text_or_none, sha256_text

logger = logging.getLogger(__name__)

SELECT_SYSTEM = (
    "You pick which existing files must be edited to implement a developer's request. "
    "Choose only from the candidate list, at most {max_files} files, most important first."
)

_STOP_WORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "should", "when", "add", "make", "change"}


class FileSelection(BaseModel):
    paths: list[str] = Field(description="Files to edit, chosen from the candidates")
    reason: str


class ChangeNotFoundError(RepositoryError):
    """No change set with this id."""


class ChangeStateError(RepositoryError):
    """The change set is not in a state that allows this action."""


class ChangeService:
    def __init__(
        self, llm: LLMService, repositories: RepositoryManager, session: Session, settings: Settings
    ) -> None:
        self._llm = llm
        self._repositories = repositories
        self._session = session
        self._settings = settings

    # ----- proposal ----------------------------------------------------------------------------

    async def propose(self, repository_id: int, request: str, paths: Sequence[str] | None = None) -> ChangeSet:
        """Generate patches for ``request``. Nothing on disk changes until ``approve``."""
        repository = self._repositories.get(repository_id)
        root = Path(repository.root_path)
        writer = SafeFileWriter(root, self._backup_dir(0))
        targets = list(paths) if paths else await self._select_files(repository_id, request)
        if not targets:
            raise ChangeStateError("Could not identify which files to change; pass 'paths' explicitly")

        change = ChangeSet(repository_id=repository_id, request=request, status="proposed", summary="", model="")
        explanations = []
        for raw_path in targets[: self._settings.change_max_files]:
            full_path = writer.validate_target(raw_path)
            relative = full_path.relative_to(root).as_posix()
            original = read_text_or_none(full_path)
            if original is not None:
                # Existing files must be indexed: the same rule as reading (no secrets, no ignored files).
                self._repositories.read_file(repository_id, relative, 1, 1)
                if original.count("\n") > self._settings.change_max_file_lines:
                    raise ChangeStateError(
                        f"{relative} is longer than {self._settings.change_max_file_lines} lines; "
                        "split the request or raise APP_CHANGE_MAX_FILE_LINES"
                    )
            file_change, explanation, model = await self._patch_file(request, relative, original, targets)
            change.model = model or change.model
            change.files.append(file_change)
            if explanation:
                explanations.append(f"{relative}: {explanation}")

        if not any(f.diff for f in change.files):
            change.status = "failed"
        change.summary = "\n".join(explanations) or "No explanation given."
        self._session.add(change)
        self._session.commit()
        logger.info("Change %s proposed (%s) for %s", change.id, change.status, [f.path for f in change.files])
        return change

    async def _select_files(self, repository_id: int, request: str) -> list[str]:
        """Rank indexed files by keyword hits, then let the model choose among the top candidates."""
        words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", request)} - _STOP_WORDS
        hits: Counter[str] = Counter()
        for word in list(words)[:8]:
            for match in self._repositories.search(repository_id, word, max_results=50).matches:
                hits[match.path] += 1
        candidates = [path for path, _ in hits.most_common(15)]
        if not candidates:
            _, files = self._repositories.list_files(repository_id, limit=40)
            candidates = [f.path for f in files]
        if not candidates:
            return []

        listing = "\n".join(f"- {p} ({hits[p]} keyword matches)" for p in candidates)
        selection, _ = await self._llm.generate_structured(
            SELECT_SYSTEM.format(max_files=self._settings.change_max_files),
            f"Request: {request}\n\nCandidate files:\n{listing}",
            FileSelection,
        )
        allowed = set(candidates)
        return [p for p in selection.paths if p in allowed][: self._settings.change_max_files]

    async def _patch_file(
        self, request: str, path: str, original: str | None, all_targets: Sequence[str]
    ) -> tuple[FileChange, str, str]:
        patch = await generate_file_patch(self._llm, request, path, original, [t for t in all_targets if t != path])
        return self._file_change(path, original, patch.new_content, patch.notes), patch.explanation, patch.model

    @staticmethod
    def _file_change(path: str, original: str | None, new_content: str, notes: list[str]) -> FileChange:
        diff = unified_diff(path, original, new_content) if new_content != original else ""
        error = syntax_error(path, new_content) if diff else None
        if error:
            notes.append(f"Warning: {error}")
        return FileChange(
            path=path, is_new=original is None,
            original_sha256=sha256_text(original) if original is not None else None,
            new_sha256=sha256_text(new_content), new_content=new_content, diff=diff,
            syntax_ok=error is None, notes="\n".join(notes),
        )

    def create_reviewed(
        self, repository_id: int, request: str, summary: str, model: str, files: Sequence[FileChange]
    ) -> ChangeSet:
        """Record a patch that was produced and validated elsewhere (a fix session) as a proposal,
        so it is applied, backed up, branched and rolled back exactly like any other change."""
        change = ChangeSet(
            repository_id=repository_id, request=request, status="proposed", summary=summary, model=model,
            files=list(files),
        )
        self._session.add(change)
        self._session.commit()
        return change

    # ----- review and decisions ----------------------------------------------------------------

    def get(self, change_id: int) -> ChangeSet:
        change = self._session.get(ChangeSet, change_id)
        if change is None:
            raise ChangeNotFoundError(f"Change {change_id} not found")
        return change

    def list(self, repository_id: int) -> Sequence[ChangeSet]:
        return self._session.scalars(
            select(ChangeSet).where(ChangeSet.repository_id == repository_id).order_by(ChangeSet.id.desc())
        ).all()

    def reject(self, change_id: int) -> ChangeSet:
        change = self._require_status(change_id, {"proposed", "failed"})
        change.status, change.decided_at = "rejected", datetime.now(UTC)
        self._session.commit()
        return change

    def approve(self, change_id: int, create_branch: bool = True) -> ChangeSet:
        """Apply all patches of a change set, or none of them.

        Checks that no file changed since the proposal, optionally creates a Git branch,
        backs up every file it replaces, and undoes earlier writes if a later one fails.
        """
        change = self._require_status(change_id, {"proposed"})
        files = [f for f in change.files if f.diff]
        root = Path(self._repositories.get(change.repository_id).root_path)
        writer = SafeFileWriter(root, self._backup_dir(change.id))
        for file in files:
            current = read_text_or_none(writer.validate_target(file.path))
            current_sha = sha256_text(current) if current is not None else None
            if current_sha != file.original_sha256:
                raise ChangeStateError(f"{file.path} changed after the patch was proposed; create a new proposal")

        git = GitService(root)
        if create_branch and git.is_repository():
            dirty = git.status([f.path for f in files])
            if dirty:
                raise GitError("Uncommitted changes in files this patch touches; commit or stash them first: "
                               + "; ".join(dirty))
            change.base_branch = git.current_branch()
            change.branch_name = f"ai-assistant/change-{change.id}"
            git.create_branch(change.branch_name)

        written: list[FileChange] = []
        try:
            for file in files:
                result = writer.write(file.path, file.new_content, file.original_sha256)
                file.backup_path = result.backup_path
                written.append(file)
        except Exception:
            for file in reversed(written):
                writer.restore(file.path, file.backup_path, file.new_sha256)
            if change.branch_name and change.base_branch:
                git.switch(change.base_branch)
                git.delete_branch(change.branch_name)
            self._session.rollback()
            raise

        change.status, change.decided_at = "applied", datetime.now(UTC)
        self._session.commit()
        self._repositories.rescan(change.repository_id)
        logger.info("Change %s applied to %s (branch %s)", change.id, [f.path for f in files], change.branch_name)
        return change

    def rollback(self, change_id: int) -> ChangeSet:
        """Restore every file to its state before ``approve``."""
        change = self._require_status(change_id, {"applied"})
        root = Path(self._repositories.get(change.repository_id).root_path)
        writer = SafeFileWriter(root, self._backup_dir(change.id))
        for file in reversed([f for f in change.files if f.diff]):
            writer.restore(file.path, file.backup_path, file.new_sha256)

        git = GitService(root)
        if change.branch_name and change.base_branch and git.is_repository():
            if git.current_branch() == change.branch_name:
                git.switch(change.base_branch)
        change.status, change.decided_at = "rolled_back", datetime.now(UTC)
        self._session.commit()
        self._repositories.rescan(change.repository_id)
        logger.info("Change %s rolled back", change.id)
        return change

    def _require_status(self, change_id: int, allowed: set[str]) -> ChangeSet:
        change = self.get(change_id)
        if change.status not in allowed:
            raise ChangeStateError(f"Change {change_id} is {change.status}")
        return change

    def _backup_dir(self, change_id: int) -> Path:
        return self._settings.data_path / "backups" / "changes" / str(change_id)
