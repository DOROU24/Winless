"""Помощник координатора: колонки из фразы, вопросы по истории, подсказка команды.

Помощник ничего не меняет сам. Инструменты только читают данные; изменения отображения (колонка, доска, порог,
звук, сортировка) он оформляет предложением, а применяет их человек кнопкой «Применить».
В ITSM и в данных инцидентов помощник не пишет ничего, сотрудников не называет — только команды.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import count
from pathlib import Path
from statistics import median
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from .boards import board_columns, load_incidents, place
from .db import Board, Column, Incident, IncidentEvent, Snapshot, get_setting
from .fields import _active, build_context, field_value, minutes, parse_dt, registry, waves
from .rules import (ANY_OPS, OP_LABELS, OPS_BY_TYPE, describe_leaf, explain, fmt_value, is_empty_rule, is_group,
                    match, rule_fields, validate)

RESOLVED_STATES = (7, 10)
LEVELS = {"critical": "критично", "elevated": "внимание", "info": "для сведения"}
ALERTS = {"off": "без оповещения", "popup": "окно", "sound": "окно и звук"}
WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}")
LIMITATIONS_TEAM = [
    "Время решения считается от регистрации до завершения, а не по рабочему времени команды; инцидент мог "
    "прийти в команду перед самым закрытием.",
    "Число активных инцидентов не равно реальной загрузке: смен, графиков и сложности задач в данных нет.",
]
_ids = count(1)
HISTORY_TOOLS = {"field_change_stats", "top_problems", "service_timeline", "recent_changes"}
HISTORY_FORMAT = (
    "Ответь строго по порядку: 1) вывод одной-двумя фразами; 2) таблица markdown с цифрами (если строк 2+); "
    "3) **Основания:** номера инцидентов; 4) **Факт:** … и **Предположение:** … (причины — только предположение); "
    "5) **Ограничения данных:** период истории из блока «История» в инструкции и чего нет в данных; "
    "6) последней строкой: «Могу создать колонку, куда будут попадать … Создать?»")
TEAM_FORMAT = (
    "Ответь: 1) одна фраза — какую команду рассмотреть первой (это совет, не назначение); 2) по каждой команде из "
    "teams (до трёх) отдельным пунктом: похожих решённых, медиана решения похожих или «мало данных», активных сейчас "
    "и их приоритеты; 3) подсказка ИИ, если есть; 4) **Основания:** номера из basis; 5) **Ограничения:** оба пункта "
    "из limitations полностью. Сотрудников не называй")


def min_similar(s: Session) -> int:
    v = get_setting(s, "assistant_min_similar", 3)
    return v if isinstance(v, int) and v > 0 else 3


# ---- описание правила словами ----------------------------------------------------------------

def _val_text(fd, v) -> str:
    if isinstance(v, list):
        return ", ".join(_val_text(fd, x) for x in v)
    if fd.labels:
        lab = fd.labels.get(str(v))
        if lab is None and isinstance(v, float) and v.is_integer():
            lab = fd.labels.get(str(int(v)))
        if lab:
            return lab
    return fmt_value(fd, v) if not isinstance(v, str) else f"«{v}»"


def _leaf_name(n, fd, reg) -> str:
    """Подпись условия; у полей истории — с подписями значений: «Число переходов (Статус: Завершен → Назначен)»."""
    if not fd.params:
        return fd.label
    args = n.get("args") or {}
    inner = reg.get(args.get("field"))
    if inner is None:
        return describe_leaf(n, fd, reg)
    if args.get("from") or args.get("to"):
        side = lambda xs: "/".join(_val_text(inner, x).strip("«»") for x in xs) if xs else "любое"
        return f"{fd.label} ({inner.label}: {side(args.get('from'))} → {side(args.get('to'))})"
    return f"{fd.label} ({inner.label})"


def rule_text(rule, reg) -> str:
    """«Переоткрытий ≥ 2 И активный» — для карточки предложения и ответа модели."""
    if is_empty_rule(rule):
        return "все инциденты (правила нет)"

    def walk(n, top=False) -> str:
        if is_group(n):
            parts = [walk(c) for c in n["children"] if not is_empty_rule(c)]
            joined = (" И " if n["op"] == "and" else " ИЛИ ").join(parts)
            return joined if top or len(parts) < 2 else f"({joined})"
        fd = reg.get(n.get("field"))
        if fd is None:
            return f"{n.get('field')} (нет такого поля)"
        name, cmp_ = _leaf_name(n, fd, reg), n.get("cmp")
        if cmp_ == "is_true":
            return name
        if cmp_ == "is_false":
            return f"не «{name}»"
        if cmp_ in ("is_empty", "not_empty"):
            return f"{name}: {OP_LABELS[cmp_]}"
        if cmp_ == "between" and isinstance(n.get("value"), list) and len(n["value"]) == 2:
            return f"{name} от {_val_text(fd, n['value'][0])} до {_val_text(fd, n['value'][1])}"
        return f"{name} {OP_LABELS.get(cmp_, cmp_)} {_val_text(fd, n.get('value'))}"

    return walk(rule, top=True)


# ---- вспомогательное --------------------------------------------------------------------------

def _tokens(inc: Incident) -> set[str]:
    d = inc.data
    text = f"{d.get('header') or ''} {d.get('shortDescription') or ''}".lower()
    return {w[:6] for w in WORD_RE.findall(text)}  # обрезка — грубая замена основы слова для русских окончаний


def _resolved(inc: Incident) -> bool:
    return inc.data.get("stateId") in RESOLVED_STATES


def _resolve_min(inc: Incident) -> float | None:
    """От регистрации до завершения, мин."""
    sub, done = parse_dt(inc.data.get("submitDate")), parse_dt(inc.data.get("completionDate"))
    if not sub or not done or not _resolved(inc):
        return None
    m = minutes(done, sub)
    return round(m, 1) if m >= 0 else None


def _brief(inc: Incident) -> dict:
    d = inc.data
    return {"id": inc.id, "header": d.get("header"), "state": d.get("state"), "priority": d.get("priority"),
            "team": d.get("assignedGroupName"), "service": d.get("service"), "product": d.get("productName"),
            "submitDate": d.get("submitDate"), "disappeared": inc.disappeared}


def _fmt_min(m) -> str | None:
    if m is None:
        return None
    m = int(round(m))
    if m >= 1440:
        return f"{m // 1440} д {m % 1440 // 60} ч"
    if m >= 60:
        return f"{m // 60} ч {m % 60} мин"
    return f"{m} мин"


# ---- инструменты ------------------------------------------------------------------------------

RULE_PARAM = {"type": "object", "description": "Дерево правила: группа {op: and|or, children: [...]} или условие "
                                              "{field, cmp, value?, args?}. Поля и операторы — из get_fields"}
COLUMN_SPEC = {
    "type": "object",
    "description": "Настройки колонки. Для update_column — только то, что меняется",
    "properties": {
        "name": {"type": "string"},
        "rule": RULE_PARAM,
        "level": {"type": "string", "enum": ["critical", "elevated", "info"]},
        "alert": {"type": "string", "enum": ["off", "popup", "sound"]},
        "repeatMin": {"type": ["integer", "null"], "description": "Повтор оповещения, мин; null — без повтора"},
        "sortField": {"type": ["string", "null"], "description": "Ключ поля сортировки"},
        "sortDir": {"type": "string", "enum": ["asc", "desc"]},
        "limit": {"type": ["integer", "null"], "description": "Сколько карточек показывать; null — все"},
        "groupSimilar": {"type": "boolean", "description": "Схлопывать волну сбоя в одну карточку (этап 2)"},
    },
}

TOOLS = [
    {"name": "get_fields", "description": "Реестр полей для правил: ключ, подпись, тип, значения, параметры, "
                                          "операторы по типам. Вызывай перед тем, как собирать правило",
     "parameters": {"type": "object", "properties": {
         "group": {"type": "string", "enum": ["all", "json", "calc", "hist", "ai"]}}}},
    {"name": "get_board", "description": "Текущая доска: колонки с их правилами (JSON и словами), сортировкой, "
                                         "оповещением и числом карточек; список остальных досок",
     "parameters": {"type": "object", "properties": {"boardId": {"type": "integer"}}}},
    {"name": "search_incidents", "description": "Найти инциденты по правилу без создания колонки: сколько "
                                                "подходит и какие, с объяснением почему",
     "parameters": {"type": "object", "required": ["rule"], "properties": {
         "rule": RULE_PARAM, "limit": {"type": "integer"},
         "includeDisappeared": {"type": "boolean"}}}},
    {"name": "explain_incident", "description": "Почему инцидент на доске (в каких колонках и по каким "
                                                "условиям), его поля, сигналы ИИ и последние изменения",
     "parameters": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "field_change_stats", "description": "Статистика изменений поля между загрузками за период: "
                                                  "например переназначения (assignedGroupName) — из каких команд, "
                                                  "в какие, какие пары; сколько инцидентов меняли поле 2+ раз",
     "parameters": {"type": "object", "required": ["field"], "properties": {
         "field": {"type": "string"}, "days": {"type": ["number", "null"]},
         "groupBy": {"type": "string", "enum": ["from", "to", "pair"]},
         "rule": RULE_PARAM, "top": {"type": "integer"}}}},
    {"name": "recent_changes", "description": "Что изменилось с прошлой загрузки: новые, пропавшие, какие поля "
                                              "менялись и у каких инцидентов",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "top_problems", "description": "Какие проблемы чаще всего возникали и возвращались за период: группы "
                                            "одинаковых инцидентов (по ИИ, сервису, продукту, команде или "
                                            "заголовку), переоткрытия, частые тексты решений",
     "parameters": {"type": "object", "properties": {
         "days": {"type": ["number", "null"]},
         "groupBy": {"type": "string", "enum": ["ai", "service", "product", "team", "header"]},
         "rule": RULE_PARAM, "top": {"type": "integer"},
         "query": {"type": "string", "description": "Тема: слово из заголовка, описания, сервиса или решения "
                                                    "(например «диск», «авторизация»)"}}}},
    {"name": "service_timeline", "description": "Хронология по сервису, продукту или теме за период: какие "
                                                "инциденты возникали, что с ними менялось, когда решены",
     "parameters": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "Часть названия сервиса, продукта или заголовка"},
         "days": {"type": ["number", "null"]}}}},
    {"name": "find_similar", "description": "Похожие инциденты на данный: по сигналам ИИ и по совпадению слов "
                                            "заголовка и сервиса",
     "parameters": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "suggest_team", "description": "Кому отдать инцидент: по командам — опыт на похожих решённых, "
                                            "медиана времени решения похожих, очередь активных сейчас",
     "parameters": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "propose_change", "description": "Предложить изменение отображения: создать колонку, изменить "
                                              "колонку (порог, звук, сортировку, название) или создать доску. "
                                              "Проверяет правило и считает предпросмотр. Человек увидит карточку и "
                                              "сам нажмёт «Применить». Если вернулись problems — исправь и вызови снова",
     "parameters": {"type": "object", "required": ["kind", "title"], "properties": {
         "kind": {"type": "string", "enum": ["create_column", "update_column", "create_board"]},
         "title": {"type": "string", "description": "Коротко, что предлагается"},
         "boardId": {"type": "integer"},
         "columnId": {"type": "integer", "description": "Для update_column"},
         "column": COLUMN_SPEC,
         "board": {"type": "object", "properties": {
             "name": {"type": "string"}, "mode": {"type": "string", "enum": ["all", "first"]},
             "columns": {"type": "array", "items": COLUMN_SPEC}}},
         "assumptions": {"type": "array", "items": {"type": "string"},
                         "description": "Допущения, которые ты сделал за координатора"}}}},
]

TOOL_LABELS = {
    "get_fields": "смотрю поля", "get_board": "смотрю доску", "search_incidents": "ищу инциденты",
    "explain_incident": "разбираю инцидент", "field_change_stats": "считаю изменения",
    "recent_changes": "сравниваю загрузки", "top_problems": "ищу повторы", "service_timeline": "собираю хронологию",
    "find_similar": "ищу похожие", "suggest_team": "считаю по командам", "propose_change": "готовлю предложение",
}


class Tools:
    def __init__(self, s: Session, board_id: int | None = None):
        self.s = s
        self.ctx = build_context(s)
        self.reg = registry(self.ctx)
        self.board_id = board_id or get_setting(s, "active_board_id")
        self.proposals: list[dict] = []
        self.failed: list[str] = []  # проблемы последней неудачной попытки propose_change
        self._incs: list[Incident] | None = None
        self._snaps: dict[int, Snapshot] | None = None

    # общие данные
    @property
    def incidents(self) -> list[Incident]:
        if self._incs is None:
            self._incs = load_incidents(self.s)
        return self._incs

    @property
    def by_id(self) -> dict[str, Incident]:
        return {i.id: i for i in self.incidents}

    @property
    def snaps(self) -> dict[int, Snapshot]:
        if self._snaps is None:
            self._snaps = {x.id: x for x in self.s.execute(select(Snapshot).order_by(Snapshot.id)).scalars()}
        return self._snaps

    def _event_time(self, e: IncidentEvent):
        sn = self.snaps.get(e.snapshot_id)
        return (sn.uploaded_at if sn else e.at).replace(tzinfo=self.ctx.now.tzinfo)

    @property
    def anchor(self):
        """Конец данных: самое позднее время в выгрузке, но не позже «сейчас».

        Если выгрузка старая, «за последнюю неделю» считается от её конца, а не от сегодняшней даты.
        """
        if "anchor" not in self.__dict__:
            ts = [d for i in self.incidents for k in ("submitDate", "completionDate", "lastAssignmentToTeamDateTime")
                  if (d := parse_dt(i.data.get(k)))]
            self.__dict__["anchor"] = min(self.ctx.now, max(ts)) if ts else self.ctx.now
        return self.__dict__["anchor"]

    def _since(self, days):
        try:
            return self.anchor - timedelta(days=float(days)) if days else None
        except (TypeError, ValueError):
            return None

    def _period(self, days) -> str:
        if not self._since(days):
            return "вся история"
        end = self.anchor.strftime("%d.%m.%Y %H:%M")
        return f"{days} дн. до {end}" + ("" if (self.ctx.now - self.anchor).days < 1 else " (конец данных выгрузки)")

    def _incident(self, iid: str) -> Incident:
        inc = self.s.get(Incident, (iid or "").strip())
        if inc is None:  # разрешаем короткую запись: «10482» или «INC-10482»
            digits = re.sub(r"\D", "", iid or "")
            cand = [i for i in self.incidents if digits and i.id.endswith(digits)]
            if len(cand) == 1:
                return cand[0]
            raise ValueError(f"Инцидент {iid} не найден")
        return inc

    def _board(self, board_id=None) -> Board | None:
        bid = board_id or self.board_id
        b = self.s.get(Board, bid) if bid else None
        from .db import CURRENT_USER
        uid = CURRENT_USER.get()
        return b if b is not None and (uid is None or b.owner_id == uid) else None

    def history_period(self) -> dict:
        snaps = list(self.snaps.values())
        subs = [d for d in (parse_dt(i.data.get("submitDate")) for i in self.incidents) if d]
        return {
            "uploads": len(snaps),
            "firstUpload": snaps[0].uploaded_at.isoformat() + "Z" if snaps else None,
            "lastUpload": snaps[-1].uploaded_at.isoformat() + "Z" if snaps else None,
            "incidents": len(self.incidents),
            "oldestIncident": min(subs).isoformat() if subs else None,
            "newestIncident": max(subs).isoformat() if subs else None,
        }

    def call(self, name: str, args: dict):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ValueError(f"Нет инструмента {name}")
        out = fn(**{k: v for k, v in (args or {}).items() if v is not None})
        if isinstance(out, dict) and name in HISTORY_TOOLS:
            out["howToAnswer"] = HISTORY_FORMAT
        elif isinstance(out, dict) and name == "suggest_team":
            out["howToAnswer"] = TEAM_FORMAT
        return out

    # ---- поля и доска
    def t_get_fields(self, group: str = "all"):
        out = []
        for f in self.reg.values():
            if group not in ("all", f.group):
                continue
            item = {"key": f.key, "label": f.label, "type": f.type, "group": f.group, "description": f.description}
            if f.labels:
                item["labels"] = dict(list(f.labels.items())[:30])
            elif f.values:
                item["values"] = f.values[:30]
            if f.params:
                item["params"] = f.params
            if not f.available:
                item["available"] = False
                item["unavailableReason"] = f.unavailable_reason
            out.append(item)
        return {"fields": out, "opsByType": {t: ops + ANY_OPS for t, ops in OPS_BY_TYPE.items()},
                "opLabels": OP_LABELS}

    def t_get_board(self, boardId: int | None = None):
        b = self._board(boardId)
        from .db import CURRENT_USER
        q = select(Board).order_by(Board.position)
        if CURRENT_USER.get() is not None:
            q = q.where(Board.owner_id == CURRENT_USER.get())
        boards = [{"id": x.id, "name": x.name} for x in self.s.execute(q)
                  .scalars()]
        if b is None:
            return {"board": None, "boards": boards}
        cols = board_columns(self.s, b.id)
        placed = place(b, cols, self.incidents, self.reg, self.ctx)
        return {"board": {"id": b.id, "name": b.name, "mode": b.mode, "columns": [{
            "id": c.id, "name": c.name, "rule": c.rule, "ruleText": rule_text(c.rule, self.reg),
            "level": c.level, "alert": c.alert, "repeatMin": c.repeat_min, "sortField": c.sort_field,
            "sortDir": c.sort_dir, "limit": c.limit, "groupSimilar": c.group_similar,
            "count": len(placed[c.id])} for c in cols]}, "boards": boards}

    # ---- поиск и объяснение
    def t_search_incidents(self, rule, limit: int = 20, includeDisappeared: bool = False):
        errors = validate(rule, self.reg)
        if errors:
            return {"ok": False, "errors": errors}
        found = [i for i in self.incidents
                 if (includeDisappeared or not i.disappeared) and match(rule, i, self.reg, self.ctx)]
        return {"ok": True, "ruleText": rule_text(rule, self.reg), "count": len(found),
                "items": [{**_brief(i), "why": explain(rule, i, self.reg, self.ctx)} for i in found[:min(limit, 50)]]}

    def t_explain_incident(self, id: str):
        inc = self._incident(id)
        b = self._board()
        why = []
        if b is not None:
            cols = board_columns(self.s, b.id)
            placed = place(b, cols, self.incidents, self.reg, self.ctx)
            for c in cols:
                if any(x.id == inc.id for x in placed[c.id]):
                    why.append({"column": c.name, "level": LEVELS.get(c.level), "reasons":
                                explain(c.rule, inc, self.reg, self.ctx) or ["правила нет, подходят все"]})
        calc = {}
        for k, f in self.reg.items():
            if f.group in ("calc", "ai") and f.available and not f.params:
                v = field_value(self.reg, k, inc, self.ctx)
                if v not in (None, False, ""):
                    calc[f.label] = v
        events = [e for e in self.ctx.events.get(inc.id, [])][-12:]
        d = inc.data
        return {
            "incident": {**_brief(inc), "description": (d.get("description") or "")[:600],
                         "solution": (d.get("solution") or "")[:400], "deadline": d.get("deadline"),
                         "completionDate": d.get("completionDate"), "urgency": d.get("urgency"),
                         "impact": d.get("impact")},
            "board": b.name if b else None, "onBoard": why,
            "notOnBoardReason": None if why else ("пропал из последней загрузки" if inc.disappeared and b and
                                                  b.hide_disappeared else "не подходит ни под одно правило колонок"),
            "computed": calc,
            "changes": [{"field": self.reg[e.field].label if e.field in self.reg else e.field, "old": e.old,
                         "new": e.new, "at": self._event_time(e).isoformat()} for e in events],
        }

    # ---- история
    def t_field_change_stats(self, field: str, days=None, groupBy: str = "from", rule=None, top: int = 10):
        if field not in self.reg:
            raise ValueError(f"Поля {field} нет в реестре")
        if rule is not None and validate(rule, self.reg):
            return {"ok": False, "errors": validate(rule, self.reg)}
        since = self._since(days)
        incs = self.by_id
        groups: dict[str, dict] = defaultdict(lambda: {"changes": 0, "incidents": set()})
        per_inc: Counter = Counter()
        total = 0
        for iid, evs in self.ctx.events.items():
            inc = incs.get(iid)
            if inc is None or (rule is not None and not match(rule, inc, self.reg, self.ctx)):
                continue
            for e in evs:
                if e.field != field or (since and self._event_time(e) < since):
                    continue
                total += 1
                per_inc[iid] += 1
                key = {"from": e.old, "to": e.new}.get(groupBy, None)
                key = f"{e.old} → {e.new}" if groupBy == "pair" else str(key)
                groups[key]["changes"] += 1
                groups[key]["incidents"].add(iid)
        rows = sorted(groups.items(), key=lambda kv: -kv[1]["changes"])[:top]
        multi = [iid for iid, n in per_inc.items() if n >= 2]
        return {"field": self.reg[field].label, "period": self._period(days),
                "groupBy": {"from": "из какого значения", "to": "в какое значение", "pair": "пара из → в"}[groupBy],
                "totalChanges": total, "incidentsChanged": len(per_inc),
                "incidentsChanged2plus": len(multi), "examples2plus": multi[:10],
                "groups": [{"value": k, "changes": v["changes"], "incidents": len(v["incidents"]),
                            "examples": sorted(v["incidents"])[:6]} for k, v in rows]}

    def t_recent_changes(self):
        ids = sorted(self.snaps)
        if not ids:
            return {"note": "Загрузок ещё не было"}
        last = self.snaps[ids[-1]]
        prev = self.snaps[ids[-2]] if len(ids) > 1 else None
        evs = self.s.execute(select(IncidentEvent).where(IncidentEvent.snapshot_id == last.id)).scalars().all()
        incs = self.by_id
        by_type: dict[str, list] = defaultdict(list)
        by_field: dict[str, list] = defaultdict(list)
        for e in evs:
            if e.type == "field_changed":
                by_field[e.field].append({"id": e.incident_id, "old": e.old, "new": e.new})
            else:
                by_type[e.type].append(e.incident_id)

        def head(iid):
            i = incs.get(iid)
            return {"id": iid, "header": i.data.get("header") if i else None,
                    "priority": i.data.get("priority") if i else None}

        return {
            "lastUpload": {"file": last.filename, "at": last.uploaded_at.isoformat() + "Z", "count": last.count},
            "previousUpload": {"file": prev.filename, "at": prev.uploaded_at.isoformat() + "Z"} if prev else None,
            "new": [head(x) for x in by_type["created"]][:20], "newCount": len(by_type["created"]),
            "disappeared": [head(x) for x in by_type["disappeared"]][:20],
            "disappearedCount": len(by_type["disappeared"]),
            "reappeared": by_type["reappeared"][:20],
            "fieldChanges": [{"field": self.reg[f].label if f in self.reg else f, "key": f, "count": len(v),
                              "examples": v[:8]} for f, v in sorted(by_field.items(), key=lambda kv: -len(kv[1]))],
        }

    def _ai_clusters(self) -> dict[str, str]:
        """Все инциденты (и решённые тоже), которые ИИ признал одной проблемой: id → id кластера."""
        parent: dict[str, str] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for iid, data in self.ctx.ai.items():
            find(iid)
            for x in data.get("similar") or []:
                if x.get("relation") == "same" and x.get("id"):
                    parent[find(x["id"])] = find(iid)
        return {x: find(x) for x in parent}

    def t_top_problems(self, days=7, groupBy: str = "ai", rule=None, top: int = 8, query: str = ""):
        if rule is not None and validate(rule, self.reg):
            return {"ok": False, "errors": validate(rule, self.reg)}
        since = self._since(days)
        q = (query or "").lower().strip()
        qstems = {w[:5] for w in WORD_RE.findall(q)} or ({q} if q else set())

        def about(i):
            if not q:
                return True
            text = " ".join(str(i.data.get(k) or "") for k in
                            ("header", "shortDescription", "description", "service", "productName", "solution")).lower()
            return any(st in text for st in qstems)

        pool = [i for i in self.incidents
                if (since is None or (parse_dt(i.data.get("submitDate")) or self.ctx.now) >= since)
                and (rule is None or match(rule, i, self.reg, self.ctx)) and about(i)]
        note, clusters = None, {}
        if groupBy == "ai":
            clusters = self._ai_clusters()
            if not clusters:
                groupBy, note = "service", "Сигналов ИИ нет — сгруппировал по сервису"
        groups: dict[str, list[Incident]] = defaultdict(list)
        for i in pool:
            d = i.data
            key = {"ai": lambda: clusters.get(i.id, i.id) if groupBy == "ai" else None,
                   "service": lambda: d.get("service") or d.get("productName"),
                   "product": lambda: d.get("productName"), "team": lambda: d.get("assignedGroupName"),
                   "header": lambda: re.sub(r"\s+", " ", (d.get("header") or "").lower()).strip()}[groupBy]()
            if key:
                groups[str(key)].append(i)
        reopen_args = {"field": "stateId", "from": list(RESOLVED_STATES), "to": [0, 2]}
        rows = []
        for key, items in groups.items():
            reopens = sum(field_value(self.reg, "hist.transitions", i, self.ctx, reopen_args) or 0 for i in items)
            if len(items) < 2 and not reopens:
                continue
            sols = Counter((i.data.get("solution") or "").strip()[:120] for i in items if i.data.get("solution"))
            times = [t for t in (_resolve_min(i) for i in items) if t is not None]
            first = min(items, key=lambda i: i.data.get("submitDate") or "")
            rows.append({
                "group": first.data.get("header") if groupBy == "ai" else key,
                "incidents": len(items), "resolved": sum(_resolved(i) for i in items),
                "active": sum(_active(i) and not i.disappeared for i in items), "reopens": reopens,
                "services": sorted({i.data.get("service") for i in items if i.data.get("service")})[:3],
                "teams": [t for t, _ in Counter(i.data.get("assignedGroupName") for i in items).most_common(3)],
                "medianResolve": _fmt_min(median(times)) if times else None,
                "repeatedSolutions": [{"text": t, "times": n} for t, n in sols.most_common(3) if n >= 2],
                "examples": [i.id for i in sorted(items, key=lambda i: i.data.get("submitDate") or "")][:8],
            })
        rows.sort(key=lambda r: (-(r["incidents"] + r["reopens"]), r["group"] or ""))
        return {"groupedBy": groupBy, "note": note, "period": self._period(days),
                "incidentsInPeriod": len(pool), "groups": rows[:top]}

    def t_service_timeline(self, query: str, days=3):
        q = (query or "").lower().strip()
        if not q:
            raise ValueError("Пустой запрос")
        since = self._since(days)
        items = [i for i in self.incidents if q in " ".join(str(i.data.get(k) or "") for k in
                                                              ("service", "productName", "header", "scenarioName")).lower()]
        timeline = []
        for i in items:
            sub = parse_dt(i.data.get("submitDate"))
            if sub and (since is None or sub >= since):
                timeline.append({"at": sub.isoformat(), "id": i.id, "what": f"возник: {i.data.get('header')}",
                                 "priority": i.data.get("priority")})
            for e in self.ctx.events.get(i.id, []):
                t = self._event_time(e)
                if since is None or t >= since:
                    fd = self.reg.get(e.field)
                    timeline.append({"at": t.isoformat(), "id": i.id, "what":
                                     f"{fd.label if fd else e.field}: {_val_text(fd, e.old) if fd else e.old} → "
                                     f"{_val_text(fd, e.new) if fd else e.new}"})
            done = parse_dt(i.data.get("completionDate"))
            if done and (since is None or done >= since):
                timeline.append({"at": done.isoformat(), "id": i.id, "what": "завершён",
                                 "solution": (i.data.get("solution") or "")[:150]})
        timeline.sort(key=lambda x: x["at"])
        return {"query": query, "period": self._period(days),
                "matchedIncidents": len(items), "active": sum(_active(i) and not i.disappeared for i in items),
                "events": timeline[-80:], "truncated": len(timeline) > 80}

    # ---- похожие и команда
    def similar(self, inc: Incident, limit: int = 30) -> list[dict]:
        out: dict[str, dict] = {}
        for x in (self.ctx.ai.get(inc.id) or {}).get("similar") or []:
            if x.get("id") and x["id"] != inc.id:
                out[x["id"]] = {"source": "ИИ", "relation": x.get("relation"), "explanation": x.get("explanation"),
                                "score": 1.0 if x.get("relation") == "same" else 0.8}
        # обратное направление: ИИ мог отметить похожесть в результате другого инцидента
        for iid, data in self.ctx.ai.items():
            for x in data.get("similar") or []:
                if x.get("id") == inc.id and iid != inc.id and iid not in out:
                    out[iid] = {"source": "ИИ", "relation": x.get("relation"), "explanation": x.get("explanation"),
                                "score": 1.0 if x.get("relation") == "same" else 0.8}
        mine, svc = _tokens(inc), inc.data.get("serviceId") or inc.data.get("service")
        for o in self.incidents:
            if o.id == inc.id or o.id in out or not mine:
                continue
            other = _tokens(o)
            j = len(mine & other) / len(mine | other) if other else 0
            same_svc = svc and (o.data.get("serviceId") or o.data.get("service")) == svc
            if j >= 0.34 or (same_svc and j >= 0.2):
                out[o.id] = {"source": "совпадение слов" + (" и сервиса" if same_svc else ""), "relation": "words",
                             "explanation": None, "score": round(j + (0.1 if same_svc else 0), 2)}
        by_id = self.by_id
        rows = []
        for iid, meta in out.items():
            o = by_id.get(iid)
            if o is None:
                continue
            rows.append({**_brief(o), **meta, "resolved": _resolved(o), "resolveMin": _resolve_min(o)})
        rows.sort(key=lambda r: -r["score"])
        return rows[:limit]

    def t_find_similar(self, id: str, limit: int = 15):
        inc = self._incident(id)
        rows = self.similar(inc, limit)
        return {"incident": _brief(inc), "count": len(rows), "similar": rows}

    def t_suggest_team(self, id: str):
        inc = self._incident(id)
        need = min_similar(self.s)
        sims = [r for r in self.similar(inc, 60) if r["resolved"] and r["team"]]
        by_team: dict[str, list[dict]] = defaultdict(list)
        for r in sims:
            by_team[r["team"]].append(r)
        queue: dict[str, Counter] = defaultdict(Counter)
        for i in self.incidents:
            if _active(i) and not i.disappeared and i.data.get("assignedGroupName"):
                queue[i.data["assignedGroupName"]][i.data.get("priority") or "без приоритета"] += 1
        ai = (self.ctx.ai.get(inc.id) or {}).get("team") or {}
        teams = set(by_team) | ({ai["name"]} if ai.get("name") else set())
        rows = []
        for t in teams:
            items = by_team.get(t, [])
            times = [r["resolveMin"] for r in items if r["resolveMin"] is not None]
            enough = len(times) >= need
            rows.append({
                "team": t, "similarResolved": len(items),
                "medianResolve": _fmt_min(median(times)) if enough else None,
                "medianResolveMin": round(median(times)) if enough else None,
                "resolveNote": None if enough else f"мало данных: похожих решённых с датами {len(times)}, нужно {need}",
                "activeNow": sum(queue[t].values()), "activeByPriority": dict(queue[t]),
                "aiSuggested": t == ai.get("name"),
                "basis": [r["id"] for r in items][:6],
            })
        rows.sort(key=lambda r: (-r["similarResolved"], r["activeNow"]))
        return {
            "incident": _brief(inc), "currentTeam": inc.data.get("assignedGroupName"),
            "teams": rows[:5], "minSimilar": need,
            "aiSuggestion": {"team": ai.get("name"), "reason": ai.get("reason")} if ai.get("name") else None,
            "limitations": LIMITATIONS_TEAM,
            "note": "Только совет: помощник никого не назначает и ничего не меняет в ITSM" + (
                "" if rows else ". Похожих решённых инцидентов с командой не нашлось"),
        }

    # ---- предложения
    def _fix_rule(self, n):
        """Частые промахи модели, которые однозначно исправляются: для флага «= true/false» → «да/нет»."""
        if isinstance(n, dict) and isinstance(n.get("children"), list):
            return {**n, "children": [self._fix_rule(c) for c in n["children"]]}
        if isinstance(n, dict) and n.get("field") in self.reg and self.reg[n["field"]].type == "flag"                 and n.get("cmp") in ("eq", "neq"):
            v = n.get("value")
            truthy = v in (True, 1, "true", "True", "да")
            positive = truthy if n["cmp"] == "eq" else not truthy
            return {k: x for k, x in n.items() if k != "value"} | {"cmp": "is_true" if positive else "is_false"}
        return n

    def _column(self, spec: dict | None, partial: bool, problems: list[str]) -> dict:
        spec = spec or {}
        out: dict = {}
        if "name" in spec:
            out["name"] = str(spec["name"]).strip()[:120]
        if not partial and not out.get("name"):
            problems.append("У колонки нет названия")
        if "rule" in spec or not partial:
            rule = self._fix_rule(spec.get("rule"))
            errs = validate(rule, self.reg)
            problems += [e["message"] for e in errs]
            if not errs:
                off = [self.reg[f] for f in rule_fields(rule) if f in self.reg and not self.reg[f].available]
                problems += [f"Поле «{f.label}» сейчас недоступно: {f.unavailable_reason or 'нужен этап 2'}"
                             for f in off]
            out["rule"] = rule
        for k, allowed in (("level", LEVELS), ("alert", ALERTS), ("sortDir", ("asc", "desc"))):
            if k in spec:
                if spec[k] in allowed:
                    out[k] = spec[k]
                else:
                    problems.append(f"{k}: значение «{spec[k]}» не подходит")
        if "sortField" in spec:
            if spec["sortField"] and spec["sortField"] not in self.reg:
                problems.append(f"Поля сортировки «{spec['sortField']}» нет в реестре")
            out["sortField"] = spec["sortField"] or None
        for k in ("repeatMin", "limit"):
            if k in spec:
                v = spec[k]
                out[k] = int(v) if isinstance(v, (int, float)) and v > 0 else None
        if "groupSimilar" in spec:
            out["groupSimilar"] = bool(spec["groupSimilar"])
        if not partial:
            out.setdefault("level", "info")
            out.setdefault("alert", "off")
        return out

    def _settings_text(self, col: dict) -> list[str]:
        out = []
        if "level" in col:
            out.append(f"Важность: {LEVELS[col['level']]}")
        if "alert" in col:
            out.append(f"Оповещение: {ALERTS[col['alert']]}")
        if col.get("repeatMin"):
            out.append(f"Повтор каждые {col['repeatMin']} мин")
        if col.get("sortField"):
            out.append(f"Сортировка: {self.reg[col['sortField']].label}, "
                       f"{'по убыванию' if col.get('sortDir') == 'desc' else 'по возрастанию'}")
        if col.get("limit"):
            out.append(f"Показывать первые {col['limit']}")
        if col.get("groupSimilar"):
            out.append("Волна сбоя схлопывается в одну карточку")
        return out

    def _rule_diff(self, old, new) -> list[str]:
        """Если структура правила та же, показываем только изменившиеся условия: «Не взят в работу > 30 → > 20»."""
        def leaves(n, path="r"):
            if is_group(n):
                out = [(path, n["op"])]
                for i, ch in enumerate(n["children"]):
                    out += leaves(ch, f"{path}.{i}")
                return out
            return [(path, n)]
        a, b = leaves(old) if old else [], leaves(new) if new else []
        same_shape = len(a) == len(b) and all(
            pa == pb and ((isinstance(x, str) and x == y) or (isinstance(x, dict) and isinstance(y, dict)
                                                             and x.get("field") == y.get("field")))
            for (pa, x), (pb, y) in zip(a, b))
        if not same_shape:
            return [f"Правило: {rule_text(old, self.reg)} → {rule_text(new, self.reg)}"]
        out = []
        for (_, x), (_, y) in zip(a, b):
            if isinstance(x, dict) and x != y:
                fd = self.reg.get(y.get("field"))
                name = _leaf_name(y, fd, self.reg) if fd else str(y.get("field"))
                tail = lambda n: rule_text(n, self.reg)[len(name):].strip()
                out.append(f"{name}: {tail(x)} → {tail(y)}")
        return out or ["Правило без изменений"]

    def _diff(self, c: Column, patch: dict) -> list[str]:
        """Что именно поменяется в колонке — строками «было → станет»."""
        reg = self.reg
        old = {"name": c.name, "rule": c.rule, "level": c.level, "alert": c.alert, "repeatMin": c.repeat_min,
               "sortField": c.sort_field, "sortDir": c.sort_dir, "limit": c.limit, "groupSimilar": c.group_similar}
        show = {
            "name": ("Название", lambda v: f"«{v}»"),
            "level": ("Важность", lambda v: LEVELS.get(v, v)),
            "alert": ("Оповещение", lambda v: ALERTS.get(v, v)),
            "repeatMin": ("Повтор оповещения", lambda v: f"каждые {v} мин" if v else "без повтора"),
            "sortField": ("Сортировка", lambda v: reg[v].label if v in reg else "по номеру"),
            "sortDir": ("Порядок", lambda v: "по убыванию" if v == "desc" else "по возрастанию"),
            "limit": ("Показывать", lambda v: f"первые {v}" if v else "все"),
            "groupSimilar": ("Схлопывать волну", lambda v: "да" if v else "нет"),
        }
        out = self._rule_diff(c.rule, patch["rule"]) if "rule" in patch and patch["rule"] != c.rule else []
        for k, (label, fmt) in show.items():
            if k in patch and patch[k] != old[k]:
                out.append(f"{label}: {fmt(old[k])} → {fmt(patch[k])}")
        return out

    def _count(self, board: Board | None, cols: list, target_id) -> tuple[int, int, list[Incident]]:
        """Сколько подходит под правило колонки и сколько окажется в ней с учётом режима доски."""
        b = board or SimpleNamespace(mode="all", hide_disappeared=True)
        target = next(c for c in cols if c.id == target_id)
        raw = [i for i in self.incidents if not (b.hide_disappeared and i.disappeared)
               and match(target.rule, i, self.reg, self.ctx)]
        placed = place(b, cols, self.incidents, self.reg, self.ctx)
        return len(raw), len(placed[target_id]), placed[target_id]

    @staticmethod
    def _ns(c: Column | None, spec: dict, cid) -> SimpleNamespace:
        base = {"rule": c.rule, "sort_field": c.sort_field, "sort_args": c.sort_args, "sort_dir": c.sort_dir} if c \
            else {"rule": None, "sort_field": None, "sort_args": None, "sort_dir": "asc"}
        if "rule" in spec:
            base["rule"] = spec["rule"]
        if "sortField" in spec:
            base["sort_field"] = spec["sortField"]
        if "sortDir" in spec:
            base["sort_dir"] = spec["sortDir"]
        return SimpleNamespace(id=cid, **base)

    def t_propose_change(self, kind: str, title: str = "", boardId: int | None = None, columnId: int | None = None,
                         column: dict | None = None, board: dict | None = None, assumptions: list | None = None):
        problems: list[str] = []
        b = self._board(boardId)
        prop: dict = {"id": f"p{next(_ids)}", "kind": kind, "title": (title or "").strip() or
                      (column or {}).get("name") or (board or {}).get("name") or "Изменение",
                      "assumptions": [str(a) for a in (assumptions or [])][:6]}
        if kind in ("create_column", "update_column") and b is None:
            return {"ok": False, "problems": ["Нет активной доски: сначала предложи create_board"]}
        if kind == "create_column":
            col = self._column(column, False, problems)
            if problems:
                self.failed = problems
                return {"ok": False, "problems": problems,
                        "note": "Предложение НЕ показано. Исправь и вызови propose_change снова"}
            existing = board_columns(self.s, b.id)
            cols = [self._ns(c, {}, c.id) for c in existing] + [self._ns(None, col, -1)]
            total, inside, items = self._count(b, cols, -1)
            prop.update(boardId=b.id, boardName=b.name, column=col, ruleText=rule_text(col["rule"], self.reg),
                        settings=self._settings_text(col), count=total, inColumn=inside,
                        sample=[_brief(i) for i in items[:5]])
            if b.mode == "first" and inside < total:
                prop["note"] = (f"Доска в режиме «только в первой подходящей»: {total - inside} из {total} "
                                f"уже заняты колонками выше")
        elif kind == "update_column":
            c = self.s.get(Column, columnId) if columnId else None
            if c is None or c.board_id != b.id:
                return {"ok": False, "problems": ["Колонка не найдена на доске: возьми columnId из get_board"]}
            patch = self._column(column, True, problems)
            if not patch:
                problems.append("Нечего менять: в column нет изменений")
            if problems:
                self.failed = problems
                return {"ok": False, "problems": problems,
                        "note": "Предложение НЕ показано. Исправь и вызови propose_change снова"}
            existing = board_columns(self.s, b.id)
            before = self._count(b, [self._ns(x, {}, x.id) for x in existing], c.id)
            after_cols = [self._ns(x, patch if x.id == c.id else {}, x.id) for x in existing]
            after = self._count(b, after_cols, c.id)
            changes = self._diff(c, patch)
            if prop["title"] == "Изменение":
                prop["title"] = f"Изменение колонки «{c.name}»"
            prop.update(boardId=b.id, boardName=b.name, columnId=c.id, columnName=c.name, column=patch,
                        changes=changes or ["Без видимых изменений"],
                        ruleText=rule_text(patch.get("rule", c.rule), self.reg),
                        countBefore=before[1], count=after[0], inColumn=after[1],
                        sample=[_brief(i) for i in after[2][:5]])
        elif kind == "create_board":
            spec = board if isinstance(board, dict) else {}
            name = str(spec.get("name") or title or "").strip()[:120]
            if not name:
                problems.append("У доски нет названия")
            mode = spec.get("mode") if spec.get("mode") in ("all", "first") else "all"
            cols = []
            for n, cs in enumerate(spec.get("columns") or []):
                sub: list[str] = []
                col = self._column(cs if isinstance(cs, dict) else {}, False, sub)
                problems += [f"Колонка {n + 1}: {p}" for p in sub]
                cols.append(col)
            if not cols:
                problems.append("В доске нет колонок")
            if problems:
                self.failed = problems
                return {"ok": False, "problems": problems,
                        "note": "Предложение НЕ показано. Исправь и вызови propose_change снова"}
            fake = SimpleNamespace(mode=mode, hide_disappeared=True)
            ns = [self._ns(None, c, -(k + 1)) for k, c in enumerate(cols)]
            prop.update(board={"name": name, "mode": mode, "columns": cols})
            prop["columns"] = []
            for k, c in enumerate(cols):
                total, inside, items = self._count(fake, ns, -(k + 1))
                prop["columns"].append({"name": c["name"], "ruleText": rule_text(c["rule"], self.reg),
                                        "settings": self._settings_text(c), "count": total, "inColumn": inside,
                                        "sample": [_brief(i) for i in items[:3]]})
        else:
            problems = [f"Неизвестный вид изменения {kind}"]
        if problems:
            self.failed = problems
            return {"ok": False, "problems": problems,
                    "note": "Предложение НЕ показано. Исправь и вызови propose_change снова; не пиши «Предлагаю», "
                            "пока не получишь ok: true"}
        self.proposals.append(prop)
        self.failed = []
        short = {k: v for k, v in prop.items() if k not in ("sample", "column", "board")}
        return {"ok": True, "proposal": short,
                "note": "Карточка показана координатору. Не применяй сам: он нажмёт «Применить»"}


def _plain(problem: str, reg) -> str:
    """Текст проблемы для человека: подписи полей вместо ключей, без списка операторов."""
    problem = re.sub(r"\. Можно: .*$", "", problem)
    return re.sub(r"«([\w.]+)»", lambda m: f"«{reg[m.group(1)].label}»" if m.group(1) in reg else m.group(0), problem)


# ---- диалог -----------------------------------------------------------------------------------

# Инструкция модели лежит рядом отдельным файлом, чтобы её было удобно читать и править
SYSTEM = (Path(__file__).with_name("assistant_prompt.txt")).read_text(encoding="utf-8")


def chat(s: Session, messages: list[dict], board_id: int | None = None, incident_id: str | None = None) -> dict:
    from .llm.adapter import StepLimit, run_tools

    tools = Tools(s, board_id)
    b = tools._board()
    period = tools.history_period()
    context = (f"\n\nСейчас: {tools.ctx.now.isoformat()}. История: загрузок {period['uploads']}, "
               f"с {period['firstUpload']} по {period['lastUpload']}; инцидентов {period['incidents']}, "
               f"возникли с {period['oldestIncident']} по {period['newestIncident']}. "
               f"Текущая доска: {b.name + ' (id ' + str(b.id) + ')' if b else 'нет'}."
               + (f" Открыта карточка инцидента {incident_id}." if incident_id else ""))
    history = [{"role": m["role"], "content": str(m["content"])[:6000]} for m in messages[-16:]
               if m.get("role") in ("user", "assistant") and m.get("content")]
    try:
        res = run_tools(SYSTEM + context, history, TOOLS, tools.call, max_steps=10)
    except StepLimit as e:
        res = {"text": "", "steps": e.steps}
    reply = res["text"].strip()
    tried = any(x["tool"] == "propose_change" for x in res["steps"])
    if tried and not tools.proposals:
        reply = ("Не получилось собрать предложение — правило не прошло проверку:\n"
                 + "\n".join(f"- {_plain(p, tools.reg)}" for p in tools.failed[:4])
                 + "\nПереформулируйте, пожалуйста, или уточните условия.")
    elif tools.proposals:  # кнопки уже на карточке — вопрос «Создать?» в конце лишний
        reply = re.sub(r"\s*(Создать|Применить|Добавить|Создаём|Применяем)\s*\?\s*$", "", reply).strip()
    elif not reply:
        reply = "Не успел ответить за отведённое число шагов. Сформулируйте вопрос проще или разбейте на части."
    return {"reply": reply, "proposals": tools.proposals,
            "steps": [{"tool": x["tool"], "label": TOOL_LABELS.get(x["tool"], x["tool"])} for x in res["steps"]]}
