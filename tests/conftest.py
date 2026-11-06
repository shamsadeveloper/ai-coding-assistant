import os

# Keep tests away from the developer's real database; must run before the app is imported.
os.environ["APP_DATABASE_URL"] = "sqlite:///:memory:"

from collections.abc import Iterator, Sequence  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.dependencies import get_db, get_llm_service, get_session_factory  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.database import create_db_engine, init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.services.llm_service import (  # noqa: E402
    AgentMessage,
    AssistantTurn,
    ChatMessage,
    LLMHealth,
    LLMResult,
    ToolCall,
)
from app.services.repository.manager import RepositoryManager  # noqa: E402

TEST_MAX_FILE_BYTES = 2_000


def tool_turn(*calls: tuple[str, dict]) -> AssistantTurn:
    return AssistantTurn("fake-model", "", [ToolCall(name, args) for name, args in calls], 10, 5)


def answer_turn(text: str) -> AssistantTurn:
    return AssistantTurn("fake-model", text, [], 10, 5)


class FakeLLMService:
    """In-memory stand-in for Ollama so unit tests are fast and deterministic.

    For the agent, queue replies in ``turns``; each ``chat_with_tools`` call pops one.
    """

    def __init__(self) -> None:
        self.received: list[ChatMessage] = []
        self.received_model: str | None = None
        self.error: Exception | None = None
        self.turns: list[AssistantTurn] = []
        self.agent_calls: list[tuple[list[AgentMessage], list]] = []
        self.structured: list[BaseModel] = []
        self.structured_prompts: list[tuple[str, str]] = []
        self.chat_replies: list[str] = []
        self.chat_prompts: list[str] = []

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model):
        if self.error:
            raise self.error
        self.structured_prompts.append((system_prompt, user_prompt))
        reply = self.structured.pop(0) if self.structured else output_model.model_validate(
            {"findings": [], "suggestions": [], "summary": "nothing found"}
        )
        return reply, "fake-model"

    async def chat_with_tools(self, messages, tools) -> AssistantTurn:
        if self.error:
            raise self.error
        self.agent_calls.append((list(messages), list(tools)))
        return self.turns.pop(0) if self.turns else answer_turn("default answer")

    async def chat(self, messages: Sequence[ChatMessage], model: str | None = None) -> LLMResult:
        """Pops a queued reply from ``chat_replies``; otherwise echoes the last message."""
        if self.error:
            raise self.error
        self.received = list(messages)
        self.received_model = model
        self.chat_prompts.append(messages[-1].content)
        return LLMResult(
            model=model or "fake-model",
            content=self.chat_replies.pop(0) if self.chat_replies else f"echo: {messages[-1].content}",
            prompt_tokens=3,
            completion_tokens=4,
            duration_ms=1.5,
        )

    async def health(self) -> LLMHealth:
        return LLMHealth(True, "fake-model", True, ["fake-model"])


@pytest.fixture
def fake_llm() -> FakeLLMService:
    return FakeLLMService()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(max_file_bytes=TEST_MAX_FILE_BYTES, max_search_results=50, data_dir=str(tmp_path / "data"))


@pytest.fixture
def client(fake_llm: FakeLLMService, test_settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    app.dependency_overrides[get_llm_service] = lambda: fake_llm
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_db] = lambda: (yield from session_scope(factory))
    app.dependency_overrides[get_session_factory] = lambda: factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def repo_manager(test_settings: Settings, tmp_path: Path) -> Iterator[RepositoryManager]:
    """A RepositoryManager on a throwaway database, for tests below the HTTP layer."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'manager.db').as_posix()}")
    init_db(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield RepositoryManager(session, test_settings)
    session.close()
    engine.dispose()


def _write(root: Path, relative: str, content: str | bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A small multi-language project with everything the scanner must skip."""
    root = (tmp_path / "sample_project").resolve()
    files: dict[str, str | bytes] = {
        "app/main.py": "def hello():\n    return 'hi'\n\n# TODO: handle errors\n",
        "app/utils.py": "def add(a, b):\n    return a + b\n",
        "src/index.ts": "export const greet = (n: string) => `Hello ${n}`; // todo\n",
        "package.json": '{"dependencies": {"react": "^19.0.0"}}\n',
        "requirements.txt": "fastapi==0.162.0\n",
        "README.md": "# Sample\n",
        ".env": "SECRET_KEY=super-secret\n",
        ".env.example": "SECRET_KEY=\n",
        "keys/server.pem": "-----BEGIN PRIVATE KEY-----\n",
        "node_modules/lib/index.js": "module.exports = 1;\n",
        "my_env/pyvenv.cfg": "home = C:/Python\n",
        "my_env/lib.py": "x = 1\n",
        ".gitignore": "*.log\nbuild_output/\n",
        "debug.log": "log line\n",
        "build_output/gen.py": "generated = True\n",
        "docs/.gitignore": "*.md\n!keep.md\n",
        "docs/drop.md": "dropped\n",
        "docs/keep.md": "kept\n",
        "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00",
        "data/big.txt": "x" * (TEST_MAX_FILE_BYTES + 1),
    }
    for relative, content in files.items():
        _write(root, relative, content)
    return root
