"""Этап 2: сигналы ИИ — похожие инциденты, отписки в решении, подсказка команды.

Результаты кэшируются в ai_results по id инцидента и хэшу текстовых полей.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import AiResult, Incident, IncidentEvent, Link, get_setting, utcnow
from ..fields import EvalContext
from .adapter import ask_json, is_configured

log = logging.getLogger("llm")
TEXT_FIELDS = ("header", "shortDescription", "description", "solution")
MAX_PER_UPLOAD = 40
MAX_CANDIDATES = 20
WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}")

SCHEMA = {
    "type": "object",
    "required": ["similar", "solution", "team"],
    "properties": {
        "similar": {"type": "array", "items": {
            "type": "object", "required": ["id", "relation", "explanation"],
            "properties": {"id": {"type": "string"}, "relation": {"type": "string",
                                                                  "enum": ["same", "symptom", "words"]},
                           "explanation": {"type": "string"}}}},
        "solution": {"type": "object", "required": ["weak", "reason"],
                     "properties": {"weak": {"type": ["boolean", "null"]}, "reason": {"type": "string"}}},
        "team": {"type": "object", "required": ["name", "reason"],
                 "properties": {"name": {"type": ["string", "null"]}, "reason": {"type": "string"},
                                "basedOn": {"type": "array", "items": {"type": "string"}}}},
        "facts": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = (
    "Ты помогаешь координатору массовых инцидентов. Тебе дают новый инцидент и кандидатов из истории. "
    "Для каждого кандидата реши: same — та же проблема; symptom — связанный симптом; words — просто похожие слова. "
    "В similar включай только same и symptom. Оцени текст решения (solution) нового инцидента: weak=true, если это "
    "отписка («ничего не делал», «само устранилось», «перезагрузил» без объяснения, что и почему); weak=null, если "
    "решения нет. Предложи команду по похожим прошлым инцидентам и таблице команд, укажи id, на которые опираешься. "
    "Отделяй факты (facts) от предположений (assumptions). Каждый вывод ссылается на конкретные id. "
    "Если похожих нет или они противоречат друг другу, так и пиши, ничего не выдумывай."
)


def text_hash(d: dict) -> str:
    return hashlib.sha256(json.dumps([d.get(f) for f in TEXT_FIELDS], ensure_ascii=False).encode()).hexdigest()


def _words(d: dict) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(f"{d.get('header') or ''} {d.get('description') or ''}")}


def _resolved(inc: Incident) -> bool:
    return inc.disappeared or inc.data.get("stateId") in (7, 10, 20)


def candidates(inc: Incident, pool: list[Incident]) -> list[Incident]:
    d = inc.data
    words = _words(d)
    scored = []
    for o in pool:
        if o.id == inc.id:
            continue
        od = o.data
        same_obj = (d.get("productId") and od.get("productId") == d.get("productId")) or \
                   (d.get("serviceId") and od.get("serviceId") == d.get("serviceId"))
        if not same_obj:
            continue
        overlap = len(words & _words(od))
        if overlap == 0:
            continue
        scored.append((overlap, o))
    scored.sort(key=lambda x: -x[0])
    return [o for _, o in scored[:MAX_CANDIDATES]]


def _brief(inc: Incident) -> dict:
    d = inc.data
    return {"id": inc.id, "header": d.get("header"), "description": d.get("description"), "state": d.get("state"),
            "solution": d.get("solution"), "team": d.get("assignedGroupName"), "submitDate": d.get("submitDate"),
            "resolved": _resolved(inc)}


def analyze(s: Session, inc: Incident, pool: list[Incident]) -> dict:
    cands = candidates(inc, pool)
    teams = get_setting(s, "teams", [])
    user = json.dumps({"incident": _brief(inc), "candidates": [_brief(c) for c in cands], "teams": teams},
                      ensure_ascii=False)
    resp = ask_json(SYSTEM, user, SCHEMA)
    by_id = {c.id: c for c in cands}
    same = [x for x in resp["similar"] if x["relation"] == "same" and x["id"] in by_id]
    past = [x for x in same if _resolved(by_id[x["id"]])]
    open_ = [x for x in same if by_id[x["id"]].data.get("stateId") in (0, 2) and not by_id[x["id"]].disappeared]
    return {
        "similarPastCount": len(past), "similarOpenCount": len(open_),
        "weakSolution": resp["solution"]["weak"], "weakSolutionReason": resp["solution"]["reason"],
        "suggestedTeam": resp["team"]["name"], "suggestedTeamReason": resp["team"]["reason"],
        "similar": [{**x, "resolved": _resolved(by_id[x["id"]])} for x in resp["similar"] if x["id"] in by_id],
        "facts": resp.get("facts", []), "assumptions": resp.get("assumptions", []),
        "candidates": [c.id for c in cands],
    }


def _targets(s: Session, ctx: EvalContext) -> set[str]:
    if ctx.latest_snapshot_id is None:
        return set()
    out = set()
    for e in s.execute(select(IncidentEvent).where(IncidentEvent.snapshot_id == ctx.latest_snapshot_id)).scalars():
        if e.type == "created" or (e.type == "field_changed" and e.field in TEXT_FIELDS):
            out.add(e.incident_id)
    return out


def process_after_ingest(s: Session, ctx: EvalContext) -> dict:
    if not is_configured():
        return {"enabled": False}
    pool = list(s.execute(select(Incident)).scalars())
    by_id = {i.id: i for i in pool}
    ids = [i for i in _targets(s, ctx) if i in by_id]
    # сначала активные, потом остальные
    ids.sort(key=lambda i: (by_id[i].data.get("stateId") not in (0, 2), i))
    done = errors = cached = 0
    for iid in ids[:MAX_PER_UPLOAD]:
        inc = by_id[iid]
        h = text_hash(inc.data)
        row = s.get(AiResult, iid)
        if row is not None and row.text_hash == h and not row.error:
            cached += 1
            continue
        try:
            data = analyze(s, inc, pool)
            err = None
        except Exception as e:  # невалидный ответ = сигнал не посчитан
            log.exception("LLM: не удалось обработать %s", iid)
            data, err = {}, str(e)
            errors += 1
        if row is None:
            row = AiResult(incident_id=iid, text_hash=h)
            s.add(row)
        row.text_hash, row.data, row.error, row.updated_at = h, data, err, utcnow()
        s.execute(delete(Link).where(Link.source == "ai", Link.src == iid))
        for x in data.get("similar", []):
            if x["relation"] != "same":
                continue
            s.add(Link(src=iid, dst=x["id"], type="repeat" if x.get("resolved") else "double", directed=False,
                       source="ai", note=x.get("explanation", "")))
        done += 1
    s.flush()
    return {"enabled": True, "processed": done, "cached": cached, "errors": errors, "skipped": max(0, len(ids) - MAX_PER_UPLOAD)}
