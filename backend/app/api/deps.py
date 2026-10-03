from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import decode_token, has_permission
from ..models import User

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "نیاز به ورود")
    try:
        payload = decode_token(creds.credentials)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "توکن نامعتبر یا منقضی") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "کاربر غیرفعال است")
    return user


def require(permission: str):
    def checker(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "دسترسی کافی ندارید")
        return user
    return checker


def plugin_enabled_dependency(name: str):
    def checker(request: Request) -> None:
        from ..plugins.manager import manager

        # webhooks are allowed to reach a plugin only if enabled; same for UI calls
        if not manager.enabled.get(name):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"افزونه {name} غیرفعال است")
    return Depends(checker)
