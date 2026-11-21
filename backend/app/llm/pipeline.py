"""Этап 2: сигналы ИИ — похожие инциденты, отписки в решении, подсказка команды.

Результаты кэшируются в ai_results по id инцидента и хэшу текстовых полей.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import AiResult, Incident, IncidentEvent, Link, SessionLocal, get_setting, utcnow
from .adapter import ask_json, is_configured

log = logging.getLogger("llm")
TEXT_FIELDS = ("header", "shortDescription", "description", "solution")
# Сколько инцидентов одной загрузки отдавать модели (защита от расходов) и сколько запросов слать параллельно.
MAX_PER_UPLOAD = int(os.environ.get("AI_MAX_PER_UPLOAD", "300"))
CONCURRENCY = int(os.environ.get("AI_CONCURRENCY", "4"))
MAX_CANDIDATES = 20
WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}")

SCHEMA = {
    "type": "object",
    "required": ["similar", "solution", "team"],
    "properties": {
        "similar": {"type": "array", "items": {
            "type": "object", "required": ["id", "relation", "explanation"],
            "properties": {"id": {"type": "string"}, "relation": {"type": "string",
                                                                  "enum": ["same", "symptom", "words"]},
                           "explanation": {"type": "string"}}}},
        "solution": {"type": "object", "required": ["weak", "reason"],
                     "properties": {"weak": {"type": ["boolean", "null"]}, "reason": {"type": "string"}}},
        "team": {"type": "object", "required": ["name", "reason"],
                 "properties": {"name": {"type": ["string", "null"]}, "reason": {"type": "string"},
                                "basedOn": {"type": "array", "items": {"type": "string"}}}},
        "facts": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = (
    "Ты помогаешь координатору массовых инцидентов. Тебе дают новый инцидент и кандидатов из истории. "
    "Для каждого кандидата реши: same — та же проблема; symptom — связанный симптом; words — просто похожие слова. "
    "В similar включай только same и symptom. Оцени текст решения (solution) нового инцидента: weak=true, если это "
    "отписка («ничего не делал», «само устранилось», «перезагрузил» без объяснения, что и почему); weak=null, если "
    "решения нет. Предложи команду по похожим прошлым инцидентам и таблице команд, укажи id, на которые опираешься. "
    "Отделяй факты (facts) от предположений (assumptions). Каждый вывод ссылается на конкретные id. "
    "Если похожих нет или они противоречат друг другу, так и пиши, ничего не выдумывай."
)


def text_hash(d: dict) -> str:
    return hashlib.sha256(json.dumps([d.get(f) for f in TEXT_FIELDS], ensure_ascii=False).encode()).hexdigest()


def _words(d: dict) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(f"{d.get('header') or ''} {d.get('description') or ''}")}


def _resolved(inc: Incident) -> bool:
    return inc.disappeared or inc.data.get("stateId") in (7, 10, 20)


def candidates(inc: Incident, pool: list[Incident]) -> list[Incident]:
    d = inc.data
    words = _words(d)
    scored = []
    for o in pool:
        if o.id == inc.id:
            continue
        od = o.data
        same_obj = (d.get("productId") and od.get("productId") == d.get("productId")) or \
                   (d.get("serviceId") and od.get("serviceId") == d.get("serviceId"))
        if not same_obj:
            continue
        overlap = len(words & _words(od))
        if overlap == 0:
            continue
        scored.append((overlap, o))
    scored.sort(key=lambda x: -x[0])
    return [o for _, o in scored[:MAX_CANDIDATES]]


def _brief(inc: Incident) -> dict:
    d = inc.data
    return {"id": inc.id, "header": d.get("header"), "description": d.get("description"), "state": d.get("state"),
            "solution": d.get("solution"), "team": d.get("assignedGroupName"), "submitDate": d.get("submitDate"),
            "resolved": _resolved(inc)}


def prepare(inc: Incident, pool: list[Incident], teams: list) -> tuple[str, dict]:
    """Запрос к модели и сведения о кандидатах. Работает с данными из базы, саму модель не вызывает."""
    cands = candidates(inc, pool)
    user = json.dumps({"incident": _brief(inc), "candidates": [_brief(c) for c in cands], "teams": teams},
                      ensure_ascii=False)
    meta = {c.id: {"resolved": _resolved(c), "active": c.data.get("stateId") in (0, 2) and not c.disappeared}
            for c in cands}
    return user, meta


def interpret(resp: dict, meta: dict, has_solution: bool = True) -> dict:
    """Ответ модели → поля ai.*. Ссылки только на переданных кандидатов: чужие id отбрасываются.

    Если у инцидента нет текста решения, оценивать нечего: «отписка» пустая, что бы ни ответила модель
    (gpt-4.1-mini иногда помечает отпиской инцидент без решения, хотя промпт просит null).
    """
    same = [x for x in resp["similar"] if x["relation"] == "same" and x["id"] in meta]
    past = [x for x in same if meta[x["id"]]["resolved"]]
    open_ = [x for x in same if meta[x["id"]]["active"]]
    return {
        "similarPastCount": len(past), "similarOpenCount": len(open_),
        "weakSolution": resp["solution"]["weak"] if has_solution else None,
        "weakSolutionReason": resp["solution"]["reason"] if has_solution else None,
        "suggestedTeam": resp["team"]["name"], "suggestedTeamReason": resp["team"]["reason"],
        "similar": [{**x, "resolved": meta[x["id"]]["resolved"]} for x in resp["similar"] if x["id"] in meta],
        "facts": resp.get("facts", []), "assumptions": resp.get("assumptions", []),
        "candidates": list(meta),
    }


def analyze(s: Session, inc: Incident, pool: list[Incident]) -> dict:
    user, meta = prepare(inc, pool, get_setting(s, "teams", []))
    return interpret(ask_json(SYSTEM, user, SCHEMA), meta, bool((inc.data.get("solution") or "").strip()))


def _targets(s: Session, snapshot_id: int) -> set[str]:
    out = set()
    for e in s.execute(select(IncidentEvent).where(IncidentEvent.snapshot_id == snapshot_id)).scalars():
        if e.type == "created" or (e.type == "field_changed" and e.field in TEXT_FIELDS):
            out.add(e.incident_id)
    return out


def _save(s: Session, iid: str, h: str, data: dict, err: str | None) -> None:
    row = s.get(AiResult, iid)
    if row is None:
        row = AiResult(incident_id=iid, text_hash=h)
        s.add(row)
    row.text_hash, row.data, row.error, row.updated_at = h, data, err, utcnow()
    s.execute(delete(Link).where(Link.source == "ai", Link.src == iid))
    for x in data.get("similar", []):
        if x["relation"] != "same":
            continue
        s.add(Link(src=iid, dst=x["id"], type="repeat" if x.get("resolved") else "double", directed=False,
                   source="ai", note=x.get("explanation", "")))


def process_snapshot(snapshot_id: int, progress: dict | None = None) -> dict:
    """Анализ инцидентов одной загрузки: подготовка из базы, параллельные запросы к модели, запись по мере готовности."""
    progress = progress if progress is not None else {}
    with SessionLocal() as s:
        pool = list(s.execute(select(Incident)).scalars())
        by_id = {i.id: i for i in pool}
        teams = get_setting(s, "teams", [])
        ids = [i for i in _targets(s, snapshot_id) if i in by_id]
        # инциденты, на которых анализ раньше упал (сбой сети, лимит), пробуем снова
        failed = set(s.execute(select(AiResult.incident_id).where(AiResult.error.is_not(None))).scalars())
        # и те, по которым анализа ещё не было (например, загружены до того, как появился ключ)
        analyzed = set(s.execute(select(AiResult.incident_id)).scalars())
        never = {i for i in by_id if i not in analyzed}
        ids += [i for i in (failed | never) if i not in ids]
        ids.sort(key=lambda i: (by_id[i].data.get("stateId") not in (0, 2), i))  # сначала активные
        skipped = max(0, len(ids) - MAX_PER_UPLOAD)
        jobs, cached = [], 0
        for iid in ids[:MAX_PER_UPLOAD]:
            h = text_hash(by_id[iid].data)
            row = s.get(AiResult, iid)
            if row is not None and row.text_hash == h and not row.error:
                cached += 1  # текст не менялся — модель повторно не вызываем
                continue
            has_solution = bool((by_id[iid].data.get("solution") or "").strip())
            jobs.append((iid, h, has_solution, *prepare(by_id[iid], pool, teams)))
    progress.update(total=len(jobs), done=0, errors=0, cached=cached, skipped=skipped)
    done = errors = 0
    if jobs:
        with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="llm") as ex, SessionLocal() as s:
            futures = {ex.submit(ask_json, SYSTEM, user, SCHEMA): (iid, h, sol, meta) for iid, h, sol, user, meta in jobs}
            for fut in as_completed(futures):
                iid, h, has_solution, meta = futures[fut]
                try:
                    data, err = interpret(fut.result(), meta, has_solution), None
                except Exception as e:  # невалидный ответ = сигнал не посчитан, ошибка в лог
                    log.warning("LLM: не удалось обработать %s: %s", iid, e)
                    data, err = {}, str(e)
                    errors += 1
                _save(s, iid, h, data, err)
                s.commit()  # пишем сразу: карточки обновляются по мере готовности
                done += 1
                progress.update(done=done, errors=errors)
    return {"processed": done, "cached": cached, "errors": errors, "skipped": skipped}


class AiWorker:
    """Один фоновый поток: загрузки анализируются по очереди, загрузка файла модель не ждёт."""

    def __init__(self) -> None:
        self.tasks: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.progress: dict = {}
        self.running = False
        self.last: dict | None = None

    def enqueue(self, snapshot_id: int) -> dict:
        if not is_configured():
            return {"enabled": False}
        self.tasks.put(snapshot_id)
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, name="ai-worker", daemon=True)
            self.thread.start()
        return {"enabled": True, "queued": True}

    def view(self) -> dict:
        return {"enabled": is_configured(), "running": self.running, "queuedUploads": self.tasks.qsize(),
                **{k: self.progress.get(k) for k in ("total", "done", "errors")}, "last": self.last}

    def _run(self) -> None:
        while True:
            try:
                sid = self.tasks.get(timeout=60)
            except queue.Empty:
                return
            self.running, self.progress = True, {}
            try:
                self.last = process_snapshot(sid, self.progress)
            except Exception:  # noqa: BLE001 - фоновый поток не должен падать
                log.exception("ai-worker: снимок %s", sid)
            finally:
                self.running = False


worker = AiWorker()
