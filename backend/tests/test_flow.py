"""Сквозные тесты: загрузка, история, реестр полей, правила каталога на демо-данных, доски, оповещения, связи."""
import json
import os
import time

from conftest import demo_ids, upload

DEMO_NOW = "2026-11-21T09:00:00Z"
FILES = ["00_history.json", "01_now.json", "02_new_critical.json", "03_reassign_and_resolve.json",
         "04_reopen_again.json", "05_complaints_growth.json", "06_disk_full_again.json"]


def set_now(client, when=DEMO_NOW):
    """Фиксирует время для правил. Через интерфейс и API время не меняется — только MTS_NOW для тестов."""
    os.environ["MTS_NOW"] = when


class Browser:
    """Открытый пульт: опрашивает /api/alerts/due со временем своего прошлого опроса."""
    def __init__(self, client):
        self.client = client
        self.since = client.get("/api/alerts/due").json()["now"]

    def poll(self, since=None):
        r = self.client.get("/api/alerts/due", params={"since": since or self.since}).json()
        self.since = r["now"]
        return r["due"]


def preset_matches(client, pid):
    presets = {p["id"]: p for p in client.get("/api/presets").json()}
    r = client.post("/api/rules/preview", json={"rule": presets[pid]["rule"], "limit": 500}).json()
    assert r["ok"], r
    return {x["id"] for x in r["items"]}


def test_health_and_first_board(client):
    assert client.get("/api/health").json()["ok"]
    boards = client.get("/api/boards").json()
    assert [b["name"] for b in boards["boards"]] == ["Фокус внимания"]
    cols = [c["name"] for c in boards["boards"][0]["columns"]]
    assert cols == ["Срочно", "Нужно передать", "Срок", "Решение буксует", "Проверить"]
    assert boards["boards"][0]["mode"] == "first"


def test_ingest_events_and_idempotency(client):
    upload(client, "00_history.json")
    r1 = upload(client, "01_now.json")
    assert r1["created"] == 57 and r1["disappeared"] == 35 and r1["changed"] == 3
    r2 = upload(client, "02_new_critical.json")
    assert r2["created"] == 1 and r2["changed"] == 0 and r2["disappeared"] == 0
    again = upload(client, "02_new_critical.json")
    assert again["created"] == 0 and again["changed"] == 0 and again["disappeared"] == 0
    ids = demo_ids()
    card = client.get(f"/api/incidents/{ids['reopened']}").json()
    changes = [e for e in card["events"] if e["type"] == "field_changed" and e["field"] == "stateId"]
    assert changes and changes[0]["old"] == 7 and changes[0]["new"] == 0


def test_rules_work_before_first_upload(client):
    """Регресс: до первой загрузки стандартные поля уже есть в реестре."""
    r = client.post("/api/rules/validate", json={"rule": {"op": "and", "children": [
        {"field": "priorityId", "cmp": "eq", "value": 4},
        {"field": "calc.deadlineLeftPct", "cmp": "lt", "value": 5}]}}).json()
    assert r["ok"], r
    b = client.get("/api/boards").json()["boards"][0]
    c = client.post(f"/api/boards/{b['id']}/columns", json={"name": "x", "rule": {"field": "priorityId", "cmp": "eq", "value": 4}})
    assert c.status_code == 200
    fields = {f["key"]: f for f in client.get("/api/fields").json()}
    assert fields["priorityId"]["labels"]["4"] == "Наивысший"


def test_root_object_warning(client):
    payload = json.dumps({"meta": 1, "items": [{"id": "X1", "stateId": 0, "state": "Назначен"}]}).encode()
    r = client.post("/api/snapshots", files={"file": ("x.json", payload, "application/json")}).json()
    assert r["created"] == 1 and any("объект" in w for w in r["warnings"])
    bad = client.post("/api/snapshots", files={"file": ("x.json", b"not json", "application/json")})
    assert bad.status_code == 400


def test_field_registry(client):
    upload(client, "01_now.json")
    fields = {f["key"]: f for f in client.get("/api/fields").json()}
    assert fields["priorityId"]["type"] == "number"
    assert fields["priorityId"]["labels"]["4"] == "Наивысший"
    assert fields["stateId"]["labels"]["20"] == "Исследование ситуации"
    assert fields["submitDate"]["type"] == "date"
    assert fields["isMaster"]["type"] == "flag"
    assert fields["complaintsCount"]["type"] == "number"  # неизвестное поле подхватилось
    assert fields["waitingExitDate"]["type"] in ("text", "date")
    for k in ("calc.deadlineLeftPct", "calc.idleMin", "hist.transitions"):
        assert k in fields
    assert not any(k.startswith("user.") for k in fields)  # только просмотр: полей пользователя нет
    assert fields["ai.weakSolution"]["available"] is False


def test_calc_fields_and_virtual_now(client):
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    ids = demo_ids()
    c1 = client.get(f"/api/incidents/{ids['c1']}").json()["calc"]
    assert 0 < c1["calc.deadlineLeftPct"] < 5 and c1["calc.deadlineLeftMin"] == 10
    assert c1["calc.isActive"] and not c1["calc.overdue"] and c1["calc.idleMin"] is None
    c2 = client.get(f"/api/incidents/{ids['c2']}").json()["calc"]
    assert c2["calc.idleMin"] == 45 and c2["calc.isNew"]
    # пропавший из истории
    chain = client.get(f"/api/incidents/{ids['disk_chain'][0]}").json()
    assert chain["calc"]["calc.disappeared"] and chain["calc"]["calc.deadlineLeftMin"] < 0
    assert chain["calc"]["calc.overdue"] is False  # закрыт — не просрочен
    # «сейчас» сдвинули — значения поменялись
    set_now(client, "2026-11-21T09:20:00Z")
    c1b = client.get(f"/api/incidents/{ids['c1']}").json()["calc"]
    assert c1b["calc.overdue"] and c1b["calc.deadlineLeftMin"] == -10


def test_rule_validation_errors(client):
    upload(client, "01_now.json")
    r = client.post("/api/rules/validate", json={"rule": {"op": "and", "children": [
        {"field": "nope", "cmp": "eq", "value": 1},
        {"field": "priorityId", "cmp": "contains", "value": "x"},
        {"field": "priorityId", "cmp": "eq", "value": "abc"},
        {"field": "hist.changes", "cmp": "gte", "value": 2},
    ]}}).json()
    assert not r["ok"] and len(r["errors"]) == 4
    assert "nope" in r["errors"][0]["message"] and r["errors"][0]["path"] == "rule.children[0]"


def test_operators(client):
    upload(client, "01_now.json")
    set_now(client)
    def count(rule):
        return client.post("/api/rules/preview", json={"rule": rule}).json()["count"]
    total = count(None)
    assert total == 60
    assert count({"field": "priorityId", "cmp": "eq", "value": 4}) == 3
    assert count({"field": "priorityId", "cmp": "between", "value": [3, 4]}) == count(
        {"field": "priorityId", "cmp": "gte", "value": 3})
    assert count({"field": "header", "cmp": "contains", "value": "оплате"}) >= 1
    assert count({"field": "deadline", "cmp": "is_empty"}) == 2
    assert count({"field": "submitDate", "cmp": "within_last_min", "value": 60}) >= 2
    assert count({"field": "calc.isActive", "cmp": "is_true"}) + count({"field": "calc.isActive", "cmp": "is_false"}) == total
    assert count({"field": "stateId", "cmp": "not_in", "value": [10]}) <= total


def test_catalog_rules_on_demo(client):
    ids = demo_ids()
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    assert {ids["c1"], ids["c2"], ids["c3"]} <= preset_matches(client, 1)
    assert ids["c2"] in preset_matches(client, 3)
    assert len(preset_matches(client, 2)) == 2
    assert len(preset_matches(client, 4)) >= 2
    assert len(preset_matches(client, 5)) >= 2
    assert len(preset_matches(client, 9)) == 2
    assert len(preset_matches(client, 10)) == 2
    upload(client, "02_new_critical.json")
    assert preset_matches(client, 1) == {ids["new_crit"]}
    upload(client, "03_reassign_and_resolve.json")
    assert ids["reassigned"] in preset_matches(client, 2)
    assert ids["team_changes"] in preset_matches(client, 7)
    upload(client, "04_reopen_again.json")
    assert ids["reopened"] in preset_matches(client, 6)
    assert ids["priority_ups"] in preset_matches(client, 8)
    upload(client, "05_complaints_growth.json")
    presets = {p["id"]: p for p in client.get("/api/presets").json()}
    assert presets[11]["name"].startswith("Повтор") and presets[11]["noData"] is True  # без ключа ИИ — «нет данных»


def test_catalog_numbering_has_no_gaps(client):
    """Правило «Низкий приоритет, а жалобы растут» убрано; номера в каталоге идут подряд без дыр."""
    from app.templates import PRESETS, TEMPLATES
    ids = [p["id"] for p in PRESETS]
    assert ids == list(range(1, len(ids) + 1)) and len(ids) == 18
    assert not any("жалобы" in p["name"].lower() for p in PRESETS)
    assert [p["id"] for p in client.get("/api/presets").json() if not p["custom"]] == ids
    focus = {c["name"]: c for c in TEMPLATES["focus"]["columns"]}
    assert focus["Проверить"]["presets"] == [9, 10] and focus["Проверить"]["ai_presets"] == [13, 17]
    assert focus["Повторы и дубли"]["ai_presets"] == [11, 12, 14, 16] and focus["Нужно передать"]["ai_presets"] == [15]
    assert focus["Повторы и дубли"]["group_similar"] is True and focus["Срок"]["ai_presets"] == [18]


def test_column_preset_marks_renumbered_once(client):
    """Пометки у старых колонок сдвигаются один раз: 11 удаляется, 12–16 становятся 11–15."""
    from app.db import Column, SessionLocal, set_setting
    from app.templates import seed
    b = client.get("/api/boards").json()["boards"][0]
    with SessionLocal() as s:
        s.add(Column(board_id=b["id"], position=50, name="Старая", rule=None, card_fields=["id"], preset_ids=[9, 11, 12, 16]))
        set_setting(s, "presets_renumbered_v1", None)
        s.commit()
        seed(s)
        seed(s)  # второй запуск ничего не сдвигает
        col = s.query(Column).filter(Column.name == "Старая").one()
        assert col.preset_ids == [9, 11, 15]

def test_board_crud_layout_and_modes(client):
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    b = client.post("/api/boards", json={"name": "Моя"}).json()
    c1 = client.post(f"/api/boards/{b['id']}/columns", json={
        "name": "Критич. <5%", "rule": {"op": "and", "children": [
            {"field": "priorityId", "cmp": "eq", "value": 4}, {"field": "calc.deadlineLeftPct", "cmp": "lt", "value": 5}]},
        "level": "critical", "alert": "sound"}).json()
    c2 = client.post(f"/api/boards/{b['id']}/columns", json={
        "name": "Все критические", "rule": {"field": "priorityId", "cmp": "eq", "value": 4},
        "sortField": "calc.deadlineLeftPct", "sortDir": "asc", "limit": 2}).json()
    lay = client.get(f"/api/boards/{b['id']}/layout").json()
    cols = {c["id"]: c for c in lay["columns"]}
    assert cols[c1["id"]]["total"] == 1 and cols[c2["id"]]["total"] == 3 and cols[c2["id"]]["hidden"] == 1
    assert any("Осталось до срока" in w for w in cols[c1["id"]]["cards"][0]["why"])
    assert "Приоритет = 4 (Наивысший)" in cols[c1["id"]]["cards"][0]["why"]
    client.patch(f"/api/boards/{b['id']}", json={"mode": "first"})
    lay = client.get(f"/api/boards/{b['id']}/layout").json()
    assert {c["id"]: c for c in lay["columns"]}[c2["id"]]["total"] == 2
    client.patch(f"/api/boards/{b['id']}", json={"columnOrder": [c2["id"], c1["id"]]})
    lay = client.get(f"/api/boards/{b['id']}/layout").json()
    assert lay["columns"][0]["id"] == c2["id"] and lay["columns"][1]["total"] == 0
    bad = client.patch(f"/api/boards/{b['id']}/columns/{c1['id']}", json={"rule": {"field": "x", "cmp": "eq", "value": 1}})
    assert bad.status_code == 422
    dup = client.post(f"/api/boards/{b['id']}/duplicate").json()
    assert len(dup["columns"]) == 2
    mts = client.post("/api/boards/from-template", json={"template": "mts"}).json()
    assert [c["name"] for c in mts["columns"]][0] == "Критические"


def test_alerts_read_only(client):
    ids = demo_ids()
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    browser = Browser(client)
    r = upload(client, "02_new_critical.json")
    assert r["newAlerts"] >= 1
    al = [a for a in client.get("/api/alerts").json()["items"] if a["incidentId"] == ids["new_crit"]]
    assert len(al) == 1 and al[0]["level"] == "critical" and al[0]["mode"] == "sound"
    assert al[0]["repeatMin"] is None and al[0]["inColumn"] is True  # правило 1: окно и звук, без повтора
    assert "acked" not in al[0]
    due = browser.poll()
    assert any(a["incidentId"] == ids["new_crit"] for a in due)
    assert not any(a["incidentId"] == ids["new_crit"] for a in browser.poll())  # этому браузеру второй раз не показываем
    # повторная загрузка того же файла — новых оповещений нет
    assert upload(client, "02_new_critical.json")["newAlerts"] == 0
    # эндпоинтов подтверждения больше нет
    assert client.post(f"/api/alerts/{al[0]['id']}/ack").status_code in (404, 405)
    assert client.post(f"/api/incidents/{ids['new_crit']}/ack").status_code in (404, 405)
    assert client.get("/api/alerts?unacked=true").status_code == 200  # параметр просто игнорируется


def test_alert_repeat_while_in_column(client):
    """Повтор идёт каждые N минут, пока инцидент остаётся в колонке, и прекращается, когда он вышел."""
    from datetime import timedelta
    from app.db import Alert, SessionLocal
    ids = demo_ids()
    upload(client, "00_history.json")
    set_now(client)
    b = client.post("/api/boards", json={"name": "Повторы"}).json()
    col = client.post(f"/api/boards/{b['id']}/columns", json={
        "name": "Новые критические", "rule": {"op": "and", "children": [
            {"field": "priorityId", "cmp": "eq", "value": 4}, {"field": "calc.isNew", "cmp": "is_true"}]},
        "alert": "popup", "repeatMin": 5}).json()
    upload(client, "01_now.json")  # оповещения приходят из-за новых данных

    def rewind():  # «прошло 6 минут с момента оповещения»: сдвигаем время создания назад
        with SessionLocal() as s:
            for a in s.query(Alert).filter(Alert.column_ids.isnot(None)).all():
                if col["id"] in (a.column_ids or []):
                    a.created_at -= timedelta(minutes=6)
            s.commit()

    def due_here():  # браузер последний раз опрашивал 2 минуты назад, то есть на 4-й минуте после оповещения
        from app.db import utcnow
        since = (utcnow() - timedelta(minutes=2)).isoformat() + "Z"
        due = client.get("/api/alerts/due", params={"since": since}).json()["due"]
        return {a["incidentId"] for a in due if "Новые критические" in a["columns"]}

    rewind()
    assert ids["c1"] in due_here()  # всё ещё в колонке — повтор на 5-й минуте
    upload(client, "02_new_critical.json")  # c1 больше не новый и выходит из колонки
    assert ids["c1"] not in due_here()
    c1_alert = [a for a in client.get("/api/alerts").json()["items"] if a["incidentId"] == ids["c1"] and "Новые критические" in a["columns"]]
    assert c1_alert and c1_alert[0]["inColumn"] is False


def test_links_only_from_rules(client):
    ids = demo_ids()
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    links = client.get("/api/links").json()
    assert links and all(l["source"] == "auto" for l in links)
    assert client.post("/api/links", json={"src": ids["c1"], "dst": ids["c2"], "type": "cause"}).status_code == 405
    assert client.delete(f"/api/links/{links[0]['id']}").status_code in (404, 405)
    card = client.get(f"/api/incidents/{ids['c1']}").json()
    assert all(l["source"] == "auto" for l in card["links"]["outgoing"] + card["links"]["incoming"])
    g = client.get("/api/graph").json()
    assert g["nodes"] and g["edges"]
    rules = client.get("/api/link-rules").json()
    client.patch(f"/api/link-rules/{rules[0]['id']}", json={"enabled": False})
    assert not any(l["ruleId"] == rules[0]["id"] for l in client.get("/api/links").json())


def test_legacy_user_acked_rule(client):
    """Сохранённое правило с удалённым user.acked помечается ошибкой и не роняет приложение."""
    from app.db import Column, SessionLocal
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    legacy = {"op": "and", "children": [{"field": "priorityId", "cmp": "eq", "value": 4},
                                        {"field": "user.acked", "cmp": "is_false"}]}
    v = client.post("/api/rules/validate", json={"rule": legacy}).json()
    assert not v["ok"] and "только для просмотра" in v["errors"][0]["message"]
    board = client.get("/api/boards").json()["boards"][0]
    with SessionLocal() as s:  # как будто правило сохранено старой версией приложения
        s.add(Column(board_id=board["id"], position=99, name="Старая", rule=legacy, card_fields=["id"],
                     level="critical", alert="sound"))
        s.commit()
    lay = client.get(f"/api/boards/{board['id']}/layout")
    assert lay.status_code == 200
    old = [c for c in lay.json()["columns"] if c["name"] == "Старая"][0]
    assert old["ruleErrors"] and "user.acked" in old["ruleErrors"][0]["message"] and old["total"] == 0
    assert client.get("/api/alerts/due").status_code == 200
    assert client.get("/api/graph").status_code == 200


def test_templates_and_presets_have_no_user_fields(client):
    from app.rules import rule_fields
    from app.templates import PRESETS, TEMPLATES
    for p in PRESETS:
        assert not any(f.startswith("user.") for f in rule_fields(p["rule"])), p["name"]
    for t in TEMPLATES.values():
        for c in t["columns"]:
            assert not any(f.startswith("user.") for f in rule_fields(c.get("rule"))), c["name"]
    p1 = {p["id"]: p for p in client.get("/api/presets").json()}[1]
    assert p1["name"] == "Новый критический" and p1["repeatMin"] is None and p1["alert"] == "sound"
    assert rule_fields(p1["rule"]) == {"priorityId", "calc.isNew", "calc.isActive"}


def test_performance_5000(client):
    import random
    rnd = random.Random(1)
    recs = []
    base = json.loads(open(__import__("conftest").DEMO / "01_now.json", encoding="utf-8").read())
    for i in range(5000):
        r = dict(rnd.choice(base))
        r["id"] = f"P{i:06d}"
        recs.append(r)
    client.post("/api/snapshots", files={"file": ("big.json", json.dumps(recs).encode(), "application/json")})
    b = client.post("/api/boards", json={"name": "Нагрузка"}).json()
    presets = client.get("/api/presets?with_counts=false").json()
    for i in range(20):
        p = presets[i % 11]
        client.post(f"/api/boards/{b['id']}/columns", json={"name": p["name"], "rule": p["rule"], "limit": 30})
    t = time.perf_counter()
    client.get(f"/api/boards/{b['id']}/layout")
    assert time.perf_counter() - t < 3.0  # с запасом на медленные машины CI


def test_new_board_does_not_realert(client):
    """Регресс: новая доска / колонка без новой загрузки не шлёт оповещения по уже известным инцидентам."""
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    browser = Browser(client)
    before = len(client.get("/api/alerts").json()["items"])
    for tpl in ("focus", "mts"):
        client.post("/api/boards/from-template", json={"template": tpl})
    b = client.post("/api/boards", json={"name": "Своя"}).json()
    c = client.post(f"/api/boards/{b['id']}/columns", json={
        "name": "Все критические", "rule": {"field": "priorityId", "cmp": "eq", "value": 4}, "alert": "sound"}).json()
    dup = client.post(f"/api/boards/{b['id']}/duplicate").json()
    client.patch(f"/api/boards/{b['id']}/columns/{c['id']}", json={"rule": {"field": "priorityId", "cmp": "gte", "value": 3}})
    client.patch(f"/api/boards/{dup['id']}", json={"mode": "first"})
    due = browser.poll()
    assert due == [], [a["column"] for a in due]
    assert len(client.get("/api/alerts").json()["items"]) == before
    # а новая загрузка с новым критическим по-прежнему оповещает, в том числе на новых досках
    r = upload(client, "02_new_critical.json")
    assert r["newAlerts"] >= 1


def test_custom_presets(client):
    """Своё правило сохраняется в каталог, переживает перезапуск, добавляется в колонку и удаляется."""
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    rule = {"op": "and", "children": [{"field": "priorityId", "cmp": "eq", "value": 4},
                                      {"field": "calc.deadlineLeftPct", "cmp": "lt", "value": 5}]}
    bad = client.post("/api/presets", json={"name": "Плохое", "rule": {"field": "nope", "cmp": "eq", "value": 1}})
    assert bad.status_code == 422
    assert client.post("/api/presets", json={"name": "", "rule": rule}).status_code == 422
    assert client.post("/api/presets", json={"name": "Пустое", "rule": {"op": "and", "children": []}}).status_code == 422
    p = client.post("/api/presets", json={"name": "Критический, < 5% срока", "description": "Горит прямо сейчас",
                                          "rule": rule, "level": "critical", "alert": "sound"}).json()
    assert p["custom"] and p["id"] >= 1000 and p["origin"] == "своё"
    listed = {x["id"]: x for x in client.get("/api/presets").json()}
    assert listed[p["id"]]["count"] == 1 and 1 in listed  # встроенные на месте

    # перезапуск: каталог из кода пересоздаётся, своё правило остаётся
    from app.db import SessionLocal
    from app.templates import seed
    with SessionLocal() as s:
        seed(s)
    assert p["id"] in {x["id"] for x in client.get("/api/presets").json()}

    # встроенные не меняются и не удаляются
    assert client.patch("/api/presets/1", json={"name": "x"}).status_code == 403
    assert client.delete("/api/presets/1").status_code == 403

    # добавление в колонку и правка
    b = client.get("/api/boards").json()["boards"][0]
    c = client.post(f"/api/boards/{b['id']}/columns", json={"name": p["name"], "rule": p["rule"], "presetIds": [p["id"]]})
    assert c.status_code == 200
    r = client.patch(f"/api/presets/{p['id']}", json={"name": "Горит", "repeatMin": 10}).json()
    assert r["name"] == "Горит" and r["repeatMin"] == 10
    assert client.delete(f"/api/presets/{p['id']}").json()["ok"]
    assert p["id"] not in {x["id"] for x in client.get("/api/presets").json()}
    # колонка с этим правилом осталась как была
    col = [x for x in client.get(f"/api/boards/{b['id']}/columns").json() if x["id"] == c.json()["id"]][0]
    assert col["rule"] == rule


def test_wall_layout(client):
    b = client.get("/api/boards").json()["boards"][0]
    cid = str(b["columns"][0]["id"])
    layout = {"scale": 5, "showClock": False, "tiles": {cid: {"x": 20, "y": 2, "w": 6, "h": 99, "hidden": True,
                                                             "showCards": True, "maxCards": 3, "evil": "<script>"}}}
    r = client.patch(f"/api/boards/{b['id']}", json={"wallLayout": layout}).json()
    wl = r["wallLayout"]
    assert wl["scale"] == 2.0 and wl["showClock"] is False
    assert wl["tiles"][cid] == {"x": 11, "y": 2, "w": 6, "h": 40, "hidden": True, "showCards": True, "maxCards": 3}
    assert client.get(f"/api/boards/{b['id']}").json()["wallLayout"] == wl
    dup = client.post(f"/api/boards/{b['id']}/duplicate").json()
    assert dup["wallLayout"] is None
    assert client.patch(f"/api/boards/{b['id']}", json={"wallLayout": None}).json()["wallLayout"] is None


def test_ai_pipeline_async_with_fake_model(client, monkeypatch):
    """Анализ ИИ идёт в фоне после загрузки; результаты пишутся в ai.* и связи «ИИ». Модель подменена."""
    import time
    from app.llm import adapter, pipeline
    ids = demo_ids()
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    calls = []

    def fake_ask(system, user, schema):
        req = json.loads(user)
        calls.append(req["incident"]["id"])
        cands = req["candidates"]
        same = [{"id": c["id"], "relation": "same", "explanation": "та же проблема"} for c in cands
                if c["header"] == req["incident"]["header"]]
        sol = req["incident"]["solution"] or ""
        return {"similar": same, "solution": {"weak": ("Перезагрузил" in sol or "Само" in sol) if sol else None,
                                              "reason": "проверка"},
                "team": {"name": None, "reason": "нет данных"}, "facts": [], "assumptions": []}

    monkeypatch.setattr(pipeline, "ask_json", fake_ask)
    assert adapter.is_configured()
    upload(client, "00_history.json")
    for f in ("01_now.json", "02_new_critical.json", "03_reassign_and_resolve.json", "04_reopen_again.json",
              "05_complaints_growth.json"):
        upload(client, f)
    t = time.time()
    r = upload(client, "06_disk_full_again.json")
    assert r["ai"] == {"enabled": True, "queued": True} and time.time() - t < 5  # загрузка не ждёт модель
    for _ in range(100):  # ждём, пока фоновый поток обработает все загрузки
        v = client.get("/api/alerts/due").json()["ai"]
        if not v["running"] and v["queuedUploads"] == 0 and ids["disk_new"] in calls:
            break
        time.sleep(0.1)
    card = client.get(f"/api/incidents/{ids['disk_new']}").json()
    assert card["calc"]["ai.similarPastCount"] == 5
    chain = client.get(f"/api/incidents/{ids['disk_chain'][0]}").json()
    assert chain["calc"]["ai.weakSolution"] is True
    ai_links = [l for l in client.get("/api/links").json() if l["source"] == "ai" and ids["disk_new"] in (l["src"], l["dst"])]
    assert len(ai_links) == 5 and all(l["type"] == "repeat" for l in ai_links)
    # повторная загрузка того же файла модель не вызывает: текст не менялся
    n = len(calls)
    upload(client, "06_disk_full_again.json")
    time.sleep(0.5)
    assert len(calls) == n


def test_llm_adapter_retries_transient_errors(monkeypatch):
    """Обрыв связи и 503 повторяются, неверный ключ — нет."""
    import io
    import urllib.error
    from app.llm import adapter
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    seq = []

    def fake_urlopen(req, timeout=0):
        seq.append(1)
        if len(seq) == 1:
            raise urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING")
        if len(seq) == 2:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"{}"))
        body = json.dumps({"choices": [{"message": {"content": json.dumps({"ok": True})}}]}).encode()
        return io.BytesIO(body)

    class Ctx(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda req, timeout=0: Ctx(fake_urlopen(req, timeout).getvalue()))
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    assert adapter.ask_json("s", "u", {"type": "object", "properties": {"ok": {"type": "boolean"}}}) == {"ok": True}
    assert len(seq) == 3
    seq.clear()

    def unauthorized(req, timeout=0):
        seq.append(1)
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"error": {"message": "bad key"}}'))

    monkeypatch.setattr(adapter.urllib.request, "urlopen", unauthorized)
    try:
        adapter.ask_json("s", "u", {"type": "object"})
        assert False, "должна быть ошибка"
    except adapter.LLMError as e:
        assert "401" in str(e) and len(seq) == 1


def test_ai_pair_found_from_both_sides_shown_once(client):
    from app.db import Link, SessionLocal
    upload(client, "01_now.json")
    ids = demo_ids()
    with SessionLocal() as s:
        s.add(Link(src=ids["c1"], dst=ids["c2"], type="double", directed=False, source="ai", note="A→B"))
        s.add(Link(src=ids["c2"], dst=ids["c1"], type="repeat", directed=False, source="ai", note="B→A"))
        s.commit()
    g = client.get("/api/graph?source=ai").json()
    assert len(g["edges"]) == 1 and g["edges"][0]["type"] == "repeat"  # разные типы с двух сторон — одна связь
    card = client.get(f"/api/incidents/{ids['c1']}").json()["links"]
    assert len([l for l in card["outgoing"] + card["incoming"] if l["source"] == "ai"]) == 1


def test_preview_respects_first_column_mode(client):
    """Регресс: предпросмотр «подходит 2», а в колонке 1 — второй уже в колонке выше (режим «только в первой»)."""
    upload(client, "01_now.json")
    set_now(client)
    b = client.post("/api/boards", json={"name": "Порядок", "mode": "first"}).json()
    a = client.post(f"/api/boards/{b['id']}/columns", json={"name": "Критические", "rule": {"field": "priorityId", "cmp": "eq", "value": 4}}).json()
    rule_b = {"field": "priorityId", "cmp": "gte", "value": 3}
    c = client.post(f"/api/boards/{b['id']}/columns", json={"name": "Высокий и выше", "rule": rule_b}).json()
    p = client.post("/api/rules/preview", json={"rule": rule_b, "boardId": b["id"], "columnId": c["id"], "limit": 100}).json()
    lay = {x["id"]: x for x in client.get(f"/api/boards/{b['id']}/layout").json()["columns"]}
    assert p["takenAbove"] == 3 and p["inColumn"] == p["count"] - 3
    assert p["inColumn"] == lay[c["id"]]["total"]  # предпросмотр совпадает с колонкой на доске
    assert sum(1 for i in p["items"] if i["takenBy"] == "Критические") == 3
    # первая колонка ничего «выше» не имеет; без доски — старое поведение
    pa = client.post("/api/rules/preview", json={"rule": a["rule"], "boardId": b["id"], "columnId": a["id"]}).json()
    assert pa["takenAbove"] == 0
    assert client.post("/api/rules/preview", json={"rule": rule_b}).json()["takenAbove"] == 0
    # новая колонка (без id) встаёт в конец: всё, что выше, уже занято
    pn = client.post("/api/rules/preview", json={"rule": rule_b, "boardId": b["id"]}).json()
    assert pn["inColumn"] == 0


def test_graph_shows_only_linked_incidents(client):
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    g = client.get("/api/graph").json()
    linked = {e["src"] for e in g["edges"]} | {e["dst"] for e in g["edges"]}
    assert g["nodes"] and {n["id"] for n in g["nodes"]} == linked
    # параметр доски больше не добавляет узлы без связей
    b = client.get("/api/boards").json()["boards"][0]
    g2 = client.get(f"/api/graph?board_id={b['id']}").json()
    assert {n["id"] for n in g2["nodes"]} == linked


def test_weak_solution_empty_without_solution():
    """Модель иногда помечает отпиской инцидент без решения — такой ответ игнорируется."""
    from app.llm.pipeline import interpret
    resp = {"similar": [], "solution": {"weak": True, "reason": "нет решения"}, "team": {"name": None, "reason": ""}}
    assert interpret(resp, {}, has_solution=False)["weakSolution"] is None
    assert interpret(resp, {}, has_solution=True)["weakSolution"] is True


def test_time_cannot_be_changed_via_api(client):
    """Настройку времени убрали: время для правил не меняется через API."""
    os.environ.pop("MTS_NOW", None)
    st = client.get("/api/settings").json()
    assert "virtualNow" not in st
    client.patch("/api/settings", json={"virtualNow": "2020-01-01T00:00:00Z"})  # поле игнорируется
    from datetime import datetime, timezone
    from app.fields import parse_dt
    now = parse_dt(client.get("/api/settings").json()["now"])
    assert abs((now - datetime.now(timezone.utc)).total_seconds()) < 60



def _run_fake_ai(client, monkeypatch, files, answer):
    """Загрузить файлы и прогнать фоновый анализ ИИ с подменённой моделью."""
    import time
    from app.llm import pipeline
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setattr(pipeline, "ask_json", answer)
    for f in files:
        upload(client, f)
    for _ in range(200):
        v = client.get("/api/alerts/due").json()["ai"]
        if not v["running"] and v["queuedUploads"] == 0:
            break
        time.sleep(0.05)


def _same_header_answer(priority=None):
    def answer(system, user, schema):
        req = json.loads(user)
        inc = req["incident"]
        same = [{"id": c["id"], "relation": "same", "explanation": "та же проблема"} for c in req["candidates"]
                if c["header"] == inc["header"]]
        return {"similar": same, "solution": {"weak": None, "reason": ""}, "team": {"name": None, "reason": ""},
                "priority": priority(inc) if priority else {"suggested": None, "reason": ""}}
    return answer


def test_waves_group_active_same_problem_and_collapse(client, monkeypatch):
    """Волна: активные инциденты одной проблемы объединяются по цепочке и схлопываются в колонке."""
    from app.db import Board, SessionLocal
    import random
    base = json.loads((__import__("conftest").DEMO / "01_now.json").read_text(encoding="utf-8"))
    tpl = base[0]
    recs = []
    for i, h in enumerate(["Не приходят SMS-коды для входа"] * 3 + ["Отказ оплаты"]):
        r = dict(tpl, id=f"W{i}", header=h, stateId=0, state="Назначен", serviceId="S-W", productId="P-W",
                 description=h + " у абонентов", submitDate=f"2026-11-21T0{i}:00:00Z", solution=None)
        recs.append(r)
    path = __import__("conftest").DEMO.parent / "backend" / "tests" / "_waves.json"
    path.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    try:
        _run_fake_ai(client, monkeypatch, [], _same_header_answer())
        with open(path, "rb") as f:
            client.post("/api/snapshots", files={"file": ("_waves.json", f, "application/json")})
        import time
        for _ in range(200):
            v = client.get("/api/alerts/due").json()["ai"]
            if not v["running"] and v["queuedUploads"] == 0:
                break
            time.sleep(0.05)
    finally:
        path.unlink()
    card = client.get("/api/incidents/W1").json()
    assert card["calc"]["ai.waveSize"] == 3 and card["wave"]["size"] == 3
    assert [m["id"] for m in card["wave"]["members"]] == ["W0", "W1", "W2"]
    assert client.get("/api/incidents/W3").json()["calc"]["ai.waveSize"] is None
    # колонка со схлопыванием: одна карточка волны, остальные внутри
    b = client.post("/api/boards", json={"name": "Волны"}).json()
    col = client.post(f"/api/boards/{b['id']}/columns", json={"name": "Все", "rule": {"field": "id", "cmp": "starts_with", "value": "W"},
                                                               "groupSimilar": True, "sortField": "id"}).json()
    lay = [c for c in client.get(f"/api/boards/{b['id']}/layout").json()["columns"] if c["id"] == col["id"]][0]
    assert lay["total"] == 4 and len(lay["cards"]) == 2
    lead = lay["cards"][0]
    assert lead["id"] == "W0" and [x["id"] for x in lead["waveMembers"]] == ["W1", "W2"]
    presets = {p["id"]: p for p in client.get("/api/presets").json()}
    assert presets[16]["count"] == 3


def test_priority_underestimated(client, monkeypatch):
    ids = demo_ids()

    def prio(inc):
        return {"suggested": 4, "reason": "массовые жалобы"} if inc["id"] == ids["complaints"] else {"suggested": None, "reason": ""}

    _run_fake_ai(client, monkeypatch, ["01_now.json"], _same_header_answer(prio))
    c = client.get(f"/api/incidents/{ids['complaints']}").json()["calc"]
    assert c["ai.suggestedPriority"] == 4 and c["ai.priorityUnderestimated"] is True
    assert c["ai.priorityReason"] == "массовые жалобы"
    presets = {p["id"]: p for p in client.get("/api/presets").json()}
    assert presets[17]["count"] == 1
    from app.llm.pipeline import _priority
    assert _priority({"suggested": 2, "reason": "x"}, 3)["priorityUnderestimated"] is False  # завышенный не сигнал
    assert _priority({"suggested": 9, "reason": "x"}, 1)["suggestedPriority"] is None  # вне шкалы отбрасывается


def test_overdue_risk_from_similar_history(client, monkeypatch):
    """Риск просрочки: похожие случаи решались дольше, чем осталось до срока."""
    ids = demo_ids()
    _run_fake_ai(client, monkeypatch, ["00_history.json", "01_now.json", "02_new_critical.json",
                                       "03_reassign_and_resolve.json", "04_reopen_again.json",
                                       "05_complaints_growth.json", "06_disk_full_again.json"], _same_header_answer())
    # новый «диск заполнен»: цепочка решалась 25–70 мин; поставим «сейчас» за 10 минут до срока
    from app.fields import parse_dt
    from datetime import timedelta
    d = client.get(f"/api/incidents/{ids['disk_new']}").json()
    deadline = parse_dt(d["data"]["deadline"])
    set_now(client, (deadline - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"))
    c = client.get(f"/api/incidents/{ids['disk_new']}").json()["calc"]
    assert c["ai.overdueRisk"] is True and 25 <= c["ai.expectedResolveMin"] <= 70
    assert "найденным ИИ" in c["ai.overdueRiskReason"]
    # за сутки до срока риска нет
    set_now(client, (deadline - timedelta(hours=20)).isoformat().replace("+00:00", "Z"))
    assert client.get(f"/api/incidents/{ids['disk_new']}").json()["calc"]["ai.overdueRisk"] is False


def test_alerts_read_mark_and_unread_count(client):
    """Отметка «прочитано»: уменьшает счётчик непрочитанных, повторная отметка ничего не меняет, инциденты не трогает."""
    for f in FILES[:3]:
        upload(client, f)
    set_now(client)
    due = client.get("/api/alerts/due").json()
    body = client.get("/api/alerts").json()
    assert body["unread"] == due["unread"] and body["unread"] > 0
    unread_ids = [a["id"] for a in body["items"] if not a["read"]]
    first = unread_ids[:2]
    r = client.post("/api/alerts/read", json={"ids": first}).json()
    assert r["marked"] == len(first) and r["unread"] == body["unread"] - len(first)
    again = client.post("/api/alerts/read", json={"ids": first}).json()
    assert again["marked"] == 0 and again["unread"] == r["unread"]
    items = {a["id"]: a for a in client.get("/api/alerts").json()["items"]}
    assert all(items[i]["read"] and items[i]["readAt"] for i in first)
    assert client.post("/api/alerts/read", json={"ids": []}).json()["marked"] == 0


def test_popups_reach_every_open_browser(client):
    """Окно оповещения получает каждый открытый пульт, а не только тот, что загрузил файл и опросил первым."""
    ids = demo_ids()
    upload(client, "00_history.json")
    upload(client, "01_now.json")
    set_now(client)
    uploader, colleague, wall = Browser(client), Browser(client), Browser(client)
    upload(client, "02_new_critical.json")
    first = uploader.poll()  # загрузивший опросил сразу
    assert any(a["incidentId"] == ids["new_crit"] for a in first)
    for other in (colleague, wall):  # остальные — на своём следующем опросе
        assert any(a["incidentId"] == ids["new_crit"] for a in other.poll())
        assert not any(a["incidentId"] == ids["new_crit"] for a in other.poll())
    # первый опрос нового браузера старое не показывает
    assert client.get("/api/alerts/due").json()["due"] == []
