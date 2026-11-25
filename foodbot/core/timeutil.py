"""Utilidades de tiempo con zona horaria configurable.

Los instantes se guardan siempre en UTC (ISO 8601), pero la agrupacion por
"dia del usuario" depende de su zona horaria. Sin esto, un usuario en UTC+2 ve
como "hoy" el dia anterior a partir de las 22:00 UTC, y su racha de dias se
rompe a diario.

La zona horaria se configura con la variable `TIMEZONE` (nombre IANA, p. ej.
`Europe/Madrid`). Si no es valida se usa UTC, nunca se rompe el bot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

# --- franjas horarias (hora local) -----------------------------------------
MEAL_SLOTS: tuple[tuple[int, int, str], ...] = (
    (5, 10, "desayuno"),
    (10, 15, "comida"),
    (15, 17, "merienda"),
    (17, 22, "cena"),
    (22, 24, "snack"),
    (0, 5, "snack"),
)

SLOT_EMOJI = {
    "desayuno": "☕",
    "comida": "🍽",
    "merienda": "🥨",
    "cena": "🌙",
    "snack": "🍎",
}


def resolve_tz(name: str | None) -> tzinfo:
    """Devuelve la zona horaria; si el nombre no es valido, UTC."""
    if not name:
        return UTC
    candidate = name.strip()
    if not candidate or candidate.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def local_now(tz: tzinfo = UTC) -> datetime:
    return datetime.now(tz)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: datetime | None = None) -> str:
    """Marca de tiempo ISO en UTC (formato estable para comparar en SQL)."""
    return to_utc(dt or datetime.now(UTC)).isoformat(timespec="seconds")


def parse_day(day: str) -> datetime:
    """'2026-11-25' -> datetime naive a medianoche."""
    return datetime.strptime(day.strip()[:10], "%Y-%m-%d")


def day_bounds(day: str | None = None, tz: tzinfo = UTC) -> tuple[str, str, str]:
    """Devuelve (inicio ISO UTC, fin ISO UTC, clave 'YYYY-MM-DD') del dia local.

    El limite superior es exclusivo: un meal con `eaten_at` exactamente en el
    `fin` pertenece al dia siguiente.
    """
    if day:
        base = parse_day(day).replace(tzinfo=tz)
    else:
        base = local_now(tz)
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        iso_utc(start),
        iso_utc(end),
        start.strftime("%Y-%m-%d"),
    )


def day_key(dt: datetime | None = None, tz: tzinfo = UTC) -> str:
    """Clave del dia local al que pertenece un instante."""
    moment = dt or datetime.now(UTC)
    return to_utc(moment).astimezone(tz).strftime("%Y-%m-%d")


def week_bounds(day: str | None = None, tz: tzinfo = UTC) -> tuple[str, str, str]:
    """Semana local (lunes 00:00 -> lunes siguiente 00:00).

    Devuelve (inicio ISO UTC, fin ISO UTC, clave 'YYYY-MM-DD' del lunes).
    """
    if day:
        base = parse_day(day).replace(tzinfo=tz)
    else:
        base = local_now(tz)
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=start.weekday())  # lunes de esa semana
    end = start + timedelta(days=7)
    return (
        iso_utc(start),
        iso_utc(end),
        start.strftime("%Y-%m-%d"),
    )


def week_progress(day: str | None = None, tz: tzinfo = UTC) -> tuple[str, int, int]:
    """(clave de la semana, dias transcurridos, dias que quedan).

    El dia de hoy cuenta como pendiente: quedan los dias que faltan para
    llegar al domingo, hoy incluido.
    """
    _, _, week_key = week_bounds(day, tz)
    start = parse_day(week_key)
    today = parse_day(local_now(tz).strftime("%Y-%m-%d"))
    elapsed = max(1, min(7, (today - start).days + 1))
    return week_key, elapsed, 8 - elapsed


def local_hour(dt: datetime | None = None, tz: tzinfo = UTC) -> int:
    moment = dt or datetime.now(UTC)
    return to_utc(moment).astimezone(tz).hour


def meal_slot(dt: datetime | None = None, tz: tzinfo = UTC) -> str:
    """Franja horaria del instante: desayuno, comida, merienda, cena o snack."""
    hour = local_hour(dt, tz)
    for start, end, name in MEAL_SLOTS:
        if start <= hour < end:
            return name
    return "snack"


def format_local_time(value: str | datetime | None, tz: tzinfo = UTC) -> str:
    """'2026-11-25T20:58:53+00:00' -> '21:58' en hora local."""
    if not value:
        return "--:--"
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return "--:--"
    else:
        moment = value
    return to_utc(moment).astimezone(tz).strftime("%H:%M")


def format_local_date(value: str | datetime | None, tz: tzinfo = UTC) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return value
    else:
        moment = value
    return to_utc(moment).astimezone(tz).strftime("%d/%m/%Y")


def friendly_day(key: str, today: str) -> str:
    """'Hoy' / 'Ayer' / 'hace 3 dias' a partir de una clave YYYY-MM-DD."""
    try:
        day = parse_day(key)
        today_day = parse_day(today)
    except ValueError:
        return key
    delta = (today_day - day).days
    if delta == 0:
        return "Hoy"
    if delta == 1:
        return "Ayer"
    if 1 < delta < 7:
        return f"Hace {delta} dias"
    if delta < 0:
        return "Mañana"
    return day.strftime("%d/%m")
