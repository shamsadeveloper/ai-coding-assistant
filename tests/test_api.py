import pytest

from app.services.llm_service import (
    LLMError,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_llm_health(client):
    response = client.get("/api/v1/llm/health")

    assert response.status_code == 200
    assert response.json() == {
        "reachable": True,
        "configured_model": "fake-model",
        "model_available": True,
        "available_models": ["fake-model"],
    }


def test_chat_returns_model_reply(client, fake_llm):
    response = client.post(
        "/api/v1/llm/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "echo: hello"
    assert body["completion_tokens"] == 4
    assert fake_llm.received[0].role == "user"
    assert fake_llm.received_model is None


def test_chat_passes_model_override(client, fake_llm):
    response = client.post(
        "/api/v1/llm/chat",
        json={"model": "llama3.2:latest", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert fake_llm.received_model == "llama3.2:latest"


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"model": "bad model; rm -rf", "messages": [{"role": "user", "content": "x"}]},
    ],
)
def test_chat_rejects_invalid_requests(client, payload):
    response = client.post("/api/v1/llm/chat", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (LLMUnavailableError("down"), 503),
        (LLMModelNotFoundError("missing"), 404),
        (LLMTimeoutError("slow"), 504),
        (LLMError("other"), 502),
    ],
)
def test_chat_maps_llm_errors_to_http_status(client, fake_llm, error, status_code):
    fake_llm.error = error

    response = client.post(
        "/api/v1/llm/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)
