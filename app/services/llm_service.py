"""LLM service: the only module that talks to Ollama.

Routes and the agent engine depend on ``LLMService``, not on the ``ollama``
package, so the model provider can be swapped or faked in tests.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

import httpx
from ollama import AsyncClient, ChatResponse, ResponseError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]
AgentRole = Literal["system", "user", "assistant", "tool"]

DEFAULT_SYSTEM_PROMPT = (
    "You are a senior software engineer acting as a coding assistant. "
    "Answer precisely, show code in fenced blocks, and say clearly when you are unsure."
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMError(Exception):
    """Base error for LLM failures."""


class LLMUnavailableError(LLMError):
    """The Ollama server could not be reached."""


class LLMModelNotFoundError(LLMError):
    """The requested model is not pulled on the Ollama server."""


class LLMTimeoutError(LLMError):
    """The model did not answer within the configured timeout."""


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMResult:
    model: str
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: float | None


@dataclass(frozen=True)
class LLMHealth:
    reachable: bool
    configured_model: str
    model_available: bool
    available_models: list[str]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentMessage:
    """One message in an agent conversation, including tool calls and tool results."""

    role: AgentRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_name: str | None = None  # set on role="tool" messages


@dataclass(frozen=True)
class AssistantTurn:
    """The model's reply: either tool calls to execute, or final text (``tool_calls`` empty)."""

    model: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMService(Protocol):
    """Interface the rest of the application depends on."""

    async def chat(self, messages: Sequence[ChatMessage], model: str | None = None) -> LLMResult: ...

    async def chat_with_tools(
        self, messages: Sequence[AgentMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AssistantTurn: ...

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, output_model: type[ModelT]
    ) -> tuple[ModelT, str]: ...

    async def health(self) -> LLMHealth: ...


class LLMInvalidOutputError(LLMError):
    """The model's structured output did not match the requested schema."""


_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_TOOL_CALL_TAGS = re.compile(r"</?tool_call>")


def _as_tool_call(obj: Any, known_tools: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict) or obj.get("name") not in known_tools:
        return None
    arguments = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    return ToolCall(obj["name"], arguments) if isinstance(arguments, dict) else None


def _parse_whole_message(text: str, known_tools: set[str]) -> list[ToolCall]:
    """The message is nothing but one or more JSON tool calls (or a JSON list of them)."""
    if not text.startswith(("{", "[")):
        return []
    decoder = json.JSONDecoder()
    objects: list[Any] = []
    position = 0
    while position < len(text):
        try:
            obj, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            return []
        objects.extend(obj if isinstance(obj, list) else [obj])
        position = end
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
    calls = [_as_tool_call(obj, known_tools) for obj in objects]
    return [c for c in calls if c] if all(calls) else []


def _parse_embedded(text: str, known_tools: set[str]) -> list[ToolCall]:
    """Tool calls written inside prose or markdown, e.g. a numbered plan with JSON blocks."""
    decoder = json.JSONDecoder()
    calls = []
    for match in re.finditer(r"\{\s*\"name\"\s*:", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if call := _as_tool_call(obj, known_tools):
            calls.append(call)
    return calls


def parse_text_tool_calls(content: str, known_tools: set[str]) -> list[ToolCall]:
    """Recover tool calls that a model wrote as JSON text instead of native ``tool_calls``.

    Some models (e.g. qwen2.5-coder) answer with ``{"name": ..., "arguments": {...}}``
    in the message content, sometimes wrapped in prose. Only objects whose ``name`` is
    a known tool and whose ``arguments`` is an object are accepted, so ordinary JSON in
    an answer is not mistaken for a tool call.
    """
    text = _TOOL_CALL_TAGS.sub("", _CODE_FENCE.sub("", content.strip())).strip()
    return _parse_whole_message(text, known_tools) or _parse_embedded(content, known_tools)


def _to_ollama_message(message: AgentMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls
        ]
    if message.tool_name:
        payload["tool_name"] = message.tool_name
    return payload


class OllamaLLMService:
    """``LLMService`` implementation backed by a local Ollama server."""

    def __init__(
        self,
        client: AsyncClient,
        default_model: str,
        temperature: float,
        keep_alive: str,
        num_ctx: int = 8192,
        max_tokens: int = 2048,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._client = client
        self._default_model = default_model
        self._temperature = temperature
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt

    async def chat(self, messages: Sequence[ChatMessage], model: str | None = None) -> LLMResult:
        """Send a conversation to the model and return its reply.

        A default system prompt is prepended when the caller supplies none.
        """
        model_name = model or self._default_model
        payload = [{"role": m.role, "content": m.content} for m in messages]
        if not any(m.role == "system" for m in messages):
            payload.insert(0, {"role": "system", "content": self._system_prompt})

        response = await self._call(
            model_name,
            lambda: self._client.chat(
                model=model_name,
                messages=payload,
                options=self._options(),
                keep_alive=self._keep_alive,
            ),
        )
        return LLMResult(
            model=response.model or model_name,
            content=response.message.content or "",
            prompt_tokens=response.prompt_eval_count,
            completion_tokens=response.eval_count,
            # Ollama reports durations in nanoseconds.
            duration_ms=response.total_duration / 1_000_000 if response.total_duration else None,
        )

    async def chat_with_tools(
        self, messages: Sequence[AgentMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AssistantTurn:
        """One agent step: the model either requests tool calls or gives its final answer."""
        model_name = self._default_model
        response = await self._call(
            model_name,
            lambda: self._client.chat(
                model=model_name,
                messages=[_to_ollama_message(m) for m in messages],
                tools=list(tools) or None,
                options=self._options(),
                keep_alive=self._keep_alive,
            ),
        )

        content = response.message.content or ""
        calls = [
            ToolCall(c.function.name, dict(c.function.arguments))
            for c in response.message.tool_calls or []
        ]
        if not calls and tools:
            calls = parse_text_tool_calls(content, {t["function"]["name"] for t in tools})
            if calls:
                content = ""  # the text was the tool call itself, not an answer

        return AssistantTurn(
            model=response.model or model_name,
            content=content,
            tool_calls=calls,
            prompt_tokens=response.prompt_eval_count,
            completion_tokens=response.eval_count,
        )

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, output_model: type[ModelT]
    ) -> tuple[ModelT, str]:
        """Ask for JSON matching ``output_model``'s schema and return it validated, with the model name.

        Ollama constrains generation to the schema, so malformed JSON is rare; the
        result is still validated because a schema cannot express every rule.
        """
        model_name = self._default_model
        response = await self._call(
            model_name,
            lambda: self._client.chat(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=output_model.model_json_schema(),
                options=self._options(),
                keep_alive=self._keep_alive,
            ),
        )
        try:
            parsed = output_model.model_validate_json(response.message.content or "")
        except ValidationError as exc:
            raise LLMInvalidOutputError(f"Model output did not match the expected format: {exc}") from exc
        return parsed, response.model or model_name

    async def health(self) -> LLMHealth:
        """Check that Ollama is reachable and the default model is pulled."""
        try:
            listing = await self._client.list()
        except (ConnectionError, httpx.HTTPError, ResponseError):
            return LLMHealth(False, self._default_model, False, [])

        names = [m.model for m in listing.models if m.model]
        return LLMHealth(True, self._default_model, self._default_model in names, names)

    def _options(self) -> dict[str, Any]:
        # num_predict caps every reply, so a model stuck in a loop stops instead of running
        # until the HTTP timeout.
        return {"temperature": self._temperature, "num_ctx": self._num_ctx, "num_predict": self._max_tokens}

    async def _call(self, model_name: str, request: Callable[[], Awaitable[ChatResponse]]) -> ChatResponse:
        """Run an Ollama request and translate client errors into ``LLMError`` subclasses."""
        try:
            response = await request()
        except ConnectionError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Model '{model_name}' did not respond in time") from exc
        except ResponseError as exc:
            if exc.status_code == 404:
                raise LLMModelNotFoundError(
                    f"Model '{model_name}' is not available. Run: ollama pull {model_name}"
                ) from exc
            raise LLMError(exc.error) from exc

        logger.info(
            "LLM call completed model=%s prompt_tokens=%s completion_tokens=%s",
            model_name,
            response.prompt_eval_count,
            response.eval_count,
        )
        return response
