"""FastAPI application factory and entry point.

Run with:  uvicorn app.main:app --port 8001
"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from ollama import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.agent.errors import AgentError, ConversationNotFoundError, ConversationRepositoryMismatchError
from app.api.routes import agent, analysis, changes, fixing, health, llm, repositories, testing
from app.services.fixing.errors import FixSessionBusyError, FixSessionNotFoundError, FixSessionStateError
from app.services.fixing.runner import FixSessionRunner
from app.services.fixing.service import mark_interrupted_sessions
from app.core.config import get_settings
from app.core.database import create_db_engine, init_db
from app.core.logging import configure_logging, request_id_var
from app.services.changes.git import GitError
from app.services.changes.service import ChangeNotFoundError, ChangeStateError
from app.services.code.insight import SymbolNotFoundError
from app.services.intelligence.service import AnalysisRunNotFoundError
from app.services.repository.writer import WriteConflictError
from app.services.testing.runner import TestExecutionError
from app.services.testing.service import InvalidStateError, TestingNotFoundError
from app.services.llm_service import (
    LLMError,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.repository.errors import (
    DuplicateRepositoryError,
    FileAccessDeniedError,
    FileNotIndexedError,
    FileTooLargeError,
    InvalidLineRangeError,
    InvalidRepositoryPathError,
    InvalidSearchQueryError,
    PathOutsideRepositoryError,
    RepositoryError,
    RepositoryNotFoundError,
)

logger = logging.getLogger(__name__)

_ERROR_STATUS: dict[type[Exception], int] = {
    LLMUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    LLMModelNotFoundError: status.HTTP_404_NOT_FOUND,
    LLMTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
    LLMError: status.HTTP_502_BAD_GATEWAY,
    InvalidRepositoryPathError: status.HTTP_400_BAD_REQUEST,
    PathOutsideRepositoryError: status.HTTP_400_BAD_REQUEST,
    InvalidLineRangeError: status.HTTP_400_BAD_REQUEST,
    InvalidSearchQueryError: status.HTTP_400_BAD_REQUEST,
    FileAccessDeniedError: status.HTTP_403_FORBIDDEN,
    RepositoryNotFoundError: status.HTTP_404_NOT_FOUND,
    FileNotIndexedError: status.HTTP_404_NOT_FOUND,
    DuplicateRepositoryError: status.HTTP_409_CONFLICT,
    FileTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    ConversationNotFoundError: status.HTTP_404_NOT_FOUND,
    ConversationRepositoryMismatchError: status.HTTP_400_BAD_REQUEST,
    SymbolNotFoundError: status.HTTP_404_NOT_FOUND,
    AnalysisRunNotFoundError: status.HTTP_404_NOT_FOUND,
    TestingNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidStateError: status.HTTP_409_CONFLICT,
    WriteConflictError: status.HTTP_409_CONFLICT,
    TestExecutionError: status.HTTP_400_BAD_REQUEST,
    ChangeNotFoundError: status.HTTP_404_NOT_FOUND,
    ChangeStateError: status.HTTP_409_CONFLICT,
    GitError: status.HTTP_409_CONFLICT,
    FixSessionNotFoundError: status.HTTP_404_NOT_FOUND,
    FixSessionStateError: status.HTTP_409_CONFLICT,
    FixSessionBusyError: status.HTTP_429_TOO_MANY_REQUESTS,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create process-wide resources (Ollama client, database engine) and release them on shutdown."""
    settings = get_settings()
    app.state.ollama_client = AsyncClient(
        host=settings.ollama_host, timeout=settings.ollama_timeout_seconds
    )
    engine = create_db_engine(settings.database_url)
    if settings.database_auto_create:
        init_db(engine)
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.fix_runner = FixSessionRunner()
    try:
        with app.state.session_factory() as session:
            if interrupted := mark_interrupted_sessions(session, settings.data_path):
                logger.warning("%d fix session(s) were running when the server stopped; marked interrupted", interrupted)
    except (SQLAlchemyError, OSError):
        logger.warning("Could not check for interrupted fix sessions (run 'alembic upgrade head'?)", exc_info=True)
    logger.info("Using Ollama at %s with model %s", settings.ollama_host, settings.ollama_model)
    yield
    await app.state.fix_runner.shutdown()
    await app.state.ollama_client.close()
    engine.dispose()


async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    code = next(
        (_ERROR_STATUS[cls] for cls in type(exc).__mro__ if cls in _ERROR_STATUS),
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
    logger.warning("%s on %s: %s", type(exc).__name__, request.url.path, exc)
    return JSONResponse(status_code=code, content={"detail": str(exc)})


_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


async def request_context(request: Request, call_next) -> Response:
    """Assign a request id (or accept a safe incoming one), log the request with its duration,
    and turn unexpected exceptions into a generic 500 without leaking internals."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse(
                status_code=500, content={"detail": "Internal server error", "request_id": request_id}
            )
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith("/ui/assets"):
            logger.info(
                "%s %s -> %s in %.0f ms", request.method, request.url.path, response.status_code,
                (time.perf_counter() - started) * 1000,
            )
        return response
    finally:
        request_id_var.reset(token)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(title=settings.name, version="1.0.0", lifespan=lifespan)
    app.middleware("http")(request_context)
    for error_type in (LLMError, RepositoryError, AgentError):
        app.add_exception_handler(error_type, handle_domain_error)
    app.include_router(health.router)
    app.include_router(llm.router)
    app.include_router(repositories.router)
    app.include_router(agent.router)
    app.include_router(analysis.router)
    app.include_router(testing.router)
    app.include_router(changes.router)
    app.include_router(changes.git_router)
    app.include_router(fixing.router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    dist = Path(settings.frontend_dist)
    if dist.is_dir():
        app.mount("/ui", StaticFiles(directory=dist, html=True), name="ui")
    return app


app = create_app()
