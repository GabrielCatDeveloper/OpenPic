"""Tests del cliente de inferencia: cola de peticiones y proveedor mock."""

from __future__ import annotations

import asyncio

import pytest

from foodbot.config import load_config
from foodbot.core.foodr1 import FoodR1Client, parse_food_answer


@pytest.fixture()
def config(tmp_path):
    cfg = load_config(tmp_path)
    object.__setattr__(cfg, "provider", "mock")
    return cfg


def test_the_mock_provider_returns_a_parsable_answer(config):
    client = FoodR1Client(config)
    analysis = parse_food_answer(client._mock_response("test"))
    assert analysis.calories > 0
    assert analysis.dish
    assert analysis.ingredients


def test_analyze_photo_parses_the_mock_answer(config):
    client = FoodR1Client(config)

    async def scenario():
        return await client.analyze_photo(b"\xff\xd8\xff" + b"0" * 64, "image/jpeg")

    analysis = asyncio.run(scenario())
    assert analysis.calories == 530
    assert analysis.engine == config.llm_model
    assert analysis.confidence > 0


def test_health_reports_the_mock_as_available(config):
    client = FoodR1Client(config)

    async def scenario():
        return await client.health()

    ok, detail = asyncio.run(scenario())
    assert ok
    assert "mock" in detail


def test_inferences_are_serialized_with_a_single_slot(config):
    """Con LLM_CONCURRENCY=1 solo hay una peticion viva a la vez."""
    object.__setattr__(config, "provider", "openai_compat")
    object.__setattr__(config, "llm_concurrency", 1)
    client = FoodR1Client(config)
    active = 0
    peak = 0

    async def fake(system, prompt, image, mime, max_tokens):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    client._openai_compat = fake

    async def scenario():
        await asyncio.gather(
            client.complete("s", "p1"),
            client.complete("s", "p2"),
            client.complete("s", "p3"),
        )

    asyncio.run(scenario())
    assert peak == 1


def test_inferences_can_run_in_parallel_with_more_slots(config):
    object.__setattr__(config, "provider", "openai_compat")
    object.__setattr__(config, "llm_concurrency", 3)
    client = FoodR1Client(config)
    active = 0
    peak = 0

    async def fake(system, prompt, image, mime, max_tokens):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    client._openai_compat = fake

    async def scenario():
        await asyncio.gather(
            client.complete("s", "p1"),
            client.complete("s", "p2"),
            client.complete("s", "p3"),
        )

    asyncio.run(scenario())
    assert peak == 3


def test_the_prompt_of_the_plan_can_be_formatted(config):
    """El prompt del plan lleva llaves literales: si no se escapan, revienta."""
    from foodbot.core.foodr1 import PLAN_PROMPT

    rendered = PLAN_PROMPT.format(
        profile="Hombre, 30 anos",
        kcal=2200,
        protein=140.0,
        carbs=250.0,
        fat=70.0,
        eaten=500,
        week="Me quedan 3000 kcal para 4 dias. ",
    )
    assert "{calories}" in rendered
    assert "2200" in rendered
    assert "Hombre, 30 anos" in rendered
    # El contexto semanal (deficit semanal) llega al prompt.
    assert "3000 kcal" in rendered


def test_the_prompt_of_the_plan_works_without_the_weekly_context(config):
    """Sin datos de la semana el prompt tambien se formatea."""
    from foodbot.core.foodr1 import PLAN_PROMPT

    rendered = PLAN_PROMPT.format(
        profile="Hombre, 30 anos",
        kcal=2200,
        protein=140.0,
        carbs=250.0,
        fat=70.0,
        eaten=500,
        week="",
    )
    assert "Objetivo diario: 2200 kcal" in rendered


def test_the_prompt_of_the_advice_can_be_formatted(config):
    from foodbot.core.foodr1 import ADVICE_PROMPT

    rendered = ADVICE_PROMPT.format(
        profile="Hombre, 30 anos",
        goal="Perder peso",
        eaten=500,
        kcal=2200,
        protein=140.0,
        carbs=250.0,
        fat=70.0,
    )
    assert "Perder peso" in rendered
    assert "2200" in rendered
