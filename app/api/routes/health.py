from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_llm_service
from app.core.config import get_settings
from app.services.llm_service import LLMService

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the API process is up (does not call Ollama or the database)."""
    settings = get_settings()
    return {"status": "ok", "app": settings.name, "environment": settings.environment}


@router.get("/ready")
async def ready(
    session: Annotated[Session, Depends(get_db)], llm: Annotated[LLMService, Depends(get_llm_service)]
) -> JSONResponse:
    """Readiness: the database answers and Ollama has the configured model.

    Returns 503 when the database is down. Ollama problems are reported but do not
    fail readiness, because repository browsing works without the model.
    """
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # report, do not raise
        database = f"error: {type(exc).__name__}"
    model = await llm.health()
    body = {
        "status": "ready" if database == "ok" else "not_ready",
        "database": database,
        "ollama": "ok" if model.reachable else "unreachable",
        "model": model.configured_model,
        "model_available": model.model_available,
    }
    return JSONResponse(status_code=200 if database == "ok" else 503, content=body)
