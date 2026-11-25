"""Tests del formato de los mensajes (lo que el usuario ve en Telegram)."""

from __future__ import annotations

from foodbot.core.nutrition import build_target
from foodbot.core.text import (
    analysis_message,
    esc,
    history_message,
    privacy_message,
    progress_bar,
    progress_message,
    target_line,
    today_message,
)
from foodbot.core.timeutil import resolve_tz

MADRID = resolve_tz("Europe/Madrid")


def test_esc_escapes_html():
    assert esc("<b>hola</b>") == "&lt;b&gt;hola&lt;/b&gt;"
    assert esc("a & b") == "a &amp; b"
    assert esc(None) == ""


def test_progress_bar_fills_proportionally():
    assert progress_bar(0, 100) == "▱" * 14
    assert progress_bar(100, 100) == "▰" * 14
    assert progress_bar(50, 100).count("▰") == 7


def test_progress_bar_marks_the_overrun():
    bar = progress_bar(150, 100)
    assert bar.endswith("!")


def test_progress_bar_with_no_target():
    assert progress_bar(100, 0) == "▱" * 14


def test_analysis_message_contains_the_essentials():
    from foodbot.core.foodr1 import parse_food_answer

    raw = (
        "<answer>The dish is Paella, and has 3 ingredients in total. "
        "They are: 150g of rice, 100g of chicken breast, 10g of olive oil. "
        "They have a total of 520 kcal.</answer>"
    )
    text = analysis_message(parse_food_answer(raw))
    assert "Paella" in text
    assert "520 kcal" in text
    assert "Ingredientes detectados" in text
    assert "Confianza" in text
    # Se ofrece corregir las cifras.
    assert "Corregir" in text


def test_today_message_without_a_target():
    text = today_message({"calories": 0, "meals": [], "target": None})
    assert "/perfil" in text
    assert "Todavía no has registrado" in text


def test_today_message_with_a_target_and_quota():
    target = build_target(
        sex="male",
        age=30,
        height_cm=180,
        weight_kg=80,
        activity="moderate",
        goal="lose",
    )
    summary = {
        "calories": 500,
        "protein_g": 30,
        "carbs_g": 60,
        "fat_g": 15,
        "meals": [
            {"eaten_at": "2026-03-10T13:00:00+00:00", "dish": "Paella", "calories": 500,
             "edited": 0}
        ],
        "target": target,
        "remaining": target.daily_kcal - 500,
    }
    quota = {"photos_left": 7}
    text = today_message(summary, MADRID, quota)
    assert "500" in text
    assert "Te quedan" in text
    assert "Paella" in text
    assert "7 análisis hoy" in text


def test_history_message_uses_friendly_day_names():
    stats = {
        "days": 7,
        "today": "2026-03-10",
        "meals": 2,
        "total_calories": 900,
        "avg_calories": 450,
        "avg_per_day": 128,
        "active_days": 2,
        "by_day": {
            "2026-03-10": {"calories": 500, "meals": 1, "protein_g": 30},
            "2026-03-09": {"calories": 400, "meals": 1, "protein_g": 25},
        },
    }
    text = history_message(stats, MADRID)
    assert "Hoy" in text
    assert "Ayer" in text
    assert "450" in text


def test_progress_message_without_data():
    text = progress_message({"current": None, "trend": {}, "target": None})
    assert "/peso" in text


def test_target_line_without_a_target():
    assert "/perfil" in target_line(None)


def test_privacy_message_says_that_photos_are_not_stored():
    text = privacy_message({"meals": 3, "weights": 2, "targets": 1, "usage": 1, "cache": 0})
    assert "3 comidas" in text
    assert "en memoria" in text
    assert "Borrar todos mis datos" in text
