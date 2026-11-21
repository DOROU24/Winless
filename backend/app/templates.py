"""Шаблоны досок, каталог готовых правил и значения по умолчанию.

Здесь лежат только значения по умолчанию: при первом запуске они попадают в базу, дальше всё
редактируется из интерфейса.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .db import Board, Column, LinkRule, RulePreset, get_setting, set_setting
from .rules import or_of

COORD = "КЦ ИТ Координаторы Массовых Инцидентов"
ACTIVE = {"field": "stateId", "cmp": "in", "value": [0, 2]}
DEFAULT_CARD_FIELDS = ["id", "productName", "submitDate", "assignedGroupName", "assignedEmployee", "state", "deadline"]
LEVEL_RANK = {"info": 0, "elevated": 1, "critical": 2}
ALERT_RANK = {"off": 0, "popup": 1, "sound": 2}


def AND(*c):
    return {"op": "and", "children": list(c)}


def OR(*c):
    return {"op": "or", "children": list(c)}


def C(field, cmp, value=None, args=None):
    d = {"field": field, "cmp": cmp}
    if value is not None:
        d["value"] = value
    if args is not None:
        d["args"] = args
    return d


PRESETS = [
    dict(id=1, name="Новый критический", origin="кейс",
         description="Активный критический инцидент впервые появился в последней загрузке.",
         rule=AND(C("priorityId", "eq", 4), C("calc.isNew", "is_true"), C("calc.isActive", "is_true")),
         level="critical", alert="sound"),
    dict(id=2, name="Назначен на координаторов", origin="кейс",
         description="Инцидент висит на группе координаторов: его нужно передать профильной команде.",
         rule=AND(C("assignedGroupName", "eq", COORD), C("calc.isActive", "is_true")),
         level="elevated", alert="popup"),
    dict(id=3, name="Не взяли в работу дольше порога", origin="МТС + наш",
         description="Критический не взят за 30 минут, высокий — за 10% срока, низкий и средний — за 20% срока.",
         rule=OR(AND(C("priorityId", "eq", 4), C("calc.idleMin", "gt", 30)),
                 AND(C("priorityId", "eq", 3), C("calc.idlePct", "gt", 10)),
                 AND(C("priorityId", "in", [1, 2]), C("calc.idlePct", "gt", 20))),
         level="critical", alert="sound"),
    dict(id=4, name="Скоро срок", origin="наш",
         description="Активный инцидент, до срока решения осталось не больше двух часов.",
         rule=AND(C("calc.isActive", "is_true"), C("calc.deadlineLeftMin", "between", [0, 120])),
         level="elevated", alert="popup"),
    dict(id=5, name="Срок просрочен", origin="наш",
         description="Срок решения прошёл, а инцидент всё ещё активен.",
         rule=C("calc.overdue", "is_true"), level="critical", alert="popup"),
    dict(id=6, name="Переоткрывали", origin="МТС",
         description="Инцидент дважды или чаще возвращали из «Завершен» в работу.",
         rule=C("hist.transitions", "gte", 2, {"field": "stateId", "from": [7], "to": [0, 2]}),
         level="elevated", alert="popup"),
    dict(id=7, name="Команду меняли 2 и больше раз", origin="наш",
         description="Инцидент передают из команды в команду, и никто его не решает.",
         rule=AND(C("hist.changes", "gte", 2, {"field": "assignedGroupName"}), C("calc.isActive", "is_true")),
         level="elevated", alert="off"),
    dict(id=8, name="Приоритет повышали 2 и больше раз", origin="МТС",
         description="Ситуация ухудшается: приоритет поднимали минимум дважды.",
         rule=C("hist.increases", "gte", 2, {"field": "priorityId"}), level="elevated", alert="off"),
    dict(id=9, name="Решён подозрительно быстро", origin="наш",
         description="От назначения до решения прошло меньше 3 минут — стоит проверить, что проблему правда устранили.",
         rule=AND(C("stateId", "in", [7, 10]), C("calc.resolvedInMin", "lt", 3)), level="info", alert="off"),
    dict(id=10, name="Исследование без срока", origin="наш",
         description="Разбор причин идёт без срока и может длиться бесконечно.",
         rule=AND(C("stateId", "eq", 20), C("calc.noDeadline", "is_true")), level="info", alert="off"),
    # Правило 11 («Низкий приоритет, а жалобы растут») убрано из каталога по решению команды.
    # ---- этап 2 ----
    dict(id=12, name="Повтор: такая проблема уже была", origin="МТС + наш", stage=2,
         description="ИИ нашёл решённые инциденты с той же проблемой.",
         rule=AND(C("ai.similarPastCount", "gte", 1), C("calc.isActive", "is_true")), level="elevated", alert="popup"),
    dict(id=13, name="Несколько одинаковых инцидентов сразу", origin="МТС + наш", stage=2,
         description="Сейчас открыто несколько инцидентов про одну и ту же проблему.",
         rule=C("ai.similarOpenCount", "gte", 2), level="elevated", alert="popup"),
    dict(id=14, name="Отписка в решении", origin="МТС", stage=2,
         description="Текст решения не объясняет, что сделали и почему.",
         rule=C("ai.weakSolution", "is_true"), level="info", alert="off"),
    dict(id=15, name="Отписка в решении и повтор", origin="МТС", stage=2,
         description="Решение — отписка, и проблема уже повторялась: причина, скорее всего, не устранена.",
         rule=AND(C("ai.weakSolution", "is_true"), C("ai.similarPastCount", "gte", 1)), level="elevated",
         alert="popup"),
    dict(id=16, name="Непонятно, какой команде отдать", origin="МТС", stage=2,
         description="Инцидент на координаторах, и ИИ предлагает команду по похожим случаям.",
         rule=AND(C("assignedGroupName", "eq", COORD), C("ai.suggestedTeam", "not_empty")), level="elevated",
         alert="off"),
]

PALETTE = {"critical": "#ff0032", "elevated": "#f57c00", "info": "#0070e5"}

TEMPLATES = {
    "focus": {
        "name": "Фокус внимания", "mode": "first",
        "description": "Пять колонок из каталога готовых правил: что срочно, что передать, что буксует.",
        "columns": [
            {"name": "Срочно", "presets": [1, 3, 5], "sort": ("calc.deadlineLeftPct", "asc")},
            {"name": "Нужно передать", "presets": [2], "ai_presets": [16], "sort": ("calc.ageMin", "desc")},
            {"name": "Срок", "presets": [4], "sort": ("calc.deadlineLeftMin", "asc")},
            {"name": "Повторы и дубли", "presets": [], "ai_presets": [12, 13, 15], "sort": ("calc.ageMin", "desc")},
            {"name": "Решение буксует", "presets": [6, 7, 8], "sort": ("calc.ageMin", "desc")},
            {"name": "Проверить", "presets": [9, 10], "ai_presets": [14], "sort": ("submitDate", "desc")},
        ],
    },
    "mts": {
        "name": "Задание МТС", "mode": "all",
        "description": "Четыре группы из кейса: критические, на координаторах, высокий приоритет, исследование.",
        "columns": [
            {"name": "Критические", "rule": AND(C("priorityId", "eq", 4), ACTIVE), "level": "critical",
             "alert": "sound", "sort": ("calc.deadlineLeftPct", "asc")},
            {"name": "Назначены на координаторов",
             "rule": AND(C("assignedGroupName", "eq", COORD), C("stateId", "not_in", [10])), "level": "elevated",
             "alert": "popup", "sort": ("calc.ageMin", "desc")},
            {"name": "Высокий приоритет", "rule": AND(C("priorityId", "eq", 3), ACTIVE), "level": "elevated",
             "alert": "off", "sort": ("calc.deadlineLeftPct", "asc")},
            {"name": "Исследование ситуации", "rule": C("stateId", "eq", 20), "level": "info", "alert": "off",
             "sort": ("submitDate", "desc")},
        ],
    },
    "empty": {"name": "Пустая доска", "mode": "all", "description": "Без колонок — соберите свою.", "columns": []},
}


def preset_dicts(s: Session) -> dict[int, RulePreset]:
    return {p.id: p for p in s.execute(select(RulePreset)).scalars()}


def column_from_presets(presets: list[RulePreset]) -> dict:
    """Колонка из нескольких правил: группа or, самый высокий уровень, самое сильное оповещение."""
    level = max((p.level for p in presets), key=lambda l: LEVEL_RANK[l], default="info")
    alert = max((p.alert for p in presets), key=lambda a: ALERT_RANK[a], default="off")
    repeats = [p.repeat_min for p in presets if p.repeat_min]
    return {"rule": or_of([p.rule for p in presets]), "level": level, "alert": alert,
            "repeat_min": min(repeats) if repeats else None, "preset_ids": [p.id for p in presets]}


def create_board_from_template(s: Session, key: str, name: str | None = None, ai_enabled: bool | None = None) -> Board:
    from .llm.adapter import is_configured
    if ai_enabled is None:
        ai_enabled = is_configured()
    t = TEMPLATES[key]
    pos = (s.execute(select(func.max(Board.position))).scalar() or 0) + 1
    b = Board(name=name or t["name"], mode=t["mode"], template=key, position=pos)
    s.add(b)
    s.flush()
    fill_board(s, b, key, ai_enabled)
    return b


def fill_board(s: Session, b: Board, key: str, ai_enabled: bool) -> None:
    presets = preset_dicts(s)
    i = 0
    for col in TEMPLATES[key]["columns"]:
        ids = list(col.get("presets", [])) + (list(col.get("ai_presets", [])) if ai_enabled else [])
        if "rule" in col:
            cfg = {"rule": col["rule"], "level": col["level"], "alert": col["alert"], "repeat_min": None,
                   "preset_ids": []}
        else:
            if not ids:
                continue
            cfg = column_from_presets([presets[x] for x in ids if x in presets])
        sf, sd = col.get("sort", (None, "asc"))
        s.add(Column(board_id=b.id, position=i, name=col["name"], color=PALETTE[cfg["level"]], rule=cfg["rule"],
                     sort_field=sf, sort_dir=sd, limit=None, card_fields=list(DEFAULT_CARD_FIELDS),
                     level=cfg["level"], alert=cfg["alert"], repeat_min=cfg["repeat_min"],
                     preset_ids=cfg["preset_ids"]))
        i += 1


DEFAULT_LINK_RULES = [
    dict(name="Один сервис за 24 часа", fields=["serviceId"], window_min=24 * 60, filter=None, link_type="related",
         enabled=True),
    dict(name="Один продукт и сценарий за 6 часов", fields=["productId", "scenarioName"], window_min=6 * 60,
         filter=None, link_type="related", enabled=False),
]

DEFAULT_TEAMS = [
    {"team": "ИТ Эксплуатация серверов", "scope": "Серверы, диски, виртуализация, перезагрузки, место на дисках"},
    {"team": "Сеть и каналы связи", "scope": "Маршрутизаторы, коммутаторы, VPN, потеря пакетов, недоступность сети"},
    {"team": "Платёжные сервисы", "scope": "Оплата, эквайринг, списания, чеки, биллинг"},
    {"team": "Мобильное приложение", "scope": "Сбои приложения, авторизация в приложении, push-уведомления"},
    {"team": "Базы данных", "scope": "СУБД, репликация, блокировки, медленные запросы"},
    {"team": "Видеоаналитика", "scope": "Камеры, сканеры, распознавание, объективы"},
]


# Стандартные поля выгрузки ITSM (раздел 4.1 ТЗ). Попадают в реестр сразу, чтобы конструктор и шаблоны
# работали до первой загрузки. Загруженные данные потом дополняют и уточняют этот список.
BASE_FIELDS: dict[str, str] = {
    "sysId": "text", "id": "text", "state": "text", "stateEnum": "text", "stateId": "number",
    "stateReason": "text", "stateReasonId": "text", "submitDate": "date", "deadline": "date",
    "completionDate": "date", "lastAssignmentToTeamDateTime": "date", "header": "text", "description": "text",
    "shortDescription": "text", "assignedGroupName": "text", "assignedEmployee": "text",
    "assignedEmployeeLogin": "text", "initiatorName": "text", "initiatorLogin": "text", "isEffect": "flag",
    "isReason": "flag", "isDouble": "flag", "isMaster": "flag", "incType": "text", "serviceTypeId": "number",
    "serviceType": "text", "priorityId": "number", "priority": "text", "urgencyId": "number", "urgency": "text",
    "impactId": "number", "impact": "text", "waitingExitDate": "date", "isRootIncident": "flag",
    "productName": "text", "productId": "text", "productMasterId": "text", "productUrl": "text", "service": "text",
    "serviceId": "text", "scenarioName": "text", "isNeedUp": "flag", "eliminationType": "text",
    "eliminationTypeId": "text", "receivingChannel": "text", "solution": "text",
}
BASE_LABELS: dict[str, dict[str, str]] = {
    "stateId": {"0": "Назначен", "2": "Выполняется", "7": "Завершен", "10": "Закрыт", "20": "Исследование ситуации"},
    "priorityId": {"1": "Низкий", "2": "Средний", "3": "Высокий", "4": "Наивысший"},
    "urgencyId": {"1": "Низкая", "2": "Средняя", "3": "Высокая", "4": "Наивысшая"},
    "impactId": {"1": "Низкое", "2": "Среднее", "3": "Высокое", "4": "Наивысшее"},
}
BASE_VALUES: dict[str, list] = {"assignedGroupName": [COORD]}


def seed_fields(s: Session) -> None:
    from .db import FieldCatalog
    existing = {r.name for r in s.execute(select(FieldCatalog)).scalars()}
    for name, typ in BASE_FIELDS.items():
        if name not in existing:
            s.add(FieldCatalog(name=name, type=typ, labels=BASE_LABELS.get(name, {}), values=BASE_VALUES.get(name, [])))


def seed(s: Session) -> None:
    """Значения по умолчанию при первом запуске. Повторно ничего не перезаписывает."""
    seed_fields(s)
    # Каталог готовых правил всегда пересоздаётся из кода: в интерфейсе он не редактируется, а правило,
    # добавленное в колонку, копируется в неё и дальше живёт там.
    s.execute(delete(RulePreset).where(RulePreset.custom.is_not(True)))  # свои правила пользователя не трогаем
    s.flush()
    for p in PRESETS:
        s.add(RulePreset(stage=p.get("stage", 1), repeat_min=p.get("repeat_min"),
                         **{k: v for k, v in p.items() if k not in ("stage", "repeat_min")}))
    s.flush()
    if not s.execute(select(LinkRule).limit(1)).first():
        for r in DEFAULT_LINK_RULES:
            s.add(LinkRule(**r))
    if get_setting(s, "itsm_url_template") is None:
        set_setting(s, "itsm_url_template", "/itsm/{id}")
    if get_setting(s, "teams") is None:
        set_setting(s, "teams", DEFAULT_TEAMS)
    if get_setting(s, "initialized") is None:
        b = create_board_from_template(s, "focus")
        set_setting(s, "active_board_id", b.id)
        set_setting(s, "initialized", True)
    # Настройку виртуального «сейчас» убрали: время всегда реальное. Старое значение из базы стираем.
    if get_setting(s, "virtual_now") is not None:
        set_setting(s, "virtual_now", None)
    s.commit()
