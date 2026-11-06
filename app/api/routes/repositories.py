"""Repository routes.

These are plain ``def`` routes: file and SQLite I/O is blocking, and FastAPI runs
sync routes in a thread pool so they do not block the event loop.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_repository_manager
from app.schemas.repository import (
    FileContentOut,
    FileListOut,
    FileOut,
    RegisterRepositoryRequest,
    RepositoryOut,
    SearchOut,
)
from app.services.repository.manager import RepositoryManager

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])

ManagerDep = Annotated[RepositoryManager, Depends(get_repository_manager)]


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
def register_repository(request: RegisterRepositoryRequest, manager: ManagerDep) -> RepositoryOut:
    """Register a local folder (absolute path) and build its file index."""
    return RepositoryOut.model_validate(manager.register(request.path, request.name))


@router.get("", response_model=list[RepositoryOut])
def list_repositories(manager: ManagerDep) -> list[RepositoryOut]:
    return [RepositoryOut.model_validate(r) for r in manager.list()]


@router.get("/{repository_id}", response_model=RepositoryOut)
def get_repository(repository_id: int, manager: ManagerDep) -> RepositoryOut:
    return RepositoryOut.model_validate(manager.get(repository_id))


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_repository(repository_id: int, manager: ManagerDep) -> None:
    """Remove the repository from the index. Files on disk are not touched."""
    manager.remove(repository_id)


@router.post("/{repository_id}/scan", response_model=RepositoryOut)
def rescan_repository(repository_id: int, manager: ManagerDep) -> RepositoryOut:
    """Rebuild the file index after files were added, removed or changed."""
    return RepositoryOut.model_validate(manager.rescan(repository_id))


@router.get("/{repository_id}/files", response_model=FileListOut)
def list_files(
    repository_id: int,
    manager: ManagerDep,
    language: str | None = None,
    path_prefix: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FileListOut:
    total, files = manager.list_files(repository_id, language, path_prefix, limit, offset)
    return FileListOut(total=total, items=[FileOut.model_validate(f) for f in files])


@router.get("/{repository_id}/file", response_model=FileContentOut)
def read_file(
    repository_id: int,
    manager: ManagerDep,
    path: Annotated[str, Query(min_length=1, max_length=1000)],
    start_line: Annotated[int, Query(ge=1)] = 1,
    end_line: Annotated[int | None, Query(ge=1)] = None,
) -> FileContentOut:
    """Read an indexed file, optionally only lines ``start_line..end_line``."""
    return FileContentOut(**asdict(manager.read_file(repository_id, path, start_line, end_line)))


@router.get("/{repository_id}/search", response_model=SearchOut)
def search_code(
    repository_id: int,
    manager: ManagerDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    regex: bool = False,
    case_sensitive: bool = False,
    path_glob: Annotated[str | None, Query(max_length=200, examples=["app/*.py"])] = None,
    max_results: Annotated[int | None, Query(ge=1)] = None,
) -> SearchOut:
    """Search indexed files line by line (literal text by default, regex when ``regex=true``)."""
    result = manager.search(
        repository_id,
        q,
        regex=regex,
        case_sensitive=case_sensitive,
        path_glob=path_glob,
        max_results=max_results,
    )
    return SearchOut(
        query=q,
        files_searched=result.files_searched,
        truncated=result.truncated,
        matches=[asdict(m) for m in result.matches],
    )
