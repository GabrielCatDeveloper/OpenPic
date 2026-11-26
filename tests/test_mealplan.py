# -*- coding: utf-8 -*-
"""Tests del plan de comidas generado por reglas (no por el modelo)."""
from __future__ import annotations

from foodbot.core.mealplan import build_meal_plan
from foodbot.core.text import meal_plan_message


def plan_at(
    hour: float,
    eaten: int = 0,
    target: int = 1700,
    seed: str = "u|d",
    floor: int = 1200,
):
    return build_meal_plan(
        target_kcal=target,
        now_hour=hour,
        eaten_today=eaten,
        floor_kcal=floor,
        seed=seed,
    )


def test_the_plan_has_the_format_that_was_always_asked_for():
    plan = plan_at(8.0)
    text = meal_plan_message(plan)
    assert text
    for line in text.splitlines():
        assert line.startswith("- COMIDA: ")
        assert " kcal | P " in line
        assert " g | C " in line
        assert " g | G " in line


def test_the_plan_covers_only_the_meals_that_are_still_to_come():
    manana = plan_at(7.0)
    tarde = plan_at(16.0)
    assert len(manana) > len(tarde)
    # A las 21 solo queda la cena.
    assert len(plan_at(21.0)) == 1


def test_the_plan_adds_up_to_what_is_left():
    target = 1700
    eaten = 500
    plan = plan_at(8.0, eaten=eaten, target=target)
    total = sum(dish["kcal"] for dish in plan)
    remaining = target - eaten
    # Ni se pasa de la mitad del objetivo, ni se queda muy corto.
    assert total <= remaining * 1.25
    assert total >= remaining * 0.6


def test_the_safety_floor_is_daily_not_per_meal():
    """Si ya has comido mucho, el plan no baja del suelo de seguridad."""
    plan = plan_at(8.0, eaten=2400, target=1700, floor=1200)
    total = sum(dish["kcal"] for dish in plan)
    # El suelo es diario: con 2400 kcal ya encima, lo que queda son 1200 y
    # el plan los reparte entre las comidas que faltan.
    assert total >= 1200 * 0.9
    assert total <= 1200 * 1.3


def test_the_portions_are_realistic():
    plan = plan_at(8.0)
    for dish in plan:
        for item in dish["items"]:
            assert 10 <= item["grams"] <= 260, item


def test_two_days_do_not_give_the_same_plan():
    uno = meal_plan_message(plan_at(8.0, seed="555|2026-11-26"))
    otro = meal_plan_message(plan_at(8.0, seed="555|2026-11-27"))
    assert uno != otro


def test_the_same_day_gives_the_same_plan():
    uno = meal_plan_message(plan_at(8.0, seed="555|2026-11-26"))
    otro = meal_plan_message(plan_at(8.0, seed="555|2026-11-26"))
    assert uno == otro


def test_the_macros_come_from_the_table():
    """Los macros de un plato salen de la tabla de ingredientes."""
    from foodbot.data.ingredients import INGREDIENTS

    plan = plan_at(8.0)
    dish = plan[0]
    for item in dish["items"]:
        values = None
        for key, data in INGREDIENTS.items():
            if key.lower() == item["name"].lower():
                values = data
                break
        assert values is not None
        factor = item["grams"] / 100.0
        assert abs(item["kcal"] - round(values[0] * factor)) <= 1
