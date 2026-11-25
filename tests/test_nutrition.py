"""Tests del motor nutricional."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from foodbot.core.nutrition import (
    build_target,
    compute_bmr,
    compute_tdee,
    eta_weeks,
    linear_trend,
    weight_trend,
)


def test_bmr_mifflin_st_jeor():
    # Hombre 30 años, 180 cm, 80 kg -> 10*80 + 6.25*180 - 5*30 + 5 = 1780
    assert round(compute_bmr("male", 80, 180, 30)) == 1780
    # Mujer 30 años, 165 cm, 60 kg -> 10*60 + 6.25*165 - 5*30 - 161 = 1320.25
    assert round(compute_bmr("female", 60, 165, 30)) == 1320
    # La mujer tiene siempre un BMR menor con los mismos parametros.
    assert compute_bmr("female", 80, 180, 30) < compute_bmr("male", 80, 180, 30)


def test_tdee_uses_activity_multiplier():
    bmr = 1790
    assert round(compute_tdee(bmr, "sedentary")) == round(bmr * 1.2)
    assert round(compute_tdee(bmr, "very_active")) == round(bmr * 1.9)


def test_weight_loss_target_applies_deficit():
    target = build_target(
        sex="male", age=30, height_cm=180, weight_kg=80, activity="sedentary",
        goal="lose", weekly_rate_kg=0.5,
    )
    assert target.deficit_kcal > 0
    assert target.daily_kcal == target.tdee - target.deficit_kcal
    assert target.daily_kcal < target.tdee


def test_deficit_is_capped_at_25_percent():
    """Un ritmo agresivo no puede comer mas del 25% del TDEE."""
    target = build_target(
        sex="female", age=25, height_cm=160, weight_kg=55, activity="light",
        goal="lose", weekly_rate_kg=1.5,
    )
    assert target.deficit_kcal <= target.tdee * 0.25 + 1
    assert any("25%" in note for note in target.notes)


def test_calorie_floor_is_applied():
    """Con un TDEE muy bajo nunca se baja de 1200 kcal (mujer)."""
    target = build_target(
        sex="female", age=55, height_cm=150, weight_kg=45, activity="sedentary",
        goal="lose", weekly_rate_kg=0.75,
    )
    assert target.daily_kcal >= 1200
    assert any("minimo" in note.lower() for note in target.notes)


def test_maintain_has_no_deficit():
    target = build_target(
        sex="male", age=30, height_cm=180, weight_kg=80, activity="moderate", goal="maintain"
    )
    assert target.deficit_kcal == 0
    assert target.daily_kcal == target.tdee


def test_gain_creates_surplus():
    target = build_target(
        sex="male", age=25, height_cm=175, weight_kg=70, activity="moderate", goal="gain"
    )
    assert target.deficit_kcal < 0
    assert target.daily_kcal > target.tdee


def test_macros_add_up_to_the_calorie_target():
    target = build_target(
        sex="male", age=30, height_cm=180, weight_kg=80, activity="moderate", goal="lose"
    )
    macro = target.macro
    total = macro.protein_g * 4 + macro.carbs_g * 4 + macro.fat_g * 9
    assert abs(total - macro.kcal) / macro.kcal < 0.02
    assert macro.protein_g == 144  # 80 kg * 1.8 g/kg


def test_linear_trend_detects_loss():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    points = [(base + timedelta(days=i), 80 - 0.1 * i) for i in range(10)]
    assert abs(linear_trend(points) * 7 - (-0.7)) < 0.05


def test_weight_trend_flags_plateau():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    points = [(base + timedelta(days=i), 80.0) for i in range(8)]
    trend = weight_trend(points, days=14)
    assert trend["weekly_rate"] == 0.0
    assert trend["plateau"] is True


def test_eta_weeks():
    assert eta_weeks(80, 75, 0.5) == 10.0
    assert eta_weeks(75, 80, 0.5) == 0.0
    assert eta_weeks(80, 75, 0) is None
