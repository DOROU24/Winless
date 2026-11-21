"""Загрузка снимка: разбор, нормализация, сравнение с прошлым состоянием, журнал событий."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import FieldCatalog, Incident, IncidentEvent, IncidentVersion, Snapshot, utcnow

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
MAX_LABELS = 500
MAX_VALUES = 150


class IngestError(Exception):
    pass


def parse_payload(raw: bytes) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise IngestError(f"Файл не похож на JSON: {e}") from e
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                warnings.append(f"Корень файла — объект; взят массив из ключа «{k}»")
                data = v
                break
        else:
            raise IngestError("В файле нет массива инцидентов")
    if not isinstance(data, list):
        raise IngestError("Корень файла должен быть массивом инцидентов")
    records = []
    skipped = 0
    for r in data:
        if isinstance(r, dict) and r.get("id") not in (None, ""):
            records.append(r)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"Пропущено записей без поля id: {skipped}")
    return records, warnings


def normalize(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and v.startswith("0001-01-01"):
            v = None
        out[k] = v
    out["id"] = str(out["id"])
    return out


def detect_type(values: list[Any]) -> str:
    vals = [v for v in values if v is not None and v != ""]
    if not vals:
        return "text"
    if all(isinstance(v, bool) for v in vals):
        return "flag"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number"
    if all(isinstance(v, str) and ISO_RE.match(v) for v in vals):
        return "date"
    return "text"


def update_catalog(s: Session, records: list[dict]) -> None:
    columns: dict[str, list] = {}
    for r in records:
        for k, v in r.items():
            columns.setdefault(k, []).append(v)
    existing = {r.name: r for r in s.execute(select(FieldCatalog)).scalars()}
    for name, vals in columns.items():
        typ = detect_type(vals)
        row = existing.get(name)
        if row is None:
            row = FieldCatalog(name=name, type=typ, labels={}, values=[])
            s.add(row)
        elif row.type != typ and any(v is not None for v in vals):
            # тип уточнился (например, раньше все значения были пустыми)
            if row.type == "text" and not row.values:
                row.type = typ
        # словарь «код → подпись»: пара xxxId + xxx
        if name.endswith("Id") and name[:-2] in columns:
            base = name[:-2]
            labels = dict(row.labels or {})
            for r in records:
                code, lab = r.get(name), r.get(base)
                if code is not None and lab not in (None, "") and len(labels) < MAX_LABELS:
                    labels[str(code)] = lab
            row.labels = labels
        if typ in ("text", "number"):
            distinct = list(dict.fromkeys(list(row.values or []) + [v for v in vals if v not in (None, "")
                                                                      and not isinstance(v, (dict, list))]))
            row.values = distinct[:MAX_VALUES] if len(distinct) <= MAX_VALUES else []


def ingest(s: Session, raw: bytes, filename: str) -> dict:
    records, warnings = parse_payload(raw)
    by_id: dict[str, dict] = {}
    for r in records:
        n = normalize(r)
        if n["id"] in by_id:
            warnings.append(f"Инцидент {n['id']} встречается в файле несколько раз; взята последняя запись")
        by_id[n["id"]] = n

    snap = Snapshot(filename=filename, count=len(by_id), uploaded_at=utcnow())
    s.add(snap)
    s.flush()

    existing = {i.id: i for i in s.execute(select(Incident)).scalars()}
    created, changed, disappeared, reappeared = [], [], [], []
    events: list[IncidentEvent] = []
    now = utcnow()

    for iid, rec in by_id.items():
        s.add(IncidentVersion(incident_id=iid, snapshot_id=snap.id, data=rec))
        inc = existing.get(iid)
        if inc is None:
            s.add(Incident(id=iid, data=rec, first_snapshot_id=snap.id, last_snapshot_id=snap.id, disappeared=False))
            events.append(IncidentEvent(incident_id=iid, snapshot_id=snap.id, type="created", at=now))
            created.append(iid)
            continue
        if inc.disappeared:
            events.append(IncidentEvent(incident_id=iid, snapshot_id=snap.id, type="reappeared", at=now))
            reappeared.append(iid)
        old = inc.data or {}
        diff = False
        for k in sorted(set(old) | set(rec)):
            if old.get(k) != rec.get(k):
                events.append(IncidentEvent(incident_id=iid, snapshot_id=snap.id, type="field_changed", field=k,
                                            old=old.get(k), new=rec.get(k), at=now))
                diff = True
        if diff:
            changed.append(iid)
            inc.data = rec
        inc.last_snapshot_id = snap.id
        inc.disappeared = False

    for iid, inc in existing.items():
        if iid not in by_id and not inc.disappeared:
            inc.disappeared = True
            events.append(IncidentEvent(incident_id=iid, snapshot_id=snap.id, type="disappeared", at=now))
            disappeared.append(iid)

    s.add_all(events)
    update_catalog(s, list(by_id.values()))
    snap.stats = {"created": len(created), "changed": len(changed), "disappeared": len(disappeared),
                  "reappeared": len(reappeared), "warnings": warnings}
    s.flush()
    return {
        "snapshotId": snap.id, "filename": filename, "count": len(by_id),
        "created": len(created), "changed": len(changed), "disappeared": len(disappeared),
        "reappeared": len(reappeared), "warnings": warnings,
        "createdIds": created[:50], "changedIds": changed[:50],
    }
