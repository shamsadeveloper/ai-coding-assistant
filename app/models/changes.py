"""ORM models for proposed code changes and their per-file patches."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.repository import _utcnow


class ChangeSet(Base):
    """One developer request and the patches proposed for it. Kept as the change history."""

    __tablename__ = "change_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    request: Mapped[str] = mapped_column(Text)
    # proposed -> applied -> rolled_back, or proposed -> rejected; failed = no usable patch
    status: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    base_branch: Mapped[str | None] = mapped_column(String(200))
    branch_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list["FileChange"]] = relationship(
        order_by="FileChange.id", cascade="all, delete-orphan", passive_deletes=True
    )


class FileChange(Base):
    __tablename__ = "file_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(ForeignKey("change_sets.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    is_new: Mapped[bool] = mapped_column(Boolean)
    original_sha256: Mapped[str | None] = mapped_column(String(64))  # None for a new file
    new_sha256: Mapped[str] = mapped_column(String(64))
    new_content: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)  # empty when no edit could be applied
    syntax_ok: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text)
    backup_path: Mapped[str | None] = mapped_column(String(1500))
