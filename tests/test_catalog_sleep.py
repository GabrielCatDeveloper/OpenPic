# -*- coding: utf-8 -*-
"""Tests del catalogo de productos, las etiquetas y el sueño."""
from __future__ import annotations

import pytest

from foodbot.core.label import label_to_portion, parse_label, parse_weight
from foodbot.core.products import (
    describe,
    estimate_weight,
    find_product,
    parse_request,
)
from foodbot.core.sleep import (
    SleepNight,
    bedtime_regularity,
    late_snack_explanation,
    sleep_quality,
    sleep_summary,
)


# --- el catalogo ----------------------------------------------------------


def test_it_understands_three_magdalenas():
    request = parse_request("3 magdalenas del mercadona")
    assert request is not None
    assert request.quantity == 3
    assert request.name == "magdalenas"
    assert request.brand == "mercadona"


def test_it_understands_one_natilla():
    request = parse_request("1 natilla del dia")
    assert request.quantity == 1
    assert request.name == "natilla"
    assert request.brand == "dia"


def test_it_understands_words_and_fractions():
    assert parse_request("dos yogures").quantity == 2
    assert parse_request("media tortilla").quantity == 0.5
    assert parse_request("1/2 kg de arroz").quantity == 0.5


def test_the_catalog_finds_a_known_food():
    request = parse_request("2 huevos")
    match = find_product(request, [])
    assert match is not None
    assert match.kcal_100g > 0


def test_the_catalog_uses_the_users_own_products_first():
    request = parse_request("2 yogures")
    rows = [
        {
            "name": "yogur",
            "brand": "marca",
            "kcal_100g": 60.0,
            "protein_100g": 4.0,
            "carbs_100g": 6.0,
            "fat_100g": 1.0,
            "portion_g": 125.0,
        }
    ]
    match = find_product(request, rows)
    assert match is not None
    assert match.source == "producto"
    assert match.portion_g == 125.0


def test_the_calculation_uses_the_portion_weight():
    request = parse_request("2 yogures")
    rows = [
        {
            "name": "yogur",
            "brand": None,
            "kcal_100g": 60.0,
            "protein_100g": 4.0,
            "carbs_100g": 6.0,
            "fat_100g": 1.0,
            "portion_g": 125.0,
        }
    ]
    match = find_product(request, rows)
    info = describe(match, request)
    # 2 x 125 g = 250 g -> 250 g x 60 kcal/100 g = 150 kcal
    assert info["grams"] == 250.0
    assert info["calories"] == 150


def test_the_typical_unit_weights_are_known():
    assert estimate_weight(parse_request("3 magdalenas")) == 55
    assert estimate_weight(parse_request("2 huevos")) == 55


def test_an_unknown_product_is_not_invented():
    request = parse_request("3 ranas de chocolate")
    match = find_product(request, [])
    assert match is None or match.score < 3


# --- la etiqueta ----------------------------------------------------------


def test_it_reads_a_label_transcription():
    text = (
        "CALORIAS_100G: 450 kcal por 100 g\n"
        "PROTEINA_100G: 12 g por 100 g\n"
        "HIDRATOS_100G: 20 g por 100 g\n"
        "GRASA_100G: 15 g por 100 g\n"
        "PESO_NETO: 250 g"
    )
    data = parse_label(text)
    assert data.kcal_100g == 450
    assert data.protein_100g == 12
    assert data.net_weight_g == 250


def test_it_reads_a_label_in_another_wording():
    text = "Energía 450 kcal por 100 g. Proteínas 12 g. Hidratos 20 g. Grasas 15 g."
    data = parse_label(text)
    assert data.kcal_100g == 450
    assert data.protein_100g == 12


def test_the_calculation_scales_to_the_weight():
    text = "CALORIAS_100G: 450 kcal por 100 g\nPESO_NETO: 250 g"
    data = parse_label(text)
    portion = label_to_portion(data, 250)
    assert portion["calories"] == 1125


def test_a_label_without_data_is_detected():
    data = parse_label("No se ve la etiqueta")
    assert data.has_values is False


def test_the_weight_estimate_is_parsed():
    assert parse_weight("PESO_ESTIMADO: 250 g") == 250


# --- el sueño --------------------------------------------------------------


def noche(bed: str, wake: str) -> SleepNight:
    from datetime import datetime

    return SleepNight(
        bedtime=datetime.fromisoformat(bed), waketime=datetime.fromisoformat(wake)
    )


def test_the_sleep_hours_are_counted():
    night = noche("2026-11-25T23:30:00", "2026-11-26T07:15:00")
    assert night.hours == 7.75


def test_short_sleep_is_detected():
    assert sleep_quality(5.0) == "muy corta"
    assert sleep_quality(6.5) == "corta"
    assert sleep_quality(7.5) == "justa"


def test_the_summary_says_if_the_schedule_is_irregular():
    nights = [
        noche("2026-11-20T20:00:00", "2026-11-21T04:00:00"),
        noche("2026-11-21T23:30:00", "2026-11-22T07:30:00"),
        noche("2026-11-22T02:30:00", "2026-11-22T09:00:00"),
        noche("2026-11-23T21:00:00", "2026-11-24T05:00:00"),
    ]
    summary = sleep_summary(nights)
    assert summary["nights"] == 4
    assert summary["avg_hours"] > 7
    assert summary["irregular"] is True


def test_a_regular_schedule_is_not_flagged():
    nights = [
        noche("2026-11-20T23:00:00", "2026-11-21T07:00:00"),
        noche("2026-11-21T23:05:00", "2026-11-22T07:05:00"),
        noche("2026-11-22T23:10:00", "2026-11-23T07:10:00"),
    ]
    assert sleep_summary(nights)["irregular"] is False


def test_a_night_snack_is_explained_by_the_bedtime():
    from datetime import datetime

    meals = [{"eaten_at": datetime(2026, 11, 26, 1, 30).isoformat()}]
    nights = [noche("2026-11-25T02:30:00", "2026-11-25T10:00:00")]
    text = late_snack_explanation(meals, nights)
    assert text is not None
    assert "fisiología" in text


def test_a_night_snack_without_a_late_bedtime_is_not_explained():
    from datetime import datetime

    meals = [{"eaten_at": datetime(2026, 11, 26, 1, 30).isoformat()}]
    nights = [noche("2026-11-25T22:30:00", "2026-11-26T06:30:00")]
    assert late_snack_explanation(meals, nights) is None
