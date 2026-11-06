from app.agent.tools.base import Tool
from app.agent.tools.change_tools import GenerateCodePatchTool, InspectGitDiffTool
from app.agent.tools.repository_tools import repository_tools
from app.agent.tools.testing_tools import GenerateUnitTestTool, RunProjectTestsTool


def default_tools() -> list[Tool]:
    """Every tool the agent may call. Nothing outside this list is reachable by the model."""
    return [
        *repository_tools(),
        GenerateUnitTestTool(),
        RunProjectTestsTool(),
        GenerateCodePatchTool(),
        InspectGitDiffTool(),
    ]
