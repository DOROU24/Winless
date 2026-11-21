"""Адаптер языковой модели (этап 2): одна функция «отправить запрос — получить JSON».

Провайдер, модель и ключ берутся из переменных окружения или файла .env в корне проекта:
  LLM_API_KEY  = ключ
  LLM_PROVIDER = openai | anthropic   (необязательно: по умолчанию определяется по ключу)
  LLM_MODEL    = имя модели           (по умолчанию gpt-4.1-mini для OpenAI)
  LLM_BASE_URL = необязательно, свой адрес API (для OpenAI-совместимых провайдеров)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.request

log = logging.getLogger("llm")
DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-sonnet-5-5"}


def is_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


def provider() -> str:
    """Явно заданный LLM_PROVIDER, иначе — по виду ключа: sk-ant-… это Anthropic, остальные sk-… это OpenAI."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return "anthropic" if os.environ.get("LLM_API_KEY", "").startswith("sk-ant-") else "openai"


def model() -> str:
    return os.environ.get("LLM_MODEL") or DEFAULT_MODELS.get(provider(), "gpt-4.1-mini")


class LLMError(RuntimeError):
    pass


ATTEMPTS = int(os.environ.get("LLM_ATTEMPTS", "4"))


def _post(url: str, headers: dict, body: dict, timeout=60) -> dict:
    """POST с повторами при сбоях связи, таймаутах, 429 и 5xx. Неверный ключ и ошибки запроса не повторяем."""
    data = json.dumps(body).encode()
    last = "нет попыток"
    for attempt in range(ATTEMPTS):
        req = urllib.request.Request(url, data=data, headers={**headers, "content-type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - адрес задаёт администратор
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            text = _explain_http(e.code, e.read().decode(errors="replace"))
            if e.code not in (408, 409, 429, 500, 502, 503, 504) or "квота" in text:
                raise LLMError(text) from None
            last = text
            retry_after = e.headers.get("retry-after") if e.headers else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = f"Нет связи с API модели: {e}"
            retry_after = None
        if attempt < ATTEMPTS - 1:
            try:
                delay = min(30.0, float(retry_after)) if retry_after else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
            time.sleep(delay * (0.75 + random.random() / 2))
    raise LLMError(f"{last} (после {ATTEMPTS} попыток)")


def _explain_http(code: int, body: str) -> str:
    """Понятный текст ошибки провайдера. Тело ответа ключа не содержит."""
    try:
        err = json.loads(body).get("error") or {}
        msg, kind = (err.get("message") or "", err.get("code") or err.get("type") or "") if isinstance(err, dict) else (str(err), "")
    except ValueError:
        msg, kind = body[:200], ""
    if code == 401:
        return "Ключ не принят (401): проверьте LLM_API_KEY"
    if code == 429 and "quota" in f"{kind} {msg}".lower():
        return "На аккаунте закончились средства или квота (429 insufficient_quota): пополните баланс OpenAI"
    if code == 429:
        return "Слишком много запросов (429): лимит провайдера, повторите позже"
    if code == 404 and "model" in msg.lower():
        return f"Модель не найдена или недоступна этому ключу (404): {model()}"
    return f"Ошибка API модели {code}: {msg[:200]}"


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
    prov = provider()
    key = os.environ["LLM_API_KEY"]
    instr = f"{system}\n\nОтвечай только JSON по схеме:\n{json.dumps(schema, ensure_ascii=False)}"
    if prov == "anthropic":
        base = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com")
        resp = _post(f"{base}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, {
            "model": model(), "max_tokens": 2000, "system": instr,
            "messages": [{"role": "user", "content": user}],
        })
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    else:
        base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        resp = _post(f"{base}/chat/completions", {"authorization": f"Bearer {key}"}, {
            "model": model(), "temperature": 0,
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
