"""Local-only account endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from ..config import settings
from ..infrastructure.security import (
    create_login_session,
    hash_password,
    normalize_username,
    revoke_token,
    verify_password,
)
from ..infrastructure.tables import User
from .dependencies import CurrentUser, Database
from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_token(db: Database, user: User) -> TokenResponse:
    token, expires_at = create_login_session(db, user, lifetime_hours=settings.session_hours)
    db.flush()
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Database) -> TokenResponse:
    try:
        username = normalize_username(payload.username)
        password_hash = hash_password(payload.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    db.flush()
    return _issue_token(db, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Database) -> TokenResponse:
    try:
        username = normalize_username(payload.username)
    except ValueError:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from None
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(user.password_hash, payload.password.get_secret_value()):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _issue_token(db, user)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        profile_complete=user.profile is not None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Database,
    user: CurrentUser,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    del user
    if authorization and authorization.casefold().startswith("bearer "):
        revoke_token(db, authorization.split(" ", 1)[1])
