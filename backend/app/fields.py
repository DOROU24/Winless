"""Реестр полей: поля JSON, вычисляемые (calc.*), по истории (hist.*), ИИ (ai.*).

Дашборд только для просмотра: в реестре нет полей, которые ставит пользователь. Всё берётся из JSON
или вычисляется из него.

Добавить поле = добавить запись в реестр. Движок правил, сортировка и карточка работают только через реестр.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import AiResult, FieldCatalog, Incident, IncidentEvent, Snapshot, get_setting

# ---- даты -------------------------------------------------------------------

def parse_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if s.startswith("0001-01-01"):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def minutes(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 60.0


# ---- контекст вычислений -------------------------------------------------------

@dataclass
class EvalContext:
    """Всё, что нужно для вычисления полей, загружено одним набором запросов."""
    session: Session
    now: datetime
    latest_snapshot_id: int | None
    catalog: dict[str, FieldCatalog]
    _events: dict[str, list[IncidentEvent]] | None = None
    _ai: dict[str, dict] | None = None
    cache: dict = dc_field(default_factory=dict)

    @property
    def events(self) -> dict[str, list[IncidentEvent]]:
        """Журнал изменений полей — одним запросом на весь контекст."""
        if self._events is None:
            by_inc: dict[str, list[IncidentEvent]] = defaultdict(list)
            rows = self.session.execute(
                select(IncidentEvent).where(IncidentEvent.type == "field_changed").order_by(IncidentEvent.id)
            ).scalars()
            for e in rows:
                by_inc[e.incident_id].append(e)
            self._events = by_inc
        return self._events

    @property
    def ai(self) -> dict[str, dict]:
        if self._ai is None:
            self._ai = {r.incident_id: (r.data or {}) for r in self.session.execute(select(AiResult)).scalars()
                        if not r.error}
        return self._ai


def current_now() -> datetime:
    """Текущее время для правил. Всегда реальное; менять его из интерфейса или API нельзя.

    Только для автотестов и демо-стенда можно зафиксировать время переменной окружения MTS_NOW (ISO 8601).
    """
    fixed = parse_dt(os.environ.get("MTS_NOW", "")) if os.environ.get("MTS_NOW") else None
    return fixed or datetime.now(timezone.utc)


def build_context(s: Session) -> EvalContext:
    now = current_now()
    latest = s.execute(select(Snapshot.id).order_by(Snapshot.id.desc()).limit(1)).scalar()
    catalog = {r.name: r for r in s.execute(select(FieldCatalog)).scalars()}
    return EvalContext(session=s, now=now, latest_snapshot_id=latest, catalog=catalog)


# ---- описание полей -------------------------------------------------------------

@dataclass
class FieldDef:
    key: str
    label: str
    type: str  # number | text | date | flag
    description: str = ""
    group: str = "json"  # json | calc | hist | ai
    compute: Callable[[Incident, EvalContext, dict | None], Any] | None = None
    params: list[dict] | None = None  # для hist.*: описание параметров
    labels: dict | None = None
    values: list | None = None
    available: bool = True
    unavailable_reason: str = ""

    def public(self) -> dict:
        return {
            "key": self.key, "label": self.label, "type": self.type, "description": self.description,
            "group": self.group, "params": self.params, "labels": self.labels or {}, "values": self.values or [],
            "available": self.available, "unavailableReason": self.unavailable_reason,
        }


# Человеческие подписи для известных полей JSON. Это только подписи интерфейса: неизвестные поля
# получают подпись, равную имени, и всё равно доступны в конструкторе.
JSON_LABELS: dict[str, str] = {
    "id": "Номер", "sysId": "Системный ID", "state": "Статус", "stateId": "Статус",
    "stateEnum": "Статус (англ.)", "stateReason": "Подстатус", "stateReasonId": "Подстатус",
    "submitDate": "Возник", "deadline": "Срок решения", "completionDate": "Завершён",
    "lastAssignmentToTeamDateTime": "Назначен на команду", "header": "Заголовок", "description": "Описание",
    "shortDescription": "Краткое описание", "assignedGroupName": "Команда", "assignedEmployee": "Сотрудник",
    "assignedEmployeeLogin": "Логин сотрудника", "initiatorName": "Инициатор", "initiatorLogin": "Логин инициатора",
    "isEffect": "Следствие", "isReason": "Причина", "isDouble": "Дубль", "isMaster": "Мастер-инцидент",
    "incType": "Тип инцидента", "serviceTypeId": "Тип сервиса", "serviceType": "Тип сервиса",
    "priorityId": "Приоритет", "priority": "Приоритет", "urgencyId": "Срочность", "urgency": "Срочность",
    "impactId": "Влияние", "impact": "Влияние", "waitingExitDate": "Выход из ожидания",
    "isRootIncident": "Корневой", "productName": "Продукт", "productId": "Продукт (ID)",
    "productMasterId": "Мастер-ID продукта", "productUrl": "Ссылка на продукт", "service": "Сервис",
    "serviceId": "Сервис (ID)", "scenarioName": "Сценарий", "isNeedUp": "Нужно устранить последствия",
    "eliminationType": "Тип устранения", "eliminationTypeId": "Тип устранения",
    "receivingChannel": "Канал", "solution": "Решение", "complaintsCount": "Жалоб",
}


def _f(name: str) -> Callable:
    return lambda inc, ctx, args: inc.data.get(name)


def _active(inc: Incident) -> bool:
    return inc.data.get("stateId") in (0, 2)


def _window(inc: Incident) -> float | None:
    sub, dl = parse_dt(inc.data.get("submitDate")), parse_dt(inc.data.get("deadline"))
    if not sub or not dl:
        return None
    w = minutes(dl, sub)
    return w if w > 0 else None


def c_deadline_left_min(inc, ctx, args):
    dl = parse_dt(inc.data.get("deadline"))
    return None if dl is None else round(minutes(dl, ctx.now), 1)


def c_deadline_left_pct(inc, ctx, args):
    dl = parse_dt(inc.data.get("deadline"))
    w = _window(inc)
    if dl is None or w is None:
        return None
    return round(minutes(dl, ctx.now) / w * 100, 1)


def c_overdue(inc, ctx, args):
    dl = parse_dt(inc.data.get("deadline"))
    return bool(dl is not None and dl < ctx.now and _active(inc))


def c_idle_min(inc, ctx, args):
    if inc.data.get("stateId") != 0:
        return None
    t = parse_dt(inc.data.get("lastAssignmentToTeamDateTime"))
    return None if t is None else round(minutes(ctx.now, t), 1)


def c_idle_pct(inc, ctx, args):
    idle = c_idle_min(inc, ctx, args)
    w = _window(inc)
    if idle is None or w is None:
        return None
    return round(idle / w * 100, 1)


def c_resolved_in(inc, ctx, args):
    c, a = parse_dt(inc.data.get("completionDate")), parse_dt(inc.data.get("lastAssignmentToTeamDateTime"))
    return None if not c or not a else round(minutes(c, a), 1)


def c_age(inc, ctx, args):
    s = parse_dt(inc.data.get("submitDate"))
    return None if s is None else round(minutes(ctx.now, s), 1)


# ---- поля по истории ---------------------------------------------------------------

def _norm(v):
    """Сравнение значений из JSON: 2 и "2" считаем одним кодом."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _field_events(inc, ctx, f):
    return [e for e in ctx.events.get(inc.id, ()) if e.field == f]


def h_changes(inc, ctx, args):
    return len(_field_events(inc, ctx, (args or {}).get("field")))


def h_increases(inc, ctx, args):
    n = 0
    for e in _field_events(inc, ctx, (args or {}).get("field")):
        o, nw = _num(e.old), _num(e.new)
        if o is not None and nw is not None and nw > o:
            n += 1
    return n


def h_transitions(inc, ctx, args):
    args = args or {}
    frm = {_norm(x) for x in (args.get("from") or [])}
    to = {_norm(x) for x in (args.get("to") or [])}
    n = 0
    for e in _field_events(inc, ctx, args.get("field")):
        if (not frm or _norm(e.old) in frm) and (not to or _norm(e.new) in to):
            n += 1
    return n


def h_delta(inc, ctx, args):
    f = (args or {}).get("field")
    if inc.first_snapshot_id == ctx.latest_snapshot_id or inc.last_snapshot_id != ctx.latest_snapshot_id:
        return None  # в прошлом снимке инцидента не было или в последнем его нет
    for e in reversed(_field_events(inc, ctx, f)):
        if e.snapshot_id == ctx.latest_snapshot_id:
            o, nw = _num(e.old), _num(e.new)
            if nw is None:
                return None
            return nw - (o or 0)
        break
    return 0 if _num(inc.data.get(f)) is not None else None


# ---- ИИ (этап 2) -----------------------------------------------------------------------

def _ai(name):
    return lambda inc, ctx, args: ctx.ai.get(inc.id, {}).get(name)


# ---- сигналы ИИ, которые считаются на лету -------------------------------------------------------

def _fmt_min(m: float) -> str:
    m = int(round(abs(m)))
    if m >= 1440:
        return f"{m // 1440} д {m % 1440 // 60} ч" if m % 1440 >= 60 else f"{m // 1440} д"
    if m >= 60:
        return f"{m // 60} ч {m % 60} мин" if m % 60 else f"{m // 60} ч"
    return f"{m} мин"


def waves(ctx: EvalContext) -> dict[str, dict]:
    """Волны сбоев: активные инциденты, которые ИИ признал одной проблемой, объединённые по цепочке.

    Если A похож на B, а B на C, — это одна волна A, B, C. Считается на лету, поэтому волна растёт сразу,
    как приходит новый похожий инцидент. Возвращает {id инцидента: {id, size, members, title}} для волн от 2.
    """
    if "waves" in ctx.cache:
        return ctx.cache["waves"]
    incs = {i.id: i for i in ctx.session.execute(select(Incident)).scalars()}
    active = {iid for iid, i in incs.items() if _active(i) and not i.disappeared}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for iid, data in ctx.ai.items():
        if iid not in active:
            continue
        find(iid)
        for x in data.get("similar") or []:
            if x.get("relation") == "same" and x.get("id") in active:
                parent[find(x["id"])] = find(iid)
    groups: dict[str, list[str]] = defaultdict(list)
    for iid in parent:
        groups[find(iid)].append(iid)
    out: dict[str, dict] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (incs[m].data.get("submitDate") or "", m))
        wave = {"id": f"W-{members[0]}", "size": len(members), "members": members,
                "title": incs[members[0]].data.get("header") or ""}
        for m in members:
            out[m] = wave
    ctx.cache["waves"] = out
    return out


def _resolution_index(ctx: EvalContext) -> dict:
    """Сколько решались прошлые инциденты (назначение → «Завершен»): по id, по сервису и по команде."""
    if "resolution_index" in ctx.cache:
        return ctx.cache["resolution_index"]
    by_id, by_service, by_team = {}, defaultdict(list), defaultdict(list)
    for inc in ctx.session.execute(select(Incident)).scalars():
        d = inc.data
        done, start = parse_dt(d.get("completionDate")), parse_dt(d.get("lastAssignmentToTeamDateTime"))
        if not done or not start or done <= start:
            continue
        m = minutes(done, start)
        by_id[inc.id] = m
        if d.get("serviceId"):
            by_service[d["serviceId"]].append((inc.id, m))
        if d.get("assignedGroupName"):
            by_team[d["assignedGroupName"]].append((inc.id, m))
    idx = {"by_id": by_id, "by_service": by_service, "by_team": by_team}
    ctx.cache["resolution_index"] = idx
    return idx


def overdue_risk(inc: Incident, ctx: EvalContext) -> dict | None:
    """Риск просрочки: похожие случаи решались дольше, чем осталось до срока.

    Опора по порядку: случаи, которые ИИ признал той же проблемой; иначе тот же сервис; иначе та же команда.
    Нужно минимум 2 прошлых случая. Считается от текущего времени, поэтому риск появляется без новых загрузок.
    """
    key = ("overdue_risk", inc.id)
    if key in ctx.cache:
        return ctx.cache[key]
    d = inc.data
    deadline = parse_dt(d.get("deadline"))
    result = None
    if _active(inc) and not inc.disappeared and deadline and deadline > ctx.now:
        idx = _resolution_index(ctx)
        same = [x["id"] for x in (ctx.ai.get(inc.id, {}).get("similar") or []) if x.get("relation") == "same"]
        samples, basis = [idx["by_id"][i] for i in same if i in idx["by_id"]], "похожим случаям, найденным ИИ"
        if len(samples) < 2:
            samples = [m for i, m in idx["by_service"].get(d.get("serviceId"), []) if i != inc.id]
            basis = "инцидентам этого сервиса"
        if len(samples) < 2:
            samples = [m for i, m in idx["by_team"].get(d.get("assignedGroupName"), []) if i != inc.id]
            basis = "инцидентам этой команды"
        if len(samples) >= 2:
            samples.sort()
            mid = len(samples) // 2
            expected = samples[mid] if len(samples) % 2 else (samples[mid - 1] + samples[mid]) / 2
            start = parse_dt(d.get("lastAssignmentToTeamDateTime")) or parse_dt(d.get("submitDate")) or ctx.now
            elapsed = max(0.0, minutes(ctx.now, start))
            left = minutes(deadline, ctx.now)
            stuck = elapsed >= expected
            # Ещё не вышли за обычное время — сравниваем остаток обычной работы со временем до срока.
            # Уже дольше обычного (застрял) — риск, если до срока меньше, чем обычно занимает решение целиком.
            risk = (left < expected) if stuck else (expected - elapsed > left)
            note = "уже дольше обычного" if stuck else f"в работе {_fmt_min(elapsed)}"
            result = {"risk": risk, "expected": round(expected, 1),
                      "reason": (f"Обычно решается за {_fmt_min(expected)} (медиана по {len(samples)} {basis}); "
                                 f"{note}, до срока {_fmt_min(left)}")}
    ctx.cache[key] = result
    return result


PRIORITY_LABELS = {"1": "Низкий", "2": "Средний", "3": "Высокий", "4": "Наивысший"}

AI_FIELDS = [
    ("ai.similarPastCount", "Похожих в прошлом", "number", "Сколько решённых инцидентов ИИ признал той же проблемой"),
    ("ai.similarOpenCount", "Похожих открытых сейчас", "number", "Сколько активных инцидентов описывают ту же проблему"),
    ("ai.weakSolution", "Отписка в решении", "flag", "ИИ считает текст решения отпиской"),
    ("ai.weakSolutionReason", "Почему отписка", "text", "Одна фраза объяснения от ИИ"),
    ("ai.suggestedTeam", "Предложенная команда", "text", "Команда по похожим прошлым инцидентам и таблице команд"),
    ("ai.suggestedTeamReason", "Почему эта команда", "text", "На какие инциденты опирается подсказка"),
    ("ai.suggestedPriority", "Приоритет по оценке ИИ", "number", "Какой приоритет ИИ считает верным по масштабу из текста"),
    ("ai.priorityUnderestimated", "Приоритет занижен", "flag", "ИИ оценивает приоритет выше текущего"),
    ("ai.priorityReason", "Почему приоритет выше", "text", "На что опирается оценка приоритета"),
]

# Сигналы ИИ, которые считаются на лету из сохранённых результатов и текущего времени
AI_LIVE_FIELDS = [
    ("ai.waveId", "Волна сбоя", "text", "Номер волны: активные инциденты, которые ИИ признал одной проблемой",
     lambda inc, ctx, a: (waves(ctx).get(inc.id) or {}).get("id")),
    ("ai.waveSize", "Инцидентов в волне", "number", "Сколько активных инцидентов в той же волне, включая этот",
     lambda inc, ctx, a: (waves(ctx).get(inc.id) or {}).get("size")),
    ("ai.overdueRisk", "Риск просрочки", "flag", "Похожие случаи решались дольше, чем осталось до срока",
     lambda inc, ctx, a: (overdue_risk(inc, ctx) or {}).get("risk")),
    ("ai.expectedResolveMin", "Обычно решается за, мин", "number", "Медиана времени решения похожих случаев",
     lambda inc, ctx, a: (overdue_risk(inc, ctx) or {}).get("expected")),
    ("ai.overdueRiskReason", "Почему риск просрочки", "text", "На каких случаях основан прогноз",
     lambda inc, ctx, a: (overdue_risk(inc, ctx) or {}).get("reason")),
]

HIST_FIELD_PARAM = {"name": "field", "label": "Поле", "kind": "field"}

STATIC_FIELDS: list[FieldDef] = [
    FieldDef("calc.isActive", "Активный", "flag", "Статус «Назначен» или «Выполняется» (stateId 0 или 2)", "calc",
             lambda inc, ctx, a: _active(inc)),
    FieldDef("calc.deadlineLeftMin", "Осталось до срока, мин", "number",
             "Срок минус «сейчас». Пусто, если срока нет", "calc", c_deadline_left_min),
    FieldDef("calc.deadlineLeftPct", "Осталось до срока, %", "number",
             "Какая доля отведённого времени ещё осталась. Пусто, если срока нет", "calc", c_deadline_left_pct),
    FieldDef("calc.overdue", "Просрочен", "flag", "Срок прошёл, а инцидент ещё активен", "calc", c_overdue),
    FieldDef("calc.noDeadline", "Нет срока", "flag", "Срок решения не задан", "calc",
             lambda inc, ctx, a: parse_dt(inc.data.get("deadline")) is None),
    FieldDef("calc.idleMin", "Не взят в работу, мин", "number",
             "Сколько минут инцидент в статусе «Назначен» с последнего назначения на команду", "calc", c_idle_min),
    FieldDef("calc.idlePct", "Не взят в работу, % от срока", "number",
             "Простой в статусе «Назначен» как доля отведённого на решение времени", "calc", c_idle_pct),
    FieldDef("calc.isNew", "Новый", "flag", "Впервые появился в последней загрузке", "calc",
             lambda inc, ctx, a: inc.first_snapshot_id == ctx.latest_snapshot_id),
    FieldDef("calc.disappeared", "Пропал из выгрузки", "flag", "Был раньше, но в последней загрузке его нет", "calc",
             lambda inc, ctx, a: bool(inc.disappeared)),
    FieldDef("calc.resolvedInMin", "Решён за, мин", "number",
             "От последнего назначения на команду до перевода в «Завершен»", "calc", c_resolved_in),
    FieldDef("calc.ageMin", "Возраст, мин", "number", "Сколько минут прошло с момента возникновения", "calc", c_age),
    FieldDef("hist.changes", "Число изменений поля", "number", "Сколько раз поле меняло значение между загрузками",
             "hist", h_changes, [HIST_FIELD_PARAM]),
    FieldDef("hist.increases", "Число увеличений поля", "number", "Сколько раз числовое поле росло", "hist",
             h_increases, [{**HIST_FIELD_PARAM, "fieldType": "number"}]),
    FieldDef("hist.transitions", "Число переходов", "number",
             "Сколько раз значение поля менялось с одного из «из» на одно из «в»", "hist", h_transitions,
             [HIST_FIELD_PARAM, {"name": "from", "label": "Из", "kind": "values"},
              {"name": "to", "label": "В", "kind": "values"}]),
    FieldDef("hist.delta", "Изменение с прошлой загрузки", "number",
             "Текущее значение числового поля минус значение в прошлой загрузке", "hist", h_delta,
             [{**HIST_FIELD_PARAM, "fieldType": "number"}]),
]


def ai_available() -> tuple[bool, str]:
    from .llm.adapter import is_configured
    ok = is_configured()
    return ok, "" if ok else "Языковая модель не подключена: задайте LLM_API_KEY"


def registry(ctx_or_session) -> dict[str, FieldDef]:
    """Полный реестр: поля из загруженных данных + статические."""
    if isinstance(ctx_or_session, EvalContext):
        catalog = ctx_or_session.catalog
    else:
        catalog = {r.name: r for r in ctx_or_session.execute(select(FieldCatalog)).scalars()}
    reg: dict[str, FieldDef] = {}
    for name, row in sorted(catalog.items()):
        label = JSON_LABELS.get(name, name)
        reg[name] = FieldDef(name, label, row.type, f"Поле «{name}» из загруженного файла", "json", _f(name),
                             labels=row.labels or None, values=row.values or None)
    if "id" not in reg:
        reg["id"] = FieldDef("id", "Номер", "text", "Номер инцидента", "json", _f("id"))
    for fd in STATIC_FIELDS:
        reg[fd.key] = fd
    ok, why = ai_available()
    for key, label, typ, desc in AI_FIELDS:
        reg[key] = FieldDef(key, label, typ, desc, "ai", _ai(key.split(".", 1)[1]), available=ok,
                            unavailable_reason=why)
    reg["ai.suggestedPriority"].labels = PRIORITY_LABELS
    for key, label, typ, desc, fn in AI_LIVE_FIELDS:
        reg[key] = FieldDef(key, label, typ, desc, "ai", fn, available=ok, unavailable_reason=why)
    return reg


def field_value(reg: dict[str, FieldDef], key: str, inc: Incident, ctx: EvalContext, args: dict | None = None):
    fd = reg.get(key)
    if fd is None or fd.compute is None or not fd.available:
        return None
    ck = (inc.id, key, repr(sorted(args.items())) if args else None)
    if ck in ctx.cache:
        return ctx.cache[ck]
    v = fd.compute(inc, ctx, args)
    ctx.cache[ck] = v
    return v
