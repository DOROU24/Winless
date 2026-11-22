"""Оповещения.

Оповещение — производные данные: система создаёт его, когда инцидент по правилам впервые попал в колонку с
включённым оповещением. Пользователь оповещения не подтверждает и не меняет, только смотрит.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, delete, select
from sqlalchemy.orm import Session

from .boards import LEVEL_RANK, board_columns, load_incidents, place
from .db import Alert, AlertMembership, Board, Column, Incident, utcnow
from .fields import EvalContext, build_context, registry

MODE_RANK = {"popup": 1, "sound": 2}


def evaluate_alerts(s: Session, ctx: EvalContext | None = None) -> list[Alert]:
    """Создаёт оповещения для инцидентов, впервые попавших в колонку с включённым оповещением."""
    ctx = ctx or build_context(s)
    reg = registry(ctx)
    incidents = None
    current: dict[int, set[str]] = {}
    col_by_id: dict[int, Column] = {}
    for b in s.execute(select(Board)).scalars():
        cols = board_columns(s, b.id)
        alert_cols = [c for c in cols if c.alert != "off"]
        if not alert_cols:
            continue
        if incidents is None:
            incidents = load_incidents(s)
        placed = place(b, cols, incidents, reg, ctx)
        for c in alert_cols:
            current[c.id] = {i.id for i in placed[c.id]}
            col_by_id[c.id] = c

    prev: dict[int, set[str]] = {}
    for m in s.execute(select(AlertMembership)).scalars():
        prev.setdefault(m.column_id, set()).add(m.incident_id)

    entered: dict[str, list[Column]] = {}
    for cid, ids in current.items():
        for iid in ids - prev.get(cid, set()):
            entered.setdefault(iid, []).append(col_by_id[cid])

    # обновляем, кто сейчас в колонках с оповещением
    for cid in set(prev) | set(current):
        old, new = prev.get(cid, set()), current.get(cid, set())
        gone = old - new
        if gone:
            s.execute(delete(AlertMembership).where(AlertMembership.column_id == cid,
                                                    AlertMembership.incident_id.in_(gone)))
        for iid in new - old:
            s.add(AlertMembership(column_id=cid, incident_id=iid))

    created = []
    now = utcnow()
    for iid, cols in entered.items():
        # одно оповещение на инцидент, с уровнем самой важной колонки
        top = max(cols, key=lambda c: (LEVEL_RANK[c.level], MODE_RANK[c.alert]))
        mode = max((c.alert for c in cols), key=lambda m: MODE_RANK[m])
        repeats = [c.repeat_min for c in cols if c.repeat_min]
        a = Alert(incident_id=iid, column_id=top.id, column_ids=[c.id for c in cols], level=top.level, mode=mode,
                  repeat_min=min(repeats) if repeats else None, created_at=now)
        s.add(a)
        created.append(a)
    s.flush()
    return created


def baseline(s: Session, board_ids: list[int] | None = None, column_ids: list[int] | None = None,
             ctx: EvalContext | None = None) -> None:
    """Молча запоминает, какие инциденты сейчас лежат в колонках с оповещением, не создавая оповещений.

    Вызывается, когда колонки появляются или меняется то, что влияет на раскладку: новая доска, шаблон, копия,
    новая колонка, правка правила, режим доски, виртуальное «сейчас». Иначе все подходящие инциденты считались бы
    «впервые попавшими в колонку» и пришли бы оповещения по старым данным. Оповещение приходит, только когда
    инцидент попадает в колонку позже: после новой загрузки JSON или когда подошёл срок.
    """
    ctx = ctx or build_context(s)
    reg = registry(ctx)
    q = select(Board)
    if board_ids is not None:
        q = q.where(Board.id.in_(board_ids))
    incidents = None
    for b in s.execute(q).scalars():
        cols = board_columns(s, b.id)
        targets = [c for c in cols if c.alert != "off" and (column_ids is None or c.id in column_ids)]
        if not targets:
            continue
        if incidents is None:
            incidents = load_incidents(s)
        placed = place(b, cols, incidents, reg, ctx)
        for c in targets:
            s.execute(delete(AlertMembership).where(AlertMembership.column_id == c.id))
            for inc in placed[c.id]:
                s.add(AlertMembership(column_id=c.id, incident_id=inc.id))
    s.flush()


def _memberships(s: Session) -> set[tuple[int, str]]:
    return {(m.column_id, m.incident_id) for m in s.execute(select(AlertMembership)).scalars()}


def _in_column(a: Alert, members: set[tuple[int, str]]) -> bool:
    """Инцидент всё ещё лежит хотя бы в одной из колонок, из-за которых пришло оповещение."""
    return any((cid, a.incident_id) in members for cid in (a.column_ids or []))


def alert_dict(a: Alert, inc: Incident | None, cols: dict[int, Column], members: set[tuple[int, str]]) -> dict:
    c = cols.get(a.column_id or -1)
    return {
        "id": a.id, "incidentId": a.incident_id, "header": (inc.data.get("header") if inc else "") or "",
        "priority": inc.data.get("priority") if inc else None, "columnId": a.column_id,
        "column": c.name if c else "колонка удалена", "columns": [cols[x].name for x in a.column_ids or [] if x in cols],
        "level": a.level, "mode": a.mode, "repeatMin": a.repeat_min, "createdAt": a.created_at.isoformat() + "Z",
        "lastFiredAt": a.last_fired_at.isoformat() + "Z" if a.last_fired_at else None, "firedCount": a.fired_count,
        "inColumn": _in_column(a, members),
        "read": a.read_at is not None, "readAt": a.read_at.isoformat() + "Z" if a.read_at else None,
    }


def user_column_ids(s: Session) -> set[int] | None:
    """Колонки досок текущего пользователя. None — пользователь не задан (служебные вызовы и старые тесты)."""
    from .db import CURRENT_USER
    uid = CURRENT_USER.get()
    if uid is None:
        return None
    return set(s.execute(select(Column.id).join(Board, Board.id == Column.board_id).where(Board.owner_id == uid)).scalars())


def _mine(a: Alert, cols: set[int] | None) -> bool:
    return cols is None or bool(set(a.column_ids or []) & cols)


def list_alerts(s: Session, limit=300) -> list[dict]:
    cols_mine = user_column_ids(s)
    rows = [a for a in s.execute(select(Alert).order_by(Alert.id.desc())).scalars() if _mine(a, cols_mine)][:limit]
    incs = {i.id: i for i in s.execute(select(Incident).where(Incident.id.in_({a.incident_id for a in rows})))
            .scalars()}
    cols = {c.id: c for c in s.execute(select(Column)).scalars()}
    members = _memberships(s)
    return [alert_dict(a, incs.get(a.incident_id), cols, members) for a in rows]


def unread_count(s: Session) -> int:
    cols_mine = user_column_ids(s)
    return sum(1 for a in s.execute(select(Alert).where(Alert.read_at.is_(None))).scalars() if _mine(a, cols_mine))


def mark_read(s: Session, ids: list[int]) -> int:
    """Отметить оповещения прочитанными. Повторная отметка ничего не меняет. Возвращает, сколько отмечено сейчас."""
    cols_mine = user_column_ids(s)
    rows = [a for a in s.execute(select(Alert).where(Alert.id.in_(set(ids)), Alert.read_at.is_(None))).scalars()
            if _mine(a, cols_mine)] if ids else []
    now = utcnow()
    for a in rows:
        a.read_at = now
    s.flush()
    return len(rows)


def active_count(s: Session) -> int:
    """Сколько оповещений относится к инцидентам, которые сейчас лежат в колонках с оповещением."""
    members = _memberships(s)
    cols_mine = user_column_ids(s)
    return sum(1 for a in s.execute(select(Alert)).scalars() if _in_column(a, members) and _mine(a, cols_mine))


def due_alerts(s: Session, since: datetime | None, now: datetime | None = None) -> list[dict]:
    """Оповещения, которые сработали в промежутке (since, now]: новые и подошедшие повторы.

    Сервер ничего не помечает: каждый браузер присылает время своего прошлого опроса и получает всё, что
    сработало после него. Поэтому окно и звук получают все открытые пульты и экран на стене, а не только тот,
    кто спросил первым. Повтор срабатывает в моменты «создано + k·N минут», пока инцидент лежит в колонке.
    Без since (первый опрос браузера) возвращается пустой список — старое не всплывает при открытии страницы.
    """
    now = now or utcnow()
    if since is None or since >= now:
        return []
    since = max(since, now - timedelta(minutes=30))  # браузер долго спал — не вываливаем всё накопленное
    members = _memberships(s)
    cols_mine = user_column_ids(s)
    due = []
    for a in s.execute(select(Alert).where(Alert.created_at <= now)).scalars():
        if not _mine(a, cols_mine):
            continue
        if since < a.created_at <= now:
            due.append(a)
            continue
        if a.repeat_min and _in_column(a, members):
            step = timedelta(minutes=a.repeat_min)
            k_now, k_since = (now - a.created_at) // step, (since - a.created_at) // step
            if k_now >= 1 and k_now > k_since:
                due.append(a)
    incs = {i.id: i for i in s.execute(select(Incident).where(Incident.id.in_({a.incident_id for a in due})))
            .scalars()}
    cols = {c.id: c for c in s.execute(select(Column)).scalars()}
    return [alert_dict(a, incs.get(a.incident_id), cols, members) for a in due]
