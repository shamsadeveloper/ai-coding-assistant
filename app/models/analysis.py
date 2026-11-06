"""ORM models for stored bug-detection and improvement analyses."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.repository import _utcnow


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # "bugs" | "improvements"
    path: Mapped[str] = mapped_column(String(1000))
    symbol: Mapped[str | None] = mapped_column(String(300))
    model: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text)
    # Findings the model returned that pointed outside the analysed lines and were dropped.
    discarded_findings: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    findings: Mapped[list["AnalysisFinding"]] = relationship(
        order_by="AnalysisFinding.id", cascade="all, delete-orphan", passive_deletes=True
    )


class AnalysisFinding(Base):
    __tablename__ = "analysis_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(10))  # "llm" or "static" (deterministic check)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str | None] = mapped_column(String(10))  # bugs: Critical/High/Medium/Low
    status: Mapped[str | None] = mapped_column(String(10))  # bugs: confirmed/possible
    impact: Mapped[str | None] = mapped_column(String(10))  # improvements: High/Medium/Low
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    benefits: Mapped[str | None] = mapped_column(Text)
    risks: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text)
