"""Fix sessions: investigate -> fix -> validate in a sandbox -> retry -> review -> developer approval.

    evidence (deterministic) -> root cause (read-only agent) -> evidence verified?
        no  -> "unverified": no fix is attempted
        yes -> sandbox copy + baseline tests
               attempt 1..N (N <= 3): patch -> checks -> sandbox tests -> compare with baseline
                   new failures -> failure analysis -> next attempt
               -> code review -> "awaiting_approval"
    approve -> a normal ChangeSet: conflict check, backup, Git branch, all-or-nothing write, rollback

The original repository is read, never written, until the developer approves.
"""

import asyncio
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.secrets import redact_text
from app.models import AnalysisFinding, AnalysisRun, FileChange, FixAttempt, FixEvent, FixSession
from app.services.changes.edits import unified_diff
from app.services.changes.patching import generate_file_patch
from app.services.changes.service import ChangeService
from app.services.code.insight import CodeInsightService
from app.services.code.validation import syntax_error
from app.services.fixing.budget import BudgetedLLM
from app.services.fixing.errors import (
    FixSessionNotFoundError,
    FixSessionStateError,
    SessionLimitError,
    SessionStoppedError,
)
from app.services.fixing.evidence import BugContext, collect_context
from app.services.fixing.investigator import Investigation, RootCauseInvestigator
from app.services.fixing.prompts import (
    FAILURE_ANALYSIS_SYSTEM,
    FIX_REQUEST,
    REPRODUCTION_SYSTEM,
    RETRY_FEEDBACK,
    REVIEW_SYSTEM,
)
from app.services.fixing.reports import ReviewReport
from app.services.fixing.sandbox import Sandbox, remove_tree
from app.services.fixing.validation import (
    SANDBOX_FRAMEWORKS,
    CandidateFile,
    Check,
    TestOutcome,
    changed_lines,
    check_candidate,
    compare_runs,
)
from app.services.llm_service import ChatMessage, LLMError, LLMService
from app.services.repository.errors import RepositoryError
from app.services.repository.manager import RepositoryManager
from app.services.repository.path_guard import resolve_inside
from app.services.repository.writer import read_text_or_none, sha256_text
from app.services.testing.frameworks import is_test_file
from app.services.testing.runner import TestExecutionError, TestRunner
from app.services.testing.service import TestingService, add_missing_imports, parse_generated_test

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"queued", "investigating", "fixing", "validating", "reviewing"})
REJECTABLE_STATUSES = frozenset({"awaiting_approval", "failed", "unverified"})
_DEFINITION_LINE = re.compile(r"^-\s*(?:async\s+)?(?:def|function|func|fn|class)\s+(\w+)|^-\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=")


def _now() -> datetime:
    return datetime.now(UTC)


async def _in_thread[T](function: Callable[[threading.Event], T]) -> T:
    """Run blocking work in a thread. If the session is cancelled meanwhile, tell the work to stop
    (``kill`` event) and wait for it, so no test process outlives the session or locks the sandbox."""
    kill = threading.Event()
    task = asyncio.ensure_future(asyncio.to_thread(function, kill))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        kill.set()
        await asyncio.wait({task})
        raise


def mark_interrupted_sessions(session: Session, data_path: Path) -> int:
    """At startup: sessions that were running when the server stopped cannot resume."""
    stale = session.scalars(select(FixSession).where(FixSession.status.in_(ACTIVE_STATUSES))).all()
    for fs in stale:
        fs.status, fs.finished_at = "interrupted", _now()
        fs.error = "The server stopped while this session was running; start a new session."
        session.add(FixEvent(session_id=fs.id, stage="interrupted", message=fs.error, llm_calls=fs.llm_calls))
    session.commit()
    remove_tree(data_path / "sandboxes")
    return len(stale)


class FixSessionService:
    def __init__(
        self,
        llm: LLMService,
        repositories: RepositoryManager,
        insight: CodeInsightService,
        testing: TestingService,
        changes: ChangeService,
        session: Session,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._repositories = repositories
        self._insight = insight
        self._testing = testing
        self._changes = changes
        self._session = session
        self._settings = settings
        self._runner = TestRunner(settings.test_commands, settings.test_timeout_seconds, settings.test_execution_enabled)
        self._budget: BudgetedLLM | None = None
        self._sandbox: Sandbox | None = None
        self._root: Path | None = None

    # ----- create / read ---------------------------------------------------------------------------

    def create(
        self,
        repository_id: int,
        *,
        finding_id: int | None = None,
        description: str | None = None,
        path: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        run_tests: bool = True,
        reproduction_test: bool = False,
    ) -> FixSession:
        self._repositories.get(repository_id)
        if finding_id is not None:
            finding = self._session.get(AnalysisFinding, finding_id)
            run = self._session.get(AnalysisRun, finding.run_id) if finding else None
            if finding is None or run is None:
                raise FixSessionNotFoundError(f"Finding {finding_id} not found")
            if run.repository_id != repository_id:
                raise FixSessionStateError(f"Finding {finding_id} belongs to another repository")
            path = path or run.path
            line_start, line_end = line_start or finding.line_start, line_end or finding.line_end
            description = description or f"{finding.title}. {finding.explanation} Suggested fix: {finding.suggestion}"
        if not description or not path:
            raise FixSessionStateError("Give a finding_id, or a description and a file path")
        file = self._repositories.read_file(repository_id, path, 1, 1)  # inside the repo, indexed, not a secret

        fs = FixSession(
            repository_id=repository_id, finding_id=finding_id, description=description[:4000], path=file.path,
            line_start=line_start, line_end=line_end, status="queued", validation_note="",
            options={"run_tests": run_tests, "reproduction_test": reproduction_test},
        )
        self._session.add(fs)
        self._session.commit()
        self._event(fs, "queued", f"Session created for {file.path}" + (f" (finding #{finding_id})" if finding_id else ""))
        return fs

    def get(self, session_id: int) -> FixSession:
        fs = self._session.get(FixSession, session_id)
        if fs is None:
            raise FixSessionNotFoundError(f"Fix session {session_id} not found")
        return fs

    def reload(self, session_id: int) -> FixSession:
        """Re-read a session that another database session (the background task) has changed."""
        self._session.expire_all()
        return self.get(session_id)

    def list(self, repository_id: int, limit: int = 50) -> list[FixSession]:
        return list(self._session.scalars(
            select(FixSession).where(FixSession.repository_id == repository_id).order_by(FixSession.id.desc()).limit(limit)
        ).all())

    def events(self, session_id: int, after_id: int = 0) -> list[FixEvent]:
        self.get(session_id)
        return list(self._session.scalars(
            select(FixEvent).where(FixEvent.session_id == session_id, FixEvent.id > after_id).order_by(FixEvent.id)
        ).all())

    # ----- the session ------------------------------------------------------------------------------

    async def run(self, session_id: int, stop: threading.Event) -> FixSession:
        """Run a session to a resting state. Never raises: every outcome is recorded on the session."""
        fs = self.get(session_id)
        self._budget = BudgetedLLM(self._llm, self._settings.fix_max_llm_calls, stop)
        self._root = Path(self._repositories.get(fs.repository_id).root_path)
        try:
            async with asyncio.timeout(self._settings.fix_session_timeout_seconds):
                await self._pipeline(fs, stop)
        except (SessionStoppedError, asyncio.CancelledError):
            self._session.rollback()
            if stop.is_set():
                self._finish(fs, "cancelled", "Stopped by the developer. Nothing was written to the repository.")
            else:
                self._finish(fs, "interrupted", "The server stopped while the session was running.")
        except TimeoutError:
            self._session.rollback()
            self._finish(fs, "failed", f"Session time limit reached ({self._settings.fix_session_timeout_seconds} s).")
        except SessionLimitError as exc:
            self._session.rollback()
            self._finish(fs, "failed", str(exc))
        except (LLMError, RepositoryError) as exc:
            self._session.rollback()
            self._finish(fs, "failed", f"{type(exc).__name__}: {exc}")
        except Exception:
            logger.exception("Fix session %s failed unexpectedly", fs.id)
            self._session.rollback()
            self._finish(fs, "failed", "Unexpected error; see the server log.")
        finally:
            if self._sandbox is not None:
                try:
                    self._sandbox.remove()
                    self._sandbox = None
                except OSError:
                    logger.warning("Could not remove sandbox %s", self._sandbox.base, exc_info=True)
            self._record_usage(fs)
            self._session.commit()
        return fs

    async def _pipeline(self, fs: FixSession, stop: threading.Event) -> None:
        assert self._budget is not None and self._root is not None
        settings = self._settings

        # 1. Evidence, without the model.
        self._set_status(fs, "investigating")
        started = time.perf_counter()
        context = collect_context(self._repositories, self._insight, fs.repository_id, fs.path, fs.line_start, fs.line_end)
        fs.context = context.to_dict()
        where = f"function {context.symbol}" if context.symbol else f"lines {context.code_start}-{context.code_end}"
        self._event(fs, "evidence", (
            f"Collected evidence around {where}: {len(context.callers)} caller(s), "
            f"{len(context.imported_by)} importing file(s), {len(context.related_tests)} related test file(s)"
        ), started=started)

        # 2. Root cause, read-only agent; evidence verified against the real files.
        started = time.perf_counter()
        investigator = RootCauseInvestigator(
            self._budget, self._repositories, self._insight,
            max_steps=settings.fix_investigation_max_steps, max_files=settings.change_max_files,
        )
        investigation = await investigator.investigate(fs.repository_id, fs.description, context)
        fs.root_cause = investigation.root_cause_dict()
        verification = investigation.verification
        self._event(fs, "investigation", (
            f"Root cause: {investigation.report.summary[:300]} ({len(verification.verified)} of "
            f"{len(verification.verified) + len(verification.rejected)} evidence line(s) verified, "
            f"confidence {investigation.confidence})"
        ), data={"tools": [
            {"tool": s.tool_name, "arguments": s.arguments, "error": s.is_error, "ms": s.duration_ms}
            for s in investigation.steps
        ]}, started=started)
        if not investigation.verified:
            reason = (
                "The investigation concluded that the code is correct" if not investigation.report.is_real_bug
                else "None of the evidence the model cited could be found in the code"
            )
            self._finish(fs, "unverified", f"{reason}; no fix was attempted.")
            return

        # 3. Snapshot the files to change (read only; approval later checks they did not change).
        originals = self._snapshot(fs.repository_id, investigation.files_to_change)

        # 4. Sandbox and baseline.
        framework, not_validated_reason = self._framework(fs)
        baseline: TestOutcome | None = None
        if framework:
            self._set_status(fs, "validating")
            started = time.perf_counter()
            self._sandbox = await _in_thread(lambda _kill: Sandbox.create(
                self._root, settings.data_path / "sandboxes" / str(fs.id),
                max_files=settings.fix_sandbox_max_files, max_bytes=settings.fix_sandbox_max_bytes,
                exclude=[settings.data_path],
            ))
            skipped = ", ".join(f"{n} {k}" for k, n in sorted(self._sandbox.skipped.items())) or "nothing"
            self._event(fs, "sandbox", f"Copied {self._sandbox.files} files to an isolated sandbox (skipped: {skipped})", started=started)

            started = time.perf_counter()
            baseline = await self._run_tests(framework, stop)
            fs.baseline = baseline.to_dict()
            failing = f": {', '.join(baseline.failed_ids[:10])}" if baseline.failed_ids else ""
            self._event(fs, "baseline", (
                f"Baseline before any change: {baseline.passed or 0} passed, {baseline.bad} failing{failing}. "
                "These failures are not blamed on the fix."
            ), started=started)
            if fs.options.get("reproduction_test") and framework == "pytest":
                await self._reproduction_test(fs, context, investigation, framework, stop)
            if not baseline.ran_tests and not (fs.reproduction or {}).get("kept"):
                framework, not_validated_reason = None, "no tests were found or none could run in the sandbox"
        fs.validation_note = f"Not validated: {not_validated_reason}." if not framework else ""

        # 5. Attempts. Tests that exercise the buggy code must be fixed, not just left unbroken.
        bug_test_files = set(context.related_tests) | {c.path for c in context.callers if is_test_file(c.path)}
        if (fs.reproduction or {}).get("kept"):
            bug_test_files.add(fs.reproduction["path"])
        final = await self._attempts(fs, investigation, originals, framework, baseline, bug_test_files, stop)
        if final is None:
            fs.validation_status = "failed"
            self._finish(fs, "failed", f"No acceptable patch after {len(fs.attempts)} attempt(s). Nothing was written to the repository.")
            return
        fs.final_attempt = final.number
        fs.validation_status = "passed" if final.status == "passed" else "not_validated"
        if final.status == "passed":
            fs.validation_note = f"Validated in the sandbox: {final.comparison['reason']}."

        # 6. Review.
        self._set_status(fs, "reviewing")
        started = time.perf_counter()
        fs.review = await self._review(fs, context, final)
        self._event(fs, "review", f"Code review: {fs.review['verdict']}. {fs.review['summary'][:300]}", started=started)

        self._set_status(fs, "awaiting_approval")
        self._event(fs, "ready", "Ready for your review. Nothing has been written to the repository.")

    async def _attempts(
        self,
        fs: FixSession,
        investigation: Investigation,
        originals: dict[str, str],
        framework: str | None,
        baseline: TestOutcome | None,
        bug_test_files: set[str],
        stop: threading.Event,
    ) -> FixAttempt | None:
        assert self._budget is not None
        report = investigation.report
        evidence = "\n".join(f"- {e.path}:{e.line}: {e.quote} ({e.explanation})" for e in investigation.verification.verified)
        base_request = FIX_REQUEST.format(
            description=fs.description, root_cause=f"{report.summary} (at {report.root_cause_path}:{report.root_cause_line})",
            evidence=evidence, strategy=report.fix_strategy,
        )
        paths = list(originals)
        seen: dict[str, int] = {}
        feedback = ""
        for number in range(1, self._settings.fix_max_attempts + 1):
            self._set_status(fs, "fixing")
            started = time.perf_counter()
            candidates, explanations = [], []
            for path in paths:
                patch = await generate_file_patch(
                    self._budget, base_request + feedback, path, originals[path], [p for p in paths if p != path]
                )
                candidates.append(self._candidate(path, originals[path], patch.new_content, patch.notes))
                if patch.explanation:
                    explanations.append(f"{path}: {patch.explanation}")

            checks = check_candidate(candidates, set(paths), self._settings.fix_max_changed_lines)
            changed = [c for c in candidates if c.diff]
            signature = hashlib.sha256("\0".join(f"{c.path}\0{c.new_content}" for c in changed).encode()).hexdigest()
            repeated = changed and signature in seen
            if repeated:
                checks.append(Check("progress", False, f"identical to attempt {seen[signature]}"))
            elif changed:
                seen[signature] = number

            attempt = FixAttempt(
                session_id=fs.id, number=number, status="", explanation="\n".join(explanations) or "No explanation given.",
                files=[self._file_record(c) for c in candidates], checks=[asdict(c) for c in checks],
            )
            diff = "\n".join(c.diff for c in changed)
            failed_checks = [c for c in checks if not c.ok]
            if failed_checks:
                attempt.status = "rejected"
                reason = "; ".join(c.detail for c in failed_checks)
                self._save_attempt(fs, attempt, started, f"Attempt {number} rejected before testing: {reason}")
                if repeated:
                    self._event(fs, "attempt", "The model repeated an earlier patch; stopping instead of looping.")
                    return None
                feedback = RETRY_FEEDBACK.format(diff=diff[:3000] or "(no applicable change)", reason=reason[:2000])
                continue

            if framework is None:
                attempt.status = "not_validated"
                self._save_attempt(fs, attempt, started, f"Attempt {number}: patch passed all checks; {fs.validation_note}")
                return attempt

            self._set_status(fs, "validating")
            assert self._sandbox is not None and baseline is not None
            for c in changed:
                self._sandbox.write(c.path, c.new_content)
            after = await self._run_tests(framework, stop)
            comparison = compare_runs(baseline, after, bug_test_files)
            attempt.test, attempt.comparison = after.to_dict(), comparison.to_dict()
            if comparison.ok:
                attempt.status = "passed"
                self._save_attempt(fs, attempt, started, f"Attempt {number} passed: {comparison.reason}")
                return attempt

            attempt.status = "failed"
            for c in changed:  # back to the unpatched copy for the next attempt
                self._sandbox.write(c.path, c.original)
            if number < self._settings.fix_max_attempts:
                attempt.failure_analysis = await self._analyse_failure(diff, after, comparison.reason)
            self._save_attempt(fs, attempt, started, f"Attempt {number} failed: {comparison.reason}")
            feedback = RETRY_FEEDBACK.format(
                diff=diff[:3000], reason=f"{comparison.reason}\n{attempt.failure_analysis or ''}"[:2000]
            )
        return None

    # ----- stages ----------------------------------------------------------------------------------

    def _snapshot(self, repository_id: int, paths: list[str]) -> dict[str, str]:
        assert self._root is not None
        originals: dict[str, str] = {}
        for path in paths:
            self._repositories.read_file(repository_id, path, 1, 1)  # same rules as reading
            text = read_text_or_none(resolve_inside(self._root, path))
            if text is None:
                raise FixSessionStateError(f"{path} no longer exists")
            if text.count("\n") > self._settings.change_max_file_lines:
                raise FixSessionStateError(f"{path} is longer than {self._settings.change_max_file_lines} lines")
            originals[path] = text
        return originals

    def _framework(self, fs: FixSession) -> tuple[str | None, str]:
        if not fs.options.get("run_tests", True):
            return None, "test execution was turned off for this session"
        if not self._settings.test_execution_enabled:
            return None, "test execution is disabled on this server"
        try:
            language = self._repositories.read_file(fs.repository_id, fs.path, 1, 1).language
            framework = self._testing.choose_framework(fs.repository_id, language, None)
        except TestExecutionError as exc:
            return None, str(exc)
        if framework not in SANDBOX_FRAMEWORKS:
            return None, (
                f"{framework} tests cannot run safely in the sandbox (their dependency folders, such as "
                "node_modules, are not copied); review the patch and run the tests yourself"
            )
        return framework, ""

    async def _run_tests(self, framework: str, stop: threading.Event, target: str | None = None) -> TestOutcome:
        sandbox, root = self._sandbox, self._root
        assert sandbox is not None and root is not None
        result = await _in_thread(lambda kill: self._runner.run(
            framework, sandbox.root, target, interpreter_root=root, env=sandbox.env(),
            should_stop=lambda: stop.is_set() or kill.is_set(),
        ))
        if result.stopped:
            raise SessionStoppedError("The session was stopped by the developer")
        return TestOutcome.from_process(framework, result, redact_text)

    async def _reproduction_test(
        self, fs: FixSession, context: BugContext, investigation: Investigation, framework: str, stop: threading.Event
    ) -> None:
        """Optional: a test that must fail now and pass after the fix. It lives only in the sandbox."""
        assert self._budget is not None and self._sandbox is not None
        started = time.perf_counter()
        folder = str(PurePosixPath(context.related_tests[0]).parent) if context.related_tests else "tests"
        test_path = f"{folder}/test_fix_session_{fs.id}_reproduction.py".lstrip("./")
        example = ""
        if context.related_tests:
            example_text = self._repositories.read_file(fs.repository_id, context.related_tests[0], 1, 40).content
            example = f"\n\nExisting test ({context.related_tests[0]}):\n{redact_text(example_text)}"
        reply = await self._budget.chat([
            ChatMessage("system", REPRODUCTION_SYSTEM),
            ChatMessage("user", f"Bug: {fs.description}\nRoot cause: {investigation.report.summary}\n\n{context.to_prompt()}{example}"),
        ])
        content = add_missing_imports(framework, parse_generated_test(reply.content).content)
        record: dict[str, Any] = {"path": test_path, "content": content, "kept": False}
        error = syntax_error(test_path, content)
        if error:
            record["note"] = f"Discarded: {error}"
        else:
            self._sandbox.write(test_path, content)
            outcome = await self._run_tests(framework, stop, target=test_path)
            if (outcome.failed or 0) > 0:
                record.update(kept=True, note="Fails on the current code, as expected; it must pass after the fix.")
            else:
                self._sandbox.delete(test_path)
                record["note"] = (
                    "Discarded: it passes on the current code, so it does not reproduce the bug."
                    if not outcome.errors else "Discarded: the test itself does not run (import or collection error)."
                )
        fs.reproduction = record
        self._event(fs, "reproduction", f"Reproduction test {test_path}: {record['note']}", started=started)

    async def _analyse_failure(self, diff: str, after: TestOutcome, reason: str) -> str:
        assert self._budget is not None
        prompt = f"Change:\n{diff[:3000]}\n\nResult: {reason}\n\nTest output (end):\n{after.output[-3000:]}"
        result = await self._budget.chat([ChatMessage("system", FAILURE_ANALYSIS_SYSTEM), ChatMessage("user", prompt)])
        return result.content.strip()[:2000]

    async def _review(self, fs: FixSession, context: BugContext, final: FixAttempt) -> dict:
        assert self._budget is not None
        diff = "\n".join(f["diff"] for f in final.files if f["diff"])
        automated = [asdict(c) for c in self._automated_review(diff, context)]
        prompt = (
            f"Bug: {fs.description}\nRoot cause: {(fs.root_cause or {}).get('summary', '')}\n"
            f"Validation: {fs.validation_status} {fs.validation_note}\n\nDiff:\n{diff[:6000]}"
        )
        try:
            report, _ = await self._budget.generate_structured(REVIEW_SYSTEM, prompt, ReviewReport)
        except SessionLimitError:
            return {"verdict": "skipped", "summary": "The LLM call limit was reached before the review.", "issues": [], "automated": automated}
        return {
            "verdict": report.verdict, "summary": report.summary[:2000],
            "issues": [i.model_dump() for i in report.issues[:10]], "automated": automated,
        }

    @staticmethod
    def _automated_review(diff: str, context: BugContext) -> list[Check]:
        removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        changed_definitions = sorted({m.group(1) or m.group(2) for line in diff.splitlines() if (m := _DEFINITION_LINE.match(line))})
        callers = ", ".join(f"{c.path}:{c.line}" for c in context.callers[:8])
        return [
            Check("size", True, f"{changed_lines(diff)} changed lines, {removed} removed"),
            Check("signatures", not changed_definitions,
                  "no definition lines changed" if not changed_definitions
                  else f"definition line changed for {', '.join(changed_definitions)}; check its callers ({callers or 'none found'})"),
        ]

    # ----- developer decisions --------------------------------------------------------------------

    def approve(self, session_id: int, create_branch: bool = True) -> FixSession:
        """Apply the final patch through the regular change pipeline (conflict check, backup, branch)."""
        fs = self._require(session_id, {"awaiting_approval"})
        attempt = next(a for a in fs.attempts if a.number == fs.final_attempt)
        change = self._changes.get(fs.change_set_id) if fs.change_set_id else None
        if change is None or change.status != "proposed":
            files = [
                FileChange(
                    path=f["path"], is_new=False, original_sha256=f["original_sha256"], new_sha256=f["new_sha256"],
                    new_content=f["new_content"], diff=f["diff"], syntax_ok=f["syntax_ok"],
                    notes=f"From fix session #{fs.id}, attempt {attempt.number}; validation: {fs.validation_status}. {f['notes']}".strip(),
                )
                for f in attempt.files if f["diff"]
            ]
            summary = (
                f"Fix session #{fs.id}. Root cause: {(fs.root_cause or {}).get('summary', '')}\n{attempt.explanation}\n"
                f"Validation: {fs.validation_status}. {fs.validation_note}"
            )
            change = self._changes.create_reviewed(
                fs.repository_id, f"Fix session #{fs.id}: {fs.description[:300]}", summary, fs.model or "unknown", files
            )
            fs.change_set_id = change.id
            self._session.commit()

        change = self._changes.approve(change.id, create_branch)  # raises on conflicts; session stays reviewable
        fs = self.get(session_id)
        fs.status, fs.finished_at = "applied", _now()
        branch = f" on branch {change.branch_name}" if change.branch_name else " (no Git branch; backups kept)"
        self._event(fs, "applied", f"Approved by the developer and applied as change #{change.id}{branch}.")
        return fs

    def reject(self, session_id: int) -> FixSession:
        fs = self._require(session_id, set(REJECTABLE_STATUSES))
        fs.status, fs.finished_at = "rejected", _now()
        self._event(fs, "rejected", "Rejected by the developer. Nothing was written to the repository.")
        return fs

    def mark_cancelled(self, session_id: int) -> FixSession:
        """For a session that is active in the database but has no running task (stale)."""
        fs = self._require(session_id, set(ACTIVE_STATUSES))
        self._finish(fs, "cancelled", "Stopped by the developer.")
        return fs

    # ----- helpers ---------------------------------------------------------------------------------

    def _require(self, session_id: int, allowed: set[str]) -> FixSession:
        fs = self.get(session_id)
        if fs.status not in allowed:
            raise FixSessionStateError(f"Fix session {session_id} is {fs.status}")
        return fs

    @staticmethod
    def _candidate(path: str, original: str, new_content: str, notes: list[str]) -> CandidateFile:
        diff = unified_diff(path, original, new_content) if new_content != original else ""
        error = syntax_error(path, new_content) if diff else None
        return CandidateFile(path, original, new_content, diff, error is None, "\n".join(notes + ([error] if error else [])))

    @staticmethod
    def _file_record(c: CandidateFile) -> dict:
        return {
            "path": c.path, "original_sha256": sha256_text(c.original), "new_sha256": sha256_text(c.new_content),
            "new_content": c.new_content, "diff": c.diff, "syntax_ok": c.syntax_ok, "notes": c.notes,
        }

    def _save_attempt(self, fs: FixSession, attempt: FixAttempt, started: float, message: str) -> None:
        attempt.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        fs.attempts.append(attempt)
        self._event(fs, "attempt", message, data={"attempt": attempt.number, "status": attempt.status}, started=started)

    def _set_status(self, fs: FixSession, status: str) -> None:
        fs.status = status
        self._record_usage(fs)
        self._session.commit()

    def _finish(self, fs: FixSession, status: str, message: str) -> None:
        fs.status, fs.finished_at = status, _now()
        if status not in {"applied", "rejected"}:
            fs.error = message
        self._event(fs, status, message)

    def _record_usage(self, fs: FixSession) -> None:
        if self._budget is not None:
            fs.llm_calls, fs.model = self._budget.calls, self._budget.model or fs.model
            fs.prompt_tokens, fs.completion_tokens = self._budget.prompt_tokens, self._budget.completion_tokens

    def _event(self, fs: FixSession, stage: str, message: str, data: dict | None = None, started: float | None = None) -> None:
        self._record_usage(fs)
        self._session.add(FixEvent(
            session_id=fs.id, stage=stage, message=message[:4000], data=data,
            duration_ms=round((time.perf_counter() - started) * 1000, 1) if started is not None else None,
            llm_calls=fs.llm_calls,
        ))
        self._session.commit()
        logger.info("Fix session %s [%s] %s", fs.id, stage, message[:300])
