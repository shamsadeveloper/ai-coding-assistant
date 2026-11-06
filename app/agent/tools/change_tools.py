"""Agent tools for code changes. The agent can propose patches and read diffs, never apply them."""

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolContext
from app.services.changes.git import GitError, GitService
from app.services.repository.path_guard import resolve_inside

DIFF_PREVIEW_CHARS = 4_000


class GenerateCodePatchArgs(BaseModel):
    request: str = Field(min_length=5, description="What to change, in plain words")
    paths: list[str] | None = Field(default=None, description="Files to change, if known (relative paths)")


class GenerateCodePatchTool(Tool[GenerateCodePatchArgs]):
    name = "generate_code_patch"
    description = (
        "Propose a code change as a patch (unified diff). The patch is stored for developer review; "
        "files are NOT modified until a developer approves it."
    )
    args_model = GenerateCodePatchArgs

    async def arun(self, args: GenerateCodePatchArgs, context: ToolContext) -> str:
        if context.changes is None:
            raise GitError("Code changes are not available in this context")
        change = await context.changes.propose(context.repository_id, args.request, args.paths)
        parts = [
            f"Change #{change.id} is {change.status}. It has NOT been applied; a developer must approve it.",
            f"Summary: {change.summary}",
        ]
        for file in change.files:
            parts.append(f"--- {file.path} (syntax ok: {file.syntax_ok}) {file.notes}".rstrip())
            parts.append(file.diff[:DIFF_PREVIEW_CHARS] or "(no change produced)")
        return "\n".join(parts)


class InspectGitDiffArgs(BaseModel):
    path: str | None = Field(default=None, description="Limit the diff to one file")


class InspectGitDiffTool(Tool[InspectGitDiffArgs]):
    name = "inspect_git_diff"
    description = "Show uncommitted changes in the repository's Git working tree as a unified diff."
    args_model = InspectGitDiffArgs

    def run(self, args: InspectGitDiffArgs, context: ToolContext) -> str:
        root = Path(context.repositories.get(context.repository_id).root_path)
        git = GitService(root)
        if not git.is_repository():
            return "This repository is not a Git repository."
        paths = [resolve_inside(root, args.path).relative_to(root).as_posix()] if args.path else []
        return git.diff(paths) or "No uncommitted changes."
