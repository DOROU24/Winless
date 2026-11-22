"""Доска «Задание МТС» по заданию: четыре группы, во всех — только незакрытые инциденты."""
from conftest import upload

CLOSED = 10


def mts_board(client):
    b = client.post("/api/boards/from-template", json={"template": "mts"}).json()
    return client.get(f"/api/boards/{b['id']}/layout").json()


def test_mts_columns_follow_spec(client):
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    lay = mts_board(client)
    cols = {c["name"]: c for c in lay["columns"]}
    assert list(cols) == ["Критические", "Назначены на координаторов", "Высокий приоритет", "Исследование ситуации"]
    for c in lay["columns"]:
        assert all(k["stateId"] != CLOSED for k in c["cards"]), f"{c['name']}: закрытый инцидент в колонке"
    # критические — любой незакрытый с приоритетом «Наивысший», срок и статус «в работе» не важны
    assert all(k["priorityId"] == 4 for k in cols["Критические"]["cards"])
    assert all(k["priorityId"] == 3 for k in cols["Высокий приоритет"]["cards"])
    assert all(k["stateId"] == 20 for k in cols["Исследование ситуации"]["cards"])


def test_old_mts_rules_are_upgraded_but_user_edits_kept():
    from app.db import Board, Column, SessionLocal
    from app.templates import AND, ACTIVE, C, TEMPLATES, _upgrade_mts_columns
    with SessionLocal() as s:
        b = Board(name="Старая МТС", mode="all", template="mts", position=99)
        s.add(b)
        s.flush()
        old = Column(board_id=b.id, name="Критические", rule=AND(C("priorityId", "eq", 4), ACTIVE))
        mine = Column(board_id=b.id, name="Высокий приоритет", rule=C("priorityId", "eq", 2))  # поменял сам
        s.add_all([old, mine])
        s.flush()
        _upgrade_mts_columns(s)
        new = {c["name"]: c["rule"] for c in TEMPLATES["mts"]["columns"]}
        assert old.rule == new["Критические"]
        assert mine.rule == C("priorityId", "eq", 2)
        s.rollback()
