from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.intelligence.reports import BugFinding, BugReport, Improvement, ImprovementReport
from tests.conftest import FakeLLMService
from tests.test_code_structure import DUPLICATED

BASE = "/api/v1/analysis"


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    (sample_repo / "app" / "orders.py").write_text(
        "from app.utils import add\n\n" + DUPLICATED + "\n\ndef broken(user):\n    return user.name.upper()\n",
        encoding="utf-8",
    )
    (sample_repo / "app" / "invoices.py").write_text(DUPLICATED, encoding="utf-8")
    return client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]


def bug(line_start: int, **overrides) -> BugFinding:
    values = dict(
        title="user may be None", severity="High", category="null_handling", status="possible",
        line_start=line_start, line_end=line_start, explanation="AttributeError if user is None",
        suggestion="Check for None first",
    )
    return BugFinding(**(values | overrides))


def test_outline_and_dependencies(client: TestClient, repo_id: int):
    outline = client.get(f"{BASE}/outline", params={"repository_id": repo_id, "path": "app/orders.py"}).json()
    assert [(s["qualified_name"], s["start_line"], s["end_line"]) for s in outline["symbols"]] == [
        ("total", 3, 9), ("broken", 12, 13),
    ]

    deps = client.get(f"{BASE}/dependencies", params={"repository_id": repo_id, "path": "app/utils.py"}).json()
    assert deps["imported_by"] == ["app/orders.py"]


def test_outline_respects_repository_security(client: TestClient, repo_id: int):
    params = {"repository_id": repo_id}
    assert client.get(f"{BASE}/outline", params=params | {"path": "../x.py"}).status_code == 400
    assert client.get(f"{BASE}/outline", params=params | {"path": ".env"}).status_code == 403


def test_duplicates_endpoint(client: TestClient, repo_id: int):
    blocks = client.get(f"{BASE}/duplicates", params={"repository_id": repo_id}).json()

    assert len(blocks) == 1
    assert {loc["path"] for loc in blocks[0]["locations"]} == {"app/orders.py", "app/invoices.py"}


def test_bug_detection_validates_and_stores_findings(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.structured = [BugReport(
        summary="One risk found.",
        findings=[
            bug(13),
            bug(13),  # duplicate of the first
            bug(500, title="hallucinated line"),  # outside the analysed range -> discarded
            bug(5, title="logic error", severity="Critical", status="confirmed", line_end=999),
        ],
    )]

    response = client.post(f"{BASE}/bugs", json={"repository_id": repo_id, "path": "app/orders.py"})

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["discarded_findings"] == 1
    assert [(f["title"], f["severity"], f["line_start"], f["line_end"]) for f in run["findings"]] == [
        ("logic error", "Critical", 5, 13),  # sorted first, end clamped to the file
        ("user may be None", "High", 13, 13),
    ]
    assert run["findings"][1]["snippet"] == "13 |     return user.name.upper()"

    system_prompt, user_prompt = fake_llm.structured_prompts[0]
    assert "confirmed" in system_prompt
    assert "13 |     return user.name.upper()" in user_prompt
    assert "Imports from this repository: app/utils.py" in user_prompt

    stored = client.get(f"{BASE}/runs/{run['id']}").json()
    assert len(stored["findings"]) == 2
    assert client.get(f"{BASE}/runs", params={"repository_id": repo_id}).json()[0]["id"] == run["id"]


def test_bug_detection_on_one_symbol_sends_only_its_lines(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    client.post(f"{BASE}/bugs", json={"repository_id": repo_id, "path": "app/orders.py", "symbol": "broken"})

    user_prompt = fake_llm.structured_prompts[0][1]
    assert "lines 12-13 of 13" in user_prompt
    assert "def total" not in user_prompt.split("```")[1]


def test_thorough_mode_analyses_each_top_level_symbol(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    run = client.post(
        f"{BASE}/bugs", json={"repository_id": repo_id, "path": "app/orders.py", "mode": "thorough"}
    ).json()

    assert len(fake_llm.structured_prompts) == 2
    assert "lines 3-9 of 13" in fake_llm.structured_prompts[0][1]
    assert "lines 12-13 of 13" in fake_llm.structured_prompts[1][1]
    assert run["summary"] == "nothing found nothing found"


def test_unknown_symbol_is_404_with_available_names(client: TestClient, repo_id: int):
    response = client.post(f"{BASE}/bugs", json={"repository_id": repo_id, "path": "app/orders.py", "symbol": "nope"})

    assert response.status_code == 404
    assert "total, broken" in response.json()["detail"]


def test_improvements_include_static_duplicate_findings(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.structured = [ImprovementReport(
        summary="Could be simpler.",
        suggestions=[Improvement(
            title="Use sum() with a generator", category="clean_code", impact="Low", line_start=4, line_end=7,
            recommendation="Replace the loop with sum(...)", benefits="Shorter", risks="None significant",
        )],
    )]

    run = client.post(f"{BASE}/improvements", json={"repository_id": repo_id, "path": "app/orders.py"}).json()

    sources = [(f["source"], f["category"]) for f in run["findings"]]
    assert ("static", "duplication") in sources
    assert ("llm", "clean_code") in sources
    duplicate = next(f for f in run["findings"] if f["source"] == "static")
    assert "app/invoices.py:1-7" in duplicate["explanation"]


def test_explain_uses_chat(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    response = client.post(f"{BASE}/explain", json={"repository_id": repo_id, "path": "app/utils.py"})

    assert response.status_code == 200
    body = response.json()
    assert body["explanation"].startswith("echo: Analyse `app/utils.py`")
    assert (body["start_line"], body["end_line"]) == (1, 2)


def test_agent_analyze_file_tool(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    from tests.conftest import answer_turn, tool_turn

    fake_llm.turns = [tool_turn(("analyze_file", {"path": "app/utils.py"})), answer_turn("done")]

    body = client.post("/api/v1/agent/chat", json={"repository_id": repo_id, "message": "q"}).json()

    output = body["steps"][0]["output"]
    assert "function add (lines 1-2)" in output
    assert "Imported by: app/orders.py" in output
