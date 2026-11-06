"""Code-change routes: propose, review diffs, approve, reject, roll back; plus Git status and diff."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from app.api.dependencies import get_change_service, get_repository_manager
from app.schemas.changes import ApproveChangeRequest, ChangeSetOut, GitStatusOut, ProposeChangeRequest
from app.services.changes.git import GitService
from app.services.changes.service import ChangeService
from app.services.repository.manager import RepositoryManager
from app.services.repository.path_guard import resolve_inside

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])
git_router = APIRouter(prefix="/api/v1/git", tags=["git"])

ServiceDep = Annotated[ChangeService, Depends(get_change_service)]
ManagerDep = Annotated[RepositoryManager, Depends(get_repository_manager)]


@router.post("", response_model=ChangeSetOut)
async def propose(request: ProposeChangeRequest, service: ServiceDep) -> ChangeSetOut:
    """Generate a patch for the request. Files on disk are NOT changed."""
    return ChangeSetOut.model_validate(await service.propose(request.repository_id, request.request, request.paths))


@router.get("", response_model=list[ChangeSetOut])
def list_changes(repository_id: int, service: ServiceDep) -> list[ChangeSetOut]:
    return [ChangeSetOut.model_validate(c) for c in service.list(repository_id)]


@router.get("/{change_id}", response_model=ChangeSetOut)
def get_change(change_id: int, service: ServiceDep) -> ChangeSetOut:
    return ChangeSetOut.model_validate(service.get(change_id))


@router.post("/{change_id}/approve", response_model=ChangeSetOut)
def approve(change_id: int, request: ApproveChangeRequest, service: ServiceDep) -> ChangeSetOut:
    """Developer approval: apply every file of the patch (backed up, on a new Git branch when possible)."""
    return ChangeSetOut.model_validate(service.approve(change_id, request.create_branch))


@router.post("/{change_id}/reject", response_model=ChangeSetOut)
def reject(change_id: int, service: ServiceDep) -> ChangeSetOut:
    return ChangeSetOut.model_validate(service.reject(change_id))


@router.post("/{change_id}/rollback", response_model=ChangeSetOut)
def rollback(change_id: int, service: ServiceDep) -> ChangeSetOut:
    """Restore the files from the backups taken when the change was applied."""
    return ChangeSetOut.model_validate(service.rollback(change_id))


@git_router.get("/status", response_model=GitStatusOut)
def git_status(repository_id: int, manager: ManagerDep) -> GitStatusOut:
    git = GitService(Path(manager.get(repository_id).root_path))
    if not git.is_repository():
        return GitStatusOut(is_repository=False, branch=None, changes=[])
    return GitStatusOut(is_repository=True, branch=git.current_branch(), changes=git.status())


@git_router.get("/diff", response_class=PlainTextResponse)
def git_diff(
    repository_id: int, manager: ManagerDep, path: Annotated[str | None, Query(max_length=1000)] = None
) -> str:
    """Uncommitted changes as a unified diff, optionally for one path."""
    root = Path(manager.get(repository_id).root_path)
    paths = [resolve_inside(root, path).relative_to(root).as_posix()] if path else []
    return GitService(root).diff(paths)
