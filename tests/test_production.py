"""Production concerns: secret redaction, request ids, readiness, logging, migrations, caching, and a full workflow."""

import json
import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.core.logging import JsonFormatter, RequestIdFilter, request_id_var
from app.core.secrets import REDACTED, redact
from app.services.code import cache
from tests.conftest import FakeLLMService, answer_turn, tool_turn

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"
SETTINGS_PY = (
    "import os\n\n"
    f'API_KEY = "{FAKE_KEY}"\n'
    'DATABASE_URL = "postgresql://admin:S3cretPass@db.internal:5432/app"\n'
    'password = os.environ["DB_PASSWORD"]\n'
    "TIMEOUT = 30\n"
)


# ----- secret redaction ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f'key = "{FAKE_KEY}"', FAKE_KEY),
        ('aws_id = "AKIAABCDEFGHIJKLMNOP"', "AKIAABCDEFGHIJKLMNOP"),
        ("token: ghp_" + "x" * 36, "ghp_" + "x" * 36),
        ('db_password = "hunter2hunter2"', "hunter2hunter2"),
        ('"clientSecret": "abcdef123456"', "abcdef123456"),
        ("redis://user:topsecret@cache:6379", "topsecret"),
    ],
)
def test_redact_masks_common_secret_formats(text: str, secret: str):
    result = redact(text)

    assert secret not in result.text
    assert REDACTED in result.text
    assert result.count >= 1


def test_redact_keeps_code_that_only_references_secrets():
    code = 'password = os.environ["DB_PASSWORD"]\napi_key = load_key()\nTIMEOUT = 30\n'

    assert redact(code).text == code


def test_redact_preserves_line_numbers_for_multiline_keys():
    text = "line1\n-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----\nline6\n"

    result = redact(text).text

    assert result.splitlines()[5] == "line6"
    assert "AAAA" not in result


@pytest.fixture
def repo_id(client: TestClient, sample_repo: Path) -> int:
    (sample_repo / "app" / "settings.py").write_text(SETTINGS_PY, encoding="utf-8")
    return client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()["id"]


def test_model_never_sees_hardcoded_secrets(client: TestClient, fake_llm: FakeLLMService, repo_id: int):
    fake_llm.turns = [
        tool_turn(("read_file", {"path": "app/settings.py"}), ("search_code", {"query": "API_KEY"})),
        answer_turn("ok"),
    ]
    client.post("/api/v1/agent/chat", json={"repository_id": repo_id, "message": "show settings"})
    client.post("/api/v1/analysis/bugs", json={"repository_id": repo_id, "path": "app/settings.py"})

    everything_sent = "\n".join(m.content for msgs, _ in fake_llm.agent_calls for m in msgs)
    everything_sent += "\n".join(prompt for pair in fake_llm.structured_prompts for prompt in pair)
    assert FAKE_KEY not in everything_sent
    assert "S3cretPass" not in everything_sent
    assert 'password = os.environ["DB_PASSWORD"]' in everything_sent  # ordinary code still visible


def test_the_developer_still_sees_their_own_file(client: TestClient, repo_id: int):
    body = client.get(f"/api/v1/repositories/{repo_id}/file", params={"path": "app/settings.py"}).json()

    assert FAKE_KEY in body["content"]


def test_patch_cannot_write_redaction_placeholder(client: TestClient, fake_llm: FakeLLMService, repo_id: int, sample_repo: Path):
    fake_llm.chat_replies = [
        f'<<<<<<< SEARCH\nTIMEOUT = 30\n=======\nTIMEOUT = 60\n>>>>>>> REPLACE\n'
        f'<<<<<<< SEARCH\nAPI_KEY = "{REDACTED}"\n=======\nAPI_KEY = "{REDACTED}"  # rotated\n>>>>>>> REPLACE\n'
        "EXPLANATION: done"
    ]

    change = client.post("/api/v1/changes", json={
        "repository_id": repo_id, "request": "raise the timeout", "paths": ["app/settings.py"],
    }).json()

    file = change["files"][0]
    assert "+TIMEOUT = 60" in file["diff"]
    assert REDACTED not in file["diff"]
    assert "touched a masked secret" in file["notes"]
    assert "masked before the model saw this file" in file["notes"]
    assert FAKE_KEY not in fake_llm.chat_prompts[0]


# ----- operations ---------------------------------------------------------------------------------

def test_request_id_header(client: TestClient):
    generated = client.get("/health")
    assert len(generated.headers["X-Request-ID"]) == 16

    echoed = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["X-Request-ID"] == "trace-abc-123"

    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\r\ninjected"})
    assert unsafe.headers["X-Request-ID"] != "bad id\r\ninjected"


def test_ready_reports_database_and_model(client: TestClient):
    body = client.get("/ready").json()

    assert body == {"status": "ready", "database": "ok", "ollama": "ok", "model": "fake-model", "model_available": True}


def test_json_log_format_includes_request_id():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    token = request_id_var.set("req-42")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)

    entry = json.loads(JsonFormatter().format(record))

    assert entry["message"] == "hello world"
    assert entry["request_id"] == "req-42"
    assert entry["level"] == "INFO"


def test_migrations_create_exactly_the_model_schema(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    engine = create_db_engine(url)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []

    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata)  # tables gone
    engine.dispose()


def test_dependency_graph_is_cached_until_rescan(
    client: TestClient, repo_id: int, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.services.code import insight

    cache.clear()
    calls = []
    original = insight.build_dependency_graph
    monkeypatch.setattr(insight, "build_dependency_graph", lambda *a: calls.append(1) or original(*a))
    params = {"repository_id": repo_id, "path": "app/utils.py"}

    client.get("/api/v1/analysis/dependencies", params=params)
    client.get("/api/v1/analysis/dependencies", params=params)
    assert len(calls) == 1

    (sample_repo / "app" / "uses_utils.py").write_text("from app.utils import add\n", encoding="utf-8")
    client.post(f"/api/v1/repositories/{repo_id}/scan")
    deps = client.get("/api/v1/analysis/dependencies", params=params).json()

    assert len(calls) == 2
    assert "app/uses_utils.py" in deps["imported_by"]


# ----- the whole workflow, end to end -------------------------------------------------------------

def test_full_workflow(client: TestClient, fake_llm: FakeLLMService, sample_repo: Path):
    """Register -> ask the agent -> find bugs -> generate, approve and run a test -> patch -> approve -> roll back."""
    (sample_repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    repo = client.post("/api/v1/repositories", json={"path": str(sample_repo)}).json()

    fake_llm.turns = [tool_turn(("find_function", {"name": "add"})), answer_turn("add is in app/utils.py:1")]
    chat = client.post("/api/v1/agent/chat", json={"repository_id": repo["id"], "message": "Where is add?"}).json()
    assert chat["answer"] == "add is in app/utils.py:1"

    bugs = client.post("/api/v1/analysis/bugs", json={"repository_id": repo["id"], "path": "app/utils.py"})
    assert bugs.status_code == 200

    fake_llm.chat_replies = [
        "```python\nfrom app.utils import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n```\nTEST CASES:\n- add\nNOTES:\nnone",
    ]
    generated = client.post("/api/v1/tests/generate", json={"repository_id": repo["id"], "path": "app/utils.py"}).json()
    client.post(f"/api/v1/tests/generated/{generated['id']}/approve", json={})
    run = client.post("/api/v1/tests/run", json={"repository_id": repo["id"], "framework": "pytest"}).json()
    assert (run["exit_code"], run["passed"]) == (0, 1), run["output"]

    fake_llm.chat_replies = ["<<<<<<< SEARCH\n    return a + b\n=======\n    return b + a\n>>>>>>> REPLACE\nEXPLANATION: swap"]
    change = client.post("/api/v1/changes", json={
        "repository_id": repo["id"], "request": "swap the operands", "paths": ["app/utils.py"],
    }).json()
    assert client.post(f"/api/v1/changes/{change['id']}/approve", json={}).json()["status"] == "applied"
    rerun = client.post("/api/v1/tests/run", json={"repository_id": repo["id"], "framework": "pytest"}).json()
    assert rerun["passed"] == 1
    assert client.post(f"/api/v1/changes/{change['id']}/rollback").json()["status"] == "rolled_back"
    assert (sample_repo / "app" / "utils.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
