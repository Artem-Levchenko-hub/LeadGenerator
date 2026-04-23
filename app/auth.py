"""Auth-хелперы: bcrypt, чтение сессии, декораторы для защиты роутов."""
from datetime import datetime

import bcrypt
from fastapi import HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ActivityLog, User


# ==== Пароли ====

def hash_password(password: str) -> str:
    """Bcrypt-хэш пароля. Включает salt и version в результат."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ==== Валидация ====

MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 32
MIN_PASSWORD_LEN = 8


def validate_username(username: str) -> str | None:
    """Возвращает ошибку или None если ОК."""
    if not username or not username.strip():
        return "Имя пользователя не может быть пустым"
    s = username.strip()
    if len(s) < MIN_USERNAME_LEN:
        return f"Имя короче {MIN_USERNAME_LEN} символов"
    if len(s) > MAX_USERNAME_LEN:
        return f"Имя длиннее {MAX_USERNAME_LEN} символов"
    if not all(ch.isalnum() or ch in "._-" for ch in s):
        return "Имя может содержать только буквы, цифры и . _ -"
    return None


def validate_password(password: str) -> str | None:
    if not password:
        return "Пароль не может быть пустым"
    if len(password) < MIN_PASSWORD_LEN:
        return f"Пароль короче {MIN_PASSWORD_LEN} символов"
    if len(password) > 256:
        return "Пароль слишком длинный"
    return None


# ==== Сессии ====

SESSION_USER_KEY = "user_id"


def login_user(request: Request, user: User) -> None:
    """Устанавливает user_id в сессии."""
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request, db: Session | None = None) -> User | None:
    """Читает user_id из сессии, загружает User. None если не авторизован."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None

    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user
    finally:
        if owned_db:
            db.close()


def require_user(request: Request, db: Session | None = None) -> User:
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
        )
    return user


def require_admin(request: Request, db: Session | None = None) -> User:
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только для администраторов",
        )
    return user


# ==== Bootstrap: первый юзер = admin ====

def is_bootstrap_mode(db: Session) -> bool:
    """True если в users ещё нет ни одного юзера — разрешаем открытую регистрацию."""
    count = db.scalar(select(func.count(User.id))) or 0
    return count == 0


def log_activity(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    lead_id: int | None = None,
    meta: dict | None = None,
    commit: bool = True,
) -> ActivityLog:
    entry = ActivityLog(
        user_id=user_id,
        lead_id=lead_id,
        action=action,
        meta=meta or None,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
