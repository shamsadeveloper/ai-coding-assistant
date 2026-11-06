"""Tool registry dispatch and the repository tools against the sample project."""

from pathlib import Path

import pytest

from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext
from app.agent.tools import default_tools
from app.agent.tools.repository_tools import definition_pattern
from app.services.code.insight import CodeInsightService
from app.services.llm_service import ToolCall
from app.services.repository.manager import RepositoryManager


@pytest.fixture
def context(repo_manager: RepositoryManager, sample_repo: Path) -> ToolContext:
    repository = repo_manager.register(str(sample_repo))
    return ToolContext(repository.id, repo_manager, CodeInsightService(repo_manager))


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry(default_tools(), max_output_chars=6000)


def run(registry: ToolRegistry, context: ToolContext, tool_name: str, /, **arguments):
    return registry.execute(ToolCall(tool_name, arguments), context)


def test_schemas_are_valid_function_definitions(registry: ToolRegistry):
    schemas = {s["function"]["name"]: s for s in registry.schemas()}

    assert set(schemas) == {
        "get_repository_overview", "list_project_files", "read_file", "search_code", "find_function",
        "analyze_file", "generate_unit_test", "run_project_tests", "generate_code_patch", "inspect_git_diff",
    }
    read_file = schemas["read_file"]["function"]["parameters"]
    assert read_file["required"] == ["path"]
    assert "title" not in read_file


def test_duplicate_tool_names_are_rejected():
    tools = default_tools()
    with pytest.raises(ValueError):
        ToolRegistry([*tools, tools[0]], max_output_chars=100)


def test_overview(registry, context):
    result = run(registry, context, "get_repository_overview")

    assert not result.is_error
    assert "Frameworks: FastAPI, React" in result.content
    assert "app/: 2 files" in result.content


def test_list_files(registry, context):
    result = run(registry, context, "list_project_files", language="Python")

    assert result.content.splitlines() == [
        "Showing 2 of 2 files:",
        "app/main.py (Python, 4 lines)",
        "app/utils.py (Python, 2 lines)",
    ]


def test_read_file_numbers_lines(registry, context):
    result = run(registry, context, "read_file", path="app/main.py", start_line=1, end_line=2)

    assert result.content.splitlines() == [
        "File: app/main.py (lines 1-2 of 4)",
        "1 | def hello():",
        "2 |     return 'hi'",
        "... 2 more lines. Call read_file with start_line=3 to continue.",
    ]


def test_search_reports_file_name_matches(registry, context):
    # Regression: the model searched for "utils.py", got no line matches, and said the file did not exist.
    result = run(registry, context, "search_code", query="utils.py")

    assert result.content.splitlines() == [
        "The file exists. Files whose path matches 'utils.py': app/utils.py",
        "To read it, call read_file with path='app/utils.py'.",
    ]  # no "no matches" sentence next to it: small models fixate on that


def test_search_and_find_function(registry, context):
    search = run(registry, context, "search_code", query="todo")
    assert "app/main.py:4: # TODO: handle errors" in search.content

    found = run(registry, context, "find_function", name="add")
    assert found.content.splitlines() == [
        "app/utils.py:1: def add(a, b):",
        "To see the implementation, call read_file with path='app/utils.py' and start_line=1.",
    ]

    arrow = run(registry, context, "find_function", name="greet")
    assert arrow.content.startswith("src/index.ts:1:")


@pytest.mark.parametrize(
    ("line", "matches"),
    [
        ("def create_app():", True),
        ("async def create_app(x):", True),
        ("class create_app:", True),
        ("function create_app() {", True),
        ("export const create_app = async () => {", True),
        ("func (s *Server) create_app() error {", True),
        ("public static void create_app(String[] a) {", True),
        ("app = create_app()", False),  # a call, not a definition
        ("def create_application():", False),
    ],
)
def test_definition_pattern(line: str, matches: bool):
    assert bool(definition_pattern("create_app").search(line)) is matches


def test_errors_are_returned_to_the_model_not_raised(registry, context):
    unknown = run(registry, context, "run_shell", command="del *")
    assert unknown.is_error and "unknown tool 'run_shell'" in unknown.content

    missing_arg = run(registry, context, "read_file")
    assert missing_arg.is_error and "path: Field required" in missing_arg.content

    traversal = run(registry, context, "read_file", path="../../Windows/win.ini")
    assert traversal.is_error and "must not contain '..'" in traversal.content

    secret = run(registry, context, "read_file", path=".env")
    assert secret.is_error and "sensitive" in secret.content

    bad_name = run(registry, context, "find_function", name="x; rm -rf /")
    assert bad_name.is_error


def test_string_arguments_are_decoded(registry, context):
    result = registry.execute(ToolCall("read_file", '{"path": "app/utils.py"}'), context)  # type: ignore[arg-type]

    assert not result.is_error
    assert "def add" in result.content


def test_long_output_is_truncated(context):
    small = ToolRegistry(default_tools(), max_output_chars=60)

    result = small.execute(ToolCall("read_file", {"path": "app/main.py"}), context)

    assert result.content.endswith("(output truncated at 60 characters; request a smaller range)")
