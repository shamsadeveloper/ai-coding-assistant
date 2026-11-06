"""Tool registry: the closed allowlist of tools the agent may call, plus safe dispatch."""

import json
import logging
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.core.secrets import redact_text
from app.services.llm_service import LLMError, ToolCall
from app.services.repository.errors import RepositoryError

logger = logging.getLogger(__name__)


def _describe_validation_error(error: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
    ]
    return "; ".join(problems)


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool], max_output_chars: int) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool
        self._max_output_chars = max_output_chars

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        """Run a sync tool call. Never raises: failures become error results the model can read."""
        prepared = self._prepare(call)
        if isinstance(prepared, ToolResult):
            return prepared
        tool, args = prepared
        try:
            return ToolResult(self._prepare_output(tool.run(args, context)))
        except Exception as exc:
            return self._failure(call, exc)

    async def aexecute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        """Async version used by the agent loop; supports tools that call the LLM."""
        prepared = self._prepare(call)
        if isinstance(prepared, ToolResult):
            return prepared
        tool, args = prepared
        try:
            return ToolResult(self._prepare_output(await tool.arun(args, context)))
        except Exception as exc:
            return self._failure(call, exc)

    def _prepare(self, call: ToolCall) -> tuple[Tool, BaseModel] | ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                f"Error: unknown tool '{call.name}'. Available tools: {', '.join(self._tools)}", is_error=True
            )

        arguments: Any = call.arguments
        if isinstance(arguments, str):  # some models send arguments as a JSON string
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return ToolResult("Error: arguments must be a JSON object", is_error=True)

        try:
            return tool, tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(
                f"Error: invalid arguments for {call.name}: {_describe_validation_error(exc)}", is_error=True
            )

    @staticmethod
    def _failure(call: ToolCall, exc: Exception) -> ToolResult:
        if isinstance(exc, (RepositoryError, LLMError)):
            return ToolResult(f"Error: {exc}", is_error=True)
        # Unexpected bugs are logged in full but not leaked to the model.
        logger.exception("Tool %s failed", call.name, exc_info=exc)
        return ToolResult(f"Error: tool {call.name} failed unexpectedly", is_error=True)

    def _prepare_output(self, output: str) -> str:
        """Every tool result passes here before the model sees it: mask secrets, then cap the size."""
        output = redact_text(output)
        if len(output) <= self._max_output_chars:
            return output
        cut = output[: self._max_output_chars]
        return f"{cut}\n... (output truncated at {self._max_output_chars} characters; request a smaller range)"
