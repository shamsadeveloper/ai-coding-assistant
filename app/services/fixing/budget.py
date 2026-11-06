"""An LLMService wrapper that enforces a session's call budget and stop flag on the backend.

Every component of a fix session (investigation agent, patch generator, failure analysis,
review) talks to the model through this wrapper, so the limits cannot be bypassed by any
single component.
"""

import threading
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.fixing.errors import SessionLimitError, SessionStoppedError
from app.services.llm_service import AgentMessage, AssistantTurn, ChatMessage, LLMHealth, LLMResult, LLMService


class BudgetedLLM:
    def __init__(self, inner: LLMService, max_calls: int, stop: threading.Event | None = None) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self._stop = stop or threading.Event()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model = ""

    @property
    def remaining_calls(self) -> int:
        return self._max_calls - self.calls

    def check(self) -> None:
        """Raise if the session was stopped or the call budget is used up."""
        if self._stop.is_set():
            raise SessionStoppedError("The session was stopped by the developer")
        if self.calls >= self._max_calls:
            raise SessionLimitError(f"LLM call limit reached ({self._max_calls} calls per session)")

    def _record(self, model: str, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        self.model = model or self.model
        self.prompt_tokens += prompt_tokens or 0
        self.completion_tokens += completion_tokens or 0

    async def chat(self, messages: Sequence[ChatMessage], model: str | None = None) -> LLMResult:
        self.check()
        self.calls += 1
        result = await self._inner.chat(messages, model)
        self._record(result.model, result.prompt_tokens, result.completion_tokens)
        return result

    async def chat_with_tools(
        self, messages: Sequence[AgentMessage], tools: Sequence[Mapping[str, Any]]
    ) -> AssistantTurn:
        self.check()
        self.calls += 1
        turn = await self._inner.chat_with_tools(messages, tools)
        self._record(turn.model, turn.prompt_tokens, turn.completion_tokens)
        return turn

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model):
        self.check()
        self.calls += 1
        result, model = await self._inner.generate_structured(system_prompt, user_prompt, output_model)
        self._record(model, None, None)
        return result, model

    async def health(self) -> LLMHealth:
        return await self._inner.health()
