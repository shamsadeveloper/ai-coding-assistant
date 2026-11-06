"""ORM models for generated tests (review area) and test runs."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.repository import _utcnow


class GeneratedTest(Base):
    __tablename__ = "generated_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    source_path: Mapped[str] = mapped_column(String(1000))
    symbol: Mapped[str | None] = mapped_column(String(300))
    framework: Mapped[str] = mapped_column(String(50))
    target_path: Mapped[str] = mapped_column(String(1000))
    content: Mapped[str] = mapped_column(Text)
    test_cases: Mapped[list] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text)
    syntax_ok: Mapped[bool] = mapped_column(Boolean)
    target_exists: Mapped[bool] = mapped_column(Boolean)
    review_path: Mapped[str] = mapped_column(String(1500))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | applied | rejected
    model: Mapped[str] = mapped_column(String(100))
    backup_path: Mapped[str | None] = mapped_column(String(1500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False  # not a pytest test class

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    framework: Mapped[str] = mapped_column(String(50))
    command: Mapped[list] = mapped_column(JSON)
    target_path: Mapped[str | None] = mapped_column(String(1000))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    passed: Mapped[int | None] = mapped_column(Integer)
    failed: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[int | None] = mapped_column(Integer)
    skipped: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    output: Mapped[str] = mapped_column(Text)
    analysis: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
