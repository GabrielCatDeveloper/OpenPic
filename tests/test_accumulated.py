# -*- coding: utf-8 -*-
"""Tests del acumulado semanal: cada semana suma o resta al total."""
from __future__ import annotations

from datetime import timedelta

import pytest

from foodbot.config import load_config
from foodbot.core.db import Database, iso, utcnow
from foodbot.core.timeutil import local_now, week_bounds
from foodbot.core.users import UserService

USER_ID = 777


@pytest.fixture()
def service(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.upsert_user(
        USER_ID,
        username="tester",
        plan="pro",
        authorized_at=iso(),
        sex="male",
        age=32,
        height_cm=178,
        weight_kg=80.0,
        activity="moderate",
        goal="lose",
        weekly_rate_kg=0.5,
        target_weight_kg=75.0,
    )
    yield UserService(db, "Europe/Madrid"), db
    db.close()


def add_meals(db, service, days_ago: int, count: int, kcal: int) -> None:
    """Registra 'count' comidas en los dias indicados hacia atras."""
    now = local_now(service.tz)
    for i in range(count):
        when = now - timedelta(days=days_ago + i)
        db.add_meal(
            user_id=USER_ID,
            created_at=iso(utcnow()),
            eaten_at=when.isoformat(),
            meal_slot="comida",
            dish="Prueba",
            calories=kcal,
            protein_g=90,
            carbs_g=160,
            fat_g=50,
            mass_g=350,
            ingredients="[]",
            raw_answer="",
            confidence=0.9,
            engine="food-r1",
        )


def test_a_week_saves_its_result_and_it_accumulates(service):
    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    # Dos semanas a 1000 kcal por debajo del mantenimiento.
    add_meals(db, svc, days_ago=7, count=3, kcal=maintenance - 1000)
    add_meals(db, svc, days_ago=14, count=3, kcal=maintenance - 1000)

    data = svc.accumulated_savings(USER_ID)
    # 3 dias x 1000 kcal de ahorro en cada semana.
    assert data["total"] == 6000
    assert data["weeks_counted"] == 2
    # 6.000 kcal con la regla de 7.000 kcal/kg.
    assert data["kg_equivalent"] == pytest.approx(0.86, abs=0.01)


def test_a_week_that_overshoots_subtracts_from_the_total(service):
    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    add_meals(db, svc, days_ago=7, count=3, kcal=maintenance - 1000)  # +3000
    add_meals(db, svc, days_ago=14, count=3, kcal=maintenance + 500)  # -1500

    data = svc.accumulated_savings(USER_ID)
    assert data["total"] == 3000 - 1500
    history = {h["week"]: h for h in data["history"]}
    assert any(h["saved"] < 0 for h in data["history"])


def test_the_history_carries_the_running_total(service):
    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    add_meals(db, svc, days_ago=7, count=2, kcal=maintenance - 1000)
    add_meals(db, svc, days_ago=14, count=2, kcal=maintenance - 1000)

    data = svc.accumulated_savings(USER_ID)
    closed = [h for h in data["history"] if not h["current"]]
    assert len(closed) == 2
    assert closed[0]["total"] == closed[0]["saved"]
    assert closed[1]["total"] == closed[0]["total"] + closed[1]["saved"]
    assert closed[-1]["total"] == data["total"]


def test_the_current_week_is_open_and_can_still_move(service):
    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    add_meals(db, svc, days_ago=0, count=1, kcal=maintenance - 1000)

    data = svc.accumulated_savings(USER_ID)
    current = [h for h in data["history"] if h["current"]]
    assert len(current) == 1
    assert current[0]["saved"] == 1000


def test_closing_a_week_twice_does_not_double_it(service):
    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    add_meals(db, svc, days_ago=7, count=2, kcal=maintenance - 1000)

    first = svc.accumulated_savings(USER_ID)
    second = svc.accumulated_savings(USER_ID)
    assert first["total"] == second["total"]
    assert len(db.list_week_savings(USER_ID)) == 1


def test_the_goal_is_the_kcal_to_the_target_weight(service):
    svc, db = service
    data = svc.accumulated_savings(USER_ID)
    # 80 kg actuales - 75 kg objetivo = 5 kg x 7000 kcal.
    assert data["goal_kcal"] == 5 * 7000


def test_without_a_target_weight_there_is_no_goal(service):
    svc, db = service
    db.touch_user(USER_ID, target_weight_kg=None)
    data = svc.accumulated_savings(USER_ID)
    assert data["goal_kcal"] is None


def test_a_week_without_records_is_not_counted(service):
    svc, db = service
    data = svc.accumulated_savings(USER_ID)
    assert data["total"] == 0
    assert db.list_week_savings(USER_ID) == []


def test_the_week_key_is_the_monday(service):
    svc, db = service
    _, _, week_key = week_bounds(None, svc.tz)
    assert week_key.endswith("-01") or True  # el lunes puede ser cualquier dia
    from datetime import date

    assert date.fromisoformat(week_key).weekday() == 0


def test_erasing_the_user_removes_the_weekly_savings(service):
    """Privacidad: el cierre semanal tambien es dato personal."""
    from foodbot.core.privacy import erase_user_data

    svc, db = service
    maintenance = svc.daily_target(USER_ID).maintenance_kcal
    add_meals(db, svc, days_ago=7, count=2, kcal=maintenance - 1000)
    svc.accumulated_savings(USER_ID)
    assert db.list_week_savings(USER_ID)

    counts = erase_user_data(db, USER_ID)
    assert counts["weekly_savings"] == 1
    assert db.list_week_savings(USER_ID) == []


def test_the_fat_equivalence_uses_7000_kcal_per_kg():
    """La regla del bot: 1 kg de grasa ~ 7.000 kcal."""
    from foodbot.core.nutrition import KCAL_PER_KG_FAT

    assert KCAL_PER_KG_FAT == 7000.0
