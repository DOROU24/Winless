"""Чтение настроек из файла .env (ключ модели и т. п.), без внешних зависимостей.

Ищем .env в корне проекта и в папке backend. Уже заданные переменные окружения не перезаписываются,
поэтому на сервере значения из окружения или docker-compose главнее файла.
Файл .env в git не попадает (см. .gitignore).
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
CANDIDATES = (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env")


def load_dotenv(paths=CANDIDATES) -> list[str]:
    loaded = []
    for path in paths:
        if not path.is_file():
            continue
        # utf-8-sig: PowerShell 5 пишет файлы с BOM
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        loaded.append(str(path))
    return loaded
