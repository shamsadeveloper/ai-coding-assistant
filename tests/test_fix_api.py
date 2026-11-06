"""Fix sessions over HTTP: background run, timeline, stop, review, approval, rollback."""

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.intelligence.reports import BugFinding, BugReport
from tests.conftest import FakeLLMService
from tests.fix_fixtures import FIXED_PRICING, PRICING, make_shop_repo
from tests.test_fix_session import APPROVE, GOOD_PATCH, root_cause

BASE = "/api/v1/fix-sessions"
RESTING = {"awaiting_approval", "applied", "rejected", "unverified", "failed", "cancelled", "interrupted"}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def wait_for(client: TestClient, session_id: int, statuses=RESTING, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = client.get(f"{BASE}/{session_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.1)
    raise AssertionError(f"session still {body.get('status')}: {body.get('error')}")


@pytest.fixture(autouse=True)
def fast_settings(test_settings: Settings) -> None:
    test_settings.fix_investigation_max_steps = 0


@pytest.fixture
def shop_root(tmp_path: Path) -> Path:
    root = make_shop_repo(tmp_path / "shop_repo")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def repo_id(client: TestClient, shop_root: Path) -> int:
    return client.post("/api/v1/repositories", json={"path": str(shop_root)}).json()["id"]


def finding_id(client: TestClient, fake_llm: FakeLLMService, repo_id: int) -> int:
    fake_llm.structured = [BugReport(summary="crash", findings=[BugFinding(
        title="Crash on empty order list", severity="High", category="logic", status="confirmed",
        line_start=6, line_end=6, explanation="average() divides by zero for []", suggestion="return 0.0",
    )])]
    run = client.post("/api/v1/analysis/bugs", json={"repository_id": repo_id, "path": "shop/orders.py"}).json()
    return run["findings"][0]["id"]


def test_fix_from_finding_then_approve_on_branch_and_roll_back(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, shop_root: Path
):
    fid = finding_id(client, fake_llm, repo_id)
    fake_llm.structured = [root_cause(), APPROVE]
    fake_llm.chat_replies = [GOOD_PATCH]

    started = client.post(BASE, json={"repository_id": repo_id, "finding_id": fid})
    assert started.status_code == 202
    assert started.json()["description"].startswith("Crash on empty order list.")

    body = wait_for(client, started.json()["id"])
    assert body["status"] == "awaiting_approval", body["error"]
    assert body["validation_status"] == "passed"
    assert body["root_cause"]["confidence"] == "high"
    assert body["attempts"][0]["files"][0]["path"] == "shop/pricing.py"
    assert "new_content" not in body["attempts"][0]["files"][0]  # not sent to the browser
    assert body["review"]["automated"][1]["name"] == "signatures"
    assert (shop_root / "shop" / "pricing.py").read_text(encoding="utf-8") == PRICING  # nothing applied yet
    assert git(shop_root, "status", "--porcelain") == ""

    events = client.get(f"{BASE}/{body['id']}/events").json()
    assert [e["stage"] for e in events][-2:] == ["review", "ready"]
    assert client.get(f"{BASE}/{body['id']}/events", params={"after": events[-2]["id"]}).json()[0]["stage"] == "ready"
    assert [s["id"] for s in client.get(BASE, params={"repository_id": repo_id}).json()] == [body["id"]]

    applied = client.post(f"{BASE}/{body['id']}/approve", json={}).json()
    assert applied["status"] == "applied"
    assert (shop_root / "shop" / "pricing.py").read_text(encoding="utf-8") == FIXED_PRICING
    change = client.get(f"/api/v1/changes/{applied['change_set_id']}").json()
    assert change["status"] == "applied"
    assert change["branch_name"] == f"ai-assistant/change-{change['id']}"
    assert git(shop_root, "rev-parse", "--abbrev-ref", "HEAD") == change["branch_name"]
    assert "applied as change" in client.get(f"{BASE}/{body['id']}/events").json()[-1]["message"]

    assert client.post(f"/api/v1/changes/{change['id']}/rollback").json()["status"] == "rolled_back"
    assert (shop_root / "shop" / "pricing.py").read_text(encoding="utf-8") == PRICING


def test_file_changed_during_review_blocks_approval(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, shop_root: Path
):
    fake_llm.structured = [root_cause(), APPROVE]
    fake_llm.chat_replies = [GOOD_PATCH]
    session_id = client.post(BASE, json={
        "repository_id": repo_id, "description": "average_order_value crashes on []", "path": "shop/orders.py", "line_start": 6,
    }).json()["id"]
    wait_for(client, session_id)
    (shop_root / "shop" / "pricing.py").write_text(PRICING + "# edited by the developer\n", encoding="utf-8")

    response = client.post(f"{BASE}/{session_id}/approve", json={"create_branch": False})

    assert response.status_code == 409
    assert "changed after the patch was proposed" in response.json()["detail"]
    assert "edited by the developer" in (shop_root / "shop" / "pricing.py").read_text(encoding="utf-8")
    assert client.get(f"{BASE}/{session_id}").json()["status"] == "awaiting_approval"
    assert client.post(f"{BASE}/{session_id}/reject").json()["status"] == "rejected"
    assert client.post(f"{BASE}/{session_id}/approve", json={}).status_code == 409


def test_developer_can_stop_a_session_over_http(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    async def hang(messages, model=None):
        await asyncio.sleep(3600)

    monkeypatch.setattr(fake_llm, "chat", hang)
    fake_llm.structured = [root_cause()]
    request = {"repository_id": repo_id, "description": "crash on empty orders", "path": "shop/orders.py"}
    session_id = client.post(BASE, json=request).json()["id"]
    wait_for(client, session_id, {"fixing"})

    busy = client.post(BASE, json=request)
    assert busy.status_code == 429  # one session at a time by default

    stopped = client.post(f"{BASE}/{session_id}/cancel")
    assert stopped.json()["status"] == "cancelled"
    assert not (test_settings.data_path / "sandboxes" / str(session_id)).exists()
    assert client.post(f"{BASE}/{session_id}/cancel").status_code == 409


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({"description": "something is wrong"}, 409),  # no path
        ({"finding_id": 999}, 404),
        ({"description": "secret bug here", "path": ".env"}, 403),
        ({"description": "outside bug", "path": "../x.py"}, 400),
        ({"description": "no", "path": "shop/orders.py"}, 422),  # too short
    ],
)
def test_invalid_start_requests(client: TestClient, repo_id: int, payload: dict, status_code: int):
    assert client.post(BASE, json={"repository_id": repo_id, **payload}).status_code == status_code


def test_finding_from_another_repository_is_refused(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, tmp_path: Path
):
    fid = finding_id(client, fake_llm, repo_id)
    other = client.post("/api/v1/repositories", json={"path": str(make_shop_repo(tmp_path / "other"))}).json()["id"]

    response = client.post(BASE, json={"repository_id": other, "finding_id": fid})

    assert response.status_code == 409
    assert "another repository" in response.json()["detail"]


def test_unknown_session(client: TestClient):
    assert client.get(f"{BASE}/42").status_code == 404
    assert client.post(f"{BASE}/42/approve", json={}).status_code == 404
