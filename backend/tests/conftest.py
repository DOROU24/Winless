import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp()
os.environ["MTS_DB_PATH"] = str(Path(_tmp) / "test.sqlite3")
os.environ.pop("LLM_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo_data"


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.main import app
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


def upload(client, name):
    path = DEMO / name
    with open(path, "rb") as f:
        r = client.post("/api/snapshots", files={"file": (name, f, "application/json")})
    assert r.status_code == 200, r.text
    return r.json()


def demo_ids():
    import json
    return json.loads((DEMO / "_ids.json").read_text(encoding="utf-8"))
