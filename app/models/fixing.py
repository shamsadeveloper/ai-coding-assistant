"""ORM models for fix sessions: one investigation-and-fix run, its attempts, and its timeline."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.repository import _utcnow


class FixSession(Base):
    """A developer-started bug fix. The repository itself is only written after approval,
    through a ChangeSet (``change_set_id``)."""

    __tablename__ = "fix_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_findings.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(String(1000))
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    options: Mapped[dict] = mapped_column(JSON)
    # queued -> investigating -> fixing <-> validating -> reviewing -> awaiting_approval -> applied
    # terminal otherwise: rejected, unverified, failed, cancelled, interrupted
    status: Mapped[str] = mapped_column(String(30))
    # passed | not_validated | failed (None until an attempt was judged)
    validation_status: Mapped[str | None] = mapped_column(String(30))
    validation_note: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict | None] = mapped_column(JSON)
    root_cause: Mapped[dict | None] = mapped_column(JSON)
    baseline: Mapped[dict | None] = mapped_column(JSON)
    reproduction: Mapped[dict | None] = mapped_column(JSON)
    review: Mapped[dict | None] = mapped_column(JSON)
    final_attempt: Mapped[int | None] = mapped_column(Integer)  # attempt number offered for approval
    change_set_id: Mapped[int | None] = mapped_column(ForeignKey("change_sets.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(100), default="")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list["FixAttempt"]] = relationship(
        order_by="FixAttempt.number", cascade="all, delete-orphan", passive_deletes=True
    )


class FixAttempt(Base):
    __tablename__ = "fix_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("fix_sessions.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    # passed | failed (new test failures) | rejected (failed a patch check) | not_validated
    status: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    files: Mapped[list] = mapped_column(JSON)  # [{path, original_sha256, new_sha256, new_content, diff, notes}]
    checks: Mapped[list] = mapped_column(JSON)  # [{name, ok, detail}]
    test: Mapped[dict | None] = mapped_column(JSON)
    comparison: Mapped[dict | None] = mapped_column(JSON)
    failure_analysis: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FixEvent(Base):
    """One line of a session's timeline (also its audit trail)."""

    __tablename__ = "fix_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("fix_sessions.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)  # running total at this point
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
