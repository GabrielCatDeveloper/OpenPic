"""Tests de la tabla de ingredientes y del cierre contable de los macros."""

from __future__ import annotations

import pytest

from foodbot.core.foodr1 import parse_food_answer
from foodbot.data.ingredients import match_ingredient, normalize


def test_english_grams_unit_is_understood():
    """Food-R1 escribe '300 grams of rice'; la tabla debe entenderlo."""
    match = match_ingredient("rice", 300, "grams")
    assert match.grams == 300
    assert match.kcal > 350  # 300 g de arroz blanco cocido


def test_spanish_and_english_grams_agree():
    a = match_ingredient("rice", 300, "grams")
    b = match_ingredient("arroz", 300, "g")
    assert a.grams == b.grams
    assert a.kcal == b.kcal


def test_pieces_use_the_weight_per_piece():
    """'2 eggs' son 2 huevos, no 2 gramos."""
    match = match_ingredient("eggs", 2, None)
    assert match.grams == 100  # 2 x 50 g
    assert match.kcal > 100


def test_slices_and_cups_are_converted():
    assert match_ingredient("bread", 2, "slices").grams == 60
    assert match_ingredient("rice", 1, "cup").grams == 240


def test_bell_pepper_is_not_black_pepper():
    """'bell pepper' es pimiento (31 kcal/100 g), no pimienta (251)."""
    match = match_ingredient("bell pepper", 50, "grams")
    assert match.grams == 50
    assert match.kcal < 25
    assert match.canonical == "pimiento"


def test_aliases_resolve_to_the_spanish_table_key():
    assert match_ingredient("shrimp", 100, "g").canonical == "gamba"
    assert match_ingredient("chicken breast", 150, "g").canonical == "pechuga de pollo"


def test_unknown_ingredient_is_flagged_but_does_not_crash():
    match = match_ingredient("xyzzy rare dish", 50, "g")
    assert match.matched is False
    assert match.grams == 50


def test_macros_add_up_exactly_to_the_calories_shown():
    """Regla de oro del bot: 4P + 4C + 9G == kcal mostradas."""
    raw = (
        "<answer>The dish is Paella, and has 7 ingredients in total. "
        "They are: 300 grams of rice, 150 grams of shrimp, 100 grams of mussels, "
        "50 grams of peas, 50 grams of bell pepper, 50 grams of sausage, lemon. "
        "They have a total of 600 kcal.</answer>"
    )
    analysis = parse_food_answer(raw)
    from_macros = analysis.protein_g * 4 + analysis.carbs_g * 4 + analysis.fat_g * 9
    assert abs(from_macros - analysis.calories) < 2.0


def test_macros_add_up_for_a_different_dish():
    raw = (
        "<answer>The dish is Caesar salad, and has 5 ingredients in total. "
        "They are: 100g of lettuce, 100g of chicken breast, 30g of cheese, "
        "10g of olive oil, 20g of bread. They have a total of 420 kcal.</answer>"
    )
    analysis = parse_food_answer(raw)
    from_macros = analysis.protein_g * 4 + analysis.carbs_g * 4 + analysis.fat_g * 9
    assert abs(from_macros - analysis.calories) < 2.0


def test_normalize_strips_accents():
    assert normalize("Pimienta") == "pimienta"
    assert normalize("Limón") == "limon"
    assert normalize("  Sal & Pimienta  ") == "sal pimienta"


@pytest.mark.parametrize(
    "unit,expected",
    [("g", 1.0), ("grams", 1.0), ("kg", 1000.0), ("oz", 28.35), ("lb", 453.6)],
)
def test_unit_conversions(unit, expected):
    match = match_ingredient("rice", 2, unit)
    assert match.grams == pytest.approx(2 * expected, rel=0.01)
