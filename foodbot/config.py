"""Configuracion central del proyecto.

Carga el fichero .env (sin dependencias externas) y expone un objeto `Config`
inmutable con todos los parametros de los dos bots y del motor de inferencia.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet

BASE_DIR = Path(__file__).resolve().parent.parent


def _read_env_file(path: Path) -> dict[str, str]:
    """Lee un .env muy simple y devuelve sus variables.

    No toca `os.environ`: las variables ya exportadas en la shell tienen
    prioridad, pero el fichero no se "contamina" globalmente (importante para
    que los tests sean hermeticos).
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Plan:
    """Limites y capacidades de un plan de acceso."""

    name: str
    label: str
    daily_photo_limit: int
    daily_message_limit: int
    features: FrozenSet[str]


@dataclass(frozen=True)
class Config:
    # --- rutas -----------------------------------------------------------
    base_dir: Path
    db_path: Path
    photos_dir: Path
    models_dir: Path
    logs_dir: Path

    # --- telegram --------------------------------------------------------
    public_bot_token: str
    gate_bot_token: str
    admin_ids: frozenset[int]

    # --- motor de vision (Food-R1) --------------------------------------
    provider: str  # openai_compat | ollama | mock
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    llm_max_tokens: int
    llm_temperature: float
    llm_extra_body: str  # JSON extra para el endpoint
    llm_concurrency: int  # inferencias simultaneas (1 = cola, ideal en local)

    # --- planes ----------------------------------------------------------
    plans: dict[str, Plan]

    # --- flags -----------------------------------------------------------
    allow_self_signup: bool
    max_photo_mb: int
    timezone: str  # zona IANA para agrupar los dias del usuario
    store_photos: bool  # si es False las fotos solo existen en memoria
    photo_ttl_hours: int  # caducidad de las copias locales de las fotos
    single_user_mode: bool  # solo el propietario puede usar el bot
    owner_plan: str  # plan con el que se activa el propietario
    public_bot_username: str  # @usuario del bot publico (para enlaces directos)

    @property
    def public_bot_configured(self) -> bool:
        return bool(self.public_bot_token) and not self.public_bot_token.startswith("PUT_")

    @property
    def gate_bot_configured(self) -> bool:
        return bool(self.gate_bot_token) and not self.gate_bot_token.startswith("PUT_")

    @property
    def owner_id(self) -> int | None:
        """El propietario del bot: el unico admin, en modo de un solo usuario."""
        if not self.admin_ids:
            return None
        return sorted(self.admin_ids)[0]

    def is_owner(self, user_id: int | None) -> bool:
        if user_id is None or not self.admin_ids:
            return False
        if self.single_user_mode:
            return user_id == self.owner_id
        return user_id in self.admin_ids

    def plan_for(self, name: str | None) -> Plan:
        return self.plans.get((name or "free").lower(), self.plans["free"])


def _build_plans() -> dict[str, Plan]:
    return {
        "free": Plan(
            name="free",
            label="Gratis",
            daily_photo_limit=15,
            daily_message_limit=200,
            features=frozenset({"analyze", "history", "weight"}),
        ),
        "pro": Plan(
            name="pro",
            label="Pro",
            daily_photo_limit=200,
            daily_message_limit=2000,
            features=frozenset(
                {"analyze", "history", "weight", "plan", "advice", "export"}
            ),
        ),
        "unlimited": Plan(
            name="unlimited",
            label="Ilimitado",
            daily_photo_limit=1000,
            daily_message_limit=10000,
            features=frozenset(
                {"analyze", "history", "weight", "plan", "advice", "export", "priority"}
            ),
        ),
    }


def load_config(base_dir: Path | None = None) -> Config:
    base = Path(base_dir) if base_dir else BASE_DIR
    file_env = _read_env_file(base / ".env")

    def _get(key: str, default: str = "") -> str:
        # Lo exportado en la shell manda sobre el fichero .env.
        if key in os.environ:
            return os.environ[key].strip()
        return file_env.get(key, default).strip()

    def _get_int(key: str, default: int) -> int:
        try:
            return int(_get(key, str(default)) or default)
        except ValueError:
            return default

    def _get_float(key: str, default: float) -> float:
        try:
            return float(_get(key, str(default)) or default)
        except ValueError:
            return default

    admin_raw = _get("ADMIN_TELEGRAM_IDS", "")
    admin_ids: frozenset[int] = frozenset(
        int(chunk) for chunk in admin_raw.replace(";", ",").split(",") if chunk.strip().isdigit()
    )

    models_dir = Path(_get("MODELS_DIR", str(base / "models"))).expanduser()

    return Config(
        base_dir=base,
        db_path=Path(_get("DB_PATH", str(base / "data" / "foodbot.db"))).expanduser(),
        photos_dir=Path(_get("PHOTOS_DIR", str(base / "data" / "photos"))).expanduser(),
        models_dir=models_dir,
        logs_dir=Path(_get("LOGS_DIR", str(base / "logs"))).expanduser(),
        public_bot_token=_get("PUBLIC_BOT_TOKEN"),
        gate_bot_token=_get("GATE_BOT_TOKEN"),
        admin_ids=admin_ids,
        provider=_get("LLM_PROVIDER", "openai_compat").lower(),
        llm_base_url=_get("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
        llm_api_key=_get("LLM_API_KEY", "sk-local"),
        llm_model=_get("LLM_MODEL", "food-r1"),
        llm_timeout=_get_float("LLM_TIMEOUT", 180.0),
        llm_max_tokens=_get_int("LLM_MAX_TOKENS", 512),
        llm_temperature=_get_float("LLM_TEMPERATURE", 0.1),
        llm_extra_body=_get("LLM_EXTRA_BODY", ""),
        llm_concurrency=max(1, _get_int("LLM_CONCURRENCY", 1)),
        plans=_build_plans(),
        allow_self_signup=_get("ALLOW_SELF_SIGNUP", "false").lower() in {"1", "true", "yes"},
        max_photo_mb=max(1, _get_int("MAX_PHOTO_MB", 12)),
        timezone=_get("TIMEZONE", "UTC"),
        store_photos=_get("STORE_PHOTOS", "true").lower() in {"1", "true", "yes"},
        photo_ttl_hours=max(0, _get_int("PHOTO_TTL_HOURS", 1)),
        single_user_mode=_get("SINGLE_USER_MODE", "true").lower() in {"1", "true", "yes"},
        owner_plan=_get("OWNER_PLAN", "unlimited").lower(),
        public_bot_username=_get("PUBLIC_BOT_USERNAME", "").lstrip("@"),
    )
