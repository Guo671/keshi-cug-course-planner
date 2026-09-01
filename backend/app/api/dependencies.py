"""FastAPI dependencies shared by API routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..infrastructure.database import get_session
from ..infrastructure.security import get_user_for_token
from ..infrastructure.tables import User

Database = Annotated[Session, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


def require_user(
    db: Database,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    user = get_user_for_token(db, credentials.credentials)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return user


CurrentUser = Annotated[User, Depends(require_user)]
