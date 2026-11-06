"""Fix sessions: sandbox, patch checks, baseline comparison, retry loop, limits and stopping.

The LLM is scripted; the tests really run pytest inside the sandbox copy.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import FixSession
from app.services.changes.service import ChangeService
from app.services.code.insight import CodeInsightService
from app.services.fixing.reports import EvidenceClaim, ReviewIssue, ReviewReport, RootCauseReport
from app.services.fixing.runner import FixSessionRunner
from app.services.fixing.sandbox import Sandbox
from app.services.fixing.service import FixSessionService, mark_interrupted_sessions
from app.services.fixing.validation import (
    CandidateFile,
    TestOutcome,
    check_candidate,
    compare_runs,
    failed_test_ids,
)
from app.services.repository.manager import RepositoryManager
from app.services.testing.runner import TestRunner
from app.services.testing.service import TestingService
from tests.conftest import FakeLLMService
from tests.fix_fixtures import PRICING, make_shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def block(search: str, replace: str, explanation: str = "Done.") -> str:
    return f"<<<<<<< SEARCH\n{search}=======\n{replace}>>>>>>> REPLACE\nEXPLANATION: {explanation}"


GOOD_PATCH = block("    total = sum(values)\n", "    if not values:\n        return 0.0\n    total = sum(values)\n", "Guard empty lists.")
BREAKS_AVERAGE = block("    return total / len(values)\n", "    return 0\n", "Always zero.")
STILL_CRASHES = block("    total = sum(values)\n", "    total = sum(values) + 0\n", "No-op.")
WRONG_GUARD = block("    total = sum(values)\n", "    if values is None:\n        return 0.0\n    total = sum(values)\n", "None check.")


def root_cause(**overrides) -> RootCauseReport:
    values = dict(
        is_real_bug=True, summary="average divides by len(values) without checking for an empty list",
        root_cause_path="shop/pricing.py", root_cause_line=3,
        evidence=[
            EvidenceClaim(path="shop/pricing.py", line=3, quote="return total / len(values)", explanation="no zero check"),
            EvidenceClaim(path="shop/orders.py", line=6, quote="return average(totals)", explanation="[] reaches it"),
        ],
        affected_files=["shop/pricing.py"], fix_strategy="return 0.0 for an empty list", confidence="high",
    )
    return RootCauseReport(**{**values, **overrides})


APPROVE = ReviewReport(verdict="approve", summary="Guards the empty case; callers unaffected.", issues=[])


@pytest.fixture
def settings(test_settings: Settings) -> Settings:
    test_settings.fix_investigation_max_steps = 0  # skip the agent loop: scripted root cause only
    return test_settings


@pytest.fixture
def shop_root(tmp_path: Path) -> Path:
    return make_shop_repo(tmp_path / "shop_repo")


@pytest.fixture
def shop(repo_manager: RepositoryManager, shop_root: Path) -> int:
    return repo_manager.register(str(shop_root)).id


def make_service(llm, repo_manager: RepositoryManager, settings: Settings) -> FixSessionService:
    session = repo_manager._session
    insight = CodeInsightService(repo_manager)
    return FixSessionService(
        llm, repo_manager, insight, TestingService(llm, repo_manager, insight, session, settings),
        ChangeService(llm, repo_manager, session, settings), session, settings,
    )


async def run_session(llm, repo_manager, settings, repository_id, **options) -> FixSession:
    service = make_service(llm, repo_manager, settings)
    fs = service.create(repository_id, description="average_order_value crashes on an empty list",
                        path="shop/orders.py", line_start=6, line_end=6, **options)
    return await service.run(fs.id, threading.Event())


def stages(fs: FixSession, repo_manager: RepositoryManager) -> list[str]:
    return [e.stage for e in make_service(None, repo_manager, Settings()).events(fs.id)]


# ----- sandbox ---------------------------------------------------------------------------------

def test_sandbox_copies_code_but_not_secrets_git_dependencies_or_assistant_data(shop_root: Path, tmp_path: Path):
    (shop_root / ".git").mkdir()
    (shop_root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (shop_root / ".venv" / "Lib").mkdir(parents=True)
    (shop_root / "node_modules" / "x").mkdir(parents=True)
    (shop_root / "keys").mkdir()
    (shop_root / "keys" / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    (shop_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (shop_root / "debug.log").write_text("log\n", encoding="utf-8")
    assistant_data = shop_root / "data"
    (assistant_data / "backups").mkdir(parents=True)
    before = {p: p.read_bytes() for p in shop_root.rglob("*") if p.is_file()}

    sandbox = Sandbox.create(shop_root, tmp_path / "sb" / "1", max_files=100, max_bytes=1_000_000, exclude=[assistant_data])
    copied = {p.relative_to(sandbox.root).as_posix() for p in sandbox.root.rglob("*") if p.is_file()}

    assert "shop/pricing.py" in copied and "tests/test_orders.py" in copied and "pytest.ini" in copied
    assert not {".env", ".git/config", "keys/deploy.pem", "debug.log"} & copied
    assert not any(p.startswith((".venv", "node_modules", "data/")) for p in copied)
    assert sandbox.skipped["sensitive"] == 2 and sandbox.skipped["assistant_data"] == 1

    sandbox.write("shop/pricing.py", "changed\n")
    env = sandbox.env()
    assert env["USERPROFILE"] == env["HOME"] == str(sandbox.home)
    assert env["PYTHONPATH"].startswith(str(sandbox.root))
    sandbox.remove()
    assert not (tmp_path / "sb" / "1").exists()
    assert {p: p.read_bytes() for p in shop_root.rglob("*") if p.is_file()} == before  # original untouched


def test_sandbox_refuses_oversized_repositories(shop_root: Path, tmp_path: Path):
    from app.services.fixing.errors import SandboxError

    with pytest.raises(SandboxError, match="too large"):
        Sandbox.create(shop_root, tmp_path / "sb", max_files=2, max_bytes=1_000_000)
    assert not (tmp_path / "sb").exists()


# ----- patch checks and test comparison ---------------------------------------------------------

def candidate(path: str, original: str = "a\n", new: str = "b\n", diff: str = "-a\n+b\n", syntax_ok: bool = True):
    return CandidateFile(path, original, new, diff, syntax_ok, "")


def test_patch_checks():
    def failed(files, allowed=frozenset({"shop/pricing.py"}), limit=60):
        return {c.name for c in check_candidate(files, set(allowed), limit) if not c.ok}

    assert failed([candidate("shop/pricing.py")]) == set()
    assert failed([candidate("shop/pricing.py", new="a\n", diff="")]) == {"edits_applied"}
    assert failed([candidate("shop/orders.py")]) == {"scope"}
    assert failed([candidate("tests/test_orders.py")], {"tests/test_orders.py"}) == {"no_test_edits"}
    assert failed([candidate("shop/pricing.py", syntax_ok=False)]) == {"syntax"}
    assert failed([candidate("shop/pricing.py", diff="-a\n+b\n+c\n")], limit=2) == {"minimal"}
    leaked = candidate("shop/pricing.py", new='KEY = "[REDACTED]"\n', diff='+KEY = "[REDACTED]"\n')
    assert failed([leaked]) == {"no_secrets"}
    token = candidate("shop/pricing.py", new="x\n", diff='+API_KEY = "sk-' + "a1B2c3D4e5F6g7H8i9J0k1L2m3" + '"\n')
    assert failed([token]) == {"no_secrets"}


def test_failed_test_ids_are_parsed():
    pytest_out = "FAILED tests/test_a.py::test_x - assert 1 == 2\nERROR tests/test_b.py - ImportError\n1 failed"
    unittest_out = "FAIL: test_x (pkg.test_a.TestA.test_x)\nERROR: test_y (pkg.test_a.TestA)\n"

    assert failed_test_ids("pytest", pytest_out) == ["tests/test_a.py::test_x", "tests/test_b.py"]
    assert failed_test_ids("unittest", unittest_out) == ["pkg.test_a.TestA.test_x", "pkg.test_a.TestA.test_y"]
    assert failed_test_ids("jest", "anything") is None


def outcome(failed_ids, passed=3, failed=None, exit_code=1, timed_out=False, framework="pytest"):
    bad = len(failed_ids) if failed_ids is not None else (failed or 0)
    return TestOutcome(framework, [], exit_code, timed_out, False, passed, bad, 0, 0, 1.0, failed_ids, "")


def test_baseline_failures_are_not_blamed_on_the_patch():
    baseline = outcome(["t::old_unrelated", "t::the_bug"])

    fixed = compare_runs(baseline, outcome(["t::old_unrelated"]))
    assert fixed.ok and fixed.fixed == ["t::the_bug"] and fixed.still_failing == ["t::old_unrelated"]
    assert "baseline failures, not caused by this patch" in fixed.reason

    regressed = compare_runs(baseline, outcome(["t::old_unrelated", "t::the_bug", "t::new"]))
    assert not regressed.ok and regressed.new_failures == ["t::new"]

    # A patch that breaks nothing but fixes none of the bug's failing tests is not a fix.
    no_op = compare_runs(baseline, outcome(["t::old_unrelated", "t::the_bug"]), bug_test_files={"t"})
    assert not no_op.ok and "related to the bug still fail" in no_op.reason
    assert compare_runs(baseline, outcome(["t::old_unrelated"]), bug_test_files={"t"}).ok

    assert not compare_runs(baseline, outcome([], timed_out=True)).ok
    assert not compare_runs(baseline, outcome([], passed=0)).ok  # nothing ran: collection broke


def test_count_based_comparison_when_output_is_not_understood():
    baseline = outcome(None, failed=1, framework="jest")
    assert compare_runs(baseline, outcome(None, failed=1, framework="jest")).ok
    assert not compare_runs(baseline, outcome(None, failed=2, framework="jest")).ok


def test_runner_kills_the_test_process_when_stopped(tmp_path: Path):
    runner = TestRunner({"slow": [sys.executable, "-c", "import time; time.sleep(60)"]}, timeout_seconds=120, enabled=True)
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.perf_counter()

    result = runner.run("slow", tmp_path, should_stop=stop.is_set)

    assert result.stopped and result.exit_code is None
    assert "session was cancelled" in result.output
    assert time.perf_counter() - started < 15


# ----- the whole session ------------------------------------------------------------------------

@pytest.mark.anyio
async def test_session_fixes_cross_file_bug_in_sandbox_without_touching_repository(
    repo_manager: RepositoryManager, shop: int, shop_root: Path, settings: Settings
):
    llm = FakeLLMService()
    llm.structured = [root_cause(), APPROVE]
    llm.chat_replies = [GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "awaiting_approval", fs.error
    assert fs.validation_status == "passed"
    assert fs.root_cause["confidence"] == "high"
    assert len(fs.root_cause["evidence"]) == 2
    assert fs.baseline["failed_ids"] == ["tests/test_orders.py::test_empty_orders"]
    attempt = fs.attempts[0]
    assert attempt.status == "passed"
    assert attempt.comparison["fixed"] == ["tests/test_orders.py::test_empty_orders"]
    assert "+    if not values:" in attempt.files[0]["diff"]
    assert fs.review["verdict"] == "approve"
    assert (shop_root / "shop" / "pricing.py").read_text(encoding="utf-8") == PRICING  # repository untouched
    assert not (settings.data_path / "sandboxes" / str(fs.id)).exists()  # sandbox cleaned up
    assert stages(fs, repo_manager) == [
        "queued", "evidence", "investigation", "sandbox", "baseline", "attempt", "review", "ready",
    ]
    assert fs.llm_calls == 3  # root cause + patch + review
    assert "Fix this bug with the smallest possible change" in llm.chat_prompts[0]
    assert "shop/pricing.py:3: return total / len(values)" in llm.chat_prompts[0]  # verified evidence only


@pytest.mark.anyio
async def test_failed_attempt_is_analysed_and_retried(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = FakeLLMService()
    llm.structured = [root_cause(), APPROVE]
    llm.chat_replies = [BREAKS_AVERAGE, "test_average now fails because average always returns 0.", GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "awaiting_approval", fs.error
    assert [a.status for a in fs.attempts] == ["failed", "passed"]
    assert fs.attempts[0].comparison["new_failures"] == [
        "tests/test_orders.py::test_average_order_value", "tests/test_pricing.py::test_average",
    ]
    assert "always returns 0" in fs.attempts[0].failure_analysis
    assert fs.final_attempt == 2
    retry_prompt = llm.chat_prompts[2]
    assert "A previous attempt did NOT work" in retry_prompt and "+    return 0" in retry_prompt


@pytest.mark.anyio
async def test_session_stops_after_the_attempt_limit(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = FakeLLMService()
    llm.structured = [root_cause()]
    llm.chat_replies = [BREAKS_AVERAGE, "analysis 1", STILL_CRASHES, "analysis 2", WRONG_GUARD]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "failed"
    assert fs.validation_status == "failed"
    assert [a.status for a in fs.attempts] == ["failed", "failed", "failed"]
    assert "No acceptable patch after 3 attempt(s)" in fs.error
    assert fs.attempts[2].failure_analysis is None  # no analysis after the last attempt


@pytest.mark.anyio
async def test_repeated_patch_stops_the_loop(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = FakeLLMService()
    llm.structured = [root_cause()]
    llm.chat_replies = [BREAKS_AVERAGE, "analysis", BREAKS_AVERAGE]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "failed"
    assert [a.status for a in fs.attempts] == ["failed", "rejected"]
    assert any(c["name"] == "progress" and not c["ok"] for c in fs.attempts[1].checks)


@pytest.mark.anyio
async def test_unverified_root_cause_never_reaches_patching(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = FakeLLMService()
    llm.structured = [root_cause(evidence=[EvidenceClaim(path="shop/pricing.py", line=3, quote="raise ZeroDivisionError()", explanation="")])]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "unverified"
    assert "no fix was attempted" in fs.error
    assert fs.attempts == [] and llm.chat_prompts == []
    assert fs.root_cause["rejected_evidence"][0]["reason"].startswith("this text is not in")


@pytest.mark.anyio
async def test_llm_call_limit_is_enforced(repo_manager: RepositoryManager, shop: int, settings: Settings):
    settings.fix_max_llm_calls = 3
    llm = FakeLLMService()
    llm.structured = [root_cause()]
    llm.chat_replies = [BREAKS_AVERAGE, "analysis", GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, shop)

    assert fs.status == "failed"
    assert "LLM call limit reached (3 calls" in fs.error
    assert fs.llm_calls == 3


@pytest.mark.anyio
async def test_patch_without_tests_is_marked_not_validated(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = FakeLLMService()
    llm.structured = [root_cause(), APPROVE]
    llm.chat_replies = [GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, shop, run_tests=False)

    assert fs.status == "awaiting_approval"
    assert fs.validation_status == "not_validated"
    assert fs.validation_note == "Not validated: test execution was turned off for this session."
    assert fs.attempts[0].status == "not_validated" and fs.attempts[0].test is None


@pytest.mark.anyio
async def test_javascript_patch_is_not_validated(repo_manager: RepositoryManager, tmp_path: Path, settings: Settings):
    root = tmp_path / "web"
    (root / "src").mkdir(parents=True)
    (root / "src" / "math.js").write_text("export function average(xs) {\n  return xs.reduce((a, b) => a + b) / xs.length;\n}\n", encoding="utf-8")
    (root / "src" / "math.test.js").write_text("import { average } from './math';\ntest('avg', () => expect(average([2, 4])).toBe(3));\n", encoding="utf-8")
    (root / "package.json").write_text('{"devDependencies": {"jest": "^29.0.0"}}\n', encoding="utf-8")
    repository_id = repo_manager.register(str(root)).id
    llm = FakeLLMService()
    llm.structured = [
        root_cause(root_cause_path="src/math.js", root_cause_line=2, affected_files=["src/math.js"], evidence=[
            EvidenceClaim(path="src/math.js", line=2, quote="return xs.reduce((a, b) => a + b) / xs.length;", explanation="empty"),
        ]),
        APPROVE,
    ]
    llm.chat_replies = [block("  return xs.reduce((a, b) => a + b) / xs.length;\n", "  if (xs.length === 0) return 0;\n  return xs.reduce((a, b) => a + b) / xs.length;\n")]
    service = make_service(llm, repo_manager, settings)
    fs = service.create(repository_id, description="average crashes on []", path="src/math.js", line_start=2)

    fs = await service.run(fs.id, threading.Event())

    assert fs.status == "awaiting_approval", fs.error
    assert fs.validation_status == "not_validated"
    assert "jest tests cannot run safely in the sandbox" in fs.validation_note
    assert fs.baseline is None and fs.attempts[0].test is None


@pytest.mark.anyio
async def test_unrelated_baseline_failure_does_not_block_the_fix(
    repo_manager: RepositoryManager, shop_root: Path, settings: Settings
):
    (shop_root / "tests" / "test_legacy.py").write_text("def test_legacy():\n    assert False\n", encoding="utf-8")
    repository_id = repo_manager.register(str(shop_root)).id
    llm = FakeLLMService()
    llm.structured = [root_cause(), APPROVE]
    llm.chat_replies = [GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, repository_id)

    assert fs.validation_status == "passed", fs.error
    assert fs.attempts[0].comparison["still_failing"] == ["tests/test_legacy.py::test_legacy"]


@pytest.mark.anyio
async def test_reproduction_test_runs_only_in_the_sandbox(
    repo_manager: RepositoryManager, shop: int, shop_root: Path, settings: Settings
):
    repro = "```python\nfrom shop.pricing import average\n\n\ndef test_empty():\n    assert average([]) == 0.0\n```"
    llm = FakeLLMService()
    llm.structured = [root_cause(), APPROVE]
    llm.chat_replies = [repro, GOOD_PATCH]

    fs = await run_session(llm, repo_manager, settings, shop, reproduction_test=True)

    assert fs.status == "awaiting_approval", fs.error
    assert fs.reproduction["kept"] is True
    assert fs.reproduction["path"] == "tests/test_fix_session_1_reproduction.py"
    assert not (shop_root / fs.reproduction["path"]).exists()
    assert fs.attempts[0].status == "passed"


# ----- stopping and limits on the clock ---------------------------------------------------------

class BlockingLLM(FakeLLMService):
    """Answers the root-cause question, then hangs on the patch request until cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.structured = [root_cause()]
        self.waiting = asyncio.Event()

    async def chat(self, messages, model=None):
        self.waiting.set()
        await asyncio.sleep(3600)


@pytest.mark.anyio
async def test_developer_can_stop_a_running_session(repo_manager: RepositoryManager, shop: int, settings: Settings):
    llm = BlockingLLM()
    service = make_service(llm, repo_manager, settings)
    fs = service.create(shop, description="crash on []", path="shop/orders.py", line_start=6)
    runner = FixSessionRunner()
    runner.start(fs.id, lambda stop: service.run(fs.id, stop), max_concurrent=1)
    await asyncio.wait_for(llm.waiting.wait(), 30)  # baseline done, patch generation in progress

    from app.services.fixing.errors import FixSessionBusyError
    with pytest.raises(FixSessionBusyError):
        runner.ensure_capacity(1)
    assert runner.stop(fs.id)
    await runner.wait(fs.id)

    fs = service.get(fs.id)
    assert fs.status == "cancelled"
    assert "Stopped by the developer" in fs.error
    assert not (settings.data_path / "sandboxes" / str(fs.id)).exists()
    assert runner.running() == []


@pytest.mark.anyio
async def test_session_time_limit(repo_manager: RepositoryManager, shop: int, settings: Settings):
    settings.fix_session_timeout_seconds = 1  # bypasses validation on purpose: the real minimum is 60 s
    settings.test_execution_enabled = False

    fs = await run_session(BlockingLLM(), repo_manager, settings, shop)

    assert fs.status == "failed"
    assert "time limit" in fs.error


def test_sessions_running_at_shutdown_become_interrupted(repo_manager: RepositoryManager, shop: int, settings: Settings):
    service = make_service(FakeLLMService(), repo_manager, settings)
    fs = service.create(shop, description="crash on []", path="shop/orders.py")
    fs.status = "fixing"
    repo_manager._session.commit()
    leftover = settings.data_path / "sandboxes" / "99" / "repo"
    leftover.mkdir(parents=True)

    assert mark_interrupted_sessions(repo_manager._session, settings.data_path) == 1
    assert service.get(fs.id).status == "interrupted"
    assert not leftover.exists()


def test_review_issue_schema_roundtrip():
    report = ReviewReport(verdict="concerns", summary="s", issues=[ReviewIssue(severity="High", description="d")])
    assert report.model_dump()["issues"][0]["severity"] == "High"
