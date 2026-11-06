"""Base class for agent tools.

A tool is a small, typed Python function the LLM may ask to run. The LLM never
gets direct access to the filesystem or a shell: it can only pick a tool by
name and supply arguments, which are validated against ``args_model``.
"""

import asyncio
from abc import ABC
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from app.services.changes.service import ChangeService
from app.services.code.insight import CodeInsightService
from app.services.repository.manager import RepositoryManager
from app.services.testing.service import TestingService


@dataclass(frozen=True)
class ToolContext:
    """What a tool is allowed to touch: exactly one repository, through these services."""

    repository_id: int
    repositories: RepositoryManager
    insight: CodeInsightService
    testing: TestingService | None = None
    changes: ChangeService | None = None


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


def _strip_titles(schema: Any) -> Any:
    """Drop Pydantic's auto-generated "title" keys: they cost tokens and add nothing for the model."""
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


class Tool[ArgsT: BaseModel](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: type[ArgsT]

    def run(self, args: ArgsT, context: ToolContext) -> str:
        """Execute the tool and return text for the model. Raise domain errors on failure.

        Sync tools implement this; it runs in a worker thread because it does blocking I/O.
        """
        raise NotImplementedError(f"{self.name} only supports async execution")

    async def arun(self, args: ArgsT, context: ToolContext) -> str:
        """Async entry point used by the agent. Tools that call the LLM override this."""
        return await asyncio.to_thread(self.run, args, context)

    def schema(self) -> dict[str, Any]:
        """The function definition sent to the model (Ollama/OpenAI tool format)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _strip_titles(self.args_model.model_json_schema()),
            },
        }
