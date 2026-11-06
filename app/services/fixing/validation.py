"""Deterministic checks on a candidate patch, and baseline-vs-after test comparison.

A patch must pass every check before it is even tested; a tested patch is judged only on
NEW failures, so failures that already existed before the patch are never blamed on it.
"""

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from app.core.secrets import REDACTED, redact
from app.services.testing.frameworks import is_test_file
from app.services.testing.runner import ProcessResult

# Frameworks whose tests can run safely in the sandbox copy. JS/TS and others need dependency
# folders (node_modules, vendor, ...) that are deliberately not copied: their patches are
# reported as "not validated" instead of being tested against the real repository.
SANDBOX_FRAMEWORKS = {"pytest", "unittest"}
OUTPUT_TAIL_CHARS = 6_000


@dataclass(frozen=True)
class CandidateFile:
    path: str
    original: str
    new_content: str
    diff: str
    syntax_ok: bool
    notes: str


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )


def check_candidate(
    files: Sequence[CandidateFile], allowed_paths: set[str], max_changed_lines: int
) -> list[Check]:
    changed = [f for f in files if f.diff]
    total = sum(changed_lines(f.diff) for f in changed)
    outside = [f.path for f in changed if f.path not in allowed_paths]
    tests = [f.path for f in changed if is_test_file(f.path)]
    broken = [f.path for f in changed if not f.syntax_ok]
    placeholders = [f.path for f in changed if REDACTED in f.new_content and REDACTED not in f.original]
    secrets = [f.path for f in changed if redact(_added_text(f.diff)).count]
    return [
        Check("edits_applied", bool(changed),
              "the model produced an applicable change" if changed else _why_no_change(files)),
        Check("syntax", not broken, "all changed files parse" if not broken else f"syntax errors in {', '.join(broken)}"),
        Check("scope", not outside,
              "only files named by the root cause changed" if not outside else f"changed files outside the root cause: {', '.join(outside)}"),
        Check("no_test_edits", not tests,
              "no test file changed" if not tests else f"test files must not be edited to make tests pass: {', '.join(tests)}"),
        Check("minimal", total <= max_changed_lines, f"{total} changed lines (limit {max_changed_lines})"),
        Check("no_secrets", not placeholders and not secrets,
              "no secrets or masked placeholders written" if not (placeholders or secrets)
              else f"secret-like values in {', '.join(placeholders + secrets)}"),
    ]


def _added_text(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _why_no_change(files: Sequence[CandidateFile]) -> str:
    notes = "; ".join(f.notes for f in files if f.notes)
    return f"no applicable change ({notes})" if notes else "the model produced no change"


# ----- test results -----------------------------------------------------------------------------

def failed_test_ids(framework: str, output: str) -> list[str] | None:
    """Ids of failed/erroring tests, or None when this framework's output is not understood."""
    if framework == "pytest":
        return sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M)))
    if framework == "unittest":
        # Python 3.11+: "FAIL: test_x (pkg.mod.Class.test_x)"; older: "FAIL: test_x (pkg.mod.Class)"
        return sorted({
            where if where.endswith(f".{name}") else f"{where}.{name}"
            for name, where in re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", output, re.M)
        })
    return None


@dataclass
class TestOutcome:
    __test__ = False

    framework: str
    command: list[str]
    exit_code: int | None
    timed_out: bool
    stopped: bool
    passed: int | None
    failed: int | None
    errors: int | None
    skipped: int | None
    duration_ms: float
    failed_ids: list[str] | None
    output: str  # redacted tail

    @classmethod
    def from_process(cls, framework: str, result: ProcessResult, redact_output) -> "TestOutcome":
        output = redact_output(result.output)
        return cls(
            framework=framework, command=result.command, exit_code=result.exit_code, timed_out=result.timed_out,
            stopped=result.stopped, passed=result.counts.passed, failed=result.counts.failed,
            errors=result.counts.errors, skipped=result.counts.skipped, duration_ms=result.duration_ms,
            failed_ids=failed_test_ids(framework, output), output=output[-OUTPUT_TAIL_CHARS:],
        )

    @property
    def bad(self) -> int:
        return (self.failed or 0) + (self.errors or 0)

    @property
    def ran_tests(self) -> bool:
        return bool((self.passed or 0) + self.bad)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comparison:
    ok: bool
    reason: str
    new_failures: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _test_file(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def compare_runs(baseline: TestOutcome, after: TestOutcome, bug_test_files: set[str] | None = None) -> Comparison:
    """Judge a patched run against the unpatched baseline.

    Only NEW failures count against the patch. And when tests related to the bug (``bug_test_files``)
    were failing before, the patch must fix at least one of them: "nothing broke" alone does not
    show that the bug is fixed.
    """
    if after.timed_out:
        return Comparison(False, "the tests timed out after the patch")
    if baseline.failed_ids is not None and after.failed_ids is not None:
        before, now = set(baseline.failed_ids), set(after.failed_ids)
        new, fixed, still = sorted(now - before), sorted(before - now), sorted(now & before)
        if not after.ran_tests and baseline.ran_tests:
            return Comparison(False, "no tests ran after the patch (collection may have broken)", new, fixed, still)
        if new:
            return Comparison(False, f"{len(new)} new failure(s): {', '.join(new[:10])}", new, fixed, still)
        bug_failures = sorted(t for t in before if _test_file(t) in (bug_test_files or set()))
        if bug_failures and not set(bug_failures) & set(fixed):
            return Comparison(
                False, f"the failing test(s) related to the bug still fail: {', '.join(bug_failures[:10])}",
                new, fixed, still,
            )
        reason = "no new failures"
        if fixed:
            reason += f"; fixed {len(fixed)} previously failing test(s)"
        if still:
            reason += f"; {len(still)} test(s) failed before and still fail (baseline failures, not caused by this patch)"
        return Comparison(True, reason, new, fixed, still)

    # Output not understood: compare counts only.
    if after.bad > baseline.bad:
        return Comparison(False, f"failures increased from {baseline.bad} to {after.bad}")
    if baseline.exit_code == 0 and after.exit_code != 0:
        return Comparison(False, f"the test command failed after the patch (exit code {after.exit_code})")
    return Comparison(True, f"failures did not increase ({baseline.bad} before, {after.bad} after)")
