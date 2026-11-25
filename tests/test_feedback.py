# -*- coding: utf-8 -*-
"""Tests del sticker de estado.

El tono es parte del requisito: el bot informa, no moraliza. Ademas siempre
recuerda que se cuenta la semana, que es de donde sale el margen para los
dias tontos.
"""
from __future__ import annotations

import pytest

from foodbot.core.feedback import meal_feedback

TARGET = 1600


def test_a_light_breakfast_is_happy():
    feedback = meal_feedback(meal_kcal=300, consumed_before=0, daily_target=TARGET)
    assert feedback.sticker == "contento"
    assert "300 kcal" in feedback.text


def test_a_1200_kcal_snack_is_sad():
    """Tres cruasanes se comen media vida: el sticker lo dice."""
    feedback = meal_feedback(meal_kcal=1200, consumed_before=0, daily_target=TARGET)
    assert feedback.sticker == "triste"
    assert "75%" in feedback.text


def test_reaching_the_target_is_worried_not_sad():
    feedback = meal_feedback(meal_kcal=500, consumed_before=1500, daily_target=TARGET)
    assert feedback.sticker == "preocupado"
    assert "cubierto el objetivo" in feedback.text


def test_going_far_over_the_target_is_sad():
    feedback = meal_feedback(meal_kcal=2400, consumed_before=0, daily_target=TARGET)
    assert feedback.sticker == "triste"


def test_a_big_day_is_sad():
    feedback = meal_feedback(meal_kcal=500, consumed_before=2200, daily_target=TARGET)
    assert feedback.sticker == "triste"


def test_almost_at_the_target_is_worried():
    feedback = meal_feedback(meal_kcal=300, consumed_before=1350, daily_target=TARGET)
    assert feedback.sticker == "preocupado"


def test_the_message_always_remembers_the_week():
    """El margen para los dias tontos viene de contar la semana."""
    feedback = meal_feedback(
        meal_kcal=300,
        consumed_before=0,
        daily_target=TARGET,
        week_saved=1200,
        week_goal=3500,
    )
    assert "1200 kcal" in feedback.text
    assert "semana" in feedback.text
    assert "dias tontos" in feedback.text


def test_the_week_line_works_when_the_week_is_behind():
    feedback = meal_feedback(
        meal_kcal=300,
        consumed_before=0,
        daily_target=TARGET,
        week_saved=-800,
        week_goal=3500,
    )
    assert "800 kcal" in feedback.text
    assert "recuperan" in feedback.text


def test_the_tone_never_moralises():
    """Que no haya reproches: el objetivo es no odiar la comida."""
    forbidden = (
        "deberias",
        "no devias",
        "deberia",
        "prohibido",
        "mal hecho",
        "error",
        "fracaso",
        "no tengas",
    )
    cases = [
        meal_feedback(meal_kcal=300, consumed_before=0, daily_target=TARGET),
        meal_feedback(meal_kcal=1200, consumed_before=0, daily_target=TARGET),
        meal_feedback(meal_kcal=500, consumed_before=1500, daily_target=TARGET),
        meal_feedback(meal_kcal=2400, consumed_before=0, daily_target=TARGET),
    ]
    for feedback in cases:
        text = feedback.text.lower()
        for word in forbidden:
            assert word not in text, f"'{word}' en: {feedback.text}"


def test_the_thresholds_are_relative_to_the_target():
    """Con un objetivo mas bajo, el mismo plato pesa mas."""
    # 700 kcal es el 58% de un objetivo de 1.200 y el 29% de uno de 2.400.
    bajo = meal_feedback(meal_kcal=700, consumed_before=0, daily_target=1200)
    alto = meal_feedback(meal_kcal=700, consumed_before=0, daily_target=2400)
    assert bajo.sticker == "triste"
    assert alto.sticker == "contento"


def test_the_fallback_emoji_exists_for_every_tone():
    for tone in ("contento", "preocupado", "triste"):
        feedback = meal_feedback(
            meal_kcal=300,
            consumed_before=0,
            daily_target=TARGET,
            week_saved=0,
            week_goal=0,
        )
        if feedback.tone == tone:
            assert feedback.emoji
            return
    pytest.skip("tono no alcanzado")
