"""Local-only account endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..infrastructure.account_recovery import (
    RecoveryAttemptLimiter,
    change_password,
    ensure_recovery_code,
    recover_password,
    replace_recovery_code,
)
from ..infrastructure.security import (
    create_login_session,
    hash_password,
    normalize_username,
    revoke_token,
    verify_password,
)
from ..infrastructure.tables import AccountRecovery, User
from .dependencies import CurrentUser, Database
from .schemas import (
    ChangePasswordRequest,
    CurrentPasswordRequest,
    LoginRequest,
    RecoveryCodeResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_token(db: Database, user: User) -> TokenResponse:
    # Another local process may have reset this password since verification.
    # Acquire the write lock and recheck before issuing any session.
    matched = db.execute(
        update(User)
        .where(User.id == user.id, User.password_hash == user.password_hash)
        .values(password_hash=user.password_hash)
        .returning(User.id)
    ).scalar_one_or_none()
    if matched is None:
        raise HTTPException(status_code=401, detail="账号凭据已变更，请重新登录")
    recovery_code = ensure_recovery_code(db, user)
    token, expires_at = create_login_session(db, user, lifetime_hours=settings.session_hours)
    db.flush()
    return TokenResponse(access_token=token, expires_at=expires_at, recovery_code=recovery_code)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Database, response: Response) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        username = normalize_username(payload.username)
        password_hash = hash_password(payload.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在，请换一个用户名") from exc
    return _issue_token(db, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Database, response: Response) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        username = normalize_username(payload.username)
    except ValueError:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from None
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(user.password_hash, payload.password.get_secret_value()):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _issue_token(db, user)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser, db: Database) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        profile_complete=user.profile is not None,
        recovery_enabled=db.get(AccountRecovery, user.id) is not None,
    )


@router.post("/change-password", status_code=204)
def update_password(
    payload: ChangePasswordRequest, db: Database, user: CurrentUser, response: Response
) -> None:
    response.headers["Cache-Control"] = "no-store"
    try:
        change_password(
            db,
            user,
            payload.current_password.get_secret_value(),
            payload.new_password.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/recovery-code", response_model=RecoveryCodeResponse)
def generate_recovery_code(
    payload: CurrentPasswordRequest, db: Database, user: CurrentUser, response: Response
) -> RecoveryCodeResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        code = replace_recovery_code(db, user, payload.current_password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecoveryCodeResponse(recovery_code=code)


@router.post("/reset-password", response_model=RecoveryCodeResponse)
def reset_password(
    payload: ResetPasswordRequest, db: Database, request: Request, response: Response
) -> RecoveryCodeResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        username = normalize_username(payload.username)
    except ValueError:
        raise HTTPException(status_code=400, detail="用户名或恢复码不正确") from None
    limiter = getattr(request.app.state, "recovery_limiter", None)
    if not isinstance(limiter, RecoveryAttemptLimiter):
        limiter = RecoveryAttemptLimiter()
        request.app.state.recovery_limiter = limiter
    if not limiter.allow(username):
        raise HTTPException(
            status_code=429,
            detail="恢复尝试过多，请等待一分钟后再试",
            headers={"Retry-After": "60"},
        )
    user = db.scalar(select(User).where(User.username == username))
    try:
        code = recover_password(
            db,
            user,
            payload.recovery_code.get_secret_value(),
            payload.new_password.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    limiter.clear(username)
    return RecoveryCodeResponse(recovery_code=code)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Database,
    user: CurrentUser,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    del user
    if authorization and authorization.casefold().startswith("bearer "):
        revoke_token(db, authorization.split(" ", 1)[1])
