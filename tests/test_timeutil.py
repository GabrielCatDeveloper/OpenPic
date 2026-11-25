"""Tests de la zona horaria: el dia del usuario no es el dia de UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from foodbot.core.timeutil import (
    day_bounds,
    day_key,
    format_local_date,
    format_local_time,
    friendly_day,
    local_hour,
    meal_slot,
    parse_day,
    resolve_tz,
)

MADRID = resolve_tz("Europe/Madrid")
UTC = resolve_tz("UTC")


def test_invalid_timezone_falls_back_to_utc():
    assert resolve_tz("no/es/una-zona") is timezone.utc
    assert resolve_tz("") is timezone.utc
    assert resolve_tz(None) is timezone.utc


def test_valid_timezone_is_resolved():
    assert str(resolve_tz("Europe/Madrid")) == "Europe/Madrid"


def test_day_bounds_in_utc_starts_at_midnight_utc():
    start, end, key = day_bounds("2026-03-10", UTC)
    assert start == "2026-03-10T00:00:00+00:00"
    assert end == "2026-03-11T00:00:00+00:00"
    assert key == "2026-03-10"


def test_day_bounds_in_madrid_are_two_hours_earlier_in_utc():
    # Madrid es UTC+1 en invierno: el dia local empieza a las 23:00 UTC.
    start, end, key = day_bounds("2026-01-15", MADRID)
    assert start == "2026-01-14T23:00:00+00:00"
    assert end == "2026-01-15T23:00:00+00:00"
    assert key == "2026-01-15"


def test_day_bounds_end_is_exclusive():
    start, end, _ = day_bounds("2026-05-05", UTC)
    assert start < end
    assert (datetime.fromisoformat(end) - datetime.fromisoformat(start)) == timedelta(days=1)


def test_day_key_uses_the_local_day():
    # 23:30 UTC del dia 10 en Madrid ya es el dia 11.
    instante = datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc)
    assert day_key(instante, UTC) == "2026-01-10"
    assert day_key(instante, MADRID) == "2026-01-11"


def test_local_hour_follows_the_timezone():
    instante = datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc)
    assert local_hour(instante, UTC) == 23
    assert local_hour(instante, MADRID) == 0


def test_meal_slot_by_hour():
    momento = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "desayuno"
    momento = datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "comida"
    momento = datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "merienda"
    momento = datetime(2026, 1, 10, 20, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "cena"
    momento = datetime(2026, 1, 10, 23, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "snack"
    momento = datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)
    assert meal_slot(momento, UTC) == "snack"


def test_format_local_time_and_date():
    marca = "2026-01-10T23:30:00+00:00"
    assert format_local_time(marca, UTC) == "23:30"
    assert format_local_time(marca, MADRID) == "00:30"
    assert format_local_date(marca, UTC) == "10/01/2026"
    assert format_local_date(marca, MADRID) == "11/01/2026"


def test_format_handles_empty_or_broken_values():
    assert format_local_time(None) == "--:--"
    assert format_local_time("no-es-una-fecha") == "--:--"
    assert format_local_date(None) == "-"
    assert format_local_date("no-es-una-fecha") == "no-es-una-fecha"


def test_friendly_day_names():
    hoy = "2026-03-10"
    assert friendly_day("2026-03-10", hoy) == "Hoy"
    assert friendly_day("2026-03-09", hoy) == "Ayer"
    assert friendly_day("2026-03-07", hoy) == "Hace 3 dias"
    assert friendly_day("2026-03-01", hoy) == "01/03"


def test_parse_day_accepts_only_the_date_part():
    assert parse_day("2026-03-10") == datetime(2026, 3, 10)
    assert parse_day("2026-03-10T12:00:00") == datetime(2026, 3, 10)


def test_parse_day_rejects_garbage():
    with pytest.raises(ValueError):
        parse_day("ayer")
