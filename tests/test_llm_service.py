"""Unit tests for OllamaLLMService using a stub in place of ollama.AsyncClient."""

import httpx
import pytest
from ollama import ChatResponse, ListResponse, Message, ResponseError

from app.services.llm_service import (
    DEFAULT_SYSTEM_PROMPT,
    ChatMessage,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
    OllamaLLMService,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return ChatResponse(
            model=kwargs["model"],
            message=Message(role="assistant", content="answer"),
            prompt_eval_count=10,
            eval_count=5,
            total_duration=2_000_000,
        )

    async def list(self):
        if self.error:
            raise self.error
        return ListResponse(models=[ListResponse.Model(model="qwen2.5-coder:7b")])


def make_service(client: StubClient) -> OllamaLLMService:
    return OllamaLLMService(client, default_model="qwen2.5-coder:7b", temperature=0.2, keep_alive="5m")


async def test_chat_adds_default_system_prompt_and_maps_result():
    stub = StubClient()

    result = await make_service(stub).chat([ChatMessage("user", "hi")])

    sent = stub.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": DEFAULT_SYSTEM_PROMPT}
    assert sent[1] == {"role": "user", "content": "hi"}
    assert result.content == "answer"
    assert result.duration_ms == 2.0


async def test_chat_keeps_caller_system_prompt():
    stub = StubClient()

    await make_service(stub).chat([ChatMessage("system", "custom"), ChatMessage("user", "hi")])

    assert [m["role"] for m in stub.calls[0]["messages"]] == ["system", "user"]
    assert stub.calls[0]["messages"][0]["content"] == "custom"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ConnectionError("no server"), LLMUnavailableError),
        (httpx.ReadTimeout("slow"), LLMTimeoutError),
        (ResponseError("model not found", 404), LLMModelNotFoundError),
    ],
)
async def test_chat_translates_client_errors(raised, expected):
    with pytest.raises(expected):
        await make_service(StubClient(error=raised)).chat([ChatMessage("user", "hi")])


async def test_health_reports_unreachable_server():
    health = await make_service(StubClient(error=ConnectionError("down"))).health()

    assert health.reachable is False
    assert health.model_available is False


async def test_health_reports_model_available():
    health = await make_service(StubClient()).health()

    assert health.reachable is True
    assert health.model_available is True
