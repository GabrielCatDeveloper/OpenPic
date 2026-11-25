# -*- coding: utf-8 -*-
"""Tests del consejo especifico y del enfoque con personas trans.

La regla: los pronombres deciden el marco del consejo; el sexo del perfil
solo se usa para la ecuacion y el suelo de seguridad. Con tratamiento
hormonal se usa el punto medio de las referencias, porque no hay ecuaciones
especificas para personas trans (Frenser & Fischer, Int J Obes 2025).
"""
from __future__ import annotations

import pytest

from foodbot.core.advice import specific_tips
from foodbot.core.nutrition import build_target, physiology_reference


def profile(**kwargs):
    base = dict(
        sex="female",
        age=34,
        height_cm=165,
        weight_kg=63.5,
        activity="moderate",
        goal="lose",
        weekly_rate_kg=0.5,
        pronouns="female",
        hormone_therapy="none",
    )
    base.update(kwargs)
    return base


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


def joined(tips):
    return " ".join(tips)


# --- la referencia fisiologica ---------------------------------------------


def test_without_hormones_the_reference_is_the_profile_sex():
    assert physiology_reference("male", "none") == "male"
    assert physiology_reference("female", "none") == "female"
    assert physiology_reference("other", "none") == "midpoint"


def test_with_hormones_the_reference_is_the_midpoint():
    """No hay ecuaciones para personas trans: se usa el punto medio."""
    assert physiology_reference("male", "estrogen") == "midpoint"
    assert physiology_reference("female", "testosterone") == "midpoint"
    assert physiology_reference("other", "other") == "midpoint"


def test_the_midpoint_sits_between_the_two_references():
    male = build_target(sex="male", age=32, height_cm=178, weight_kg=80,
                        activity="moderate", goal="maintain")
    female = build_target(sex="female", age=32, height_cm=178, weight_kg=80,
                          activity="moderate", goal="maintain")
    mid = build_target(sex="male", age=32, height_cm=178, weight_kg=80,
                       activity="moderate", goal="maintain",
                       hormone_therapy="testosterone")
    assert female.bmr < mid.bmr < male.bmr


def test_the_floor_with_hormones_is_the_midpoint_one():
    t = build_target(sex="female", age=32, height_cm=165, weight_kg=63.5,
                     activity="moderate", goal="lose", weekly_rate_kg=0.5,
                     hormone_therapy="testosterone")
    assert t.floor_kcal == 1400


def test_the_body_fat_overrides_the_sex_in_the_formula():
    """Con composicion corporal conocida, la ecuacion no mira el sexo."""
    t = build_target(sex="male", age=32, height_cm=178, weight_kg=80,
                     activity="moderate", goal="maintain", body_fat_pct=20,
                     hormone_therapy="estrogen")
    assert "Katch" in t.breakdown["bmr_formula"]


# --- los consejos especificos ---------------------------------------------


def test_a_woman_gets_the_iron_advice():
    tips = specific_tips(profile(), target())
    text = joined(tips)
    assert "hierro" in text
    assert "18 mg" in text


def test_a_man_gets_the_muscle_advice_not_the_iron_one():
    tips = specific_tips(profile(sex="male", pronouns="male", age=34), target())
    text = joined(tips)
    assert "músculo" in text or "musculo" in text
    assert "18 mg" not in text


def test_the_pronouns_decide_the_frame_not_the_sex():
    """Una mujer trans (sexo masculino, pronombres femeninos) recibe el
    consejo de hierro, no el de musculo."""
    tips = specific_tips(
        profile(sex="male", pronouns="female", hormone_therapy="estrogen"),
        target(),
    )
    text = joined(tips)
    assert "hierro" in text
    assert "Tu musculo es lo que sostiene" not in text


def test_neutral_pronouns_get_neither_set():
    tips = specific_tips(profile(pronouns="neutral"), target())
    text = joined(tips)
    assert "18 mg" not in text
    assert "Tu musculo es lo que sostiene" not in text
    # Pero si recibe los consejos comunes.
    assert "proteina" in text.lower() or "proteína" in text.lower()


def test_trans_men_on_testosterone_get_the_body_composition_advice():
    tips = specific_tips(
        profile(sex="female", pronouns="male", hormone_therapy="testosterone"),
        target(),
    )
    text = joined(tips)
    assert "testosterona" in text
    assert "masa muscular" in text
    assert "hemoglobino" in text


def test_trans_women_on_estrogen_get_the_bone_advice():
    tips = specific_tips(
        profile(sex="male", pronouns="female", hormone_therapy="estrogen"),
        target(),
    )
    text = joined(tips)
    assert "estrogeno" in text or "estrógenos" in text
    assert "calcio" in text


def test_the_scale_advice_is_always_there():
    for prof in (
        profile(),
        profile(sex="male", pronouns="male"),
        profile(pronouns="neutral"),
    ):
        assert "media de dos semanas" in joined(specific_tips(prof, target()))


def test_the_protein_advice_appears_only_in_a_deficit():
    deficit = target(goal="lose")
    maintain = target(goal="maintain")
    assert "proteina" in joined(specific_tips(profile(), deficit)).lower()
    assert "proteina" not in joined(specific_tips(profile(), maintain)).lower()


def test_the_menopause_advice_appears_after_50():
    tips = specific_tips(profile(age=52), target())
    text = joined(tips)
    assert "menopausia" in text
    assert "calcio" in text
