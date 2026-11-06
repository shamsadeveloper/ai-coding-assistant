"""The agent execution loop.

    user question
        -> LLM decides: call tools, or answer
        -> run the requested tools, append their results
        -> LLM sees the results and decides again
        ... until it answers or the step limit is reached
"""

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext, ToolResult
from app.services.llm_service import AgentMessage, AssistantTurn, LLMService, ToolCall

logger = logging.getLogger(__name__)

StopReason = Literal["completed", "max_steps"]

STEP_LIMIT_PROMPT = (
    "You have reached the tool-call limit. Answer now using only the information gathered above, "
    "and state clearly what you could not verify."
)
VERIFY_PROMPT = (
    "Your answer refers to files or line numbers, but you did not use any tool in this turn. "
    "Earlier answers may be wrong. Verify with the tools now, then answer."
)
OUTPUT_PREVIEW_CHARS = 500

# A file name with an extension (app/main.py, index.ts:12) or an explicit "line N".
_CODE_REFERENCE = re.compile(r"\b[\w./\\-]+\.[A-Za-z]{1,5}\b|\bline\s+\d+", re.IGNORECASE)


def cites_code(text: str) -> bool:
    return bool(_CODE_REFERENCE.search(text))


@dataclass(frozen=True)
class AgentStep:
    """One executed tool call, kept for the API response and the audit log."""

    iteration: int
    tool_name: str
    arguments: dict
    is_error: bool
    output: str  # preview only; the model received the full (truncated-to-limit) output
    output_chars: int
    duration_ms: float


@dataclass
class AgentRunResult:
    answer: str
    stopped_reason: StopReason
    model: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def record_usage(self, turn: AssistantTurn) -> None:
        self.model = turn.model
        self.llm_calls += 1
        self.prompt_tokens += turn.prompt_tokens or 0
        self.completion_tokens += turn.completion_tokens or 0


def _signature(call: ToolCall) -> str:
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"


class AgentEngine:
    def __init__(self, llm: LLMService, registry: ToolRegistry, max_steps: int) -> None:
        self._llm = llm
        self._registry = registry
        self._max_steps = max_steps

    async def run(
        self,
        system_prompt: str,
        history: Sequence[AgentMessage],
        user_message: str,
        context: ToolContext,
    ) -> AgentRunResult:
        messages: list[AgentMessage] = [
            AgentMessage("system", system_prompt),
            *history,
            AgentMessage("user", user_message),
        ]
        tools = self._registry.schemas()
        result = AgentRunResult(answer="", stopped_reason="completed")
        seen_calls: set[str] = set()
        verification_requested = False

        for iteration in range(1, self._max_steps + 1):
            turn = await self._llm.chat_with_tools(messages, tools)
            result.record_usage(turn)

            if not turn.tool_calls:
                answer = turn.content.strip()
                # Grounding check: an answer that cites code without looking at any is likely
                # recalled (or invented) from history. Push back once.
                if not result.steps and not verification_requested and cites_code(answer):
                    verification_requested = True
                    messages.append(AgentMessage("assistant", answer))
                    messages.append(AgentMessage("user", VERIFY_PROMPT))
                    continue
                result.answer = answer
                return result

            messages.append(AgentMessage("assistant", turn.content, tuple(turn.tool_calls)))
            for call in turn.tool_calls:
                tool_result, step = await self._execute(call, iteration, context, seen_calls)
                result.steps.append(step)
                messages.append(AgentMessage("tool", tool_result.content, tool_name=call.name))

        # Step limit reached: one last call without tools forces a final answer.
        messages.append(AgentMessage("user", STEP_LIMIT_PROMPT))
        turn = await self._llm.chat_with_tools(messages, [])
        result.record_usage(turn)
        result.answer = turn.content.strip()
        result.stopped_reason = "max_steps"
        return result

    async def _execute(
        self, call: ToolCall, iteration: int, context: ToolContext, seen_calls: set[str]
    ) -> tuple[ToolResult, AgentStep]:
        started = time.perf_counter()
        signature = _signature(call)
        if signature in seen_calls:
            # Small models sometimes loop on the same call; refuse cheaply and steer them on.
            tool_result = ToolResult(
                f"Error: you already called {call.name} with these arguments. "
                "Use the earlier result, try different arguments, or give your final answer.",
                is_error=True,
            )
        else:
            seen_calls.add(signature)
            # Sync tools run in a worker thread (blocking I/O); async tools may call the LLM.
            tool_result = await self._registry.aexecute(call, context)

        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Agent tool call iteration=%d tool=%s error=%s duration_ms=%.1f",
            iteration, call.name, tool_result.is_error, duration_ms,
        )
        step = AgentStep(
            iteration=iteration,
            tool_name=call.name,
            arguments=dict(call.arguments) if isinstance(call.arguments, dict) else {"raw": call.arguments},
            is_error=tool_result.is_error,
            output=tool_result.content[:OUTPUT_PREVIEW_CHARS],
            output_chars=len(tool_result.content),
            duration_ms=round(duration_ms, 1),
        )
        return tool_result, step
