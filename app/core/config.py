"""Environment-based application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from environment variables (prefix ``APP_``) or a ``.env`` file."""

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    name: str = "AI Coding Assistant"
    environment: str = "development"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    # Browser origins allowed to call the API (the Vite dev server). The built UI is served same-origin.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    frontend_dist: str = "frontend/dist"

    # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and every request
    # waits ~0.2 s before falling back to IPv4, where Ollama listens.
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_timeout_seconds: float = Field(default=300.0, gt=0)
    ollama_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    ollama_keep_alive: str = "10m"
    # Ollama's default context window is small; tool schemas + file contents need more.
    ollama_num_ctx: int = Field(default=8192, ge=2048)
    # Upper bound on tokens generated per reply (Ollama "num_predict").
    ollama_max_tokens: int = Field(default=2048, ge=128)

    agent_max_steps: int = Field(default=8, ge=1, le=20)
    agent_tool_output_chars: int = Field(default=6000, ge=500)
    agent_history_messages: int = Field(default=10, ge=0)

    # Longest code range sent to the model in one analysis call; longer files are chunked.
    analysis_max_lines: int = Field(default=300, ge=20)

    # Review area for generated tests, and backups made before any file is written.
    data_dir: str = "./data"

    # Code modification limits per change request.
    change_max_files: int = Field(default=3, ge=1, le=10)
    change_max_file_lines: int = Field(default=800, ge=50)

    # Test execution: only these commands can ever run. "{python}" is replaced by the
    # repository's virtualenv interpreter if it has one. No shell is used.
    test_execution_enabled: bool = True
    test_timeout_seconds: int = Field(default=300, ge=10)
    test_commands: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "pytest": ["{python}", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
            "unittest": ["{python}", "-m", "unittest"],
            "jest": ["npx", "--no-install", "jest", "--ci"],
            "vitest": ["npx", "--no-install", "vitest", "run"],
            "mocha": ["npx", "--no-install", "mocha"],
            "go": ["go", "test", "./..."],
            "cargo": ["cargo", "test"],
            "phpunit": ["vendor/bin/phpunit"],
            "maven": ["mvn", "-q", "test"],
            "gradle": ["gradle", "test"],
            "dotnet": ["dotnet", "test"],
        }
    )

    # Fix sessions. These limits are enforced in the backend, whatever the UI sends.
    fix_max_attempts: int = Field(default=3, ge=1, le=3)
    fix_max_llm_calls: int = Field(default=20, ge=3, le=60)
    fix_session_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    fix_investigation_max_steps: int = Field(default=4, ge=0, le=10)
    fix_max_changed_lines: int = Field(default=60, ge=1, le=500)
    fix_max_concurrent_sessions: int = Field(default=1, ge=1, le=4)
    fix_sandbox_max_files: int = Field(default=20_000, gt=0)
    fix_sandbox_max_bytes: int = Field(default=200_000_000, gt=0)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    database_url: str = "sqlite:///./data/assistant.db"
    # Development convenience: create missing tables at startup. In production set this to
    # false and run "alembic upgrade head" instead.
    database_auto_create: bool = True

    # Repositories may only be registered inside these folders. Empty list = any folder.
    allowed_repository_roots: list[str] = Field(default_factory=list)
    max_file_bytes: int = Field(default=1_000_000, gt=0)
    max_search_results: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
