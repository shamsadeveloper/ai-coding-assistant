"""Live fix session against the real model on the known-bug repository (tests/fix_fixtures.py).

Run with:  pytest -m integration tests/test_fix_integration.py -s
On a CPU-only machine this takes several minutes.
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import get_db, get_session_factory, get_settings
from app.core.config import Settings
from app.core.database import create_db_engine, init_db, session_scope
from app.main import app
from tests.fix_fixtures import PRICING, make_shop_repo

pytestmark = pytest.mark.integration
RESTING = {"awaiting_approval", "applied", "rejected", "unverified", "failed", "cancelled", "interrupted"}


@pytest.fixture
def live_client(tmp_path: Path) -> Iterator[TestClient]:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(data_dir=str(tmp_path / "data"))
    app.dependency_overrides[get_db] = lambda: (yield from session_scope(factory))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        if not client.get("/api/v1/llm/health").json()["model_available"]:
            pytest.skip("The configured model is not pulled")
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_live_fix_session_on_known_bug_repository(live_client: TestClient, tmp_path: Path):
    root = make_shop_repo(tmp_path / "shop")
    repo_id = live_client.post("/api/v1/repositories", json={"path": str(root)}).json()["id"]
    started_at = time.time()

    session_id = live_client.post("/api/v1/fix-sessions", json={
        "repository_id": repo_id,
        "description": "average_order_value raises ZeroDivisionError when the list of orders is empty; it should return 0.0",
        "path": "shop/orders.py", "line_start": 6,
    }).json()["id"]

    seen = 0
    body: dict = {}
    while time.time() - started_at < 40 * 60:
        for event in live_client.get(f"/api/v1/fix-sessions/{session_id}/events", params={"after": seen}).json():
            seen = event["id"]
            print(f"[{time.time() - started_at:6.0f}s] {event['stage']:<14} {event['message']}", flush=True)
        body = live_client.get(f"/api/v1/fix-sessions/{session_id}").json()
        if body["status"] in RESTING:
            break
        time.sleep(3)

    summary = {
        "status": body["status"], "validation": body["validation_status"], "attempts": [a["status"] for a in body["attempts"]],
        "llm_calls": body["llm_calls"], "tokens": body["prompt_tokens"] + body["completion_tokens"],
        "minutes": round((time.time() - started_at) / 60, 1),
        "root_cause": (body["root_cause"] or {}).get("location"), "confidence": (body["root_cause"] or {}).get("confidence"),
        "review": (body["review"] or {}).get("verdict"), "error": body["error"],
    }
    print("SUMMARY " + json.dumps(summary), flush=True)
    for attempt in body["attempts"]:
        for file in attempt["files"]:
            print(f"--- attempt {attempt['number']} ({attempt['status']}) {file['path']}\n{file['diff']}", flush=True)

    assert (root / "shop" / "pricing.py").read_text(encoding="utf-8") == PRICING  # never written before approval
    assert body["llm_calls"] <= 20
    assert body["status"] == "awaiting_approval", body["error"]
    assert body["validation_status"] == "passed"
    assert len(body["attempts"]) <= 3
