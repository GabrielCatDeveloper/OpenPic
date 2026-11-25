"""Tests del modelo de mantenimiento + banco de ahorro semanal.

La idea: el mantenimiento diario es la referencia y lo que se come por debajo
se ahorra en el deficit de la semana. Un dia fuera de la raya no rompe nada.
"""

from __future__ import annotations

import pytest

from foodbot.core.nutrition import (
    build_target,
    compute_bmr,
    compute_bmr_detail,
    compute_tdee_detail,
    daily_saving,
    estimate_body_fat_pct,
    savings_bank,
)


def target(**kwargs):
    base = dict(
        sex="male",
        age=32,
        height_cm=178,
        weight_kg=80.0,
        activity="moderate",
        goal="lose",
        weekly_rate_kg=0.5,
    )
    base.update(kwargs)
    return build_target(**base)


# --- mantenimiento --------------------------------------------------------


def test_the_maintenance_is_the_tdee_by_default():
    t = target()
    assert t.maintenance_kcal == t.tdee
    assert t.source == "formula"
    assert t.daily_kcal == t.maintenance_kcal - t.deficit_kcal


def test_body_fat_uses_katch_mcardle():
    """Con % de grasa fiable se escala por masa libre, no por peso total."""
    bmr, formula = compute_bmr_detail("male", 80, 178, 32, body_fat_pct=15)
    assert "Katch" in formula
    assert bmr == pytest.approx(370 + 21.6 * 68.0, rel=0.01)
    # Sin dato: Mifflin-St Jeor (10*80 + 6.25*178 - 5*32 + 5).
    assert compute_bmr("male", 80, 178, 32) == pytest.approx(1757.5)


def test_an_impossible_body_fat_is_ignored():
    """Fuera de un rango plausible (báscula digital) se usa Mifflin-St Jeor."""
    _, formula = compute_bmr_detail("male", 80, 178, 32, body_fat_pct=3)
    assert "Mifflin" in formula
    _, formula = compute_bmr_detail("male", 80, 178, 32, body_fat_pct=70)
    assert "Mifflin" in formula
    # Una mujer con 4% de grasa: imposible, se descarta.
    _, formula = compute_bmr_detail("female", 60, 165, 30, body_fat_pct=4)
    assert "Mifflin" in formula


def test_the_maintenance_changes_with_steps_and_training():
    """Los datos reales corrigen el factor de actividad."""
    base = target()
    more_steps = target(daily_steps=14000)
    more_training = target(training_days=6)
    assert more_steps.maintenance_kcal > base.maintenance_kcal
    assert more_training.maintenance_kcal > base.maintenance_kcal


def test_a_physical_job_raises_the_maintenance():
    assert target(job_activity="physical").maintenance_kcal > target().maintenance_kcal


def test_a_manual_maintenance_wins_over_the_formula():
    t = target(maintenance_override=2500)
    assert t.maintenance_kcal == 2500
    assert t.source == "manual"


def test_the_calibrated_maintenance_wins_over_the_formula():
    t = target(calibrated_maintenance=2300)
    assert t.maintenance_kcal == 2300
    assert t.source == "calibrado"


def test_the_weekly_goal_is_the_kg_per_week_in_kcal():
    t = target(weekly_rate_kg=0.5)
    assert t.weekly_goal_kcal == 3500  # 0.5 kg * 7000 kcal/kg
    assert t.daily_kcal == t.maintenance_kcal - round(t.weekly_goal_kcal / 7)


def test_the_safety_floor_never_goes_below_1200():
    t = build_target(
        sex="female", age=55, height_cm=150, weight_kg=45, activity="sedentary",
        goal="lose", weekly_rate_kg=0.75,
    )
    assert t.daily_kcal >= 1200
    assert t.floor_kcal == 1200


def test_the_deficit_is_capped_at_25_percent_of_the_week():
    t = build_target(
        sex="female", age=25, height_cm=160, weight_kg=55, activity="light",
        goal="lose", weekly_rate_kg=1.5,
    )
    assert t.weekly_goal_kcal <= t.maintenance_kcal * 7 * 0.25 + 1
    assert any("25%" in note for note in t.notes)


# --- banco de ahorro ------------------------------------------------------


def test_the_bank_saves_what_you_eat_below_the_maintenance():
    t = target()
    bank = savings_bank(t, consumed_kcal=1400, logged_days=1, days_left=6)
    assert bank["saved"] == t.maintenance_kcal - 1400
    assert bank["saved"] > 0


def test_the_bank_pays_for_a_day_that_goes_over():
    """Un dia de 3.000 kcal con un mantenimiento de 2.400 es deuda, noSaved."""
    t = target()
    bank = savings_bank(t, consumed_kcal=3000, logged_days=1, days_left=6)
    assert bank["saved"] < 0
    assert bank["pending"] > t.weekly_goal_kcal


def test_the_remaining_days_cover_the_missing_savings():
    """Lo que falta se reparte entre los dias que quedan: ese es el margen."""
    t = target()
    bank = savings_bank(t, consumed_kcal=1400, logged_days=1, days_left=6)
    # 3850 de objetivo, se han ahorrado ~1000: faltan ~2850 en 6 dias.
    assert bank["daily_saving_needed"] == pytest.approx(round(bank["pending"] / 6), abs=1)
    assert bank["daily_target"] == t.maintenance_kcal - bank["daily_saving_needed"]


def test_a_day_over_the_maintenance_lows_the_allowance():
    """Si te pasas, los dias siguientes se ajustan (pero nunca bajo el suelo)."""
    t = target()
    calm = savings_bank(t, consumed_kcal=1500, logged_days=1, days_left=6)
    binge = savings_bank(t, consumed_kcal=3200, logged_days=1, days_left=6)
    assert binge["daily_target"] < calm["daily_target"]


def test_the_bank_never_proposes_eating_below_the_floor():
    t = target()
    bank = savings_bank(t, consumed_kcal=9000, logged_days=1, days_left=3)
    assert bank["daily_target"] == t.floor_kcal
    assert bank["over_budget"] is True
    assert "seguro" in bank["note"]
    assert str(bank["pending"]) in bank["note"]


def test_the_bank_never_proposes_eating_above_the_maintenance():
    t = target()
    bank = savings_bank(t, consumed_kcal=0, logged_days=1, days_left=6)
    assert bank["daily_target"] <= t.maintenance_kcal


def test_daily_saving_is_maintenance_minus_intake():
    assert daily_saving(2400, 1800) == 600
    assert daily_saving(2400, 3000) == -600


def test_estimated_body_fat_is_in_a_sane_range():
    value = estimate_body_fat_pct("male", 80, 178, 32)
    assert 5 <= value <= 40
    # Las mujeres tienen, de media, mas porcentaje de grasa que los hombres
    # con el mismo indice de masa corporal.
    man = estimate_body_fat_pct("male", 70, 175, 30)
    woman = estimate_body_fat_pct("female", 70, 175, 30)
    assert woman > man


def test_the_activity_detail_explains_the_calculation():
    _, detail = compute_tdee_detail(1780, "moderate", training_days=5, daily_steps=12000)
    assert detail["base_pal"] == 1.55
    assert detail["steps_delta"] == 12000 - 8500
    assert detail["training_delta"] == 5 - 3
    assert detail["pal"] > 1.55
