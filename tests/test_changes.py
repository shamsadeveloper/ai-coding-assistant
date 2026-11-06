"""Changes: edit blocks, patch proposals, approval on a Git branch, conflicts, rollback."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.changes.edits import Edit, apply_edits, parse_edit_blocks, unified_diff
from app.services.changes.service import FileSelection
from app.services.repository.writer import SafeFileWriter
from tests.conftest import FakeLLMService, answer_turn, tool_turn

BASE = "/api/v1/changes"
UTILS = "def add(a, b):\n    return a + b\n"


def block(search: str, replace: str, explanation: str = "Done.") -> str:
    return f"<<<<<<< SEARCH\n{search}=======\n{replace}>>>>>>> REPLACE\nEXPLANATION: {explanation}"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    return client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]


@pytest.fixture
def git_repo(sample_repo: Path) -> Path:
    git(sample_repo, "init", "-q")
    git(sample_repo, "add", "-A")
    git(sample_repo, "commit", "-q", "-m", "initial")
    return sample_repo


# ----- edit engine ------------------------------------------------------------------------------

def test_parse_edit_blocks_and_explanation():
    reply = "Here you go\n" + block("    return a + b\n", "    return a + b + 0\n", "Adds zero.")

    edits, explanation = parse_edit_blocks(reply)

    assert edits == [Edit("    return a + b\n", "    return a + b + 0\n")]
    assert explanation == "Adds zero."


def test_apply_exact_edit():
    outcome = apply_edits(UTILS, [Edit("    return a + b\n", "    return int(a) + int(b)\n")])

    assert outcome.content == "def add(a, b):\n    return int(a) + int(b)\n"
    assert outcome.failures == []


def test_apply_tolerates_lost_indentation_and_reindents_replacement():
    source = "class A:\n    def run(self):\n        x = 1\n        return x\n"
    outcome = apply_edits(source, [Edit("x = 1\nreturn x\n", "x = 2\nreturn x * 10\n")])

    assert outcome.content == "class A:\n    def run(self):\n        x = 2\n        return x * 10\n"


def test_apply_refuses_ambiguous_or_missing_text():
    source = "x = 1\ny = 2\nx = 1\n"
    outcome = apply_edits(source, [Edit("x = 1\n", "x = 3\n"), Edit("z = 9\n", "z = 0\n")])

    assert outcome.content == source
    assert outcome.applied == 0
    assert "more than one place" in outcome.failures[0]
    assert "not found" in outcome.failures[1]


def test_apply_preserves_crlf_line_endings():
    outcome = apply_edits("a = 1\r\nb = 2\r\n", [Edit("b = 2\n", "b = 3\n")])

    assert outcome.content == "a = 1\r\nb = 3\r\n"


def test_unified_diff_format():
    diff = unified_diff("app/utils.py", UTILS, UTILS.replace("a + b", "a - b"))

    assert diff.startswith("--- a/app/utils.py\n+++ b/app/utils.py\n")
    assert "-    return a + b\n+    return a - b\n" in diff


# ----- proposals ----------------------------------------------------------------------------------

def test_propose_creates_diff_without_touching_disk(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path
):
    fake_llm.chat_replies = [block("    return a + b\n", "    return a - b\n", "Subtracts instead.")]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "make add subtract", "paths": ["app/utils.py"]}).json()

    assert change["status"] == "proposed"
    assert change["summary"] == "app/utils.py: Subtracts instead."
    assert "+    return a - b" in change["files"][0]["diff"]
    assert change["files"][0]["syntax_ok"] is True
    assert (sample_repo / "app" / "utils.py").read_text(encoding="utf-8") == UTILS
    assert "```\ndef add(a, b):" in fake_llm.chat_prompts[0]  # raw file content, not numbered


def test_failed_edit_gets_one_retry(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = [
        block("    return a * b\n", "    return a - b\n"),  # SEARCH text does not exist
        block("    return a + b\n", "    return a - b\n"),
    ]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "make add subtract", "paths": ["app/utils.py"]}).json()

    assert change["status"] == "proposed"
    assert len(fake_llm.chat_prompts) == 2
    assert "could not be applied" in fake_llm.chat_prompts[1]


def test_no_usable_edit_marks_change_failed(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = ["I am not sure.", "Still not sure."]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "do something", "paths": ["app/utils.py"]}).json()

    assert change["status"] == "failed"
    assert "no edit blocks" in change["files"][0]["notes"]
    assert client.post(f"{BASE}/{change['id']}/approve", json={}).status_code == 409


def test_whole_file_reply_is_accepted_when_close_to_original(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    # Verbatim behaviour of qwen2.5-coder: the full file in a fence instead of edit blocks.
    fake_llm.chat_replies = [
        "```\ndef add(a, b):\n    if a is None:\n        return b\n    return a + b\n```\n\nEXPLANATION: Handles None."
    ]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "handle None in add", "paths": ["app/utils.py"]}).json()

    file = change["files"][0]
    assert change["status"] == "proposed"
    assert "+    if a is None:" in file["diff"]
    assert "whole file instead of edit blocks" in file["notes"]
    assert len(fake_llm.chat_prompts) == 1


def test_whole_file_reply_that_rewrites_everything_is_refused(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = ["```\nprint('completely different program')\n```", "no idea"]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "handle None in add", "paths": ["app/utils.py"]}).json()

    assert change["status"] == "failed"


def test_syntax_error_in_patch_is_flagged(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.chat_replies = [block("    return a + b\n", "    return (a + b\n")]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "break it", "paths": ["app/utils.py"]}).json()

    assert change["files"][0]["syntax_ok"] is False
    assert "SyntaxError" in change["files"][0]["notes"]


def test_new_file_proposal(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    fake_llm.chat_replies = ["```python\ndef mul(a, b):\n    return a * b\n```\nEXPLANATION: New helper."]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "add a mul helper", "paths": ["app/math_utils.py"]}).json()

    file = change["files"][0]
    assert file["is_new"] is True
    assert file["diff"].startswith("--- /dev/null\n+++ b/app/math_utils.py")
    assert not (sample_repo / "app" / "math_utils.py").exists()


def test_automatic_file_selection_only_allows_candidates(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.structured = [FileSelection(paths=["app/utils.py", "../../etc/passwd"], reason="add lives here")]
    fake_llm.chat_replies = [block("    return a + b\n", "    return b + a\n")]

    change = client.post(BASE, json={"repository_id": repo_id, "request": "swap the operands in add"}).json()

    assert [f["path"] for f in change["files"]] == ["app/utils.py"]
    assert "app/utils.py" in fake_llm.structured_prompts[0][1]


@pytest.mark.parametrize(
    ("path", "status_code"),
    [(".env", 403), ("../outside.py", 400), (".git/config", 403), ("node_modules/x.js", 403), ("debug.log", 404)],
)
def test_proposals_cannot_target_unsafe_paths(client: TestClient, repo_id: int, path: str, status_code: int):
    response = client.post(BASE, json={"repository_id": repo_id, "request": "change it", "paths": [path]})

    assert response.status_code == status_code, response.text


# ----- approval, conflicts, rollback --------------------------------------------------------------

def propose_subtract(client: TestClient, fake_llm: FakeLLMService, repo_id: int) -> dict:
    fake_llm.chat_replies = [block("    return a + b\n", "    return a - b\n")]
    return client.post(BASE, json={"repository_id": repo_id, "request": "make add subtract", "paths": ["app/utils.py"]}).json()


def test_approve_on_git_branch_and_rollback(client: TestClient, fake_llm: FakeLLMService, git_repo: Path, repo_id: int):
    base_branch = git(git_repo, "rev-parse", "--abbrev-ref", "HEAD")
    change = propose_subtract(client, fake_llm, repo_id)

    applied = client.post(f"{BASE}/{change['id']}/approve", json={}).json()

    assert applied["status"] == "applied"
    assert applied["branch_name"] == f"ai-assistant/change-{change['id']}"
    assert applied["base_branch"] == base_branch
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == applied["branch_name"]
    assert "return a - b" in (git_repo / "app" / "utils.py").read_text(encoding="utf-8")
    assert Path(applied["files"][0]["backup_path"]).read_text(encoding="utf-8") == UTILS
    assert "+    return a - b" in client.get("/api/v1/git/diff", params={"repository_id": repo_id}).text
    assert client.get("/api/v1/git/status", params={"repository_id": repo_id}).json()["changes"] == [" M app/utils.py"]

    rolled_back = client.post(f"{BASE}/{change['id']}/rollback").json()

    assert rolled_back["status"] == "rolled_back"
    assert (git_repo / "app" / "utils.py").read_text(encoding="utf-8") == UTILS
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == base_branch


def test_approve_without_git_uses_backups_only(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    change = propose_subtract(client, fake_llm, repo_id)

    applied = client.post(f"{BASE}/{change['id']}/approve", json={}).json()

    assert applied["status"] == "applied"
    assert applied["branch_name"] is None
    assert "return a - b" in (sample_repo / "app" / "utils.py").read_text(encoding="utf-8")


def test_file_changed_after_proposal_is_a_conflict(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    change = propose_subtract(client, fake_llm, repo_id)
    (sample_repo / "app" / "utils.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")

    response = client.post(f"{BASE}/{change['id']}/approve", json={})

    assert response.status_code == 409
    assert "changed after the patch was proposed" in response.json()["detail"]
    assert "a + b + 1" in (sample_repo / "app" / "utils.py").read_text(encoding="utf-8")


def test_uncommitted_changes_block_branch_creation(client: TestClient, fake_llm: FakeLLMService, git_repo: Path, repo_id: int):
    (git_repo / "app" / "utils.py").write_text(UTILS + "# local edit\n", encoding="utf-8")
    client.post(f"/api/v1/repositories/{repo_id}/scan")
    fake_llm.chat_replies = [block("    return a + b\n", "    return a - b\n")]
    change = client.post(BASE, json={"repository_id": repo_id, "request": "make add subtract", "paths": ["app/utils.py"]}).json()

    response = client.post(f"{BASE}/{change['id']}/approve", json={})

    assert response.status_code == 409
    assert "commit or stash" in response.json()["detail"]


def test_apply_is_all_or_nothing(
    client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_llm.chat_replies = [
        block("    return a + b\n", "    return a - b\n"),
        block("    return 'hi'\n", "    return 'hello'\n"),
    ]
    change = client.post(BASE, json={
        "repository_id": repo_id, "request": "two edits", "paths": ["app/utils.py", "app/main.py"],
    }).json()
    original_write, calls = SafeFileWriter.write, []

    def failing_second_write(self, *args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise OSError("disk full")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(SafeFileWriter, "write", failing_second_write)

    response = client.post(f"{BASE}/{change['id']}/approve", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"  # no internals leaked
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert (sample_repo / "app" / "utils.py").read_text(encoding="utf-8") == UTILS  # first write undone
    assert client.get(f"{BASE}/{change['id']}").json()["status"] == "proposed"


def test_reject_and_state_rules(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    change = propose_subtract(client, fake_llm, repo_id)

    assert client.post(f"{BASE}/{change['id']}/rollback").status_code == 409  # not applied
    assert client.post(f"{BASE}/{change['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"{BASE}/{change['id']}/approve", json={}).status_code == 409
    assert [c["id"] for c in client.get(BASE, params={"repository_id": repo_id}).json()] == [change["id"]]


def test_agent_can_propose_but_never_apply(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    fake_llm.chat_replies = [block("    return a + b\n", "    return a - b\n")]
    fake_llm.turns = [
        tool_turn(("generate_code_patch", {"request": "make add subtract", "paths": ["app/utils.py"]})),
        answer_turn("proposed"),
    ]

    body = client.post("/api/v1/agent/chat", json={"repository_id": repo_id, "message": "fix add"}).json()

    assert "has NOT been applied" in body["steps"][0]["output"]
    assert "+    return a - b" in body["steps"][0]["output"]
    assert (sample_repo / "app" / "utils.py").read_text(encoding="utf-8") == UTILS
