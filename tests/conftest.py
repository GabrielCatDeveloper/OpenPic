"""Configuracion comun de los tests.

Los tests deben ser hermeticos: si el desarrollador tiene exportadas variables
tipo PUBLIC_BOT_TOKEN o TIMEZONE en la shell, no deben cambiar el
comportamiento de la suite.
"""

from __future__ import annotations

import pytest

APP_ENV_VARS = (
    "PUBLIC_BOT_TOKEN",
    "PUBLIC_BOT_USERNAME",
    "GATE_BOT_TOKEN",
    "ADMIN_TELEGRAM_IDS",
    "ADMIN_TELEGRAM_ID",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TIMEOUT",
    "LLM_MAX_TOKENS",
    "LLM_TEMPERATURE",
    "LLM_EXTRA_BODY",
    "LLM_CONCURRENCY",
    "DB_PATH",
    "PHOTOS_DIR",
    "MODELS_DIR",
    "LOGS_DIR",
    "MAX_PHOTO_MB",
    "TIMEZONE",
    "STORE_PHOTOS",
    "PHOTO_TTL_HOURS",
    "ALLOW_SELF_SIGNUP",
    "SINGLE_USER_MODE",
    "OWNER_PLAN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
