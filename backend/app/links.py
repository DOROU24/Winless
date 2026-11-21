"""Связи между инцидентами: только вычисленные — автоправила и (этап 2) ИИ. Пользователь связи не создаёт."""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .db import Incident, Link, LinkRule
from .fields import EvalContext, build_context, parse_dt, registry
from .rules import match

LINK_TYPES = {"double": "Дубль", "repeat": "Повтор", "related": "Связан", "cause": "Причина → следствие"}
# Связи только вычисленные: автоправила (раздел 10 ТЗ) и, на этапе 2, ИИ. Ручных связей нет.
SOURCES = {"auto": "автоправило", "ai": "ИИ"}
MAX_GROUP = 60
MAX_AUTO_LINKS = 20000


def link_dict(l: Link, rule_names: dict[int, str] | None = None) -> dict:
    return {"id": l.id, "src": l.src, "dst": l.dst, "type": l.type, "typeLabel": LINK_TYPES.get(l.type, l.type),
            "directed": l.directed, "source": l.source, "sourceLabel": SOURCES.get(l.source, l.source),
            "ruleId": l.rule_id, "rule": (rule_names or {}).get(l.rule_id or -1), "note": l.note}


def link_rule_dict(r: LinkRule) -> dict:
    return {"id": r.id, "name": r.name, "fields": r.fields, "windowMin": r.window_min, "filter": r.filter,
            "linkType": r.link_type, "enabled": r.enabled}


def _fmt_gap(mins: float) -> str:
    if mins < 60:
        return f"{int(mins)} мин"
    h = mins / 60
    return f"{h:.0f} ч" if h >= 10 else f"{h:.1f} ч".replace(".", ",")


def recompute_auto_links(s: Session, ctx: EvalContext | None = None) -> int:
    ctx = ctx or build_context(s)
    reg = registry(ctx)
    s.execute(delete(Link).where(Link.source == "auto"))
    incidents = list(s.execute(select(Incident)).scalars())
    total = 0
    for r in s.execute(select(LinkRule).where(LinkRule.enabled.is_(True))).scalars():
        if not r.fields:
            continue
        groups: dict[tuple, list[Incident]] = defaultdict(list)
        for inc in incidents:
            key = tuple(inc.data.get(f) for f in r.fields)
            if any(k in (None, "") for k in key):
                continue
            if r.filter and not match(r.filter, inc, reg, ctx):
                continue
            groups[key].append(inc)
        field_names = ", ".join(reg[f].label if f in reg else f for f in r.fields)
        for key, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda i: (parse_dt(i.data.get("submitDate")) or ctx.now))
            items = items[-MAX_GROUP:]
            for a, b in combinations(items, 2):
                da, db_ = parse_dt(a.data.get("submitDate")), parse_dt(b.data.get("submitDate"))
                gap = None
                if da and db_:
                    gap = abs((db_ - da).total_seconds()) / 60
                if r.window_min is not None and (gap is None or gap > r.window_min):
                    continue
                note = f"{r.name}: совпадает {field_names}"
                if gap is not None:
                    note += f", разница {_fmt_gap(gap)}"
                s.add(Link(src=a.id, dst=b.id, type=r.link_type, directed=False, source="auto", rule_id=r.id,
                           note=note))
                total += 1
                if total >= MAX_AUTO_LINKS:
                    s.flush()
                    return total
    s.flush()
    return total


def links_for(s: Session, incident_id: str) -> dict:
    rows = list(s.execute(select(Link).where(or_(Link.src == incident_id, Link.dst == incident_id))).scalars())
    names = {r.id: r.name for r in s.execute(select(LinkRule)).scalars()}
    others = {l.dst if l.src == incident_id else l.src for l in rows}
    incs = {i.id: i for i in s.execute(select(Incident).where(Incident.id.in_(others))).scalars()}
    out, inc_ = [], []
    for l in rows:
        other = l.dst if l.src == incident_id else l.src
        o = incs.get(other)
        d = {**link_dict(l, names), "other": other, "otherHeader": (o.data.get("header") if o else "") or "",
             "otherState": o.data.get("state") if o else None, "otherDisappeared": o.disappeared if o else None}
        # направленная связь, где этот инцидент — цель, это обратная ссылка
        (inc_ if (l.directed and l.dst == incident_id) or (not l.directed and l.dst == incident_id) else out).append(d)
    return {"outgoing": out, "incoming": inc_}
