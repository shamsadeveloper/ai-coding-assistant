"""Fix investigation: evidence collection, read-only investigation, evidence verification, LLM budget."""

import threading
from pathlib import Path

import pytest

from app.services.code.insight import CodeInsightService
from app.services.fixing.budget import BudgetedLLM
from app.services.fixing.errors import SessionLimitError, SessionStoppedError
from app.services.fixing.evidence import (
    VerificationResult,
    VerifiedEvidence,
    RejectedEvidence,
    adjusted_confidence,
    collect_context,
    files_to_change,
    find_callers,
    verify_evidence,
)
from app.services.fixing.investigator import RootCauseInvestigator
from app.services.fixing.reports import EvidenceClaim, RootCauseReport
from app.services.repository.manager import RepositoryManager
from tests.conftest import FakeLLMService, answer_turn, tool_turn
from tests.fix_fixtures import make_shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def shop(repo_manager: RepositoryManager, tmp_path: Path) -> int:
    return repo_manager.register(str(make_shop_repo(tmp_path / "shop_repo"))).id


def root_cause(**overrides) -> RootCauseReport:
    values = dict(
        is_real_bug=True,
        summary="average divides by len(values) without checking for an empty list",
        root_cause_path="shop/pricing.py",
        root_cause_line=3,
        evidence=[
            EvidenceClaim(path="shop/pricing.py", line=3, quote="return total / len(values)", explanation="no zero check"),
            EvidenceClaim(path="shop/orders.py", line=6, quote="return average(totals)", explanation="empty totals reach it"),
        ],
        affected_files=["shop/pricing.py"],
        fix_strategy="return 0.0 for an empty list",
        confidence="high",
    )
    return RootCauseReport(**{**values, **overrides})


# ----- deterministic evidence ------------------------------------------------------------------

def test_context_has_enclosing_function_callers_imports_and_tests(repo_manager: RepositoryManager, shop: int):
    context = collect_context(repo_manager, CodeInsightService(repo_manager), shop, "shop/pricing.py", 3, 3)

    assert context.symbol == "average"
    assert (context.code_start, context.code_end) == (1, 3)
    assert "3 |     return total / len(values)" in context.code
    # not the definition, not the import line; tests count as callers too
    assert [(c.path, c.line) for c in context.callers] == [("shop/orders.py", 6), ("tests/test_pricing.py", 5)]
    assert context.imported_by == ["shop/orders.py", "tests/test_pricing.py"]
    assert context.related_tests == ["tests/test_pricing.py"]
    assert "Places that call average" in context.to_prompt()


def test_context_outside_any_function_uses_a_line_window(repo_manager: RepositoryManager, shop: int):
    context = collect_context(repo_manager, CodeInsightService(repo_manager), shop, "shop/orders.py", 1, 1)

    assert context.symbol is None
    assert context.callers == []
    assert context.imports == ["shop/pricing.py"]
    assert context.code_start == 1


def test_context_refuses_sensitive_files(repo_manager: RepositoryManager, shop: int):
    from app.services.repository.errors import FileAccessDeniedError

    with pytest.raises(FileAccessDeniedError):
        collect_context(repo_manager, CodeInsightService(repo_manager), shop, ".env", 1, 1)


def test_callers_skip_definitions(repo_manager: RepositoryManager, shop: int):
    assert [(c.path, c.line) for c in find_callers(repo_manager, shop, "average_order_value")] == [
        ("tests/test_orders.py", 5), ("tests/test_orders.py", 9),
    ]


# ----- evidence verification -------------------------------------------------------------------

def test_verification_keeps_real_quotes_and_drops_invented_ones(repo_manager: RepositoryManager, shop: int):
    result = verify_evidence(repo_manager, shop, [
        ("shop/pricing.py", 3, "return total / len(values)", "real"),
        ("./shop/pricing.py", 1, "    return total / len(values)", "real, line off by two"),  # duplicate of #1
        ("shop/orders.py", 5, "totals = [order['total'] for order in orders]", "quote style differs"),
        ("shop/pricing.py", 3, "return total // count", "invented"),
        ("shop/pricing.py", 40, "return total / len(values)", "right text, wrong place"),
        (".env", 1, "PAYMENT_API_KEY=live-secret-value", "secret file"),
        ("shop/missing.py", 1, "x = 1", "no such file"),
        ("shop/pricing.py", 2, "", "empty quote"),
    ])

    assert result.verified == [
        VerifiedEvidence("shop/pricing.py", 3, "return total / len(values)", "real"),
    ]
    reasons = [r.reason for r in result.rejected]
    assert "not in shop/orders.py near line 5" in reasons[0]
    assert "not in shop/pricing.py near line 3" in reasons[1]
    assert "near line 40" in reasons[2]
    assert "sensitive" in reasons[3]
    assert "not in the index" in reasons[4]
    assert "too short" in reasons[5]


@pytest.mark.parametrize(
    ("model", "verified", "rejected", "expected"),
    [("high", 2, 0, "high"), ("high", 1, 0, "medium"), ("high", 2, 1, "medium"), ("medium", 1, 1, "low"), ("low", 3, 2, "low")],
)
def test_confidence_is_lowered_by_weak_evidence(model: str, verified: int, rejected: int, expected: str):
    result = VerificationResult(
        [VerifiedEvidence("a.py", i, "x", "") for i in range(verified)],
        [RejectedEvidence("a.py", i, "x", "") for i in range(rejected)],
    )
    assert adjusted_confidence(model, result) == expected


def test_files_to_change_only_allows_indexed_source_files():
    indexed = {"shop/pricing.py", "shop/orders.py", "tests/test_orders.py"}

    assert files_to_change(
        ["./shop/pricing.py", "tests/test_orders.py", "../../etc/passwd", ".env", "shop\\orders.py", "shop/pricing.py"],
        indexed, "shop/orders.py", 3,
    ) == ["shop/pricing.py", "shop/orders.py"]
    assert files_to_change(["nonsense.py"], indexed, "shop/orders.py", 3) == ["shop/orders.py"]
    assert files_to_change(["shop/pricing.py", "shop/orders.py"], indexed, "x", 1) == ["shop/pricing.py"]


# ----- investigation agent ---------------------------------------------------------------------

def investigator(llm, repo_manager: RepositoryManager, max_steps: int = 3) -> RootCauseInvestigator:
    return RootCauseInvestigator(llm, repo_manager, CodeInsightService(repo_manager), max_steps=max_steps, max_files=3)


@pytest.mark.anyio
async def test_investigation_finds_cross_file_root_cause(repo_manager: RepositoryManager, shop: int):
    llm = FakeLLMService()
    llm.turns = [
        tool_turn(("find_function", {"name": "average"})),
        answer_turn("Root cause: shop/pricing.py:3 divides by len(values); an empty list crashes."),
    ]
    llm.structured = [root_cause()]
    context = collect_context(repo_manager, CodeInsightService(repo_manager), shop, "shop/orders.py", 6, 6)

    result = await investigator(llm, repo_manager).investigate(shop, "average_order_value crashes on []", context)

    assert result.verified
    assert result.files_to_change == ["shop/pricing.py"]
    assert result.confidence == "high"
    assert [s.tool_name for s in result.steps] == ["find_function"]
    root = result.root_cause_dict()
    assert root["location"] == "shop/pricing.py:3"
    assert [e["line"] for e in root["evidence"]] == [3, 6]
    # the investigation notes and the deterministic context both reach the extraction prompt
    extraction_prompt = llm.structured_prompts[0][1]
    assert "shop/pricing.py:3 divides" in extraction_prompt
    assert "Places that call average_order_value" in extraction_prompt


@pytest.mark.anyio
async def test_investigator_only_has_read_only_tools(repo_manager: RepositoryManager, shop: int):
    llm = FakeLLMService()
    llm.turns = [
        tool_turn(("generate_code_patch", {"request": "fix", "paths": ["shop/pricing.py"]})),
        answer_turn("done"),
    ]
    llm.structured = [root_cause()]
    context = collect_context(repo_manager, CodeInsightService(repo_manager), shop, "shop/pricing.py", 3, 3)

    result = await investigator(llm, repo_manager).investigate(shop, "crash", context)

    offered = {t["function"]["name"] for t in llm.agent_calls[0][1]}
    assert offered == {"read_file", "search_code", "find_function", "analyze_file"}
    assert result.steps[0].is_error and "unknown tool" in result.steps[0].output


@pytest.mark.anyio
async def test_unverifiable_or_dismissed_bug_is_not_verified(repo_manager: RepositoryManager, shop: int):
    context = collect_context(repo_manager, CodeInsightService(repo_manager), shop, "shop/pricing.py", 3, 3)

    invented = FakeLLMService()
    invented.structured = [root_cause(evidence=[EvidenceClaim(path="shop/pricing.py", line=3, quote="if count == 0: raise", explanation="")])]
    result = await investigator(invented, repo_manager, max_steps=0).investigate(shop, "crash", context)
    assert not result.verified
    assert result.confidence == "low"
    assert invented.agent_calls == []  # max_steps=0 skips the agent loop

    dismissed = FakeLLMService()
    dismissed.structured = [root_cause(is_real_bug=False)]
    result = await investigator(dismissed, repo_manager, max_steps=0).investigate(shop, "crash", context)
    assert not result.verified


# ----- backend-enforced LLM budget -------------------------------------------------------------

@pytest.mark.anyio
async def test_budget_limits_calls_and_counts_tokens():
    from app.services.llm_service import ChatMessage

    budget = BudgetedLLM(FakeLLMService(), max_calls=2)
    await budget.chat([ChatMessage("user", "a")])
    await budget.chat_with_tools([], [])

    assert (budget.calls, budget.prompt_tokens, budget.completion_tokens) == (2, 13, 9)
    with pytest.raises(SessionLimitError, match="2 calls"):
        await budget.generate_structured("s", "u", RootCauseReport)


@pytest.mark.anyio
async def test_budget_stops_when_the_developer_stops_the_session():
    stop = threading.Event()
    budget = BudgetedLLM(FakeLLMService(), max_calls=10, stop=stop)
    stop.set()

    with pytest.raises(SessionStoppedError):
        await budget.chat_with_tools([], [])
    assert budget.calls == 0
