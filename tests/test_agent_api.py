from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.llm_service import LLMUnavailableError
from tests.conftest import FakeLLMService, answer_turn, tool_turn

CHAT = "/api/v1/agent/chat"


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    return client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]


def test_chat_returns_answer_and_steps(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.turns = [tool_turn(("find_function", {"name": "hello"})), answer_turn("hello is in app/main.py:1")]

    response = client.post(CHAT, json={"repository_id": repo_id, "message": "Where is hello?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "hello is in app/main.py:1"
    assert body["stopped_reason"] == "completed"
    assert body["steps"][0]["tool_name"] == "find_function"
    assert body["steps"][0]["output"].startswith("app/main.py:1: def hello():")

    system_prompt = fake_llm.agent_calls[0][0][0].content
    assert "sample_project" in system_prompt
    assert "FastAPI, React" in system_prompt


def test_conversation_keeps_history_and_audit_log(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.turns = [tool_turn(("search_code", {"query": "todo"})), answer_turn("first answer")]
    first = client.post(CHAT, json={"repository_id": repo_id, "message": "first question"}).json()

    fake_llm.turns = [answer_turn("second answer")]
    second = client.post(
        CHAT,
        json={"repository_id": repo_id, "message": "follow-up", "conversation_id": first["conversation_id"]},
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    sent = fake_llm.agent_calls[-1][0]
    assert [(m.role, m.content) for m in sent[1:]] == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "follow-up"),
    ]

    conversation = client.get(f"/api/v1/agent/conversations/{first['conversation_id']}").json()
    assert conversation["title"] == "first question"
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant", "user", "assistant"]
    assert [(t["tool_name"], t["arguments"]) for t in conversation["tool_calls"]] == [
        ("search_code", {"query": "todo"})
    ]


def test_chat_errors(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    assert client.post(CHAT, json={"repository_id": 999, "message": "q"}).status_code == 404
    assert client.post(CHAT, json={"repository_id": repo_id, "message": ""}).status_code == 422
    assert (
        client.post(CHAT, json={"repository_id": repo_id, "message": "q", "conversation_id": 999}).status_code
        == 404
    )
    assert client.get("/api/v1/agent/conversations/999").status_code == 404

    fake_llm.error = LLMUnavailableError("Ollama is down")
    assert client.post(CHAT, json={"repository_id": repo_id, "message": "q"}).status_code == 503


def test_conversation_cannot_switch_repository(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, tmp_path: Path
):
    other = tmp_path / "other_repo"
    other.mkdir()
    (other / "x.py").write_text("x = 1\n", encoding="utf-8")
    other_id = client.post("/api/v1/repositories", json={"path": str(other)}).json()["id"]
    conversation_id = client.post(CHAT, json={"repository_id": repo_id, "message": "q"}).json()["conversation_id"]

    response = client.post(
        CHAT, json={"repository_id": other_id, "message": "q", "conversation_id": conversation_id}
    )

    assert response.status_code == 400


def test_failed_run_does_not_save_a_half_conversation(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.error = LLMUnavailableError("down")
    client.post(CHAT, json={"repository_id": repo_id, "message": "q"})

    assert client.get("/api/v1/agent/conversations/1").status_code == 404


def test_tools_endpoint_lists_schemas(client: TestClient):
    names = [t["function"]["name"] for t in client.get("/api/v1/agent/tools").json()]

    assert "read_file" in names and "find_function" in names
