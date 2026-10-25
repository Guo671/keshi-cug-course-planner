"""Offline possession-based recovery with high-entropy codes and atomic consumption."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import unicodedata
from time import monotonic

from sqlalchemy import delete, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from .security import hash_password, verify_password
from .tables import AccountRecovery, LoginSession, User, utc_now

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def new_recovery_code() -> tuple[str, str]:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(32))
    pretty = "-".join(raw[i : i + 4] for i in range(0, 32, 4))
    return pretty, hashlib.sha256(raw.encode("ascii")).hexdigest()


def recovery_digest(value: str) -> str | None:
    text = unicodedata.normalize("NFKC", value).upper()
    text = "".join(text.split()).replace("-", "").replace("–", "").replace("—", "")
    if re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{32}", text) is None:
        return None
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def ensure_recovery_code(db: Session, user: User) -> str | None:
    if db.get(AccountRecovery, user.id) is not None:
        return None
    code, digest = new_recovery_code()
    inserted = db.execute(
        insert(AccountRecovery)
        .values(user_id=user.id, recovery_hash=digest, updated_at=utc_now())
        .on_conflict_do_nothing(index_elements=["user_id"])
        .returning(AccountRecovery.user_id)
    ).scalar_one_or_none()
    return code if inserted is not None else None


def require_current_password(db: Session, user: User, password: str) -> None:
    if not verify_password(user.password_hash, password):
        raise ValueError("当前密码不正确")
    # Recheck while obtaining the write lock, including a second local server/process.
    matched = db.execute(
        update(User)
        .where(User.id == user.id, User.password_hash == user.password_hash)
        .values(password_hash=user.password_hash)
        .returning(User.id)
    ).scalar_one_or_none()
    if matched is None:
        raise ValueError("账号凭据已变更，请重新登录")


def replace_recovery_code(db: Session, user: User, current_password: str) -> str:
    require_current_password(db, user, current_password)
    code, digest = new_recovery_code()
    db.execute(
        insert(AccountRecovery)
        .values(user_id=user.id, recovery_hash=digest, updated_at=utc_now())
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"recovery_hash": digest, "updated_at": utc_now()}
        )
    )
    return code


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    require_current_password(db, user, current_password)
    user.password_hash = hash_password(new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.flush()


def recover_password(db: Session, user: User | None, code: str, new_password: str) -> str:
    digest = recovery_digest(code)
    stored = db.get(AccountRecovery, user.id) if user else None
    if (
        user is None
        or stored is None
        or digest is None
        or not hmac.compare_digest(stored.recovery_hash, digest)
    ):
        raise ValueError("用户名或恢复码不正确；使用过或已更换的恢复码不能再次使用")
    password_hash = hash_password(new_password)
    replacement, replacement_hash = new_recovery_code()
    consumed = db.execute(
        update(AccountRecovery)
        .where(AccountRecovery.user_id == user.id, AccountRecovery.recovery_hash == digest)
        .values(recovery_hash=replacement_hash, updated_at=utc_now())
        .returning(AccountRecovery.user_id)
    ).scalar_one_or_none()
    if consumed is None:
        raise ValueError("用户名或恢复码不正确；使用过或已更换的恢复码不能再次使用")
    user.password_hash = password_hash
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.flush()
    return replacement


class RecoveryAttemptLimiter:
    """Bounded per-process recovery attempts; no persistent lockout of account login."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[float, int]] = {}
        self.lock = threading.Lock()

    def allow(self, username: str) -> bool:
        now = monotonic()
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if now - v[0] < 60}
            start, count = self.entries.get(username, (now, 0))
            if count >= 5:
                return False
            if username not in self.entries and len(self.entries) >= 512:
                self.entries.pop(next(iter(self.entries)))
            self.entries[username] = (start, count + 1)
            return True

    def clear(self, username: str) -> None:
        with self.lock:
            self.entries.pop(username, None)
