"""Testing: framework detection, coverage gaps, generation with review/approval, and real test runs."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.testing.frameworks import is_test_file, proposed_test_path
from app.services.testing.runner import parse_counts
from app.services.testing.service import add_missing_imports, parse_generated_test
from tests.conftest import FakeLLMService, answer_turn, tool_turn

BASE = "/api/v1/tests"
PASSING_TEST = "from app.utils import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    (sample_repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (sample_repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests" / "test_utils.py").write_text(PASSING_TEST, encoding="utf-8")
    return client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]


def generated(content: str) -> str:
    """A reply in the format the generation prompt asks for."""
    return f"```python\n{content}```\nTEST CASES:\n- returns hi\nNOTES:\nnone"


# Verbatim qwen2.5-coder:7b output from a live run: sections inside the fence, no `import pytest`.
QWEN_REPLY = '''```python
from shop.pricing import apply_discount

def test_apply_discount_with_negative_price():
    with pytest.raises(ValueError) as exc_info:
        apply_discount(-100, 10)

TEST CASES:
- test_apply_discount_with_negative_price: Tests a negative price raises ValueError.

NOTES:
- Assumes the function raises ValueError.
```'''


def test_parse_real_model_reply_and_add_missing_import():
    parsed = parse_generated_test(QWEN_REPLY)

    assert parsed.content.startswith("from shop.pricing import apply_discount")
    assert "TEST CASES" not in parsed.content
    assert parsed.test_cases == ["test_apply_discount_with_negative_price: Tests a negative price raises ValueError."]
    assert parsed.notes == "- Assumes the function raises ValueError."
    assert add_missing_imports("pytest", parsed.content).startswith("import pytest\nfrom shop.pricing")
    assert add_missing_imports("pytest", "import pytest\npytest.raises") == "import pytest\npytest.raises"


def test_parse_reply_without_fence():
    parsed = parse_generated_test("def test_a():\n    assert True\nNOTES:\nnone")

    assert parsed.content == "def test_a():\n    assert True\n"
    assert parsed.notes == "none"


# ----- pure helpers ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_utils.py", True), ("app/utils_test.py", True), ("conftest.py", True), ("app/utils.py", False),
        ("src/a.test.ts", True), ("src/__tests__/a.js", True), ("src/a.ts", False),
        ("pkg/a_test.go", True), ("src/test/java/AppTest.java", True), ("UserServiceTest.php", True),
    ],
)
def test_is_test_file(path: str, expected: bool):
    assert is_test_file(path) is expected


@pytest.mark.parametrize(
    ("framework", "source", "expected"),
    [
        ("pytest", "app/services/user.py", "tests/test_user.py"),
        ("jest", "src/lib/math.ts", "src/lib/math.test.ts"),
        ("go", "pkg/store/store.go", "pkg/store/store_test.go"),
        ("maven", "src/main/java/com/x/Account.java", "src/test/java/com/x/AccountTest.java"),
        ("phpunit", "src/UserService.php", "tests/UserServiceTest.php"),
    ],
)
def test_proposed_test_path(framework: str, source: str, expected: str):
    assert proposed_test_path(framework, source) == expected


@pytest.mark.parametrize(
    ("framework", "output", "counts"),
    [
        ("pytest", "..F.s\n3 passed, 1 failed, 1 skipped in 0.12s", (3, 1, None, 1)),
        ("pytest", "1 failed, 2 passed, 1 error in 1.0s", (2, 1, 1, None)),
        ("jest", "Tests:       1 failed, 4 passed, 5 total", (4, 1, None, None)),
        ("unittest", "Ran 4 tests in 0.01s\n\nFAILED (failures=1, errors=1)", (2, 1, 1, None)),
    ],
)
def test_parse_counts(framework: str, output: str, counts: tuple):
    c = parse_counts(framework, output)
    assert (c.passed, c.failed, c.errors, c.skipped) == counts


# ----- detection and gaps ---------------------------------------------------------------------

def test_frameworks_and_gaps(client: TestClient, repo_id: int):
    frameworks = client.get(f"{BASE}/frameworks", params={"repository_id": repo_id}).json()
    assert [f["name"] for f in frameworks] == ["pytest"]

    gaps = {g["path"]: g for g in client.get(f"{BASE}/gaps", params={"repository_id": repo_id}).json()}
    assert gaps["app/main.py"]["untested_symbols"] == ["hello"]
    assert "app/utils.py" not in gaps  # add() is used in tests/test_utils.py


# ----- generation, review, approval -------------------------------------------------------------

def test_generate_saves_to_review_area_only(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path, test_settings: Settings
):
    fake_llm.chat_replies = [generated("from app.main import hello\n\ndef test_hello():\n    assert hello() == 'hi'\n")]

    body = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/main.py"}).json()

    assert body["status"] == "pending" and body["syntax_ok"] and body["framework"] == "pytest"
    assert body["target_path"] == "tests/test_main.py"
    assert body["content"].startswith("from app.main import hello")  # markdown fence stripped
    assert body["test_cases"] == ["returns hi"]
    assert not (sample_repo / "tests" / "test_main.py").exists()  # repository untouched
    assert Path(body["review_path"]).read_text(encoding="utf-8") == body["content"]
    assert Path(body["review_path"]).is_relative_to(test_settings.data_path)

    prompt = fake_llm.chat_prompts[0]
    assert "from app.main import ..." in prompt
    assert "tests/test_utils.py" in prompt  # existing test given as a style example


def test_syntax_error_triggers_one_repair(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = [generated("def test_x(:\n    pass\n"), generated("def test_x():\n    pass\n")]

    body = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/main.py"}).json()

    assert body["syntax_ok"] is True
    assert len(fake_llm.chat_prompts) == 2
    assert "SyntaxError on line 1" in fake_llm.chat_prompts[1]


def test_unrepaired_syntax_error_is_flagged(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = [generated("def broken(:\n"), generated("def still_broken(:\n")]

    body = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/main.py"}).json()

    assert body["syntax_ok"] is False
    assert "SyntaxError" in body["notes"]


def test_approve_writes_into_repository_and_reindexes(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path
):
    fake_llm.chat_replies = [generated("def test_ok():\n    assert True\n")]
    test_id = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/main.py"}).json()["id"]

    approved = client.post(f"{BASE}/generated/{test_id}/approve", json={}).json()

    assert approved["status"] == "applied"
    assert (sample_repo / "tests" / "test_main.py").read_text(encoding="utf-8") == "def test_ok():\n    assert True\n"
    read = client.get(f"/api/v1/repositories/{repo_id}/file", params={"path": "tests/test_main.py"})
    assert read.status_code == 200
    assert client.post(f"{BASE}/generated/{test_id}/approve", json={}).status_code == 409  # already applied


def test_existing_test_is_never_overwritten_without_explicit_flag(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path
):
    fake_llm.chat_replies = [generated("def test_new():\n    pass\n")]
    record = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/utils.py"}).json()
    assert record["target_path"] == "tests/test_utils.py"
    assert record["target_exists"] is True

    refused = client.post(f"{BASE}/generated/{record['id']}/approve", json={})
    assert refused.status_code == 409
    assert (sample_repo / "tests" / "test_utils.py").read_text(encoding="utf-8") == PASSING_TEST

    applied = client.post(f"{BASE}/generated/{record['id']}/approve", json={"overwrite": True}).json()
    assert Path(applied["backup_path"]).read_text(encoding="utf-8") == PASSING_TEST


def test_reject(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    fake_llm.chat_replies = [generated("def test_x():\n    pass\n")]
    test_id = client.post(f"{BASE}/generate", json={"repository_id": repo_id, "path": "app/main.py"}).json()["id"]

    assert client.post(f"{BASE}/generated/{test_id}/reject").json()["status"] == "rejected"
    assert client.post(f"{BASE}/generated/{test_id}/approve", json={}).status_code == 409
    assert not (sample_repo / "tests" / "test_main.py").exists()


# ----- running tests ------------------------------------------------------------------------------

def test_run_real_pytest(client: TestClient, repo_id: int):
    run = client.post(f"{BASE}/run", json={"repository_id": repo_id}).json()

    assert run["framework"] == "pytest"
    assert run["exit_code"] == 0, run["output"]
    assert (run["passed"], run["failed"]) == (1, None)
    assert run["command"][1:3] == ["-m", "pytest"]


def test_run_reports_failures_and_analysis(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    (sample_repo / "tests" / "test_fail.py").write_text("def test_bad():\n    assert 1 == 2\n", encoding="utf-8")
    client.post(f"/api/v1/repositories/{repo_id}/scan")

    run = client.post(f"{BASE}/run", json={"repository_id": repo_id, "path": "tests/test_fail.py"}).json()

    assert run["exit_code"] == 1
    assert run["failed"] == 1
    assert run["target_path"] == "tests/test_fail.py"
    analysed = client.post(f"{BASE}/runs/{run['id']}/analyze").json()
    assert "assert 1 == 2" in analysed["analysis"]  # the fake LLM echoes its prompt


def test_server_secrets_do_not_reach_test_process(
    client: TestClient, repo_id: int, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("APP_SECRET_TOKEN", "super-secret-value")
    (sample_repo / "tests" / "test_env.py").write_text(
        "import os\n\ndef test_no_secret():\n    assert os.environ.get('APP_SECRET_TOKEN') is None\n", encoding="utf-8"
    )
    client.post(f"/api/v1/repositories/{repo_id}/scan")

    run = client.post(f"{BASE}/run", json={"repository_id": repo_id, "path": "tests/test_env.py"}).json()

    assert run["exit_code"] == 0, run["output"]


def test_run_rejects_unsafe_or_unknown_input(client: TestClient, repo_id: int, test_settings: Settings):
    assert client.post(f"{BASE}/run", json={"repository_id": repo_id, "path": "../x.py"}).status_code == 400
    assert client.post(f"{BASE}/run", json={"repository_id": repo_id, "framework": "rm"}).status_code == 400

    test_settings.test_execution_enabled = False
    response = client.post(f"{BASE}/run", json={"repository_id": repo_id})
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_agent_can_generate_but_not_apply(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    fake_llm.chat_replies = [generated("def test_hello():\n    pass\n")]
    fake_llm.turns = [tool_turn(("generate_unit_test", {"path": "app/main.py"})), answer_turn("generated")]

    body = client.post("/api/v1/agent/chat", json={"repository_id": repo_id, "message": "write tests"}).json()

    assert "NOT been added to the repository" in body["steps"][0]["output"]
    assert not (sample_repo / "tests" / "test_main.py").exists()
    assert os.path.exists(client.get(f"{BASE}/generated", params={"repository_id": repo_id}).json()[0]["review_path"])
