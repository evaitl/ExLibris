from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[str] = mapped_column(String, nullable=False)


class Book(Base):
    """One indexed ebook file and its extracted metadata."""

    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint(
            "format IN ('epub', 'mobi', 'azw3', 'pdf')",
            name="ck_books_format",
        ),
        CheckConstraint("file_size >= 0", name="ck_books_file_size"),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_books_page_count",
        ),
        CheckConstraint("is_missing IN (0, 1)", name="ck_books_is_missing"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    file_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_mtime: Mapped[float] = mapped_column(Float, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    sort_title: Mapped[str | None] = mapped_column(String, nullable=True)
    authors: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    published_date: Mapped[str | None] = mapped_column(String, nullable=True)
    isbn: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    series: Mapped[str | None] = mapped_column(String, nullable=True)
    series_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[str | None] = mapped_column(String, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    last_scanned_at: Mapped[datetime] = mapped_column(nullable=False)
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    epub_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    epub_deep_validated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
