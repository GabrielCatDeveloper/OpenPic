"""Cuotas de uso por plan (analisis, mensajes y tamaño de foto).

Los limites viven en `Config.plans`; aqui se aplican. El dia se cuenta en la
zona horaria del servicio, no en UTC: si no, un usuario de UTC+2 veria como
"hoy" el dia anterior a partir de las 22:00.
"""

from __future__ import annotations

from dataclasses import dataclass

from foodbot.config import Config
from foodbot.core.db import Database
from foodbot.core.timeutil import day_key, resolve_tz


@dataclass
class LimitResult:
    allowed: bool
    limit: int = 0
    used: int = 0
    reason: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.limit > 0 and self.used >= self.limit


class LimitService:
    def __init__(
        self,
        db: Database,
        config: Config,
        timezone_name: str | None = None,
    ):
        self.db = db
        self.config = config
        self.tz = resolve_tz(timezone_name or config.timezone)

    # -- dias -------------------------------------------------------------
    def today_key(self) -> str:
        return day_key(None, self.tz)

    # -- uso --------------------------------------------------------------
    def usage(self, user_id: int, day: str | None = None) -> tuple[int, int]:
        key = day or self.today_key()
        return self.db.usage_today(user_id, key)

    def photos_used(self, user_id: int, day: str | None = None) -> int:
        return self.usage(user_id, day)[0]

    def messages_used(self, user_id: int, day: str | None = None) -> int:
        return self.usage(user_id, day)[1]

    # -- comprobaciones ----------------------------------------------------
    def check_photos(self, user_id: int, plan_name: str) -> LimitResult:
        plan = self.config.plan_for(plan_name)
        used = self.photos_used(user_id)
        limit = plan.daily_photo_limit
        if limit and used >= limit:
            return LimitResult(
                allowed=False,
                limit=limit,
                used=used,
                reason=(
                    f"Has alcanzado tu limite diario de {limit} analisis "
                    f"(plan {plan.name}). Se reinicia a medianoche."
                ),
            )
        return LimitResult(allowed=True, limit=limit, used=used)

    def check_messages(self, user_id: int, plan_name: str) -> LimitResult:
        plan = self.config.plan_for(plan_name)
        used = self.messages_used(user_id)
        limit = plan.daily_message_limit
        if limit and used >= limit:
            return LimitResult(
                allowed=False,
                limit=limit,
                used=used,
                reason=(
                    f"Has alcanzado tu limite diario de {limit} mensajes "
                    f"(plan {plan.name}). Se reinicia a medianoche."
                ),
            )
        return LimitResult(allowed=True, limit=limit, used=used)

    def check_photo_size(self, size_bytes: int | None) -> LimitResult:
        """Comprueba el tamano de la foto antes de descargarla del todo."""
        max_bytes = self.config.max_photo_mb * 1024 * 1024
        if size_bytes and size_bytes > max_bytes:
            mb = size_bytes / (1024 * 1024)
            return LimitResult(
                allowed=False,
                limit=max_bytes,
                used=size_bytes,
                reason=(
                    f"La foto ocupa {mb:.1f} MB y el limite es "
                    f"{self.config.max_photo_mb} MB. Envia una foto mas pequena."
                ),
            )
        return LimitResult(allowed=True, limit=max_bytes, used=size_bytes or 0)

    # -- contadores --------------------------------------------------------
    def bump_photo(self, user_id: int, day: str | None = None) -> None:
        self.db.bump_usage(user_id, day or self.today_key(), photos=1)

    def bump_message(self, user_id: int, day: str | None = None) -> None:
        self.db.bump_usage(user_id, day or self.today_key(), messages=1)

    def bump_photo_and_message(self, user_id: int, day: str | None = None) -> None:
        key = day or self.today_key()
        self.db.bump_usage(user_id, key, photos=1, messages=1)

    # -- resumen para la interfaz ------------------------------------------
    def snapshot(self, user_id: int, plan_name: str) -> dict[str, int]:
        plan = self.config.plan_for(plan_name)
        photos_used, messages_used = self.usage(user_id)
        return {
            "photos_used": photos_used,
            "photos_limit": plan.daily_photo_limit,
            "photos_left": max(0, plan.daily_photo_limit - photos_used),
            "messages_used": messages_used,
            "messages_limit": plan.daily_message_limit,
            "messages_left": max(0, plan.daily_message_limit - messages_used),
        }
