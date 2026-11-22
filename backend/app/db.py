"""Подключение к SQLite и модели SQLAlchemy."""
from __future__ import annotations

from contextvars import ContextVar

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DB_PATH = os.environ.get("MTS_DB_PATH", str(Path(__file__).resolve().parent.parent / "data.sqlite3"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_engine(path: str = DB_PATH):
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _):  # pragma: no cover - инфраструктура
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    filename: Mapped[str] = mapped_column(String(255))
    count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    first_snapshot_id: Mapped[int] = mapped_column(Integer)
    last_snapshot_id: Mapped[int] = mapped_column(Integer)
    disappeared: Mapped[bool] = mapped_column(Boolean, default=False)


class IncidentVersion(Base):
    __tablename__ = "incident_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(32))  # created | field_changed | disappeared | reappeared
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    old: Mapped[object] = mapped_column(JSON, nullable=True)
    new: Mapped[object] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldCatalog(Base):
    __tablename__ = "field_catalog"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(16))  # number | text | date | flag
    labels: Mapped[dict] = mapped_column(JSON, default=dict)  # код -> подпись
    values: Mapped[list] = mapped_column(JSON, default=list)  # частые значения для выпадающего списка


# Кто сейчас делает запрос (ставится проверкой входа). Новые доски и свои правила получают его как владельца,
# а «активная доска» хранится у каждого пользователя своя.
CURRENT_USER: ContextVar[int | None] = ContextVar("current_user", default=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Board(Base):
    __tablename__ = "boards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, default=lambda: CURRENT_USER.get())
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(16), default="all")  # all | first
    view: Mapped[str] = mapped_column(String(16), default="normal")  # normal | wall
    hide_disappeared: Mapped[bool] = mapped_column(Boolean, default=True)
    # Раскладка экрана на стене: масштаб, часы, плитки колонок {x, y, w, h, hidden, showCards, maxCards}
    wall_layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Column(Base):
    __tablename__ = "columns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board_id: Mapped[int] = mapped_column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(32), default="#8b7cf6")
    rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sort_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sort_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sort_dir: Mapped[str] = mapped_column(String(4), default="asc")
    limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    card_fields: Mapped[list] = mapped_column(JSON, default=list)
    level: Mapped[str] = mapped_column(String(16), default="info")  # critical | elevated | info
    alert: Mapped[str] = mapped_column(String(16), default="off")  # off | popup | sound
    repeat_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False)
    group_similar: Mapped[bool] = mapped_column(Boolean, default=False)
    preset_ids: Mapped[list] = mapped_column(JSON, default=list)


class RulePreset(Base):
    __tablename__ = "rule_presets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # номер правила из каталога
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=lambda: CURRENT_USER.get())  # у своих правил
    name: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    rule: Mapped[dict] = mapped_column(JSON)
    level: Mapped[str] = mapped_column(String(16))
    alert: Mapped[str] = mapped_column(String(16))
    repeat_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[int] = mapped_column(Integer, default=1)
    custom: Mapped[bool] = mapped_column(Boolean, default=False)  # сохранено пользователем, а не из кода
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    column_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_ids: Mapped[list] = mapped_column(JSON, default=list)
    level: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))  # popup | sound
    repeat_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fired_count: Mapped[int] = mapped_column(Integer, default=0)
    # Когда оповещение прочитали в панели уведомлений. Это отметка самого оповещения, инциденты не меняются
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AlertMembership(Base):
    """В каких колонках с оповещением инцидент находится сейчас (чтобы не оповещать повторно)."""
    __tablename__ = "alert_membership"
    column_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class Link(Base):
    __tablename__ = "links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    src: Mapped[str] = mapped_column(String(64), index=True)
    dst: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32))  # double | repeat | related | cause
    directed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))  # auto | ai (только вычисленные связи)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LinkRule(Base):
    __tablename__ = "link_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    fields: Mapped[list] = mapped_column(JSON, default=list)
    window_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filter: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    link_type: Mapped[str] = mapped_column(String(32), default="related")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON, nullable=True)


class AiResult(Base):
    __tablename__ = "ai_results"
    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_events_type_snapshot", IncidentEvent.type, IncidentEvent.snapshot_id)


def init_db(eng=None) -> None:
    eng = eng or engine
    Base.metadata.create_all(eng)
    migrate(eng)


def migrate(eng) -> None:
    """Приводит старую базу к принципу «только просмотр».

    Удаляет таблицу отметок «Вижу», их колонки в оповещениях и ручные связи. Безопасно запускать много раз.
    """
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as conn:
        if insp.has_table("acks"):
            conn.execute(text("DROP TABLE acks"))
        # новые колонки в старых базах
        for table, col, ddl in (("boards", "wall_layout", "JSON"), ("rule_presets", "custom", "BOOLEAN DEFAULT 0"),
                                ("rule_presets", "created_at", "DATETIME"), ("boards", "owner_id", "INTEGER"),
                                ("rule_presets", "owner_id", "INTEGER")):
            if col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        alert_cols = {c["name"] for c in insp.get_columns("alerts")}
        if "read_at" not in alert_cols:
            conn.execute(text("ALTER TABLE alerts ADD COLUMN read_at DATETIME"))
            # то, что было до появления отметки, считаем прочитанным, иначе бейдж сразу покажет всю историю
            conn.execute(text("UPDATE alerts SET read_at = created_at"))
        for col in ("acked", "acked_at"):
            if col in alert_cols:
                conn.execute(text(f"ALTER TABLE alerts DROP COLUMN {col}"))
        conn.execute(text("DELETE FROM links WHERE source = 'manual'"))
        # Переход на фирменную палитру МТС: меняем только старые цвета по умолчанию, свои цвета пользователя не трогаем.
        for old, new in (("#f2545b", "#ff0032"), ("#f0a23b", "#f57c00"), ("#5b9bd5", "#0070e5"),
                         ("#9b8afb", "#7b4fe0"), ("#4fb39a", "#12a150"), ("#d16fb0", "#d6336c"),
                         ("#8c96a6", "#626c77")):
            conn.execute(text("UPDATE columns SET color = :new WHERE lower(color) = :old"), {"old": old, "new": new})


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---- настройки -------------------------------------------------------------

PER_USER_SETTINGS = {"active_board_id"}


def _key(key: str) -> str:
    uid = CURRENT_USER.get()
    return f"{key}:{uid}" if key in PER_USER_SETTINGS and uid is not None else key


def get_setting(s: Session, key: str, default=None):
    row = s.get(Setting, _key(key))
    return default if row is None else row.value


def set_setting(s: Session, key: str, value) -> None:
    key = _key(key)
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value
