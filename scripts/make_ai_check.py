"""Тестовые файлы для проверки правил ИИ (каталог, правила 11–15).

Файлы собираются поверх текущей выгрузки (по умолчанию — 44 инцидента из базы бэкенда), чтобы при загрузке
настоящие инциденты не «пропали из выгрузки»: каждый JSON — это полный снимок.

Запуск:  python scripts/make_ai_check.py [--base путь/к/текущей_выгрузке.json]
Результат: demo_data/ai_check/1_history.json и 2_now.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo_data" / "ai_check"
COORD = "КЦ ИТ Координаторы Массовых Инцидентов"
STATES = {0: ("Назначен", "Assigned"), 2: ("Выполняется", "InProgress"), 7: ("Завершен", "Resolved"),
          10: ("Закрыт", "Closed")}
PRIOR = {1: "Низкий", 2: "Средний", 3: "Высокий", 4: "Наивысший"}

# Три проблемы. У каждой свой сервис: ИИ ищет похожие среди инцидентов того же продукта или сервиса.
DISK = dict(productName="Отчётность для руководителей", productId="900000000000000001", productMasterId="BI_9001",
            service="[Отчётность для руководителей] Сервер отчётов", serviceId="900000000000000101", scenarioName="Диск",
            header="Диск заполнен на сервере отчётов",
            shortDescription="Свободное место на разделе /reports меньше 2%.",
            description="Свободное место на разделе /reports сервера отчётов меньше 2%. Отчёты не формируются. Требуется реакция.")
PAY = dict(productName="Платёжный шлюз", productId="900000000000000002", productMasterId="BI_9002",
           service="[Платёжный шлюз] Эквайринг", serviceId="900000000000000102", scenarioName="Оплата картой",
           header="Отказы при оплате банковской картой",
           shortDescription="Доля отказов при оплате картой выросла до 15%.",
           description="Клиенты не могут оплатить услуги банковской картой: эквайринг возвращает отказ. Доля отказов 15%.")
SMS = dict(productName="Мобильное приложение «Мой кабинет»", productId="900000000000000003", productMasterId="BI_9003",
           service="[Мобильное приложение «Мой кабинет»] Авторизация", serviceId="900000000000000103",
           scenarioName="Вход по SMS", header="Не приходят SMS-коды для входа",
           shortDescription="Абоненты не получают SMS с кодом подтверждения.",
           description="Абоненты не могут войти в приложение: SMS с кодом подтверждения не приходит. Массовые жалобы.")


def make(template: dict, iid: str, problem: dict, state: int, priority: int, submit: str, *, team: str,
         solution: str | None = None, completion: str | None = None, reason: str = "Нет", deadline: str | None = None,
         header: str | None = None) -> dict:
    r = copy.deepcopy(template)
    r.update(problem)
    st, en = STATES[state]
    r.update({
        "id": iid, "sysId": "9" + iid[-17:].rjust(17, "0"), "state": st, "stateEnum": en, "stateId": state,
        "stateReason": reason, "stateReasonId": "4" if reason != "Нет" else "1",
        "submitDate": submit, "deadline": deadline, "completionDate": completion,
        "lastAssignmentToTeamDateTime": submit, "assignedGroupName": team,
        "assignedEmployee": "Кот Матроскин (matroskin)", "assignedEmployeeLogin": "matroskin",
        "priorityId": priority, "priority": PRIOR[priority], "solution": solution,
        "isMaster": False, "isDouble": False, "isReason": False, "isEffect": False, "isRootIncident": True,
    })
    if header:
        r["header"] = header
    return r


def load_base(path: str | None) -> list[dict]:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    con = sqlite3.connect(ROOT / "backend" / "data.sqlite3")
    return [json.loads(r[0]) for r in con.execute("select data from incidents where disappeared = 0 order by id")]


def build(base: list[dict]) -> dict[str, list[dict]]:
    tpl = base[0]
    # ---- 1_history: прошлые, уже решённые случаи ----
    history = [
        # «диск заполнен» дважды закрыт отпиской — повтор с отпиской (правила 14 и 15)
        make(tpl, "INC000099900001", DISK, 10, 3, "2026-11-12T08:10:00Z", team="ИТ Эксплуатация серверов",
             solution="Перезагрузил сервер", completion="2026-11-12T08:40:00Z", deadline="2026-11-13T08:10:00Z"),
        make(tpl, "INC000099900002", DISK, 10, 3, "2026-11-16T09:30:00Z", team="ИТ Эксплуатация серверов",
             solution="Само устранилось", reason="Инцидент самоустранился", completion="2026-11-16T10:05:00Z",
             deadline="2026-11-17T09:30:00Z"),
        # оплата картой решена по-настоящему одной командой — база для подсказки команды (правило 16)
        make(tpl, "INC000099900003", PAY, 10, 3, "2026-11-14T11:00:00Z", team="Платёжные сервисы",
             solution="Обновлён сертификат эквайринга, истёкший 14.11; добавлен мониторинг срока сертификатов.",
             completion="2026-11-14T13:20:00Z", deadline="2026-11-15T11:00:00Z"),
        make(tpl, "INC000099900004", PAY, 10, 2, "2026-11-18T15:00:00Z", team="Платёжные сервисы",
             solution="Исправлен таймаут ответа банка-эквайера: увеличен с 5 до 15 секунд, проверено на проде.",
             completion="2026-11-18T17:00:00Z", deadline="2026-11-21T15:00:00Z"),
    ]
    # ---- 2_now: новые активные инциденты ----
    now_new = [
        # снова «диск заполнен», активный — такая проблема уже была (правило 12)
        make(tpl, "INC000099900010", DISK, 0, 3, "2026-11-21T09:00:00Z", team="ИТ Эксплуатация серверов",
             deadline="2026-11-22T09:00:00Z"),
        # три одинаковых активных одновременно — несколько одинаковых сразу (правило 13)
        make(tpl, "INC000099900020", SMS, 2, 3, "2026-11-21T10:00:00Z", team="Мобильное приложение",
             deadline="2026-11-22T10:00:00Z"),
        make(tpl, "INC000099900021", SMS, 0, 3, "2026-11-21T10:07:00Z", team="Мобильное приложение",
             deadline="2026-11-22T10:07:00Z", header="SMS с кодом входа не доходят до абонентов"),
        make(tpl, "INC000099900022", SMS, 0, 2, "2026-11-21T10:15:00Z", team="Мобильное приложение",
             deadline="2026-11-24T10:15:00Z", header="Не могу войти: не пришло SMS с кодом"),
        # оплата картой висит на координаторах — ИИ подскажет команду (правило 16) и повтор (правило 12)
        make(tpl, "INC000099900030", PAY, 0, 3, "2026-11-21T11:00:00Z", team=COORD, deadline="2026-11-22T11:00:00Z"),
    ]
    return {"1_history.json": base + history, "2_now.json": base + history + now_new}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="текущая выгрузка (JSON), поверх которой добавить тестовые инциденты")
    args = ap.parse_args()
    base = load_base(args.base)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in build(base).items():
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: {len(data)} инцидентов (из них тестовых {len(data) - len(base)})")


if __name__ == "__main__":
    main()
