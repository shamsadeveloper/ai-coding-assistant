"""ORM models for registered repositories and their file index."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(String(1000), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Summary of the last scan: {"languages": {...}, "frameworks": [...], "skipped": {...}}
    scan_summary: Mapped[dict | None] = mapped_column(JSON)

    files: Mapped[list["RepositoryFile"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class RepositoryFile(Base):
    """One readable text file found by the scanner. Paths are POSIX-style and relative."""

    __tablename__ = "repository_files"
    __table_args__ = (UniqueConstraint("repository_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000))
    language: Mapped[str | None] = mapped_column(String(50), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)

    repository: Mapped[Repository] = relationship(back_populates="files")
