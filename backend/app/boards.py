"""Доски и колонки: раскладка инцидентов по правилам."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Board, Column, Incident
from .fields import EvalContext, FieldDef, build_context, field_value, parse_dt, registry
from .rules import explain, match, validate

LEVEL_RANK = {"info": 0, "elevated": 1, "critical": 2}


def column_dict(c: Column) -> dict:
    return {
        "id": c.id, "boardId": c.board_id, "position": c.position, "name": c.name, "color": c.color,
        "rule": c.rule, "sortField": c.sort_field, "sortArgs": c.sort_args, "sortDir": c.sort_dir,
        "limit": c.limit, "cardFields": c.card_fields or [], "level": c.level, "alert": c.alert,
        "repeatMin": c.repeat_min, "collapsed": c.collapsed, "groupSimilar": c.group_similar,
        "presetIds": c.preset_ids or [],
    }


def board_dict(b: Board, columns: list[Column] | None = None) -> dict:
    d = {"id": b.id, "name": b.name, "position": b.position, "mode": b.mode, "view": b.view,
         "hideDisappeared": b.hide_disappeared, "template": b.template, "wallLayout": b.wall_layout}
    if columns is not None:
        d["columns"] = [column_dict(c) for c in columns]
    return d


def board_columns(s: Session, board_id: int) -> list[Column]:
    return list(s.execute(select(Column).where(Column.board_id == board_id).order_by(Column.position, Column.id))
                .scalars())


def load_incidents(s: Session) -> list[Incident]:
    return list(s.execute(select(Incident)).scalars())


def _sort_key(reg: dict[str, FieldDef], c: Column, ctx: EvalContext):
    fd = reg.get(c.sort_field or "")

    def key(inc: Incident):
        if fd is None:
            return (0, inc.id)
        v = field_value(reg, fd.key, inc, ctx, c.sort_args)
        if v is None:
            return (1, 0)
        if fd.type == "date":
            d = parse_dt(v)
            return (0, d.timestamp()) if d else (1, 0)
        if fd.type == "flag":
            return (0, 1 if v else 0)
        if isinstance(v, (int, float)):
            return (0, v)
        return (0, str(v).lower())
    return key


def place(board: Board, columns: list[Column], incidents: list[Incident], reg, ctx) -> dict[int, list[Incident]]:
    """Раскладка: какие инциденты в каких колонках, с учётом режима «во всех / только в первой»."""
    pool = [i for i in incidents if not (board.hide_disappeared and i.disappeared)]
    taken: set[str] = set()
    result: dict[int, list[Incident]] = {}
    for c in columns:
        items = []
        for inc in pool:
            if board.mode == "first" and inc.id in taken:
                continue
            if match(c.rule, inc, reg, ctx):
                items.append(inc)
        if board.mode == "first":
            taken.update(i.id for i in items)
        key = _sort_key(reg, c, ctx)
        none_items = [i for i in items if key(i)[0] == 1]
        some = sorted([i for i in items if key(i)[0] == 0], key=key, reverse=(c.sort_dir == "desc"))
        result[c.id] = some + none_items  # пустые значения всегда в конце
    return result


def card(inc: Incident, c: Column, reg, ctx, with_why=True) -> dict:
    fields = list(dict.fromkeys(["id"] + (c.card_fields or [])))
    values = {}
    for f in fields:
        values[f] = field_value(reg, f, inc, ctx)
    d = inc.data
    return {
        "id": inc.id, "header": d.get("header") or d.get("shortDescription") or "",
        "priorityId": d.get("priorityId"), "priority": d.get("priority"), "stateId": d.get("stateId"),
        "values": values, "why": explain(c.rule, inc, reg, ctx) if with_why else [],
        "deadlineLeftPct": field_value(reg, "calc.deadlineLeftPct", inc, ctx),
        "deadlineLeftMin": field_value(reg, "calc.deadlineLeftMin", inc, ctx),
        "overdue": field_value(reg, "calc.overdue", inc, ctx),
        "isNew": inc.first_snapshot_id == ctx.latest_snapshot_id,
        "disappeared": inc.disappeared, "level": c.level,
    }


def layout(s: Session, board: Board, ctx: EvalContext | None = None) -> dict:
    ctx = ctx or build_context(s)
    reg = registry(ctx)
    cols = board_columns(s, board.id)
    incidents = load_incidents(s)
    placed = place(board, cols, incidents, reg, ctx)
    out_cols = []
    for c in cols:
        items = placed[c.id]
        shown = items if c.limit is None else items[: max(c.limit, 0)]
        out_cols.append({**column_dict(c), "total": len(items), "hidden": len(items) - len(shown),
                         "ruleErrors": validate(c.rule, reg),
                         "cards": [card(i, c, reg, ctx) for i in shown]})
    return {"board": board_dict(b=board), "columns": out_cols, "now": ctx.now.isoformat(),
            "incidentCount": len(incidents)}


def column_counts(s: Session, board: Board, ctx: EvalContext | None = None) -> list[dict]:
    ctx = ctx or build_context(s)
    reg = registry(ctx)
    cols = board_columns(s, board.id)
    placed = place(board, cols, load_incidents(s), reg, ctx)
    return [{"columnId": c.id, "name": c.name, "count": len(placed[c.id])} for c in cols]


def incident_levels(s: Session, board: Board, ctx, reg, incidents) -> dict[str, dict]:
    """Для графа: в какой колонке лежит инцидент и какой у неё уровень (берём самый важный)."""
    cols = board_columns(s, board.id)
    placed = place(board, cols, incidents, reg, ctx)
    out: dict[str, dict] = {}
    for c in cols:
        for inc in placed[c.id]:
            cur = out.get(inc.id)
            if cur is None or LEVEL_RANK[c.level] > LEVEL_RANK[cur["level"]]:
                out[inc.id] = {"level": c.level, "columnId": c.id, "column": c.name, "color": c.color}
    return out


def duplicate_board(s: Session, b: Board) -> Board:
    nb = Board(name=f"{b.name} (копия)", mode=b.mode, view=b.view, hide_disappeared=b.hide_disappeared,
               wall_layout=None,  # id колонок в копии другие, раскладку стены строим заново
               template=b.template, position=b.position + 1)
    s.add(nb)
    s.flush()
    for c in board_columns(s, b.id):
        s.add(Column(board_id=nb.id, position=c.position, name=c.name, color=c.color, rule=c.rule,
                     sort_field=c.sort_field, sort_args=c.sort_args, sort_dir=c.sort_dir, limit=c.limit,
                     card_fields=list(c.card_fields or []), level=c.level, alert=c.alert, repeat_min=c.repeat_min,
                     collapsed=c.collapsed, group_similar=c.group_similar, preset_ids=list(c.preset_ids or [])))
    return nb
