"""The agent loop, driven by a scripted fake LLM."""

from pathlib import Path

import pytest

from app.agent.engine import STEP_LIMIT_PROMPT, VERIFY_PROMPT, AgentEngine, cites_code
from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext
from app.agent.tools import default_tools
from app.services.code.insight import CodeInsightService
from app.services.llm_service import AgentMessage
from app.services.repository.manager import RepositoryManager
from tests.conftest import FakeLLMService, answer_turn, tool_turn

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def context(repo_manager: RepositoryManager, sample_repo: Path) -> ToolContext:
    repository = repo_manager.register(str(sample_repo))
    return ToolContext(repository.id, repo_manager, CodeInsightService(repo_manager))


def make_engine(llm: FakeLLMService, max_steps: int = 5) -> AgentEngine:
    return AgentEngine(llm, ToolRegistry(default_tools(), max_output_chars=6000), max_steps=max_steps)


async def test_answers_directly_without_tools(fake_llm, context):
    fake_llm.turns = [answer_turn("Hello!")]

    result = await make_engine(fake_llm).run("system", [], "hi", context)

    assert result.answer == "Hello!"
    assert result.steps == []
    assert result.llm_calls == 1


async def test_runs_tools_and_feeds_results_back(fake_llm, context):
    fake_llm.turns = [
        tool_turn(("find_function", {"name": "add"})),
        tool_turn(("read_file", {"path": "app/utils.py"})),
        answer_turn("add is defined in app/utils.py:1"),
    ]

    result = await make_engine(fake_llm).run("system", [], "Where is add?", context)

    assert result.answer == "add is defined in app/utils.py:1"
    assert result.stopped_reason == "completed"
    assert [s.tool_name for s in result.steps] == ["find_function", "read_file"]
    assert result.llm_calls == 3
    assert (result.prompt_tokens, result.completion_tokens) == (30, 15)

    # The last LLM call saw: system, user, assistant(tool call), tool result, assistant(tool call), tool result.
    last_messages, tools = fake_llm.agent_calls[-1]
    assert [m.role for m in last_messages] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert last_messages[3].tool_name == "find_function"
    assert "app/utils.py:1: def add(a, b):" in last_messages[3].content
    assert "1 | def add(a, b):" in last_messages[5].content
    assert len(tools) == 10


async def test_history_is_placed_between_system_and_new_question(fake_llm, context):
    history = [AgentMessage("user", "earlier question"), AgentMessage("assistant", "earlier answer")]

    await make_engine(fake_llm).run("system", history, "follow-up", context)

    sent = fake_llm.agent_calls[0][0]
    assert [(m.role, m.content) for m in sent] == [
        ("system", "system"),
        ("user", "earlier question"),
        ("assistant", "earlier answer"),
        ("user", "follow-up"),
    ]


async def test_tool_errors_are_shown_to_the_model_so_it_can_recover(fake_llm, context):
    fake_llm.turns = [
        tool_turn(("read_file", {"path": "app/missing.py"})),
        tool_turn(("read_file", {"path": "app/main.py"})),
        answer_turn("done"),
    ]

    result = await make_engine(fake_llm).run("system", [], "read main", context)

    assert [s.is_error for s in result.steps] == [True, False]
    error_message = fake_llm.agent_calls[1][0][-1]
    assert error_message.role == "tool"
    assert "not in the index" in error_message.content


async def test_multiple_tool_calls_in_one_turn(fake_llm, context):
    fake_llm.turns = [
        tool_turn(("find_function", {"name": "add"}), ("find_function", {"name": "hello"})),
        answer_turn("both found"),
    ]

    result = await make_engine(fake_llm).run("system", [], "q", context)

    assert [s.iteration for s in result.steps] == [1, 1]
    assert [m.role for m in fake_llm.agent_calls[1][0][-3:]] == ["assistant", "tool", "tool"]


async def test_repeated_identical_call_is_not_executed_again(fake_llm, context):
    fake_llm.turns = [
        tool_turn(("search_code", {"query": "todo"})),
        tool_turn(("search_code", {"query": "todo"})),
        answer_turn("ok"),
    ]

    result = await make_engine(fake_llm).run("system", [], "q", context)

    assert [s.is_error for s in result.steps] == [False, True]
    assert "already called search_code" in result.steps[1].output


async def test_unverified_code_reference_triggers_one_verification_round(fake_llm, context):
    fake_llm.turns = [
        answer_turn("It is called in app/main.py at line 10."),  # from memory, no tools
        tool_turn(("search_code", {"query": "hello"})),
        answer_turn("It is called in app/main.py:1."),
    ]

    result = await make_engine(fake_llm).run("system", [], "where?", context)

    assert result.answer == "It is called in app/main.py:1."
    assert [s.tool_name for s in result.steps] == ["search_code"]
    second_call_messages = fake_llm.agent_calls[1][0]
    assert second_call_messages[-1].content == VERIFY_PROMPT


async def test_verification_is_requested_only_once(fake_llm, context):
    fake_llm.turns = [answer_turn("See app/main.py."), answer_turn("Still app/main.py.")]

    result = await make_engine(fake_llm).run("system", [], "where?", context)

    assert result.answer == "Still app/main.py."
    assert result.llm_calls == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Defined in app/main.py.", True),
        ("See index.ts:12", True),
        ("on line 42", True),
        ("Hello! How can I help you with this repository?", False),
        ("A decorator wraps a function.", False),
    ],
)
def test_cites_code(text: str, expected: bool):
    assert cites_code(text) is expected


async def test_step_limit_forces_a_final_answer_without_tools(fake_llm, context):
    fake_llm.turns = [
        tool_turn(("search_code", {"query": "a"})),
        tool_turn(("search_code", {"query": "b"})),
        answer_turn("partial answer"),
    ]

    result = await make_engine(fake_llm, max_steps=2).run("system", [], "q", context)

    assert result.stopped_reason == "max_steps"
    assert result.answer == "partial answer"
    final_messages, final_tools = fake_llm.agent_calls[-1]
    assert final_tools == []
    assert final_messages[-1].content == STEP_LIMIT_PROMPT
