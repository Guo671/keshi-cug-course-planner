"""Local account password and opaque-session handling."""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .tables import LoginSession, User

_password_hasher = PasswordHasher()


def normalize_username(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("用户名长度必须为 3–64 个字符")
    if any(character.isspace() for character in normalized):
        raise ValueError("用户名不能包含空白字符")
    return normalized


def validate_password(value: str) -> None:
    if len(value) < 10:
        raise ValueError("密码至少需要 10 个字符")
    if len(value) > 256:
        raise ValueError("密码不能超过 256 个字符")


def hash_password(value: str) -> str:
    validate_password(value)
    return _password_hasher.hash(value)


def verify_password(password_hash: str, candidate: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_login_session(db: Session, user: User, *, lifetime_hours: int) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=lifetime_hours)
    db.add(
        LoginSession(
            token_hash=hash_token(raw_token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    return raw_token, expires_at


def get_user_for_token(db: Session, token: str) -> User | None:
    now = datetime.now(UTC)
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    session = db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == hash_token(token), LoginSession.expires_at > now
        )
    )
    return None if session is None else db.get(User, session.user_id)


def revoke_token(db: Session, token: str) -> None:
    db.execute(delete(LoginSession).where(LoginSession.token_hash == hash_token(token)))
