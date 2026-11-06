from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1/repositories"


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    response = client.post(BASE, json={"path": str(sample_repo)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_register_builds_index_and_summary(client: TestClient, sample_repo: Path):
    response = client.post(BASE, json={"path": str(sample_repo), "name": "Sample"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Sample"
    assert body["scan_summary"]["file_count"] == 10
    assert body["scan_summary"]["frameworks"] == ["FastAPI", "React"]
    assert body["scan_summary"]["skipped"]["sensitive"] == 2


def test_register_rejects_duplicate_and_invalid_paths(client: TestClient, repo_id: int, sample_repo: Path):
    assert client.post(BASE, json={"path": str(sample_repo)}).status_code == 409
    assert client.post(BASE, json={"path": "relative/path"}).status_code == 400
    assert client.post(BASE, json={"path": str(sample_repo / "nope")}).status_code == 400


def test_list_get_and_remove(client: TestClient, repo_id: int, sample_repo: Path):
    assert [r["id"] for r in client.get(BASE).json()] == [repo_id]
    assert client.get(f"{BASE}/{repo_id}").json()["root_path"] == str(sample_repo)

    assert client.delete(f"{BASE}/{repo_id}").status_code == 204
    assert client.get(f"{BASE}/{repo_id}").status_code == 404
    assert (sample_repo / "app" / "main.py").exists()  # removing never deletes files


def test_list_files_with_filters(client: TestClient, repo_id: int):
    python_files = client.get(f"{BASE}/{repo_id}/files", params={"language": "python"}).json()
    assert python_files["total"] == 2
    assert [f["path"] for f in python_files["items"]] == ["app/main.py", "app/utils.py"]

    page = client.get(f"{BASE}/{repo_id}/files", params={"limit": 3, "offset": 0}).json()
    assert page["total"] == 10
    assert len(page["items"]) == 3

    by_prefix = client.get(f"{BASE}/{repo_id}/files", params={"path_prefix": "src/"}).json()
    assert [f["path"] for f in by_prefix["items"]] == ["src/index.ts"]


def test_read_file_full_and_line_range(client: TestClient, repo_id: int):
    full = client.get(f"{BASE}/{repo_id}/file", params={"path": "app/main.py"}).json()
    assert full["total_lines"] == 4
    assert full["language"] == "Python"
    assert full["content"].startswith("def hello():")

    part = client.get(f"{BASE}/{repo_id}/file", params={"path": "app\\main.py", "start_line": 2, "end_line": 2})
    assert part.json()["content"] == "    return 'hi'"
    assert (part.json()["start_line"], part.json()["end_line"]) == (2, 2)


@pytest.mark.parametrize(
    ("path", "status_code"),
    [
        ("../outside.txt", 400),
        ("C:/Windows/win.ini", 400),
        (".env", 403),
        ("keys/server.pem", 403),
        ("node_modules/lib/index.js", 404),
        ("debug.log", 404),
        ("assets/logo.png", 404),
        ("missing.py", 404),
    ],
)
def test_read_file_blocks_unsafe_or_unindexed_paths(client: TestClient, repo_id: int, path: str, status_code: int):
    response = client.get(f"{BASE}/{repo_id}/file", params={"path": path})

    assert response.status_code == status_code, response.text


def test_read_file_rejects_bad_line_range(client: TestClient, repo_id: int):
    response = client.get(f"{BASE}/{repo_id}/file", params={"path": "app/main.py", "start_line": 50})

    assert response.status_code == 400


def test_rescan_picks_up_new_files(client: TestClient, repo_id: int, sample_repo: Path):
    (sample_repo / "app" / "new_module.py").write_text("NEW = 1\n", encoding="utf-8")
    assert client.get(f"{BASE}/{repo_id}/file", params={"path": "app/new_module.py"}).status_code == 404

    rescanned = client.post(f"{BASE}/{repo_id}/scan").json()

    assert rescanned["scan_summary"]["file_count"] == 11
    assert client.get(f"{BASE}/{repo_id}/file", params={"path": "app/new_module.py"}).status_code == 200


def test_search_literal_is_case_insensitive_by_default(client: TestClient, repo_id: int):
    body = client.get(f"{BASE}/{repo_id}/search", params={"q": "todo"}).json()

    assert {(m["path"], m["line_number"]) for m in body["matches"]} == {("app/main.py", 4), ("src/index.ts", 1)}
    assert body["truncated"] is False


def test_search_options(client: TestClient, repo_id: int):
    case_sensitive = client.get(f"{BASE}/{repo_id}/search", params={"q": "TODO", "case_sensitive": True}).json()
    assert [m["path"] for m in case_sensitive["matches"]] == ["app/main.py"]

    regex = client.get(f"{BASE}/{repo_id}/search", params={"q": r"^def \w+\(", "regex": True}).json()
    assert [m["path"] for m in regex["matches"]] == ["app/main.py", "app/utils.py"]

    globbed = client.get(f"{BASE}/{repo_id}/search", params={"q": "todo", "path_glob": "src/*"}).json()
    assert [m["path"] for m in globbed["matches"]] == ["src/index.ts"]

    limited = client.get(f"{BASE}/{repo_id}/search", params={"q": "def", "max_results": 1}).json()
    assert len(limited["matches"]) == 1
    assert limited["truncated"] is True


def test_search_never_returns_secrets(client: TestClient, repo_id: int):
    body = client.get(f"{BASE}/{repo_id}/search", params={"q": "super-secret"}).json()

    assert body["matches"] == []


def test_search_rejects_invalid_regex(client: TestClient, repo_id: int):
    response = client.get(f"{BASE}/{repo_id}/search", params={"q": "(unclosed", "regex": True})

    assert response.status_code == 400
    assert "Invalid regular expression" in response.json()["detail"]
