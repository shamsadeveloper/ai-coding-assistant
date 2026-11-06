"""Agent tools for tests. Generated tests go to the review area; the agent can never approve them."""

from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolContext
from app.services.testing.service import TestingService
from app.services.testing.runner import TestExecutionError

PREVIEW_CHARS = 3_000
OUTPUT_TAIL_CHARS = 3_000


def _testing(context: ToolContext) -> TestingService:
    if context.testing is None:
        raise TestExecutionError("Test tools are not available in this context")
    return context.testing


class GenerateUnitTestArgs(BaseModel):
    path: str = Field(description="Source file to test, relative to the repository root")
    symbol: str | None = Field(default=None, description="Only test this function/class")


class GenerateUnitTestTool(Tool[GenerateUnitTestArgs]):
    name = "generate_unit_test"
    description = (
        "Generate a unit test file for a source file or one function. The test is saved in a review area "
        "for the developer to approve; it is not added to the repository."
    )
    args_model = GenerateUnitTestArgs

    async def arun(self, args: GenerateUnitTestArgs, context: ToolContext) -> str:
        record = await _testing(context).generate(context.repository_id, args.path, args.symbol)
        status = "syntax OK" if record.syntax_ok else "HAS SYNTAX PROBLEMS"
        exists = " (a file already exists there; approving needs overwrite)" if record.target_exists else ""
        cases = "\n".join(f"- {c}" for c in record.test_cases)
        return (
            f"Generated test #{record.id} ({record.framework}, {status}) for {record.source_path}.\n"
            f"Proposed file: {record.target_path}{exists}\n"
            "It is waiting for developer approval and has NOT been added to the repository.\n"
            f"Test cases:\n{cases}\n\n{record.content[:PREVIEW_CHARS]}"
        )


class RunProjectTestsArgs(BaseModel):
    path: str | None = Field(default=None, description="Run only this test file (relative path)")


class RunProjectTestsTool(Tool[RunProjectTestsArgs]):
    name = "run_project_tests"
    description = "Run the repository's tests with its configured test command and report the results."
    args_model = RunProjectTestsArgs

    def run(self, args: RunProjectTestsArgs, context: ToolContext) -> str:
        run = _testing(context).run(context.repository_id, path=args.path)
        state = "timed out" if run.timed_out else f"exit code {run.exit_code}"
        return (
            f"Test run #{run.id} ({run.framework}, {state}): passed={run.passed} failed={run.failed} "
            f"errors={run.errors} skipped={run.skipped}\n\nOutput (last part):\n{run.output[-OUTPUT_TAIL_CHARS:]}"
        )
