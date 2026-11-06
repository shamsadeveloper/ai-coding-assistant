import pytest

from app.services.llm_service import ToolCall, parse_text_tool_calls

KNOWN = {"search_code", "read_file"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"name": "search_code", "arguments": {"query": "x"}}', [ToolCall("search_code", {"query": "x"})]),
        ('```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```', [ToolCall("read_file", {"path": "a.py"})]),
        ('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>', [ToolCall("read_file", {"path": "a.py"})]),
        ('{"name": "read_file", "parameters": {"path": "a.py"}}', [ToolCall("read_file", {"path": "a.py"})]),
        ('{"name": "read_file", "arguments": "{\\"path\\": \\"a.py\\"}"}', [ToolCall("read_file", {"path": "a.py"})]),
        (
            '{"name": "search_code", "arguments": {"query": "a"}}\n{"name": "read_file", "arguments": {"path": "b"}}',
            [ToolCall("search_code", {"query": "a"}), ToolCall("read_file", {"path": "b"})],
        ),
        ('[{"name": "search_code", "arguments": {"query": "a"}}]', [ToolCall("search_code", {"query": "a"})]),
        (
            "Let me verify.\n1. Search:\n```json\n"
            '{"name": "search_code", "arguments": {"query": "create_db_engine"}}\n```\n'
            "2. Read:\n```json\n"
            '{"name": "read_file", "arguments": {"path": "app/main.py", "start_line": 1}}\n```\n'
            "After executing these tools, I will answer.",
            [
                ToolCall("search_code", {"query": "create_db_engine"}),
                ToolCall("read_file", {"path": "app/main.py", "start_line": 1}),
            ],
        ),
    ],
)
def test_parses_tool_calls_written_as_text(content, expected):
    assert parse_text_tool_calls(content, KNOWN) == expected


@pytest.mark.parametrize(
    "content",
    [
        "The function is defined in app/main.py:12.",
        '{"name": "delete_everything", "arguments": {}}',  # unknown tool
        'The config looks like {"name": "my-app", "version": "1.0"}.',  # JSON, but not a tool call
        '{"name": "read_file", "arguments": "not json"}',
        '{"user": "example json in an answer"}',
        "",
    ],
)
def test_ignores_normal_answers_and_unknown_tools(content):
    assert parse_text_tool_calls(content, KNOWN) == []
