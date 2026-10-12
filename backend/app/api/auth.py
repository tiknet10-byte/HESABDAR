from __future__ import annotations

import secrets
from datetime import timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.db import get_db
from ..core.security import ROLES, create_access_token, hash_password, validate_password_strength, verify_password
from ..models import User, local_now
from ..services.audit import audit, write_lock
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
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "locked": bool(u.locked_until and u.locked_until > local_now())}


# one answer for every refused login (unknown name, wrong password, locked, inactive): nobody learns which names exist
BAD_LOGIN = "نام کاربری یا رمز عبور اشتباه است. اگر چند بار پشت سر هم اشتباه شود، این حساب چند دقیقه بسته می‌شود."


def _name(username: str) -> str:
    """User names are kept trimmed and in small letters (a phone keyboard often adds a space)."""
    return (username or "").strip().lower()


def _token(u: User) -> str:
    return create_access_token(u.id, u.role, ver=u.token_version or 0)


def _end_logins(u: User) -> None:
    """Every token given before this stops working (new password, deactivated)."""
    u.token_version = (u.token_version or 0) + 1


@lru_cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Whether first-run setup (creating the owner account) is still needed."""
    return {"needs_setup": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup")
def setup(body: SetupIn, db: Session = Depends(get_db)):
    done = "راه‌اندازی قبلاً انجام شده است"
    if db.scalar(select(func.count(User.id))):
        raise HTTPException(400, done)
    name = _name(body.username)
    if not name:
        raise HTTPException(400, "نام کاربری را بنویسید")
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    pw = hash_password(body.password)
    write_lock(db)  # one setup at a time: a second one at the same moment waits, then finds the owner and stops
    if db.scalar(select(func.count(User.id))):
        db.rollback()
        raise HTTPException(400, done)
    u = User(username=name, full_name=body.full_name, role="owner", password_hash=pw)
    db.add(u)
    if body.salon_name:
        from ..services import settings_store
        settings_store.set_value(db, "salon.name", body.salon_name)
    db.flush()
    audit(db, "auth.setup", "user", u.id, user=u)
    db.commit()
    return {"access_token": _token(u), "user": _user(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    u = db.scalar(select(User).where(User.username == _name(body.username)))
    if u is None:
        verify_password(body.password, _dummy_hash())  # as slow as a real check: the time shows nothing either
        raise HTTPException(401, BAD_LOGIN)
    now = local_now()
    # count this try BEFORE checking the password, in one write: tries sent at the same moment each get their own
    # number, so only the first few are really checked; a locked account counts nothing (it opens again on time)
    not_locked = or_(User.locked_until.is_(None), User.locked_until <= now)
    counted = db.execute(update(User).where(User.id == u.id, not_locked)
                         .values(failed_logins=func.coalesce(User.failed_logins, 0) + 1)
                         .execution_options(synchronize_session=False)).rowcount
    tries = db.scalar(select(User.failed_logins).where(User.id == u.id)) if counted else None
    db.commit()
    db.refresh(u)
    ok = verify_password(body.password, u.password_hash)  # checked even when locked, so a lock looks like a wrong password
    ip = request.client.host if request.client else None
    if ok and u.is_active and tries is not None and tries <= s.max_failed_logins:
        u.failed_logins, u.locked_until, u.last_login_at = 0, None, now
        audit(db, "auth.login", "user", u.id, {"ip": ip}, user=u)
        db.commit()
        return {"access_token": _token(u), "user": _user(u)}
    if tries is not None and tries >= s.max_failed_logins:
        db.execute(update(User).where(User.id == u.id, not_locked)
                   .values(locked_until=now + timedelta(minutes=s.lockout_minutes), failed_logins=0)
                   .execution_options(synchronize_session=False))
    audit(db, "auth.login_failed", "user", u.id, {"ip": ip, **({"locked": True} if tries is None else {})}, actor=u.username)
    db.commit()
    raise HTTPException(401, BAD_LOGIN)


@router.post("/users/{uid}/unlock")
def unlock_user(uid: int, db: Session = Depends(get_db), actor=Depends(require("users"))):
    """The owner opens an account that was closed after wrong passwords, without waiting."""
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    u.failed_logins, u.locked_until = 0, None
    audit(db, "user.unlock", "user", u.id, user=actor)
    db.commit()
    return _user(u)


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
    _end_logins(u)  # other devices logged in with the old password are logged out
    audit(db, "auth.change_password", "user", u.id, user=u)
    db.commit()
    return {"ok": True, "access_token": _token(u)}  # this device stays logged in with the new token


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
    name = _name(body.username)
    if not name:
        raise HTTPException(400, "نام کاربری را بنویسید")
    taken = HTTPException(400, "این نام کاربری وجود دارد")
    if db.scalar(select(User).where(User.username == name)):
        raise taken
    u = User(username=name, full_name=body.full_name, role=body.role,
             is_active=body.is_active, password_hash=hash_password(body.password))
    db.add(u)
    try:
        db.flush()
    except IntegrityError as exc:  # the same name saved at the same moment
        db.rollback()
        raise taken from exc
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
    if body.password:
        try:
            validate_password_strength(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.password or (u.is_active and not body.is_active):
        _end_logins(u)  # a new password or a deactivated user: logins made before stop working at once
    u.full_name, u.role, u.is_active = body.full_name, body.role, body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
    u.failed_logins, u.locked_until = 0, None  # saved by the owner: an account closed after wrong passwords opens
    audit(db, "user.update", "user", u.id, {"role": u.role, "active": u.is_active}, user=actor)
    db.commit()
    return _user(u) | ({"access_token": _token(u)} if u.id == actor.id else {})  # own new password: stay logged in
