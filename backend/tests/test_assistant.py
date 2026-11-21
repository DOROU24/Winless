"""Помощник координатора: инструменты, предложения, применение, подсказка команды, диалог с моделью."""
import os

from conftest import demo_ids, upload

FILES = ["00_history.json", "01_now.json", "02_new_critical.json", "03_reassign_and_resolve.json",
         "04_reopen_again.json"]
REOPEN = {"op": "and", "children": [
    {"field": "hist.transitions", "cmp": "gte", "value": 2, "args": {"field": "stateId", "from": [7, 10], "to": [0, 2]}},
    {"field": "calc.isActive", "cmp": "is_true"}]}


def load(client):
    os.environ["MTS_NOW"] = "2026-11-21T09:00:00Z"
    for f in FILES:
        upload(client, f)


def tools():
    from app.assistant import Tools
    from app.db import SessionLocal
    s = SessionLocal()
    return s, Tools(s)


def test_search_finds_reopened_without_creating_column(client):
    load(client)
    s, t = tools()
    before = len(client.get("/api/boards").json()["boards"][0]["columns"])
    r = t.call("search_incidents", {"rule": REOPEN})
    assert r["ok"] and demo_ids()["reopened"] in [x["id"] for x in r["items"]]
    assert "Завершен/Закрыт → Назначен/Выполняется" in r["ruleText"]
    assert len(client.get("/api/boards").json()["boards"][0]["columns"]) == before
    s.close()


def test_propose_and_apply_column(client):
    load(client)
    s, t = tools()
    bad = t.call("propose_change", {"kind": "create_column", "title": "x",
                                    "column": {"name": "x", "rule": {"field": "nope", "cmp": "eq", "value": 1}}})
    assert not bad["ok"] and "нет в реестре" in bad["problems"][0]
    ok = t.call("propose_change", {"kind": "create_column", "title": "Переоткрытые",
                                   "column": {"name": "Переоткрытые", "rule": REOPEN, "alert": "sound"}})
    assert ok["ok"] and ok["proposal"]["count"] >= 1
    p = t.proposals[0]
    s.close()
    r = client.post("/api/assistant/apply", json={"proposal": p})
    assert r.status_code == 200, r.text
    cols = client.get("/api/boards").json()["boards"][0]["columns"]
    assert cols[-1]["name"] == "Переоткрытые" and cols[-1]["alert"] == "sound" and cols[-1]["rule"] == REOPEN


def test_update_column_threshold_sound_and_sort(client):
    load(client)
    board = client.get("/api/boards").json()["boards"][0]
    col = board["columns"][0]
    new_alert = "popup" if col["alert"] == "sound" else "sound"
    s, t = tools()
    r = t.call("propose_change", {"kind": "update_column", "title": "Звук", "columnId": col["id"],
                                  "column": {"alert": new_alert, "sortField": "calc.deadlineLeftMin", "sortDir": "asc"}})
    assert r["ok"], r
    assert any("Оповещение" in c for c in r["proposal"]["changes"])
    p = t.proposals[0]
    s.close()
    assert client.post("/api/assistant/apply", json={"proposal": p}).status_code == 200
    c = client.get("/api/boards").json()["boards"][0]["columns"][0]
    assert c["alert"] == new_alert and c["sortField"] == "calc.deadlineLeftMin"


def test_create_board_with_assumptions(client):
    load(client)
    s, t = tools()
    r = t.call("propose_change", {"kind": "create_board", "title": "Массовые сбои", "assumptions": ["массовый — 3+"],
                                  "board": {"name": "Массовые сбои", "columns": [
                                      {"name": "Критичные", "rule": {"field": "priorityId", "cmp": "eq", "value": 4},
                                       "level": "critical", "alert": "sound"},
                                      {"name": "Переоткрытые", "rule": REOPEN}]}})
    assert r["ok"] and len(t.proposals[0]["columns"]) == 2
    p = t.proposals[0]
    s.close()
    res = client.post("/api/assistant/apply", json={"proposal": p}).json()
    b = client.get(f"/api/boards/{res['boardId']}").json()
    assert b["name"] == "Массовые сбои" and [c["name"] for c in b["columns"]] == ["Критичные", "Переоткрытые"]


def test_stage2_fields_refused_without_model(client):
    load(client)
    s, t = tools()
    r = t.call("propose_change", {"kind": "create_column", "title": "Отписки",
                                  "column": {"name": "Отписки", "rule": {"field": "ai.weakSolution", "cmp": "is_true"}}})
    assert not r["ok"] and "недоступно" in r["problems"][0]
    s.close()


def test_history_tools(client):
    load(client)
    s, t = tools()
    ch = t.call("field_change_stats", {"field": "assignedGroupName", "groupBy": "from"})
    assert ch["totalChanges"] >= 2 and ch["groups"] and demo_ids()["team_changes"] in ch["examples2plus"]
    rc = t.call("recent_changes", {})
    assert rc["lastUpload"]["file"] == "04_reopen_again.json" and rc["previousUpload"]
    ex = t.call("explain_incident", {"id": demo_ids()["new_crit"]})
    assert ex["incident"]["id"] == demo_ids()["new_crit"] and "onBoard" in ex
    tp = t.call("top_problems", {"days": 30, "groupBy": "service"})
    assert tp["groups"]
    s.close()


def test_suggest_team_teams_only_with_limits(client):
    load(client)
    s, t = tools()
    iid = demo_ids()["disk_chain"][-1]
    r = t.call("suggest_team", {"id": iid})
    assert len(r["limitations"]) == 2 and "ITSM" in r["note"]
    for row in r["teams"]:
        assert set(row) >= {"team", "similarResolved", "activeNow", "basis"}
        assert "employee" not in str(row).lower()
        if row["medianResolve"] is None:
            assert row["resolveNote"].startswith("мало данных")
    s.close()


def test_chat_runs_tool_loop(client, monkeypatch):
    load(client)
    import app.llm.adapter as adapter
    monkeypatch.setenv("LLM_API_KEY", "test")

    def fake_run_tools(system, history, tools_spec, call_tool, max_steps=8):
        assert "только для просмотра" in system and history[-1]["role"] == "user"
        call_tool("get_fields", {})
        call_tool("propose_change", {"kind": "create_column", "title": "Переоткрытые",
                                     "column": {"name": "Переоткрытые", "rule": REOPEN}})
        return {"text": "Предлагаю колонку «Переоткрытые».", "steps": [{"tool": "get_fields", "args": {}},
                                                                         {"tool": "propose_change", "args": {}}]}

    monkeypatch.setattr(adapter, "run_tools", fake_run_tools)
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Создай колонку"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"].startswith("Предлагаю") and body["proposals"][0]["kind"] == "create_column"
    assert [x["label"] for x in body["steps"]] == ["смотрю поля", "готовлю предложение"]


def test_chat_without_model_is_clear_error(client):
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "привет"}]})
    assert r.status_code == 400 and "LLM_API_KEY" in r.json()["detail"]["message"]
