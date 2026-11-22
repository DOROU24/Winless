"""Гарантия «дашборд только для просмотра».

Ни один эндпоинт, кроме POST /api/snapshots, не меняет таблицы incidents, incident_versions и incident_events.
Таблицу links меняет только пересчёт автосвязей: при загрузке снимка и при изменении правил автосвязей
(/api/link-rules). Тест сам обходит все маршруты приложения, поэтому новый эндпоинт не проскочит мимо проверки.
"""
import hashlib
import json
import re

from sqlalchemy import text

from conftest import upload

DEMO_NOW = "2026-11-21T09:00:00Z"
INCIDENT_TABLES = ("incidents", "incident_versions", "incident_events")
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
LINK_RULE_PATHS = re.compile(r"^/api/link-rules")


def fingerprint(table: str, where: str = "") -> str:
    from app.db import engine
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table} {where} ORDER BY 1")).fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows], default=str, ensure_ascii=False).encode()).hexdigest()


def snapshot_state() -> dict:
    state = {t: fingerprint(t) for t in INCIDENT_TABLES}
    state["links"] = fingerprint("links")
    state["links_not_auto"] = fingerprint("links", "WHERE source != 'auto'")
    return state


def all_routes():
    from app.main import app
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        for m in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
            yield m, path


def sample_body(method: str, path: str, ctx: dict) -> dict:
    if path == "/api/rules/validate" or path == "/api/rules/preview":
        return {"rule": {"field": "priorityId", "cmp": "eq", "value": 4}}
    if path.endswith("/columns") and method == "POST":
        return {"name": "Проверка", "rule": {"field": "priorityId", "cmp": "eq", "value": 3}, "alert": "popup"}
    if "/columns/" in path and method == "PATCH":
        return {"name": "Проверка 2", "repeatMin": 5, "collapsed": True}
    if path in ("/api/boards/from-template",) or path.endswith("/reset"):
        return {"template": "mts"}
    if path == "/api/boards" and method == "POST":
        return {"name": "Проверочная"}
    if path.startswith("/api/boards/") and method == "PATCH":
        return {"name": "Переименована", "mode": "all"}
    if path == "/api/link-rules" and method == "POST":
        return {"name": "Проверка", "fields": ["productId"], "windowMin": 600}
    if path.startswith("/api/link-rules/") and method == "PATCH":
        return {"windowMin": 120}
    if path == "/api/presets" and method == "POST":
        return {"name": "Своё", "rule": {"field": "priorityId", "cmp": "eq", "value": 4}, "level": "critical"}
    if path.startswith("/api/presets/") and method == "PATCH":
        return {"name": "Своё 2"}
    if path.startswith("/api/boards/") and path.count("/") == 3 and method == "PATCH":
        return {"name": "Переименована", "mode": "all", "wallLayout": {"scale": 1.2, "tiles": {}}}
    if path == "/api/alerts/read":
        return {"ids": [ctx["alert"]]}
    if path == "/api/assistant/apply":
        return {"proposal": {"kind": "create_column", "boardId": ctx["board"],
                             "column": {"name": "От помощника", "rule": {"field": "priorityId", "cmp": "eq", "value": 4},
                                        "alert": "sound"}}}
    if path == "/api/assistant/chat":
        return {"messages": [{"role": "user", "content": "Что изменилось?"}]}
    if path == "/api/settings":
        return {"itsmUrlTemplate": "https://itsm.example/{id}"}
    return {}


def fill(path: str, ctx: dict) -> str:
    return (path.replace("{board_id}", str(ctx["board"])).replace("{column_id}", str(ctx["column"]))
            .replace("{incident_id}", ctx["incident"]).replace("{rule_id}", str(ctx["link_rule"]))
            .replace("{alert_id}", str(ctx["alert"])).replace("{link_id}", str(ctx["link"]))
            .replace("{preset_id}", str(ctx["preset"])))


def test_no_endpoint_except_upload_changes_incidents(client):
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    import os
    os.environ["MTS_NOW"] = DEMO_NOW
    upload(client, "02_new_critical.json")
    client.get("/api/alerts/due")

    # вход и выход не трогают инциденты; выход здесь разлогинил бы тест посреди обхода
    routes = [(m, p) for m, p in all_routes() if not (m == "POST" and p == "/api/snapshots") and not p.startswith("/api/auth/")]
    # удаления в конце, чтобы остальным эндпоинтам было на чём работать
    routes.sort(key=lambda mp: (mp[0] == "DELETE", mp[1]))
    assert len(routes) > 20, "маршруты не найдены"

    checked = []
    for method, path in routes:
        board = client.get("/api/boards").json()["boards"][0]
        ctx = {
            "board": board["id"], "column": board["columns"][0]["id"] if board["columns"] else 0,
            "incident": client.get("/api/incidents?limit=1").json()[0]["id"],
            "link_rule": client.get("/api/link-rules").json()[-1]["id"],
            "alert": (client.get("/api/alerts").json()["items"] or [{"id": 0}])[0]["id"],
            "link": (client.get("/api/links").json() or [{"id": 0}])[0]["id"],
            "preset": ([p["id"] for p in client.get("/api/presets?with_counts=false").json() if p["custom"]] or [1])[-1],
        }
        url = fill(path, ctx)
        before = snapshot_state()
        if method == "GET":
            r = client.get(url)
        else:
            r = client.request(method, url, json=sample_body(method, path, ctx))
        assert r.status_code < 500, f"{method} {path}: {r.status_code} {r.text[:200]}"
        after = snapshot_state()
        for t in INCIDENT_TABLES:
            assert before[t] == after[t], f"{method} {path} изменил таблицу {t}"
        if LINK_RULE_PATHS.match(path) and method in WRITE_METHODS:
            # пересчёт автосвязей может менять только связи с источником «автоправило»
            assert before["links_not_auto"] == after["links_not_auto"], f"{method} {path} тронул не-автосвязи"
        else:
            assert before["links"] == after["links"], f"{method} {path} изменил таблицу links"
        checked.append(f"{method} {path}")

    # write-эндпоинтов для инцидентов и связей быть не должно вовсе
    for method, path in all_routes():
        if method in WRITE_METHODS:
            assert not path.startswith("/api/incidents"), f"{method} {path}: инциденты только для чтения"
            assert not path.startswith("/api/links"), f"{method} {path}: связи создают только правила"
            # оповещения: только опрос и отметка «прочитано» — это состояние самого оповещения, не инцидента
            assert not path.startswith("/api/alerts/") or path in ("/api/alerts/due", "/api/alerts/read"), \
                f"{method} {path}"
    assert any(c.startswith("DELETE") for c in checked) and any(c.startswith("PATCH") for c in checked)


def test_upload_is_the_only_writer(client):
    """Обратная проверка: загрузка снимка действительно меняет таблицы инцидентов."""
    upload(client, "00_history.json")
    before = snapshot_state()
    upload(client, "01_now.json")
    after = snapshot_state()
    assert all(before[t] != after[t] for t in INCIDENT_TABLES)


def test_migration_cleans_old_database(client):
    """Старая база: таблица acks, колонки alerts.acked/acked_at и ручные связи удаляются при запуске."""
    from sqlalchemy import inspect
    from app.db import engine, init_db
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE acks (incident_id VARCHAR(64) PRIMARY KEY, at DATETIME)"))
        conn.execute(text("ALTER TABLE alerts ADD COLUMN acked BOOLEAN DEFAULT 0"))
        conn.execute(text("ALTER TABLE alerts ADD COLUMN acked_at DATETIME"))
        conn.execute(text("INSERT INTO links (src, dst, type, directed, source, note, created_at) "
                          "VALUES ('A', 'B', 'cause', 1, 'manual', 'старая ручная', '2026-11-01')"))
    init_db()
    init_db()  # повторный запуск ничего не ломает
    insp = inspect(engine)
    assert not insp.has_table("acks")
    assert not {"acked", "acked_at"} & {c["name"] for c in insp.get_columns("alerts")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM links WHERE source = 'manual'")).scalar() == 0
