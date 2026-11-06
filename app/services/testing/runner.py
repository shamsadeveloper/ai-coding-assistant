"""Runs a repository's tests with a configured command: no shell, a timeout, and a minimal environment."""

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.repository.errors import RepositoryError

MAX_OUTPUT_CHARS = 50_000
# Only these variables reach the test process; API keys and other secrets in the
# server's environment are not passed on.
_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "LANG", "GOPATH", "GOROOT",
    "JAVA_HOME", "CARGO_HOME", "RUSTUP_HOME",
)


class TestExecutionError(RepositoryError):
    """Tests cannot be run: disabled, unknown framework, or the command is not installed."""

    __test__ = False


@dataclass(frozen=True)
class TestCounts:
    __test__ = False

    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    skipped: int | None = None


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    exit_code: int | None
    timed_out: bool
    output: str
    duration_ms: float
    counts: TestCounts
    stopped: bool = False


def repository_python(root: Path) -> str:
    """The repository's own virtualenv interpreter if present, else the server's Python."""
    for folder in (".venv", "venv", "env"):
        for relative in ("Scripts/python.exe", "bin/python"):
            candidate = root / folder / relative
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def minimal_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env.update({"CI": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"})
    return env


def _count(pattern: str, text: str) -> int | None:
    matches = re.findall(pattern, text)
    return int(matches[-1]) if matches else None


def parse_counts(framework: str, output: str) -> TestCounts:
    """Extract pass/fail counts from common runners' summary lines."""
    if framework in {"pytest", "unittest"}:
        if framework == "unittest" and (ran := _count(r"Ran (\d+) tests?", output)) is not None:
            failed = _count(r"failures=(\d+)", output) or 0
            errors = _count(r"errors=(\d+)", output) or 0
            return TestCounts(ran - failed - errors, failed, errors, _count(r"skipped=(\d+)", output))
        return TestCounts(
            _count(r"(\d+) passed", output), _count(r"(\d+) failed", output),
            _count(r"(\d+) errors?\b", output), _count(r"(\d+) skipped", output),
        )
    if framework in {"jest", "vitest"}:
        line = next((l for l in reversed(output.splitlines()) if re.match(r"\s*Tests?\s*[: ]", l)), "")
        return TestCounts(_count(r"(\d+) passed", line), _count(r"(\d+) failed", line), None, _count(r"(\d+) skipped", line))
    if framework == "go":
        return TestCounts(len(re.findall(r"^--- PASS", output, re.M)) or None, len(re.findall(r"^--- FAIL", output, re.M)))
    return TestCounts(_count(r"(\d+) passed", output), _count(r"(\d+) failed", output))


class TestRunner:
    __test__ = False

    def __init__(self, commands: dict[str, list[str]], timeout_seconds: int, enabled: bool) -> None:
        self._commands = commands
        self._timeout = timeout_seconds
        self._enabled = enabled

    def build_command(
        self, framework: str, root: Path, target: str | None, interpreter_root: Path | None = None
    ) -> list[str]:
        if not self._enabled:
            raise TestExecutionError("Test execution is disabled (APP_TEST_EXECUTION_ENABLED=false)")
        template = self._commands.get(framework)
        if not template:
            raise TestExecutionError(f"No test command configured for '{framework}'")
        python = repository_python(interpreter_root or root)
        command = [python if part == "{python}" else part for part in template]

        executable = command[0]
        resolved = executable if Path(executable).is_absolute() else (
            str(root / executable) if (root / executable).is_file() else shutil.which(executable)
        )
        if resolved is None:
            raise TestExecutionError(f"'{executable}' is not installed or not on PATH")
        command[0] = resolved
        if target:
            command.append(target)  # already validated as an indexed repository path by the caller
        return command

    def run(
        self,
        framework: str,
        root: Path,
        target: str | None = None,
        *,
        interpreter_root: Path | None = None,
        env: dict[str, str] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> ProcessResult:
        """Run the configured command in ``root``.

        ``interpreter_root``: where to look for the virtualenv (a sandbox copy has none of its own).
        ``env``: replaces the default minimal environment. ``should_stop``: polled twice a second;
        when it returns True the process is killed (the developer stopped the session).
        """
        command = self.build_command(framework, root, target, interpreter_root)
        started = time.perf_counter()
        deadline = started + self._timeout
        timed_out = stopped = False
        with subprocess.Popen(
            command, cwd=root, env=env if env is not None else minimal_env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False,
        ) as process:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if should_stop is not None and should_stop():
                        stopped = True
                    elif time.perf_counter() >= deadline:
                        timed_out = True
                    else:
                        continue
                    process.kill()
                    stdout, stderr = process.communicate()
                    break

        output = (stdout or "") + (stderr or "")
        exit_code = None if timed_out or stopped else process.returncode
        if timed_out:
            output += f"\n[stopped after {self._timeout} seconds]"
        if stopped:
            output += "\n[stopped: the session was cancelled]"
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:2_000] + "\n... (output truncated) ...\n" + output[-(MAX_OUTPUT_CHARS - 2_000):]
        return ProcessResult(
            command=command, exit_code=exit_code, timed_out=timed_out, output=output,
            duration_ms=round((time.perf_counter() - started) * 1000, 1), counts=parse_counts(framework, output),
            stopped=stopped,
        )
