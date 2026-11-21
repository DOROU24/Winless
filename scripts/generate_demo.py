"""Генератор демо-данных для живого показа.

Запуск:  python scripts/generate_demo.py
Создаёт в demo_data/ файлы 00_history.json … 06_*.json. Каждый файл — полный снимок состояния.
Все даты считаются от «демо-сейчас» = 2026-11-21T09:00:00Z: в настройках приложения включите
виртуальное «сейчас» на это время (или загрузите файлы 21 ноября 2026 около 12:00 по Москве).
"""
from __future__ import annotations

import copy
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc)
OUT = Path(__file__).resolve().parent.parent / "demo_data"
rnd = random.Random(42)

COORD = "КЦ ИТ Координаторы Массовых Инцидентов"
STATES = {0: ("Назначен", "Assigned"), 2: ("Выполняется", "InProgress"), 7: ("Завершен", "Resolved"),
          10: ("Закрыт", "Closed"), 20: ("Исследование ситуации", "Investigation")}
PRIOR = {1: "Низкий", 2: "Средний", 3: "Высокий", 4: "Наивысший"}
URG = {1: "Низкая", 2: "Средняя", 3: "Высокая", 4: "Наивысшая"}
IMP = {1: "Низкое", 2: "Среднее", 3: "Высокое", 4: "Наивысшее"}
WINDOW_H = {4: 6, 3: 24, 2: 72, 1: 336}  # отведённое на решение время по приоритету

PEOPLE = ["Дядя Фёдор (unclefedor)", "Кот Матроскин (matroskin)", "Пёс Шарик (sharik)", "Почтальон Печкин (pechkin)",
          "Винни-Пух (winnie)", "Кролик (rabbit)", "Сова (owl)", "Крокодил Гена (gena)", "Чебурашка (cheburashka)",
          "Карлсон (karlsson)", "Малыш (malysh)", "Фрекен Бок (bock)", "Ёжик (hedgehog)", "Медвежонок (bear)",
          "Кот Леопольд (leopold)", "Старуха Шапокляк (shapoklyak)", "Пятачок (piglet)", "Иа (eeyore)"]

TEAMS = ["ИТ Эксплуатация серверов", "Сеть и каналы связи", "Платёжные сервисы", "Мобильное приложение",
         "Базы данных", "Видеоаналитика"]

# продукт, сервис, id сервиса, сценарии, профильная команда
PRODUCTS = [
    ("Биллинг Абонентов", "[Биллинг Абонентов] Сервер расчётов", "174195085103290001",
     ["Диск", "Процессор", "Память"], "ИТ Эксплуатация серверов", "BI_1001"),
    ("Мой Оператор", "[Мой Оператор] Авторизация", "174195085103290002",
     ["Вход по SMS", "Вход по паролю", "Токены"], "Мобильное приложение", "BI_1002"),
    ("Платёжный шлюз", "[Платёжный шлюз] Эквайринг", "174195085103290003",
     ["Оплата картой", "Возвраты", "Чеки"], "Платёжные сервисы", "BI_1003"),
    ("Автопросматриватель TicTok", "[Автопросматриватель TicTok] Камера-сканер", "174195085103282310",
     ["Объектив", "Фокус", "Подсветка"], "Видеоаналитика", "BI_3244"),
    ("Домашний интернет", "[Домашний интернет] Магистраль Север", "174195085103290005",
     ["Потеря пакетов", "Недоступность", "Скорость"], "Сеть и каналы связи", "BI_1005"),
    ("Хранилище договоров", "[Хранилище договоров] Кластер СУБД", "174195085103290006",
     ["Репликация", "Блокировки", "Медленные запросы"], "Базы данных", "BI_1006"),
    ("Личный кабинет", "[Личный кабинет] Веб-портал", "174195085103290007",
     ["Главная страница", "Детализация", "Тарифы"], "Мобильное приложение", "BI_1007"),
]
PROBLEMS = {
    "Диск": ("Диск заполнен на сервере расчётов", "Свободное место на разделе /data меньше 3%."),
    "Процессор": ("Высокая загрузка процессора", "Загрузка процессора держится выше 95% больше 15 минут."),
    "Память": ("Утечка памяти в сервисе расчётов", "Потребление памяти растёт без нагрузки."),
    "Вход по SMS": ("Не приходят SMS-коды для входа", "Абоненты не могут войти: код подтверждения не приходит."),
    "Вход по паролю": ("Ошибка входа по паролю", "При входе по паролю приложение показывает ошибку 500."),
    "Токены": ("Сессии сбрасываются каждые 5 минут", "Токен доступа истекает раньше срока."),
    "Оплата картой": ("Отказы при оплате картой", "Доля отказов при оплате выросла до 12%."),
    "Возвраты": ("Не проходят возвраты", "Возвраты зависают в статусе «в обработке»."),
    "Чеки": ("Не формируются электронные чеки", "Чеки не уходят в ОФД."),
    "Объектив": ("Отказ Объектив (Камера-сканер)", "Объектив камеры-сканера показывает отклонения от нормы."),
    "Фокус": ("Сбой автофокуса камеры-сканера", "Камера не фокусируется, распознавание падает."),
    "Подсветка": ("Не работает подсветка сканера", "Подсветка не включается в тёмное время."),
    "Потеря пакетов": ("Потеря пакетов на магистрали", "Потери до 8% на участке Север-2."),
    "Недоступность": ("Недоступен сегмент магистрали", "Нет связи с узлом агрегации."),
    "Скорость": ("Снижение скорости у абонентов", "Жалобы на скорость ниже тарифной."),
    "Репликация": ("Отстаёт реплика СУБД", "Задержка репликации больше 20 минут."),
    "Блокировки": ("Блокировки в базе договоров", "Долгие блокировки таблиц договоров."),
    "Медленные запросы": ("Медленные запросы к базе договоров", "Время ответа выросло в 5 раз."),
    "Главная страница": ("Главная страница кабинета не открывается", "Ошибка 502 при открытии кабинета."),
    "Детализация": ("Не загружается детализация расходов", "Детализация отдаёт пустой список."),
    "Тарифы": ("Неверно отображаются тарифы", "В кабинете показаны устаревшие цены."),
}
CHANNELS = ["Мониторинг", "Звонок в КЦ", "Портал самообслуживания", "Электронная почта", "Анонимное письмо"]
SOLUTIONS_GOOD = ["Очищены устаревшие логи, настроена ротация, добавлен алерт на 80% заполнения.",
                  "Найден и откатан релиз с ошибкой в конфигурации, проверено на тестовом стенде.",
                  "Заменён неисправный модуль, проведена проверка после замены.",
                  "Перенастроен пул соединений: лимит был ниже нагрузки в часы пик."]

_seq = [21931100]


def next_id() -> str:
    _seq[0] += rnd.randint(3, 40)
    return f"INC0000{_seq[0]}"


def iso(d: datetime | None):
    return None if d is None else d.strftime("%Y-%m-%dT%H:%M:%SZ")


def make(product_idx: int, scenario: str | None = None, *, priority: int, state: int, submit: datetime,
         team: str | None = None, assigned_ago: timedelta | None = None, deadline: datetime | None | str = "auto",
         completion: datetime | None = None, solution: str | None = None, complaints: int = 0,
         header: str | None = None, reason: str = "Нет", **extra) -> dict:
    p_name, service, service_id, scenarios, p_team, master = PRODUCTS[product_idx]
    # «Диск» берётся только явно: иначе фоновые инциденты добавляют лишние повторы к цепочке «диск заполнен»
    scenario = scenario or rnd.choice([x for x in scenarios if x != "Диск"])
    h, desc = PROBLEMS[scenario]
    if deadline == "auto":
        deadline = submit + timedelta(hours=WINDOW_H[priority])
    team = team or p_team
    assigned = submit + timedelta(minutes=rnd.randint(1, 10)) if assigned_ago is None else NOW - assigned_ago
    if completion and assigned > completion:
        assigned = completion - timedelta(minutes=5)
    person = rnd.choice(PEOPLE)
    init = rnd.choice(PEOPLE)
    st, st_en = STATES[state]
    urg = min(4, max(1, priority + rnd.choice([-1, 0, 0])))
    imp = min(4, max(1, priority + rnd.choice([-1, 0, 0, 1])))
    rec = {
        "sysId": str(179413934903700000 + rnd.randint(1, 99999)), "id": next_id(), "state": st, "stateEnum": st_en,
        "stateId": state, "stateReason": reason, "stateReasonId": {"Нет": "1", "Инцидент самоустранился": "4",
                                                                   "Постоянное решение": "2", "Дубль": "3"}.get(reason, "1"),
        "submitDate": iso(submit), "deadline": iso(deadline), "completionDate": iso(completion),
        "lastAssignmentToTeamDateTime": iso(assigned), "header": header or h,
        "description": f"{desc} Продукт: {p_name}. Требуется реакция.", "shortDescription": desc,
        "assignedGroupName": team, "assignedEmployee": person,
        "assignedEmployeeLogin": person.split("(")[1].rstrip(")"),
        "initiatorName": init, "initiatorLogin": init.split("(")[1].rstrip(")"),
        "isEffect": False, "isReason": False, "isDouble": False, "isMaster": False, "incType": "МИ",
        "serviceTypeId": 30, "serviceType": "Технологический", "priorityId": priority, "priority": PRIOR[priority],
        "urgencyId": urg, "urgency": URG[urg], "impactId": imp, "impact": IMP[imp],
        "waitingExitDate": "0001-01-01T00:00:00", "isRootIncident": True, "productName": p_name,
        "productId": str(174008642706700000 + product_idx), "productMasterId": master, "productUrl": None,
        "service": service, "serviceId": service_id, "scenarioName": scenario, "isNeedUp": None,
        "eliminationType": None, "eliminationTypeId": None, "receivingChannel": rnd.choice(CHANNELS),
        "solution": solution, "complaintsCount": complaints,
    }
    rec.update(extra)
    return rec


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def set_state(rec: dict, state: int):
    rec["stateId"] = state
    rec["state"], rec["stateEnum"] = STATES[state]


def build():
    # ---------------- 00_history: прошлый месяц, в основном решённые ----------------
    history = []
    disk_chain = []
    for i, days in enumerate([30, 24, 17, 10, 4]):
        sub = ago(days=days, hours=rnd.randint(0, 5))
        done = sub + timedelta(minutes=rnd.randint(25, 70))
        r = make(0, "Диск", priority=3, state=10, submit=sub, completion=done,
                 solution="Перезагрузил сервер" if i % 2 == 0 else "Само устранилось",
                 reason="Инцидент самоустранился" if i % 2 else "Нет", complaints=rnd.randint(0, 3))
        disk_chain.append(r)
    history += disk_chain
    # одна команда решает инциденты одного сервиса — для будущей подсказки команды
    for days in (27, 21, 15, 9, 6):
        sub = ago(days=days, hours=rnd.randint(1, 8))
        history.append(make(2, "Оплата картой", priority=3, state=10, submit=sub,
                            completion=sub + timedelta(hours=rnd.randint(1, 4)), solution=rnd.choice(SOLUTIONS_GOOD)))
    for _ in range(25):
        pi = rnd.randrange(len(PRODUCTS))
        sub = ago(days=rnd.randint(3, 30), hours=rnd.randint(0, 23))
        pr = rnd.choice([1, 2, 2, 3])
        st = rnd.choice([10, 10, 10, 7])
        history.append(make(pi, priority=pr, state=st, submit=sub,
                            completion=sub + timedelta(hours=rnd.randint(1, 30)), solution=rnd.choice(SOLUTIONS_GOOD)))
    # Y — будет переоткрыт: в истории «Завершен»
    y = make(5, "Репликация", priority=2, state=7, submit=ago(days=2), completion=ago(days=1, hours=2),
             solution="Реплика догнала мастер.", complaints=1)
    # T — будут менять команду; P — будут повышать приоритет
    t = make(6, "Детализация", priority=2, state=2, submit=ago(hours=30), team="Базы данных", complaints=4)
    p = make(4, "Скорость", priority=1, state=2, submit=ago(hours=20), complaints=3)
    history += [y, t, p]

    # ---------------- 01_now: текущее состояние ----------------
    now = []
    y1 = copy.deepcopy(y)
    set_state(y1, 0)  # переоткрытие №1 (7 → 0)
    y1["completionDate"] = None
    y1["lastAssignmentToTeamDateTime"] = iso(ago(hours=3))
    t1 = copy.deepcopy(t)
    t1["assignedGroupName"] = "Мобильное приложение"
    t1["lastAssignmentToTeamDateTime"] = iso(ago(hours=6))
    p1 = copy.deepcopy(p)
    p1["priorityId"], p1["priority"] = 2, PRIOR[2]
    now += [y1, t1, p1]

    # три критических
    c1 = make(2, "Оплата картой", priority=4, state=2, submit=ago(hours=5, minutes=50), deadline=NOW + timedelta(minutes=10),
              complaints=48, header="Массовые отказы при оплате картой")  # осталось ~2,8% срока
    c2 = make(1, "Вход по SMS", priority=4, state=0, submit=ago(minutes=50), assigned_ago=timedelta(minutes=45),
              complaints=31)  # не взят 45 минут
    c3 = make(4, "Недоступность", priority=4, state=2, submit=ago(hours=1), complaints=22)
    now += [c1, c2, c3]

    # кластер по одному сервису за несколько часов — для графа автосвязей
    for h in (7, 5, 3, 2):
        now.append(make(1, rnd.choice(["Вход по паролю", "Токены"]), priority=rnd.choice([2, 3]),
                        state=rnd.choice([0, 2, 2]), submit=ago(hours=h, minutes=rnd.randint(0, 50)),
                        complaints=rnd.randint(2, 15)))

    # высокий приоритет: один давно не взят (>10% срока), остальные в работе
    now.append(make(3, "Фокус", priority=3, state=0, submit=ago(hours=6), assigned_ago=timedelta(hours=5),
                    complaints=6))
    for _ in range(4):
        now.append(make(rnd.randrange(len(PRODUCTS)), priority=3, state=2, submit=ago(hours=rnd.randint(2, 15)),
                        complaints=rnd.randint(1, 10)))
    # на координаторах
    for _ in range(2):
        now.append(make(rnd.randrange(len(PRODUCTS)), priority=rnd.choice([2, 3]), state=0, team=COORD,
                        submit=ago(minutes=rnd.randint(20, 160)), complaints=rnd.randint(0, 8)))
    # просроченные
    now.append(make(5, "Медленные запросы", priority=3, state=2, submit=ago(hours=27), complaints=5))
    now.append(make(6, "Тарифы", priority=2, state=0, submit=ago(hours=80), assigned_ago=timedelta(hours=79),
                    complaints=3))
    # скоро срок (≤ 2 часа)
    now.append(make(4, "Потеря пакетов", priority=3, state=2, submit=ago(hours=22, minutes=40), complaints=9))
    now.append(make(0, "Процессор", priority=2, state=2, submit=ago(hours=71), complaints=2))
    # исследование ситуации, в том числе без срока
    for dl in ("auto", None, None, "auto"):
        sub = ago(days=rnd.randint(1, 6))
        now.append(make(rnd.randrange(len(PRODUCTS)), priority=rnd.choice([2, 3]), state=20, submit=sub,
                        deadline=dl if dl is None else "auto", completion=sub + timedelta(hours=3),
                        solution=rnd.choice(SOLUTIONS_GOOD), complaints=rnd.randint(0, 4)))
    # решены подозрительно быстро
    for pi in (3, 6):
        sub = ago(hours=rnd.randint(2, 9))
        r = make(pi, priority=2, state=7, submit=sub, solution="Само устранилось", reason="Инцидент самоустранился",
                 complaints=0)
        a = sub + timedelta(minutes=2)
        r["lastAssignmentToTeamDateTime"], r["completionDate"] = iso(a), iso(a + timedelta(minutes=1, seconds=40))
        now.append(r)
    # низкий приоритет, у которого потом вырастут жалобы
    low = make(3, "Подсветка", priority=1, state=2, submit=ago(hours=9), complaints=2)
    now.append(low)
    # заполнение до ~60
    while len(now) < 60:
        pr = rnd.choice([1, 1, 2, 2, 2, 3])
        st = rnd.choice([0, 2, 2, 2, 7, 7, 10])
        sub = ago(hours=rnd.randint(1, int(WINDOW_H[pr] * 0.6)))
        comp = sub + timedelta(hours=rnd.randint(1, 5)) if st in (7, 10) else None
        if comp and comp > NOW:
            comp = NOW - timedelta(minutes=10)
        now.append(make(rnd.randrange(len(PRODUCTS)), priority=pr, state=st, submit=sub, completion=comp,
                        solution=rnd.choice(SOLUTIONS_GOOD) if comp else None, complaints=rnd.randint(0, 6),
                        assigned_ago=timedelta(minutes=rnd.randint(5, 50)) if st == 0 else None))

    def idx(snapshot, rec):
        return next(i for i, r in enumerate(snapshot) if r["id"] == rec["id"])

    # ---------------- 02: новый критический ----------------
    s2 = copy.deepcopy(now)
    new_crit = make(5, "Блокировки", priority=4, state=0, submit=ago(minutes=4), assigned_ago=timedelta(minutes=3),
                    complaints=12, header="Не открываются договоры: блокировки в базе")
    s2.append(new_crit)

    # ---------------- 03: переназначение на координаторов, Y → «Завершен», T меняет команду ещё раз ----------------
    s3 = copy.deepcopy(s2)
    reassigned = next(r for r in s3 if r["priorityId"] == 3 and r["stateId"] == 2 and r["assignedGroupName"] != COORD
                      and r["id"] not in (c1["id"], c3["id"]))
    reassigned["assignedGroupName"] = COORD
    reassigned["assignedEmployee"], reassigned["assignedEmployeeLogin"] = "Дядя Фёдор (unclefedor)", "unclefedor"
    reassigned["lastAssignmentToTeamDateTime"] = iso(ago(minutes=2))
    set_state(reassigned, 0)
    y3 = s3[idx(s3, y1)]
    set_state(y3, 7)
    y3["completionDate"] = iso(ago(minutes=20))
    y3["solution"] = "Реплика снова догнала мастер."
    t3 = s3[idx(s3, t1)]
    t3["assignedGroupName"] = "Платёжные сервисы"
    t3["lastAssignmentToTeamDateTime"] = iso(ago(minutes=30))

    # ---------------- 04: Y снова «Назначен» (переоткрытие №2), P повышают до высокого ----------------
    s4 = copy.deepcopy(s3)
    y4 = s4[idx(s4, y1)]
    set_state(y4, 0)
    y4["completionDate"] = None
    y4["lastAssignmentToTeamDateTime"] = iso(ago(minutes=5))
    p4 = s4[idx(s4, p1)]
    p4["priorityId"], p4["priority"] = 3, PRIOR[3]

    # ---------------- 05: у низкого приоритета резко выросли жалобы ----------------
    s5 = copy.deepcopy(s4)
    s5[idx(s5, low)]["complaintsCount"] = 19

    # ---------------- 06: новый «диск заполнен» по сервису из цепочки ----------------
    s6 = copy.deepcopy(s5)
    disk_new = make(0, "Диск", priority=3, state=0, submit=ago(minutes=6), assigned_ago=timedelta(minutes=5),
                    complaints=3)
    s6.append(disk_new)

    files = {
        "00_history.json": history, "01_now.json": now, "02_new_critical.json": s2,
        "03_reassign_and_resolve.json": s3, "04_reopen_again.json": s4, "05_complaints_growth.json": s5,
        "06_disk_full_again.json": s6,
    }
    OUT.mkdir(exist_ok=True)
    for name, data in files.items():
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = {"c1": c1, "c2": c2, "c3": c3, "new_crit": new_crit, "reassigned": reassigned, "reopened": y,
            "team_changes": t, "priority_ups": p, "complaints": low, "disk_new": disk_new}
    (OUT / "_ids.json").write_text(json.dumps({k: v["id"] for k, v in keys.items()} |
                                              {"disk_chain": [r["id"] for r in disk_chain]},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    for name, data in files.items():
        print(f"{name}: {len(data)} инцидентов")
    print("Ключевые инциденты:", json.dumps({k: v["id"] for k, v in keys.items()}, ensure_ascii=False))


if __name__ == "__main__":
    build()
