# -*- coding: utf-8 -*-
"""Tests del objetivo manual de calorias.

El bot calcula y explica; el usuario decide. La unica linea que el bot no
cruza es el suelo de seguridad (1200/1500/1400), respaldado por las guias
clinicas (Harvard Health; Alberta Health Services 2020).
"""
from __future__ import annotations

import pytest

from foodbot.core.nutrition import build_target


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


# --- el motor --------------------------------------------------------------


def test_the_manual_target_wins_even_when_the_goal_is_to_lose():
    """2400 de mantenimiento, quiero 2200: se respeta, aunque el bot sugiera
    un deficit mayor."""
    t = target(daily_kcal_override=2200)
    assert t.daily_kcal == 2200
    assert t.deficit_kcal == t.maintenance_kcal - 2200
    assert t.source == "formula"  # el fuente es el perfil, no el objetivo


def test_the_manual_target_sets_the_weekly_goal():
    t = target(daily_kcal_override=2200)
    # El ahorro semanal es el deficit diario multiplicado por 7.
    assert t.weekly_goal_kcal == (t.maintenance_kcal - 2200) * 7
    assert t.weekly_goal_kcal > 0


def test_the_manual_target_below_the_floor_is_clamped():
    t = target(daily_kcal_override=900)
    assert t.daily_kcal == 1500  # suelo de un hombre
    assert any("minimo" in note.lower() for note in t.notes)


def test_a_manual_target_above_maintenance_is_a_surplus():
    base = target()
    above = base.maintenance_kcal + 200
    t = target(daily_kcal_override=above)
    assert t.daily_kcal == above
    assert t.deficit_kcal < 0
    assert t.weekly_goal_kcal < 0
    assert any("por encima" in note for note in t.notes)


def test_the_notes_explain_what_the_manual_target_means():
    t = target(daily_kcal_override=2200)
    joined = " ".join(t.notes)
    assert "2200" in joined
    assert "mantenimiento" in joined


def test_an_aggressive_manual_target_is_warned_about_but_allowed():
    t = target(daily_kcal_override=1900)
    assert t.daily_kcal == 1900
    assert any("25%" in note for note in t.notes)


def test_without_a_manual_target_the_suggestion_is_used():
    t = target()
    assert t.daily_kcal == t.maintenance_kcal - round(t.weekly_goal_kcal / 7)
    assert t.daily_kcal < t.maintenance_kcal
