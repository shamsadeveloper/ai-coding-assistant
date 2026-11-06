"""Testing service: framework detection, coverage gaps, test generation with review/approval, and test runs."""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.secrets import redact_text
from app.models import GeneratedTest, TestRun
from app.services.code.insight import CodeInsightService, SymbolNotFoundError
from app.services.code.syntax import parse_outline
from app.services.code.validation import syntax_error
from app.services.llm_service import ChatMessage, LLMService
from app.services.repository.errors import RepositoryError
from app.services.repository.manager import RepositoryManager
from app.services.repository.writer import SafeFileWriter
from app.services.testing.coverage import CoverageGap, find_coverage_gaps
from app.services.testing.frameworks import (
    TestFramework,
    detect_test_frameworks,
    import_hint,
    is_test_file,
    proposed_test_path,
)
from app.services.testing.runner import TestExecutionError, TestRunner

logger = logging.getLogger(__name__)

FRAMEWORKS_BY_LANGUAGE = {
    "Python": ["pytest", "unittest"],
    "JavaScript": ["vitest", "jest", "mocha"],
    "TypeScript": ["vitest", "jest", "mocha"],
    "Go": ["go"],
    "Rust": ["cargo"],
    "PHP": ["phpunit"],
    "Java": ["maven", "gradle"],
    "Kotlin": ["gradle", "maven"],
    "C#": ["dotnet"],
}
EXAMPLE_TEST_LINES = 60
ANALYSIS_OUTPUT_CHARS = 6_000

# Plain text, not a JSON schema: with long code inside a JSON string, constrained decoding made
# qwen2.5-coder loop until Ollama aborted ("token repeat limit reached").
GENERATE_SYSTEM = (
    "You write high-quality unit tests. Write one complete, runnable test file for the given framework.\n"
    "- Include every import the tests need (for pytest: `import pytest` when using pytest.raises).\n"
    "- Cover normal behaviour, edge cases (empty, zero, None/null, boundaries) and error cases.\n"
    "- Compute expected values carefully; test only behaviour visible in the code.\n"
    "- Mock external dependencies (network, database, filesystem, time). Never call real services.\n"
    "- Follow the style of the existing test example when one is given.\n"
    "Reply in exactly this format:\n"
    "```<language>\n<complete test file>\n```\n"
    "TEST CASES:\n- <one line per test>\n"
    "NOTES:\n<assumptions or limitations>"
)

ANALYZE_SYSTEM = (
    "You analyse test-run output for a developer. For each failure or error: name the test, explain the "
    "most likely root cause, point to the code involved as path:line when the output shows it, and suggest "
    "a fix. Distinguish a bug in the code under test from a bug in the test itself. If everything passed, "
    "say so briefly."
)


@dataclass(frozen=True)
class GeneratedTestFile:
    content: str
    test_cases: list[str]
    notes: str


def parse_generated_test(reply: str) -> GeneratedTestFile:
    """Split the model's reply into code, test-case list and notes.

    Tolerates the usual deviations: no closing fence, sections inside the fence, no fence at all.
    """
    lines = reply.strip().splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip().startswith("```")), 0)
    code, rest = [], []
    for index in range(start, len(lines)):
        line = lines[index]
        if line.strip().startswith("```") or line.strip().upper().startswith(("TEST CASES:", "NOTES:")):
            rest = lines[index:]
            break
        code.append(line)

    cases, notes, section = [], [], None
    for line in rest:
        stripped = line.strip()
        if stripped.upper().startswith("TEST CASES:"):
            section = "cases"
        elif stripped.upper().startswith("NOTES:"):
            section = "notes"
            notes.append(stripped[len("NOTES:"):].strip())
        elif stripped.startswith("```"):
            continue
        elif section == "cases" and stripped.startswith(("-", "*")):
            cases.append(stripped.lstrip("-* ").strip())
        elif section == "notes":
            notes.append(stripped)
    return GeneratedTestFile("\n".join(code).strip() + "\n", cases, "\n".join(n for n in notes if n).strip())


def add_missing_imports(framework: str, content: str) -> str:
    """Deterministic fix for the most common omission in generated Python tests."""
    if framework == "pytest" and "pytest." in content and not re.search(r"^\s*import pytest\b", content, re.M):
        return "import pytest\n" + content
    return content


class TestingNotFoundError(RepositoryError):
    """No generated test or test run with this id."""

    __test__ = False


class InvalidStateError(RepositoryError):
    """The generated test was already applied or rejected."""


@dataclass(frozen=True)
class SourceTarget:
    path: str
    language: str | None
    code: str
    symbol: str | None


class TestingService:
    __test__ = False

    def __init__(
        self,
        llm: LLMService,
        repositories: RepositoryManager,
        insight: CodeInsightService,
        session: Session,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._repositories = repositories
        self._insight = insight
        self._session = session
        self._settings = settings
        self._runner = TestRunner(settings.test_commands, settings.test_timeout_seconds, settings.test_execution_enabled)

    # ----- detection -------------------------------------------------------------------------

    def frameworks(self, repository_id: int) -> list[TestFramework]:
        repository = self._repositories.get(repository_id)
        _, files = self._repositories.list_files(repository_id, limit=100_000)
        return detect_test_frameworks(Path(repository.root_path), [f.path for f in files])

    def gaps(self, repository_id: int, limit: int = 100) -> list[CoverageGap]:
        files = ((r.path, r.language, text) for r, text in self._repositories.iter_texts(repository_id))
        return find_coverage_gaps(files, limit)

    def choose_framework(self, repository_id: int, language: str | None, requested: str | None) -> str:
        if requested:
            if requested not in self._settings.test_commands:
                raise TestExecutionError(f"Unknown framework '{requested}'. Known: {', '.join(self._settings.test_commands)}")
            return requested
        detected = [f.name for f in self.frameworks(repository_id)]
        candidates = FRAMEWORKS_BY_LANGUAGE.get(language or "", [])
        for name in candidates:
            if name in detected:
                return name
        if candidates:
            return candidates[0]
        if detected:
            return detected[0]
        raise TestExecutionError("No test framework detected; pass 'framework' explicitly")

    # ----- generation ------------------------------------------------------------------------

    async def generate(
        self, repository_id: int, path: str, symbol: str | None = None, framework: str | None = None
    ) -> GeneratedTest:
        """Generate a test file into the review area. Nothing is written to the repository."""
        target = self._source_target(repository_id, path, symbol)
        chosen = self.choose_framework(repository_id, target.language, framework)
        test_path = proposed_test_path(chosen, target.path)
        prompt = self._generation_prompt(repository_id, target, chosen, test_path)

        result, model = await self._generate_file(prompt, chosen)
        error = syntax_error(test_path, result.content, target.language)
        if error:
            # One repair round: show the model its own error.
            logger.info("Generated test has a syntax error (%s); asking the model to repair it", error)
            repair = f"{prompt}\n\nYour previous answer had this problem: {error}\n\nPrevious test file:\n{result.content}"
            result, model = await self._generate_file(repair, chosen)
            error = syntax_error(test_path, result.content, target.language)
        content = result.content

        repository = self._repositories.get(repository_id)
        record = GeneratedTest(
            repository_id=repository_id, source_path=target.path, symbol=symbol, framework=chosen,
            target_path=test_path, content=content, test_cases=result.test_cases,
            notes=result.notes + (f"\nWarning: {error}" if error else ""), syntax_ok=error is None,
            target_exists=(Path(repository.root_path) / test_path).exists(), review_path="", model=model,
        )
        self._session.add(record)
        self._session.flush()

        review_file = self._settings.data_path / "review" / "generated_tests" / str(record.id) / test_path
        review_file.parent.mkdir(parents=True, exist_ok=True)
        review_file.write_text(content, encoding="utf-8")
        record.review_path = str(review_file)
        self._session.commit()
        return record

    def list_generated(self, repository_id: int) -> Sequence[GeneratedTest]:
        return self._session.scalars(
            select(GeneratedTest).where(GeneratedTest.repository_id == repository_id).order_by(GeneratedTest.id.desc())
        ).all()

    def get_generated(self, generated_id: int) -> GeneratedTest:
        record = self._session.get(GeneratedTest, generated_id)
        if record is None:
            raise TestingNotFoundError(f"Generated test {generated_id} not found")
        return record

    def approve(self, generated_id: int, overwrite: bool = False) -> GeneratedTest:
        """Developer approval: copy the reviewed test into the repository."""
        record = self._pending(generated_id)
        root = Path(self._repositories.get(record.repository_id).root_path)
        writer = SafeFileWriter(root, self._settings.data_path / "backups" / "generated_tests" / str(record.id))
        result = writer.write(record.target_path, record.content, expected_sha256=None, allow_overwrite=overwrite)

        record.status, record.backup_path, record.decided_at = "applied", result.backup_path, datetime.now(UTC)
        self._session.commit()
        self._repositories.rescan(record.repository_id)  # make the new test readable and searchable
        logger.info("Generated test %s applied to %s (overwrite=%s)", record.id, record.target_path, overwrite)
        return record

    def reject(self, generated_id: int) -> GeneratedTest:
        record = self._pending(generated_id)
        record.status, record.decided_at = "rejected", datetime.now(UTC)
        self._session.commit()
        return record

    # ----- execution -------------------------------------------------------------------------

    def run(self, repository_id: int, framework: str | None = None, path: str | None = None) -> TestRun:
        repository = self._repositories.get(repository_id)
        target, language = None, None
        if path:
            indexed = self._repositories.read_file(repository_id, path, 1, 1)  # validates the path
            target, language = indexed.path, indexed.language
        chosen = self.choose_framework(repository_id, language, framework)

        result = self._runner.run(chosen, Path(repository.root_path), target)
        output = redact_text(result.output)  # test output can print credentials from config
        run = TestRun(
            repository_id=repository_id, framework=chosen, command=result.command, target_path=target,
            exit_code=result.exit_code, timed_out=result.timed_out, passed=result.counts.passed,
            failed=result.counts.failed, errors=result.counts.errors, skipped=result.counts.skipped,
            duration_ms=result.duration_ms, output=output,
        )
        self._session.add(run)
        self._session.commit()
        logger.info("Test run %s: %s exit=%s", run.id, " ".join(result.command), result.exit_code)
        return run

    def get_run(self, run_id: int) -> TestRun:
        run = self._session.get(TestRun, run_id)
        if run is None:
            raise TestingNotFoundError(f"Test run {run_id} not found")
        return run

    async def analyze_run(self, run_id: int) -> TestRun:
        run = self.get_run(run_id)
        summary = (
            f"Command: {' '.join(run.command)}\nExit code: {run.exit_code}"
            f"{' (timed out)' if run.timed_out else ''}\n"
            f"Passed: {run.passed}, failed: {run.failed}, errors: {run.errors}, skipped: {run.skipped}\n\n"
            f"Output (last part):\n{run.output[-ANALYSIS_OUTPUT_CHARS:]}"
        )
        result = await self._llm.chat([ChatMessage("system", ANALYZE_SYSTEM), ChatMessage("user", summary)])
        run.analysis = result.content
        self._session.commit()
        return run

    # ----- helpers ---------------------------------------------------------------------------

    async def _generate_file(self, prompt: str, framework: str) -> tuple[GeneratedTestFile, str]:
        reply = await self._llm.chat([ChatMessage("system", GENERATE_SYSTEM), ChatMessage("user", prompt)])
        parsed = parse_generated_test(reply.content)
        fixed = GeneratedTestFile(add_missing_imports(framework, parsed.content), parsed.test_cases, parsed.notes)
        return fixed, reply.model

    def _pending(self, generated_id: int) -> GeneratedTest:
        record = self.get_generated(generated_id)
        if record.status != "pending":
            raise InvalidStateError(f"Generated test {generated_id} is already {record.status}")
        return record

    def _source_target(self, repository_id: int, path: str, symbol: str | None) -> SourceTarget:
        relative_path, language, text = self._insight.read_source(repository_id, path)
        text = redact_text(text)
        if symbol is None:
            return SourceTarget(relative_path, language, text, None)
        found = parse_outline(relative_path, language, text).find(symbol)
        if found is None:
            raise SymbolNotFoundError(f"'{symbol}' not found in {relative_path}")
        lines = text.splitlines()[found.start_line - 1 : found.end_line]
        return SourceTarget(relative_path, language, "\n".join(lines), symbol)

    def _generation_prompt(self, repository_id: int, target: SourceTarget, framework: str, test_path: str) -> str:
        parts = [
            f"Framework: {framework}",
            f"Test file to create: {test_path}",
            import_hint(framework, target.path),
            f"Code under test ({target.path}{', symbol ' + target.symbol if target.symbol else ''}):",
            f"```\n{target.code}\n```",
        ]
        example = self._example_test(repository_id, target.language)
        if example:
            parts += [f"Existing test in this repository, for style ({example[0]}):", f"```\n{example[1]}\n```"]
        return "\n\n".join(parts)

    def _example_test(self, repository_id: int, language: str | None) -> tuple[str, str] | None:
        _, files = self._repositories.list_files(repository_id, language=language, limit=100_000)
        for record in files:
            if is_test_file(record.path) and PurePosixPath(record.path).name != "conftest.py":
                content = self._repositories.read_file(repository_id, record.path, 1, EXAMPLE_TEST_LINES)
                return record.path, redact_text(content.content)
        return None
