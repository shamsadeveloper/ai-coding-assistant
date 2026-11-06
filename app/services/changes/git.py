"""Minimal Git integration via the git CLI with fixed arguments (no shell)."""

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from app.services.repository.errors import RepositoryError
from app.services.testing.runner import minimal_env

_BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
MAX_DIFF_CHARS = 100_000


class GitError(RepositoryError):
    """A git command failed, or the working tree is not in a state we can safely change."""


class GitService:
    def __init__(self, root: Path, timeout_seconds: int = 30) -> None:
        self._root = root
        self._timeout = timeout_seconds

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = minimal_env() | {"GIT_TERMINAL_PROMPT": "0"}
        try:
            result = subprocess.run(
                ["git", "-C", str(self._root), "-c", "core.quotepath=false", *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self._timeout, env=env, shell=False,
            )
        except FileNotFoundError as exc:
            raise GitError("git is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out") from exc
        if check and result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()[:500]}")
        return result

    def is_repository(self) -> bool:
        try:
            result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        except GitError:
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def status(self, paths: Sequence[str] = ()) -> list[str]:
        """Porcelain status lines, optionally limited to some paths."""
        output = self._run("status", "--porcelain", "--", *paths).stdout
        return [line for line in output.splitlines() if line.strip()]

    def create_branch(self, name: str) -> None:
        if not _BRANCH_NAME.match(name) or ".." in name:
            raise GitError(f"Invalid branch name: {name}")
        self._run("switch", "-c", name)

    def switch(self, name: str) -> None:
        self._run("switch", name)

    def delete_branch(self, name: str) -> None:
        self._run("branch", "-D", name)

    def diff(self, paths: Sequence[str] = ()) -> str:
        """Uncommitted changes (staged and unstaged) compared to HEAD."""
        result = self._run("diff", "HEAD", "--", *paths, check=False)
        if result.returncode != 0:  # no commits yet
            result = self._run("diff", "--", *paths)
        output = result.stdout
        if len(output) > MAX_DIFF_CHARS:
            output = output[:MAX_DIFF_CHARS] + "\n... (diff truncated)\n"
        return output
