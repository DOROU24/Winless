"""FastAPI: роуты приложения."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from . import alerts as alerts_mod
from .boards import (board_columns, board_dict, column_counts, column_dict, duplicate_board, incident_levels,
                     layout, load_incidents, place)
from .env import load_dotenv

load_dotenv()  # ключ модели и прочие настройки из .env (не перезаписывает переменные окружения)

from .db import (utcnow, AiResult, Alert, AlertMembership, Board, Column, Incident, IncidentEvent, Link, LinkRule,
                 RulePreset, SessionLocal, Snapshot, get_session, get_setting, init_db, set_setting)
from .fields import build_context, field_value, parse_dt, registry
from .ingest import IngestError, ingest
from .links import LINK_TYPES, link_dict, link_rule_dict, links_for, recompute_auto_links
from .rules import explain, match, missing_fields, rule_fields, validate
from .templates import DEFAULT_CARD_FIELDS, PALETTE, TEMPLATES, create_board_from_template, fill_board, seed

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as s:
        seed(s)
        # если ключ модели есть, дозапускаем анализ инцидентов без результата ИИ (загружены до появления ключа)
        latest = s.execute(select(Snapshot.id).order_by(Snapshot.id.desc()).limit(1)).scalar()
    if latest:
        from .llm.pipeline import worker
        worker.enqueue(latest)
    yield


app = FastAPI(title="Пульт координатора", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB = Depends(get_session)


def err(status: int, message: str, errors: list | None = None):
    raise HTTPException(status, {"message": message, "errors": errors or []})


# ---- служебное ---------------------------------------------------------------------------

@app.get("/api/health")
def health(s: Session = DB):
    return {"ok": True, "incidents": s.execute(select(func.count(Incident.id))).scalar()}


# ---- снимки -------------------------------------------------------------------------------

def _after_ingest(s: Session) -> dict:
    ctx = build_context(s)
    n_links = recompute_auto_links(s, ctx)
    new_alerts = alerts_mod.evaluate_alerts(s, ctx)
    active_id = get_setting(s, "active_board_id")
    board = s.get(Board, active_id) if active_id else None
    counts = column_counts(s, board, ctx) if board else []
    return {"autoLinks": n_links, "newAlerts": len(new_alerts), "columns": counts}


@app.post("/api/snapshots")
async def upload_snapshot(file: UploadFile = File(...), s: Session = DB):
    raw = await file.read()
    try:
        summary = ingest(s, raw, file.filename or "snapshot.json")
    except IngestError as e:
        s.rollback()
        err(400, str(e))
    summary.update(_after_ingest(s))
    s.commit()
    # Анализ ИИ идёт в фоне после сохранения: загрузка не ждёт модель, поля ai.* заполнятся по мере ответов
    from .llm.pipeline import worker
    summary["ai"] = worker.enqueue(summary["snapshotId"])
    return summary


@app.get("/api/snapshots")
def list_snapshots(s: Session = DB):
    rows = s.execute(select(Snapshot).order_by(Snapshot.id.desc())).scalars()
    return [{"id": r.id, "uploadedAt": r.uploaded_at.isoformat() + "Z", "filename": r.filename, "count": r.count,
             **{k: v for k, v in (r.stats or {}).items()}} for r in rows]


# ---- реестр полей ------------------------------------------------------------------------

@app.get("/api/fields")
def get_fields(s: Session = DB):
    reg = registry(s)
    return [f.public() for f in reg.values()]


# ---- инциденты ----------------------------------------------------------------------------

def _event_dict(e: IncidentEvent, snaps: dict[int, Snapshot]):
    sn = snaps.get(e.snapshot_id)
    return {"id": e.id, "type": e.type, "field": e.field, "old": e.old, "new": e.new, "snapshotId": e.snapshot_id,
            "snapshot": sn.filename if sn else None, "at": (sn.uploaded_at if sn else e.at).isoformat() + "Z"}


@app.get("/api/incidents")
def search_incidents(search: str = "", limit: int = 20, s: Session = DB):
    q = search.strip().lower()
    out = []
    for inc in s.execute(select(Incident)).scalars():
        d = inc.data
        hay = f"{inc.id} {d.get('header') or ''} {d.get('productName') or ''} {d.get('service') or ''}".lower()
        if not q or q in hay:
            out.append({"id": inc.id, "header": d.get("header"), "state": d.get("state"),
                        "priority": d.get("priority"), "disappeared": inc.disappeared})
            if len(out) >= limit:
                break
    return out


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, board_id: int | None = None, s: Session = DB):
    inc = s.get(Incident, incident_id)
    if inc is None:
        err(404, f"Инцидент {incident_id} не найден")
    ctx = build_context(s)
    reg = registry(ctx)
    calc = {k: field_value(reg, k, inc, ctx) for k, f in reg.items()
            if f.group == "calc" or (f.group == "ai" and f.available)}
    snaps = {x.id: x for x in s.execute(select(Snapshot)).scalars()}
    events = s.execute(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                       .order_by(IncidentEvent.id.desc())).scalars()
    # «почему здесь» — по колонкам выбранной (или активной) доски
    why = []
    bid = board_id or get_setting(s, "active_board_id")
    board = s.get(Board, bid) if bid else None
    if board:
        cols = board_columns(s, board.id)
        placed = place(board, cols, [inc], reg, ctx)
        for c in cols:
            if placed[c.id]:
                why.append({"columnId": c.id, "column": c.name, "level": c.level, "color": c.color,
                            "reasons": explain(c.rule, inc, reg, ctx)})
    ai = s.get(AiResult, incident_id)
    tmpl = get_setting(s, "itsm_url_template") or "/itsm/{id}"
    return {
        "id": inc.id, "data": inc.data, "calc": calc, "disappeared": inc.disappeared,
        "why": why, "events": [_event_dict(e, snaps) for e in events],
        "links": links_for(s, incident_id),
        "wave": _wave_view(inc, reg, ctx),
        "itsmUrl": tmpl.replace("{id}", inc.id).replace("{sysId}", str(inc.data.get("sysId") or "")),
        "ai": ({"data": ai.data, "error": ai.error, "updatedAt": ai.updated_at.isoformat() + "Z"} if ai else None),
    }


def _wave_view(inc: Incident, reg, ctx) -> dict | None:
    from .fields import waves
    if not reg["ai.waveId"].available:
        return None
    w = waves(ctx).get(inc.id)
    if not w:
        return None
    members = []
    for mid in w["members"]:
        m = ctx.session.get(Incident, mid)
        members.append({"id": mid, "header": (m.data.get("header") if m else "") or "",
                        "state": m.data.get("state") if m else None})
    return {"id": w["id"], "size": w["size"], "title": w["title"], "members": members}


# ---- правила ------------------------------------------------------------------------------

class RuleBody(BaseModel):
    rule: Any = None
    limit: int = 20
    boardId: int | None = None   # чтобы предпросмотр учитывал режим доски и колонки выше
    columnId: int | None = None  # None — новая колонка, встанет в конец


@app.post("/api/rules/validate")
def rules_validate(body: RuleBody, s: Session = DB):
    reg = registry(s)
    errors = validate(body.rule, reg)
    return {"ok": not errors, "errors": errors}


def _preview(s: Session, rule, limit=20, hide_disappeared=True, board: Board | None = None,
             column_id: int | None = None):
    ctx = build_context(s)
    reg = registry(ctx)
    errors = validate(rule, reg)
    if errors:
        return {"ok": False, "errors": errors, "count": 0, "items": []}
    incidents = load_incidents(s)
    # В режиме «только в первой» инцидент, уже попавший в колонку выше, в эту колонку не попадёт.
    # Раньше предпросмотр этого не учитывал: «подходит 2», а в колонке 1 — непонятно почему.
    taken: dict[str, str] = {}
    if board is not None:
        hide_disappeared = board.hide_disappeared
        if board.mode == "first":
            cols = board_columns(s, board.id)
            me = next((c for c in cols if c.id == column_id), None)
            above = [c for c in cols if c.id != column_id and (me is None or c.position < me.position)]
            if above:
                placed = place(board, above, incidents, reg, ctx)
                for c in above:
                    for inc in placed[c.id]:
                        taken.setdefault(inc.id, c.name)
    items = []
    count = in_column = 0
    null_hits = 0
    used = [f for f in rule_fields(rule) if f in reg]
    for inc in incidents:
        if hide_disappeared and inc.disappeared:
            continue
        if match(rule, inc, reg, ctx):
            count += 1
            if inc.id not in taken:
                in_column += 1
            if len(items) < limit:
                items.append({"id": inc.id, "header": inc.data.get("header"), "priority": inc.data.get("priority"),
                              "state": inc.data.get("state"), "why": explain(rule, inc, reg, ctx),
                              "takenBy": taken.get(inc.id)})
        elif any(reg[f].group != "hist" and field_value(reg, f, inc, ctx) is None for f in used):
            null_hits += 1
    items.sort(key=lambda x: x["takenBy"] is not None)  # сначала те, что окажутся в этой колонке
    unavailable = [reg[f].label for f in used if not reg[f].available]
    return {"ok": True, "errors": [], "count": count, "inColumn": in_column, "takenAbove": count - in_column,
            "mode": board.mode if board else None, "items": items, "nullSkipped": null_hits,
            "unavailable": unavailable}


@app.post("/api/rules/preview")
def rules_preview(body: RuleBody, s: Session = DB):
    board = s.get(Board, body.boardId) if body.boardId else None
    return _preview(s, body.rule, body.limit, board=board, column_id=body.columnId)


# ---- доски --------------------------------------------------------------------------------

class BoardBody(BaseModel):
    name: str | None = None
    mode: str | None = None
    view: str | None = None
    hideDisappeared: bool | None = None
    wallLayout: dict | None = None
    position: int | None = None
    columnOrder: list[int] | None = None


@app.get("/api/boards")
def list_boards(s: Session = DB):
    rows = s.execute(select(Board).order_by(Board.position, Board.id)).scalars()
    return {"boards": [board_dict(b, board_columns(s, b.id)) for b in rows],
            "activeBoardId": get_setting(s, "active_board_id")}


@app.post("/api/boards")
def create_board(body: BoardBody, s: Session = DB):
    pos = (s.execute(select(func.max(Board.position))).scalar() or 0) + 1
    b = Board(name=body.name or "Новая доска", mode=body.mode or "all", view=body.view or "normal", position=pos,
              template="empty")
    s.add(b)
    s.flush()
    set_setting(s, "active_board_id", b.id)
    s.commit()
    return board_dict(b, [])


class FromTemplate(BaseModel):
    template: str
    name: str | None = None


@app.post("/api/boards/from-template")
def board_from_template(body: FromTemplate, s: Session = DB):
    if body.template not in TEMPLATES:
        err(400, f"Нет шаблона «{body.template}»")
    b = create_board_from_template(s, body.template, body.name)
    set_setting(s, "active_board_id", b.id)
    s.flush()
    alerts_mod.baseline(s, board_ids=[b.id])
    s.commit()
    return board_dict(b, board_columns(s, b.id))


@app.get("/api/boards/{board_id}")
def get_board(board_id: int, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    return board_dict(b, board_columns(s, b.id))


@app.patch("/api/boards/{board_id}")
def patch_board(board_id: int, body: BoardBody, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    if body.name is not None:
        b.name = body.name.strip() or b.name
    if body.mode in ("all", "first"):
        b.mode = body.mode
    if body.view in ("normal", "wall"):
        b.view = body.view
    if body.hideDisappeared is not None:
        b.hide_disappeared = body.hideDisappeared
    if "wallLayout" in body.model_fields_set:
        b.wall_layout = _clean_wall_layout(body.wallLayout)
    if body.position is not None:
        b.position = body.position
    if body.columnOrder:
        cols = {c.id: c for c in board_columns(s, b.id)}
        for i, cid in enumerate(body.columnOrder):
            if cid in cols:
                cols[cid].position = i
    if body.mode or body.hideDisappeared is not None or body.columnOrder:
        s.flush()
        alerts_mod.baseline(s, board_ids=[b.id])  # раскладка поменялась не из-за новых данных
    s.commit()
    return board_dict(b, board_columns(s, b.id))


def _clean_wall_layout(raw: dict | None) -> dict | None:
    """Раскладка экрана на стене: только известные ключи и разумные пределы."""
    if raw is None:
        return None

    def num(v, lo, hi, default):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return default

    tiles = {}
    for cid, t in (raw.get("tiles") or {}).items():
        if not isinstance(t, dict):
            continue
        tiles[str(cid)] = {
            "x": int(num(t.get("x"), 0, 11, 0)), "y": int(num(t.get("y"), 0, 199, 0)),
            "w": int(num(t.get("w"), 1, 12, 4)), "h": int(num(t.get("h"), 1, 40, 4)),
            "hidden": bool(t.get("hidden", False)), "showCards": bool(t.get("showCards", False)),
            "maxCards": int(num(t.get("maxCards"), 1, 30, 6)),
        }
    return {"scale": round(num(raw.get("scale"), 0.6, 2.0, 1.0), 2), "showClock": bool(raw.get("showClock", True)),
            "showTitle": bool(raw.get("showTitle", True)), "tiles": tiles}


@app.delete("/api/boards/{board_id}")
def delete_board(board_id: int, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    if s.execute(select(func.count(Board.id))).scalar() <= 1:
        err(400, "Нельзя удалить последнюю доску")
    col_ids = [c.id for c in board_columns(s, b.id)]
    s.execute(delete(AlertMembership).where(AlertMembership.column_id.in_(col_ids)))
    s.execute(delete(Column).where(Column.board_id == b.id))
    s.delete(b)
    if get_setting(s, "active_board_id") == board_id:
        nxt = s.execute(select(Board.id).where(Board.id != board_id).order_by(Board.position)).scalar()
        set_setting(s, "active_board_id", nxt)
    s.commit()
    return {"ok": True}


@app.post("/api/boards/{board_id}/duplicate")
def dup_board(board_id: int, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    nb = duplicate_board(s, b)
    set_setting(s, "active_board_id", nb.id)
    s.flush()
    alerts_mod.baseline(s, board_ids=[nb.id])
    s.commit()
    return board_dict(nb, board_columns(s, nb.id))


@app.post("/api/boards/{board_id}/reset")
def reset_board(board_id: int, body: FromTemplate, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    if body.template not in TEMPLATES:
        err(400, f"Нет шаблона «{body.template}»")
    col_ids = [c.id for c in board_columns(s, b.id)]
    s.execute(delete(AlertMembership).where(AlertMembership.column_id.in_(col_ids)))
    s.execute(delete(Column).where(Column.board_id == b.id))
    t = TEMPLATES[body.template]
    b.mode, b.template = t["mode"], body.template
    from .llm.adapter import is_configured
    fill_board(s, b, body.template, is_configured())
    s.flush()
    alerts_mod.baseline(s, board_ids=[b.id])
    s.commit()
    return board_dict(b, board_columns(s, b.id))


@app.get("/api/boards/{board_id}/layout")
def board_layout(board_id: int, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    return layout(s, b)


# ---- колонки ------------------------------------------------------------------------------

class ColumnBody(BaseModel):
    name: str | None = None
    color: str | None = None
    rule: Any = None
    sortField: str | None = None
    sortArgs: dict | None = None
    sortDir: str | None = None
    limit: int | None = None
    noLimit: bool | None = None
    cardFields: list[str] | None = None
    level: str | None = None
    alert: str | None = None
    repeatMin: int | None = None
    noRepeat: bool | None = None
    collapsed: bool | None = None
    groupSimilar: bool | None = None
    presetIds: list[int] | None = None
    position: int | None = None


def _apply_column(c: Column, body: ColumnBody, s: Session, fields_set: set[str]):
    reg = registry(s)
    if "rule" in fields_set:
        errors = validate(body.rule, reg)
        if errors:
            err(422, "Правило содержит ошибки", errors)
        c.rule = body.rule
    if body.name is not None:
        c.name = body.name.strip() or "Без названия"
    if body.color:
        c.color = body.color
    if "sortField" in fields_set:
        if body.sortField and body.sortField not in reg:
            err(422, f"Поле сортировки «{body.sortField}» не найдено в реестре")
        c.sort_field = body.sortField or None
    if "sortArgs" in fields_set:
        c.sort_args = body.sortArgs
    if body.sortDir in ("asc", "desc"):
        c.sort_dir = body.sortDir
    if body.noLimit:
        c.limit = None
    elif "limit" in fields_set:
        c.limit = body.limit if body.limit is None or body.limit >= 0 else None
    if body.cardFields is not None:
        c.card_fields = [f for f in body.cardFields if f in reg]
    if body.level in ("critical", "elevated", "info"):
        c.level = body.level
    if body.alert in ("off", "popup", "sound"):
        c.alert = body.alert
    if body.noRepeat:
        c.repeat_min = None
    elif "repeatMin" in fields_set:
        c.repeat_min = body.repeatMin if body.repeatMin and body.repeatMin > 0 else None
    if body.collapsed is not None:
        c.collapsed = body.collapsed
    if body.groupSimilar is not None:
        c.group_similar = body.groupSimilar
    if body.presetIds is not None:
        c.preset_ids = body.presetIds
    if body.position is not None:
        c.position = body.position


@app.get("/api/boards/{board_id}/columns")
def list_columns(board_id: int, s: Session = DB):
    return [column_dict(c) for c in board_columns(s, board_id)]


@app.post("/api/boards/{board_id}/columns")
def create_column(board_id: int, body: ColumnBody, s: Session = DB):
    b = s.get(Board, board_id) or err(404, "Доска не найдена")
    pos = len(board_columns(s, b.id))
    c = Column(board_id=b.id, position=pos, name="Новая колонка", color=PALETTE["info"], rule=None,
               card_fields=list(DEFAULT_CARD_FIELDS), level="info", alert="off")
    _apply_column(c, body, s, body.model_fields_set)
    if body.color is None:
        c.color = PALETTE[c.level]
    s.add(c)
    s.flush()
    alerts_mod.baseline(s, board_ids=[b.id], column_ids=[c.id])
    s.commit()
    return column_dict(c)


@app.patch("/api/boards/{board_id}/columns/{column_id}")
def patch_column(board_id: int, column_id: int, body: ColumnBody, s: Session = DB):
    c = s.get(Column, column_id)
    if c is None or c.board_id != board_id:
        err(404, "Колонка не найдена")
    _apply_column(c, body, s, body.model_fields_set)
    if c.alert == "off":
        s.execute(delete(AlertMembership).where(AlertMembership.column_id == c.id))
    if body.model_fields_set & {"rule", "alert", "position"}:
        s.flush()
        # в режиме «только в первой» правило одной колонки меняет состав следующих, поэтому вся доска
        alerts_mod.baseline(s, board_ids=[board_id])
    s.commit()
    return column_dict(c)


@app.delete("/api/boards/{board_id}/columns/{column_id}")
def delete_column(board_id: int, column_id: int, s: Session = DB):
    c = s.get(Column, column_id)
    if c is None or c.board_id != board_id:
        err(404, "Колонка не найдена")
    s.execute(delete(AlertMembership).where(AlertMembership.column_id == c.id))
    s.delete(c)
    s.flush()
    alerts_mod.baseline(s, board_ids=[board_id])
    s.commit()
    return {"ok": True}


# ---- шаблоны и каталог --------------------------------------------------------------------

@app.get("/api/templates")
def list_templates():
    return [{"key": k, "name": t["name"], "description": t["description"], "mode": t["mode"],
             "columns": [c["name"] for c in t["columns"]]} for k, t in TEMPLATES.items()]


@app.get("/api/presets")
def list_presets(with_counts: bool = True, s: Session = DB):
    ctx = build_context(s)
    reg = registry(ctx)
    incidents = [i for i in load_incidents(s) if not i.disappeared] if with_counts else []
    out = []
    for p in s.execute(select(RulePreset).order_by(RulePreset.id)).scalars():
        missing = missing_fields(p.rule, reg)
        unavailable = [reg[f].label for f in rule_fields(p.rule) if f in reg and not reg[f].available]
        no_data = bool(missing) or bool(unavailable)
        count = None
        if with_counts and not no_data:
            count = sum(1 for i in incidents if match(p.rule, i, reg, ctx))
        warn = None
        if missing:
            warn = f"Нет данных: в загруженных файлах нет поля {', '.join(missing)}"
        elif unavailable:
            warn = f"Нет данных: недоступно — {', '.join(unavailable)}"
        out.append(_preset_dict(p) | {"count": count, "noData": no_data, "warning": warn})
    return out


def _preset_dict(p: RulePreset) -> dict:
    return {"id": p.id, "name": p.name, "origin": p.origin, "description": p.description, "rule": p.rule,
            "level": p.level, "alert": p.alert, "repeatMin": p.repeat_min, "stage": p.stage,
            "custom": bool(p.custom)}


CUSTOM_PRESET_START = 1000  # свои правила нумеруются отдельно, чтобы не пересекаться со встроенными


class PresetBody(BaseModel):
    name: str | None = None
    description: str | None = None
    rule: Any = None
    level: str | None = None
    alert: str | None = None
    repeatMin: int | None = None


def _check_preset(body: PresetBody, s: Session, creating: bool) -> None:
    fs = body.model_fields_set
    if creating and not (body.name or "").strip():
        err(422, "Укажите название правила")
    if creating or "rule" in fs:
        if body.rule is None or (isinstance(body.rule, dict) and body.rule.get("children") == []):
            err(422, "Правило пустое: добавьте хотя бы одно условие")
        errors = validate(body.rule, registry(s))
        if errors:
            err(422, "Правило содержит ошибки", errors)
    if body.level is not None and body.level not in ("critical", "elevated", "info"):
        err(422, "Неизвестный уровень важности")
    if body.alert is not None and body.alert not in ("off", "popup", "sound"):
        err(422, "Неизвестный тип оповещения")


@app.post("/api/presets")
def create_preset(body: PresetBody, s: Session = DB):
    """Сохранить своё правило в каталог, чтобы потом быстро добавлять его в колонки."""
    _check_preset(body, s, creating=True)
    next_id = max(CUSTOM_PRESET_START, (s.execute(select(func.max(RulePreset.id))).scalar() or 0) + 1)
    p = RulePreset(id=next_id, name=body.name.strip(), origin="своё", description=(body.description or "").strip(),
                   rule=body.rule, level=body.level or "info", alert=body.alert or "off",
                   repeat_min=body.repeatMin if body.repeatMin and body.repeatMin > 0 else None,
                   stage=1, custom=True, created_at=utcnow())
    s.add(p)
    s.commit()
    return _preset_dict(p)


def _custom_preset(s: Session, preset_id: int) -> RulePreset:
    p = s.get(RulePreset, preset_id) or err(404, "Правило не найдено")
    if not p.custom:
        err(403, "Встроенные правила каталога не меняются. Добавьте правило в колонку и меняйте его там")
    return p


@app.patch("/api/presets/{preset_id}")
def patch_preset(preset_id: int, body: PresetBody, s: Session = DB):
    p = _custom_preset(s, preset_id)
    _check_preset(body, s, creating=False)
    if body.name is not None:
        p.name = body.name.strip() or p.name
    if body.description is not None:
        p.description = body.description.strip()
    if "rule" in body.model_fields_set:
        p.rule = body.rule
    if body.level:
        p.level = body.level
    if body.alert:
        p.alert = body.alert
    if "repeatMin" in body.model_fields_set:
        p.repeat_min = body.repeatMin if body.repeatMin and body.repeatMin > 0 else None
    s.commit()
    return _preset_dict(p)


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int, s: Session = DB):
    """Удаляет своё правило из каталога. Колонки, куда оно уже добавлено, не меняются: правило скопировано в них."""
    p = _custom_preset(s, preset_id)
    s.delete(p)
    s.commit()
    return {"ok": True}


# ---- оповещения ---------------------------------------------------------------------------

@app.get("/api/alerts")
def get_alerts(limit: int = 100, s: Session = DB):
    """Список оповещений для панели уведомлений, новые сверху."""
    return {"items": alerts_mod.list_alerts(s, max(1, min(limit, 300))), "unread": alerts_mod.unread_count(s)}


class ReadBody(BaseModel):
    ids: list[int]


@app.post("/api/alerts/read")
def read_alerts(body: ReadBody, s: Session = DB):
    """Пометить оповещения прочитанными (их увидели в панели). Меняется только отметка оповещения."""
    n = alerts_mod.mark_read(s, body.ids)
    s.commit()
    return {"ok": True, "marked": n, "unread": alerts_mod.unread_count(s)}


@app.get("/api/alerts/due")
def get_due_alerts(s: Session = DB):
    """Опрашивается фронтендом: пересчитывает оповещения (правила по времени идут и без загрузок)."""
    alerts_mod.evaluate_alerts(s)
    due = alerts_mod.due_alerts(s)
    active = alerts_mod.active_count(s)
    s.commit()
    from .llm.pipeline import worker
    return {"due": due, "active": active, "unread": alerts_mod.unread_count(s), "ai": worker.view()}


# ---- связи ----------------------------------------------------------------------------------

@app.get("/api/links")
def get_links(  # только чтение: связи создают правила автосвязей и (этап 2) ИИ
incident_id: str | None = None, s: Session = DB):
    if incident_id:
        return links_for(s, incident_id)
    names = {r.id: r.name for r in s.execute(select(LinkRule)).scalars()}
    return [link_dict(l, names) for l in s.execute(select(Link)).scalars()]


class LinkRuleBody(BaseModel):
    name: str | None = None
    fields: list[str] | None = None
    windowMin: int | None = None
    noWindow: bool | None = None
    filter: Any = None
    linkType: str | None = None
    enabled: bool | None = None


@app.get("/api/link-rules")
def get_link_rules(s: Session = DB):
    return [link_rule_dict(r) for r in s.execute(select(LinkRule).order_by(LinkRule.id)).scalars()]


def _apply_link_rule(r: LinkRule, body: LinkRuleBody, s: Session):
    reg = registry(s)
    fs = body.model_fields_set
    if body.name is not None:
        r.name = body.name.strip() or r.name
    if body.fields is not None:
        bad = [f for f in body.fields if f not in reg]
        if bad:
            err(422, f"Нет таких полей: {', '.join(bad)}")
        if not body.fields:
            err(422, "Укажите хотя бы одно поле, которое должно совпадать")
        r.fields = body.fields
    if body.noWindow:
        r.window_min = None
    elif "windowMin" in fs:
        r.window_min = body.windowMin
    if "filter" in fs:
        errors = validate(body.filter, reg)
        if errors:
            err(422, "Фильтр содержит ошибки", errors)
        r.filter = body.filter
    if body.linkType:
        if body.linkType not in LINK_TYPES:
            err(422, "Неизвестный тип связи")
        r.link_type = body.linkType
    if body.enabled is not None:
        r.enabled = body.enabled


@app.post("/api/link-rules")
def create_link_rule(body: LinkRuleBody, s: Session = DB):
    r = LinkRule(name=body.name or "Новое правило", fields=body.fields or ["serviceId"], window_min=24 * 60,
                 link_type="related", enabled=True)
    _apply_link_rule(r, body, s)
    s.add(r)
    s.flush()
    recompute_auto_links(s)
    s.commit()
    return link_rule_dict(r)


@app.patch("/api/link-rules/{rule_id}")
def patch_link_rule(rule_id: int, body: LinkRuleBody, s: Session = DB):
    r = s.get(LinkRule, rule_id) or err(404, "Правило не найдено")
    _apply_link_rule(r, body, s)
    s.flush()
    recompute_auto_links(s)
    s.commit()
    return link_rule_dict(r)


@app.delete("/api/link-rules/{rule_id}")
def delete_link_rule(rule_id: int, s: Session = DB):
    r = s.get(LinkRule, rule_id) or err(404, "Правило не найдено")
    s.delete(r)
    s.flush()
    recompute_auto_links(s)
    s.commit()
    return {"ok": True}


# ---- граф -------------------------------------------------------------------------------------

@app.get("/api/graph")
def graph(link_type: str | None = None, source: str | None = None, active_only: bool = False, s: Session = DB):
    """Граф связей: только инциденты, у которых есть связи. Цвет узла — уровень колонки активной доски."""
    ctx = build_context(s)
    reg = registry(ctx)
    incidents = load_incidents(s)
    by_id = {i.id: i for i in incidents}
    bid = get_setting(s, "active_board_id")
    board = s.get(Board, bid) if bid else None
    levels = incident_levels(s, board, ctx, reg, incidents) if board else {}
    q = select(Link)
    if link_type:
        q = q.where(Link.type == link_type)
    if source:
        q = q.where(Link.source == source)
    links = list(s.execute(q).scalars())

    def ok(iid: str) -> bool:
        inc = by_id.get(iid)
        if inc is None:
            return False
        if active_only and inc.data.get("stateId") not in (0, 2):
            return False
        return True

    edges, nodes = [], set()
    seen_pairs: set[tuple] = set()
    # ИИ может найти пару с обеих сторон и с разным типом: активный видит решённый как «Повтор»,
    # решённый видит активный как «Дубль». Это одна связь; «Повтор» точнее, поэтому он идёт первым.
    links.sort(key=lambda l: (l.type != "repeat", l.id))
    for l in links:
        if not (ok(l.src) and ok(l.dst)):
            continue
        pair = (min(l.src, l.dst), max(l.src, l.dst), l.type if l.source != "ai" else "*", l.source)
        if not l.directed and pair in seen_pairs:  # ИИ находит пару с обеих сторон — рисуем один раз
            continue
        seen_pairs.add(pair)
        edges.append(link_dict(l))
        nodes.update((l.src, l.dst))  # узлы берутся только из связей
    node_list = []
    for iid in nodes:
        inc = by_id[iid]
        lv = levels.get(iid)
        node_list.append({"id": iid, "header": inc.data.get("header"), "state": inc.data.get("state"),
                          "stateId": inc.data.get("stateId"), "priorityId": inc.data.get("priorityId"),
                          "service": inc.data.get("service"), "disappeared": inc.disappeared,
                          "level": lv["level"] if lv else None, "column": lv["column"] if lv else None})
    return {"nodes": node_list, "edges": edges}


# ---- помощник координатора ------------------------------------------------------------------

class AssistantChat(BaseModel):
    messages: list[dict]
    boardId: int | None = None
    incidentId: str | None = None


@app.post("/api/assistant/chat")
def assistant_chat(body: AssistantChat, s: Session = DB):
    from . import assistant
    from .llm.adapter import LLMError, is_configured
    if not is_configured():
        err(400, "Помощнику нужна языковая модель: задайте LLM_API_KEY в .env")
    try:
        return assistant.chat(s, body.messages, body.boardId, body.incidentId)
    except LLMError as e:
        err(502, str(e))
    finally:
        s.rollback()  # помощник только читает; на всякий случай ничего не оставляем в сессии


class ApplyBody(BaseModel):
    proposal: dict


def _column_body(spec: dict) -> ColumnBody:
    kw = {k: spec[k] for k in ("name", "rule", "level", "alert", "repeatMin", "sortField", "sortDir", "limit",
                               "groupSimilar") if k in spec}
    if "repeatMin" in kw and not kw["repeatMin"]:
        kw.pop("repeatMin")
        kw["noRepeat"] = True
    if "limit" in kw and not kw["limit"]:
        kw.pop("limit")
        kw["noLimit"] = True
    return ColumnBody(**kw)


@app.post("/api/assistant/apply")
def assistant_apply(body: ApplyBody, s: Session = DB):
    """Применить предложение помощника. Меняются только настройки отображения; правило проверяется заново."""
    p = body.proposal
    kind = p.get("kind")
    if kind == "create_column":
        col = create_column(int(p.get("boardId") or 0), _column_body(p.get("column") or {}), s)
        return {"ok": True, "boardId": col["boardId"], "columnId": col["id"]}
    if kind == "update_column":
        col = patch_column(int(p.get("boardId") or 0), int(p.get("columnId") or 0), _column_body(p.get("column") or {}), s)
        return {"ok": True, "boardId": col["boardId"], "columnId": col["id"]}
    if kind == "create_board":
        spec = p.get("board") or {}
        reg = registry(s)
        for i, c in enumerate(spec.get("columns") or []):  # проверяем всё до того, как что-то создать
            errors = validate(c.get("rule"), reg)
            if errors:
                err(422, f"Колонка {i + 1}: правило содержит ошибки", errors)
        b = create_board(BoardBody(name=spec.get("name") or "Новая доска", mode=spec.get("mode") or "all"), s)
        for c in spec.get("columns") or []:
            create_column(b["id"], _column_body(c), s)
        return {"ok": True, "boardId": b["id"]}
    err(400, f"Неизвестное предложение «{kind}»")


# ---- настройки ----------------------------------------------------------------------------------

class SettingsBody(BaseModel):
    itsmUrlTemplate: str | None = None
    activeBoardId: int | None = None
    teams: list[dict] | None = None
    assistantMinSimilar: int | None = None


def _settings(s: Session):
    return {"itsmUrlTemplate": get_setting(s, "itsm_url_template"),
            "activeBoardId": get_setting(s, "active_board_id"), "teams": get_setting(s, "teams", []),
            "assistantMinSimilar": get_setting(s, "assistant_min_similar", 3),
            "aiAvailable": __import__("app.llm.adapter", fromlist=["x"]).is_configured(),
            "now": build_context(s).now.isoformat()}


@app.get("/api/settings")
def get_settings(s: Session = DB):
    return _settings(s)


@app.patch("/api/settings")
def patch_settings(body: SettingsBody, s: Session = DB):
    if body.itsmUrlTemplate is not None:
        set_setting(s, "itsm_url_template", body.itsmUrlTemplate.strip() or "/itsm/{id}")
    if body.activeBoardId is not None:
        if s.get(Board, body.activeBoardId) is None:
            err(404, "Доска не найдена")
        set_setting(s, "active_board_id", body.activeBoardId)
    if body.assistantMinSimilar is not None:
        set_setting(s, "assistant_min_similar", max(1, min(50, body.assistantMinSimilar)))
    if body.teams is not None:
        set_setting(s, "teams", [t for t in body.teams if (t.get("team") or "").strip()])
    s.commit()
    return _settings(s)
