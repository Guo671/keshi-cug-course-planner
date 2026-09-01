"""Persistent tables for accounts, profiles, catalog provenance and plans."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    sessions: Mapped[list[LoginSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    profile: Mapped[StudentProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    college: Mapped[str] = mapped_column(String(128))
    major: Mapped[str] = mapped_column(String(128))
    major_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cohort_year: Mapped[int] = mapped_column(Integer)
    plan_variant: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cooperation_program: Mapped[str | None] = mapped_column(String(128), nullable=True)
    semester_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="profile")


class CatalogSnapshot(Base):
    __tablename__ = "catalog_snapshots"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    label: Mapped[str] = mapped_column(String(256))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_rank: Mapped[int] = mapped_column(Integer, default=0)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CatalogCourse(Base):
    __tablename__ = "catalog_courses"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    sections: Mapped[list[CatalogSection]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CatalogSection(Base):
    __tablename__ = "catalog_sections"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_courses.id", ondelete="CASCADE"), index=True
    )
    section_code: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    instructors: Mapped[list[str]] = mapped_column(JSON, default=list)
    meetings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    composition: Mapped[list[str]] = mapped_column(JSON, default=list)
    assessment: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enrolled_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_snapshots.id", ondelete="RESTRICT"), index=True
    )
    source_rank: Mapped[int] = mapped_column(Integer, default=0)
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    default_eligible: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    parse_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    import_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    course: Mapped[CatalogCourse] = relationship(back_populates="sections")


Index("ix_catalog_course_code_name", CatalogCourse.code, CatalogCourse.name)
Index(
    "ix_catalog_section_course_eligible",
    CatalogSection.course_id,
    CatalogSection.default_eligible,
)


class SavedPreferences(Base):
    __tablename__ = "saved_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PlanningRun(Base):
    __tablename__ = "planning_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    input_mode: Mapped[str] = mapped_column(String(32))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
