"""Вход по логину и паролю: без входа API закрыт, у каждого пользователя свои доски, правила и активная доска."""
from fastapi.testclient import TestClient


def other(client, login):
    c = TestClient(client.app)
    assert c.post("/api/auth/login", json={"login": login, "password": login}).status_code == 200
    return c


def test_api_closed_without_login(client):
    anon = TestClient(client.app)
    assert anon.get("/api/boards").status_code == 401
    assert anon.get("/api/health").status_code == 200
    r = anon.post("/api/auth/login", json={"login": "mts2", "password": "неверный"})
    assert r.status_code == 401 and "Неверный" in r.json()["detail"]["message"]


def test_each_user_has_own_boards(client):
    me = client.get("/api/auth/me").json()
    assert me["login"] == "mts1"
    mine = client.get("/api/boards").json()["boards"]
    u2 = other(client, "mts2")
    theirs = u2.get("/api/boards").json()
    assert theirs["boards"] and theirs["boards"][0]["name"] == "Фокус внимания"  # своя доска создаётся при первом входе
    assert {b["id"] for b in mine}.isdisjoint({b["id"] for b in theirs["boards"]})
    assert theirs["activeBoardId"] == theirs["boards"][0]["id"]
    # чужую доску не видно и не изменить
    foreign = mine[0]["id"]
    assert u2.get(f"/api/boards/{foreign}").status_code == 404
    assert u2.patch(f"/api/boards/{foreign}", json={"name": "взлом"}).status_code == 404
    assert u2.post(f"/api/boards/{foreign}/columns", json={"name": "x"}).status_code == 404
    # новая доска второго пользователя не появляется у первого
    nb = u2.post("/api/boards", json={"name": "Доска mts2"}).json()
    assert nb["id"] not in {b["id"] for b in client.get("/api/boards").json()["boards"]}


def test_custom_presets_are_private(client):
    p = client.post("/api/presets", json={"name": "Моё правило", "rule": {"field": "priorityId", "cmp": "eq", "value": 4}}).json()
    u3 = other(client, "mts3")
    assert p["id"] not in {x["id"] for x in u3.get("/api/presets?with_counts=false").json()}
    assert u3.delete(f"/api/presets/{p['id']}").status_code == 404


def test_logout(client):
    u4 = other(client, "mts4")
    assert u4.post("/api/auth/logout").status_code == 200
    assert u4.get("/api/boards").status_code == 401
