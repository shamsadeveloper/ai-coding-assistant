"""Fix-session routes: start (runs in the background), follow the timeline, stop, approve, reject."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import (
    SettingsDep,
    build_fix_service,
    get_fix_runner,
    get_fix_service,
    get_llm_service,
    get_session_factory,
)
from app.schemas.fixing import (
    ApproveFixRequest,
    FixEventOut,
    FixSessionOut,
    FixSessionSummaryOut,
    StartFixSessionRequest,
)
from app.services.fixing.errors import FixSessionStateError
from app.services.fixing.runner import FixSessionRunner
from app.services.fixing.service import ACTIVE_STATUSES, FixSessionService
from app.services.llm_service import LLMService

router = APIRouter(prefix="/api/v1/fix-sessions", tags=["fix sessions"])

ServiceDep = Annotated[FixSessionService, Depends(get_fix_service)]
RunnerDep = Annotated[FixSessionRunner, Depends(get_fix_runner)]


@router.post("", response_model=FixSessionOut, status_code=status.HTTP_202_ACCEPTED)
async def start(
    request: StartFixSessionRequest,
    service: ServiceDep,
    runner: RunnerDep,
    llm: Annotated[LLMService, Depends(get_llm_service)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    settings: SettingsDep,
) -> FixSessionOut:
    """Start investigating and fixing a bug in the background. The repository is NOT changed;
    follow progress with GET /{id} and /{id}/events, then approve or reject."""
    runner.ensure_capacity(settings.fix_max_concurrent_sessions)
    fs = service.create(
        request.repository_id, finding_id=request.finding_id, description=request.description, path=request.path,
        line_start=request.line_start, line_end=request.line_end,
        run_tests=request.run_tests, reproduction_test=request.reproduction_test,
    )
    session_id = fs.id

    async def job(stop: threading.Event) -> None:
        with factory() as db:  # the request's session is closed when the response is sent
            await build_fix_service(llm, db, settings).run(session_id, stop)

    runner.start(session_id, job, settings.fix_max_concurrent_sessions)
    return FixSessionOut.model_validate(fs)


@router.get("", response_model=list[FixSessionSummaryOut])
def list_sessions(repository_id: int, service: ServiceDep) -> list[FixSessionSummaryOut]:
    return [FixSessionSummaryOut.model_validate(s) for s in service.list(repository_id)]


@router.get("/{session_id}", response_model=FixSessionOut)
def get_session(session_id: int, service: ServiceDep) -> FixSessionOut:
    return FixSessionOut.model_validate(service.get(session_id))


@router.get("/{session_id}/events", response_model=list[FixEventOut])
def events(session_id: int, service: ServiceDep, after: Annotated[int, Query(ge=0)] = 0) -> list[FixEventOut]:
    """The session's timeline (and audit trail). Pass the last seen event id as ``after`` to poll."""
    return [FixEventOut.model_validate(e) for e in service.events(session_id, after)]


@router.post("/{session_id}/cancel", response_model=FixSessionOut)
async def cancel(session_id: int, service: ServiceDep, runner: RunnerDep) -> FixSessionOut:
    """Stop a running session: the model call is abandoned and any test process is killed."""
    fs = service.get(session_id)
    if fs.status not in ACTIVE_STATUSES:
        raise FixSessionStateError(f"Fix session {session_id} is {fs.status}")
    if runner.stop(session_id):
        await runner.wait(session_id)
    else:
        service.mark_cancelled(session_id)  # no task (e.g. left over from a restart)
    return FixSessionOut.model_validate(service.reload(session_id))


@router.post("/{session_id}/approve", response_model=FixSessionOut)
def approve(session_id: int, request: ApproveFixRequest, service: ServiceDep) -> FixSessionOut:
    """Developer approval: apply the validated patch (conflict check, backup, Git branch, rollback-able)."""
    return FixSessionOut.model_validate(service.approve(session_id, request.create_branch))


@router.post("/{session_id}/reject", response_model=FixSessionOut)
def reject(session_id: int, service: ServiceDep) -> FixSessionOut:
    return FixSessionOut.model_validate(service.reject(session_id))
