from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.db import get_db
from ..core.security import ROLES, create_access_token, hash_password, validate_password_strength, verify_password
from ..models import User, local_now
from ..services.audit import audit
from .deps import current_user, require

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class SetupIn(BaseModel):
    username: str
    password: str
    full_name: str = ""
    salon_name: str = ""


class UserIn(BaseModel):
    username: str
    password: str | None = None
    full_name: str = ""
    role: str = "receptionist"
    is_active: bool = True


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def _user(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Whether first-run setup (creating the owner account) is still needed."""
    return {"needs_setup": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup")
def setup(body: SetupIn, db: Session = Depends(get_db)):
    if db.scalar(select(func.count(User.id))):
        raise HTTPException(400, "راه‌اندازی قبلاً انجام شده است")
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    u = User(username=body.username.strip().lower(), full_name=body.full_name, role="owner", password_hash=hash_password(body.password))
    db.add(u)
    if body.salon_name:
        from ..services import settings_store
        settings_store.set_value(db, "salon.name", body.salon_name)
    db.flush()
    audit(db, "auth.setup", "user", u.id, user=u)
    db.commit()
    return {"access_token": create_access_token(u.id, u.role), "user": _user(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    u = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    now = local_now()
    if u and u.locked_until and u.locked_until > now:
        raise HTTPException(423, "حساب به دلیل تلاش‌های ناموفق موقتاً قفل است")
    if not u or not u.is_active or not verify_password(body.password, u.password_hash):
        if u:
            u.failed_logins += 1
            if u.failed_logins >= s.max_failed_logins:
                u.locked_until = now + timedelta(minutes=s.lockout_minutes)
                u.failed_logins = 0
            audit(db, "auth.login_failed", "user", u.id, {"ip": request.client.host if request.client else None}, actor=u.username)
            db.commit()
        raise HTTPException(401, "نام کاربری یا رمز عبور اشتباه است")
    u.failed_logins = 0
    u.locked_until = None
    u.last_login_at = now
    audit(db, "auth.login", "user", u.id, {"ip": request.client.host if request.client else None}, user=u)
    db.commit()
    return {"access_token": create_access_token(u.id, u.role), "user": _user(u)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user(user)


@router.post("/change-password")
def change_password(body: PasswordIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "رمز فعلی اشتباه است")
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    u = db.get(User, user.id)
    u.password_hash = hash_password(body.new_password)
    audit(db, "auth.change_password", "user", u.id, user=u)
    db.commit()
    return {"ok": True}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require("users"))):
    return [_user(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users")
def create_user(body: UserIn, db: Session = Depends(get_db), actor=Depends(require("users"))):
    if body.role not in ROLES:
        raise HTTPException(400, "نقش نامعتبر")
    if not body.password:
        raise HTTPException(400, "رمز عبور الزامی است")
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if db.scalar(select(User).where(User.username == body.username.lower())):
        raise HTTPException(400, "این نام کاربری وجود دارد")
    u = User(username=body.username.strip().lower(), full_name=body.full_name, role=body.role,
             is_active=body.is_active, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    audit(db, "user.create", "user", u.id, {"role": u.role}, user=actor)
    db.commit()
    return _user(u)


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, db: Session = Depends(get_db), actor=Depends(require("users"))):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    if body.role not in ROLES:
        raise HTTPException(400, "نقش نامعتبر")
    if u.id == actor.id and (body.role != "owner" or not body.is_active):
        raise HTTPException(400, "نمی‌توانید نقش یا وضعیت خودتان را تغییر دهید")
    u.full_name, u.role, u.is_active = body.full_name, body.role, body.is_active
    if body.password:
        try:
            validate_password_strength(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        u.password_hash = hash_password(body.password)
    audit(db, "user.update", "user", u.id, {"role": u.role, "active": u.is_active}, user=actor)
    db.commit()
    return _user(u)
