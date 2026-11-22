"""Простой вход по логину и паролю (без регистрации): учётные записи заводятся здесь, в коде.

Пароль хранится как PBKDF2-хэш с солью. После входа выдаётся случайный токен в cookie (httpOnly).
У каждого пользователя свои доски, своя активная доска, свои правила в каталоге и свои уведомления.
Инциденты и загрузки общие: выгрузка ITSM одна на всех.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import CURRENT_USER, Board, Setting, User, UserSession

COOKIE = "kooradar_session"
# Учётные записи. Логин и пароль задаём сами, регистрации нет.
USERS = [("mts1", "mts1"), ("mts2", "mts2"), ("mts3", "mts3"), ("mts4", "mts4"), ("mts5", "mts5")]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$")[2], digest)


def seed_users(s: Session) -> None:
    """Создаёт учётные записи из USERS. Доски, сделанные до появления входа, отдаёт первому пользователю."""
    for login, password in USERS:
        if s.execute(select(User).where(User.login == login)).scalar_one_or_none() is None:
            s.add(User(login=login, password_hash=hash_password(password)))
    s.flush()
    first = s.execute(select(User).where(User.login == USERS[0][0])).scalar_one()
    s.execute(update(Board).where(Board.owner_id.is_(None)).values(owner_id=first.id))
    old = s.get(Setting, "active_board_id")
    if old is not None and s.get(Setting, f"active_board_id:{first.id}") is None:
        s.add(Setting(key=f"active_board_id:{first.id}", value=old.value))
    s.commit()


def login(s: Session, login_: str, password: str) -> tuple[User, str] | None:
    u = s.execute(select(User).where(User.login == login_.strip())).scalar_one_or_none()
    if u is None or not check_password(password, u.password_hash):
        return None
    token = secrets.token_urlsafe(32)
    s.add(UserSession(token=token, user_id=u.id))
    s.commit()
    return u, token


def user_by_token(s: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = s.get(UserSession, token)
    return s.get(User, row.user_id) if row else None


def logout(s: Session, token: str | None) -> None:
    row = s.get(UserSession, token) if token else None
    if row:
        s.delete(row)
        s.commit()


def ensure_own_board(s: Session, user: User) -> None:
    """У каждого пользователя есть доска «Задание МТС» — доска по заданию, с неё начинается работа.

    Новому пользователю создаём её первой и делаем активной. Тому, у кого уже есть доска задания, один раз
    переключаем активную доску на неё; дальше выбор пользователя не трогаем.
    """
    from sqlalchemy import or_
    from .db import get_setting, set_setting
    from .templates import create_board_from_template
    tok = CURRENT_USER.set(user.id)
    marker = f"mts_default:{user.id}"
    try:
        mine = select(Board.id).where(Board.owner_id == user.id)
        mts = s.execute(mine.where(or_(Board.template == "mts", Board.name == "Задание МТС"))
                        .order_by(Board.position).limit(1)).scalar()
        if mts is None:
            fresh = s.execute(mine.limit(1)).first() is None
            b = create_board_from_template(s, "mts")
            if fresh:
                b.position = -1
                set_setting(s, "active_board_id", b.id)
            s.flush()
            from . import alerts
            alerts.baseline(s, board_ids=[b.id])
        elif s.get(Setting, marker) is None or get_setting(s, "active_board_id") is None:
            set_setting(s, "active_board_id", mts)
        if s.get(Setting, marker) is None:
            s.add(Setting(key=marker, value=True))
        if get_setting(s, "active_board_id") is None:
            set_setting(s, "active_board_id", s.execute(mine.order_by(Board.position)).scalar())
        s.commit()
    finally:
        CURRENT_USER.reset(tok)
