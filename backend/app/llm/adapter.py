"""Адаптер языковой модели (этап 2): одна функция «отправить запрос — получить JSON».

Провайдер, модель и ключ берутся из переменных окружения:
  LLM_PROVIDER = anthropic | openai   (по умолчанию anthropic)
  LLM_MODEL    = имя модели
  LLM_API_KEY  = ключ
  LLM_BASE_URL = необязательно, свой адрес API (для OpenAI-совместимых провайдеров)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("llm")


def is_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


def _post(url: str, headers: dict, body: dict, timeout=60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers,
                                 "content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - адрес задаёт администратор
        return json.loads(r.read().decode())


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("в ответе модели нет JSON")
    return json.loads(text[start: max(text.rfind("}"), text.rfind("]")) + 1])


def ask_json(system: str, user: str, schema: dict) -> dict:
    """Отправить запрос и получить JSON, проверенный по упрощённой схеме. Исключение — если не удалось."""
    if not is_configured():
        raise RuntimeError("LLM_API_KEY не задан")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    key = os.environ["LLM_API_KEY"]
    instr = f"{system}\n\nОтвечай только JSON по схеме:\n{json.dumps(schema, ensure_ascii=False)}"
    if provider == "anthropic":
        base = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com")
        resp = _post(f"{base}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, {
            "model": os.environ.get("LLM_MODEL", "claude-sonnet-5-5"), "max_tokens": 2000, "system": instr,
            "messages": [{"role": "user", "content": user}],
        })
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    else:
        base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        resp = _post(f"{base}/chat/completions", {"authorization": f"Bearer {key}"}, {
            "model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            "messages": [{"role": "system", "content": instr}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        })
        text = resp["choices"][0]["message"]["content"]
    data = _extract_json(text)
    validate_schema(data, schema)
    return data


def validate_schema(data, schema: dict, path="$") -> None:
    """Небольшой валидатор JSON-схемы: type, properties, required, items, enum."""
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if t == "null" and data is not None:
        raise ValueError(f"{path}: ожидался null")
    if isinstance(t, list):
        if not any(data is None if x == "null" else isinstance(data, types[x]) for x in t):
            raise ValueError(f"{path}: неверный тип")
    elif t and t != "null" and not isinstance(data, types[t]):
        raise ValueError(f"{path}: ожидался {t}")
    if "enum" in schema and data not in schema["enum"]:
        raise ValueError(f"{path}: значение не из списка")
    if isinstance(data, dict):
        for r in schema.get("required", []):
            if r not in data:
                raise ValueError(f"{path}: нет поля {r}")
        for k, sub in schema.get("properties", {}).items():
            if k in data:
                validate_schema(data[k], sub, f"{path}.{k}")
    if isinstance(data, list) and "items" in schema:
        for i, x in enumerate(data):
            validate_schema(x, schema["items"], f"{path}[{i}]")
