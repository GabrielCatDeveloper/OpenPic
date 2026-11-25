"""Tests del parser de respuestas de Food-R1."""

from __future__ import annotations

from foodbot.core.foodr1 import extract_answer, parse_food_answer, parse_ingredient_item

# Respuesta con el formato exacto que produce el modelo (ver eval del repo oficial).
REAL_ANSWER = (
    "<answer>The dish is Paella de gambas, and has 8 ingredients in total. "
    "They are: 200g of rice, 150g of shrimp, 100g of tomato, 50g of onion, "
    "10g of olive oil, 1g of salt, 50g of paprika, 100ml of broth. "
    "They have a total of 620 kcal.</answer>"
)


def test_extract_answer():
    assert extract_answer(REAL_ANSWER) == REAL_ANSWER[len("<answer>") : -len("</answer>")]


def test_parse_full_answer():
    analysis = parse_food_answer(REAL_ANSWER)
    assert analysis.dish == "Paella de gambas"
    assert analysis.calories == 620
    assert analysis.parsed is True
    assert len(analysis.ingredients) == 8
    assert analysis.confidence > 0.6


def test_ingredients_are_matched_against_the_table():
    analysis = parse_food_answer(REAL_ANSWER)
    names = [i.name for i in analysis.ingredients]
    assert "rice" in names
    assert "olive oil" in names

    oil = next(i for i in analysis.ingredients if i.name == "olive oil")
    assert oil.unit == "g"
    assert oil.quantity == 10
    assert oil.grams == 10
    # 10 g de aceite de oliva = 884 kcal/100g * 10/100 = 88.4 kcal
    assert 88 < oil.kcal < 89.5


def test_macros_are_coherent_with_calories():
    """Los macros deben sumar aproximadamente las calorías mostradas."""
    analysis = parse_food_answer(REAL_ANSWER)
    from_macros = analysis.protein_g * 4 + analysis.carbs_g * 4 + analysis.fat_g * 9
    assert abs(from_macros - analysis.calories) / analysis.calories < 0.12


def test_parse_ingredient_item_variants():
    assert parse_ingredient_item("150g of chicken breast")[0] == "chicken breast"
    assert parse_ingredient_item("2 slices of bread")[1] == 2.0
    assert parse_ingredient_item("olive oil")[0] == "olive oil"
    assert parse_ingredient_item("olive oil")[1] is None


def test_broken_answer_does_not_crash():
    analysis = parse_food_answer("Lo siento, no puedo identificar el plato.")
    assert analysis.calories == 0
    assert analysis.parsed is False
    assert analysis.confidence < 0.3
    assert analysis.warnings


def test_answer_without_tags_is_still_parsed():
    analysis = parse_food_answer(
        "The dish is Ensalada, and has 3 ingredients in total. "
        "They are: lettuce, tomato, olive oil. They have a total of 150 kcal."
    )
    assert analysis.dish == "Ensalada"
    assert analysis.calories == 150
    assert analysis.parsed is False  # sin <answer> no es parseo "formal"


def test_implausible_calories_lower_confidence():
    analysis = parse_food_answer(
        "<answer>The dish is X, and has 1 ingredients in total. "
        "They are: rice. They have a total of 9000 kcal.</answer>"
    )
    assert analysis.confidence < 0.8
    assert any("sobestimado" in w for w in analysis.warnings)
