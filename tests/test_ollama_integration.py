"""End-to-end checks against a real Ollama server.

Run with:  pytest -m integration
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import get_db
from app.core.database import create_db_engine, init_db, session_scope
from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def real_client(tmp_path: Path) -> Iterator[TestClient]:
    """Real Ollama, throwaway database."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'integration.db').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: (yield from session_scope(factory))
    with TestClient(app) as client:
        health = client.get("/api/v1/llm/health").json()
        if not health["model_available"]:
            pytest.skip(f"Model {health['configured_model']} is not pulled")
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_real_chat_round_trip(real_client: TestClient):
    response = real_client.post(
        "/api/v1/llm/chat",
        json={"messages": [{"role": "user", "content": "Reply with only the word: pong"}]},
    )

    assert response.status_code == 200
    assert "pong" in response.json()["content"].lower()


def test_real_agent_finds_a_function(real_client: TestClient, sample_repo: Path):
    repo_id = real_client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]

    response = real_client.post(
        "/api/v1/agent/chat",
        json={"repository_id": repo_id, "message": "In which file is the function add defined?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["steps"], "the agent should have used at least one tool"
    assert "app/utils.py" in body["answer"]
