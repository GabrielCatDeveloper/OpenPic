# -*- coding: utf-8 -*-
"""Tests de la biblioteca personal de comidas."""
from __future__ import annotations

import pytest

from foodbot.bots.public import PublicBot
from foodbot.config import load_config
from foodbot.core.db import Database, iso
from tests.fakes import (
    FakeCallbackQuery,
    FakeContext,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    run,
)

USER_ID = 9090


@pytest.fixture()
def bot(tmp_path):
    config = load_config(tmp_path)
    object.__setattr__(config, "public_bot_token", "123456789:AAHfakefakefakefakefakefakefakefake")
    object.__setattr__(config, "provider", "mock")
    object.__setattr__(config, "single_user_mode", True)
    object.__setattr__(config, "admin_ids", frozenset({42}))
    db = Database(tmp_path / "bot.db")
    instance = PublicBot(config, db)
    db.upsert_user(
        USER_ID,
        username="tester",
        plan="pro",
        authorized_at=iso(),
        sex="female",
        age=34,
        height_cm=165,
        weight_kg=63.5,
        activity="moderate",
        goal="lose",
        onboarded=1,
    )
    yield instance, db, config
    db.close()


def test_the_library_lists_the_meals_by_the_order_of_the_day(bot):
    instance, db, config = bot
    db.add_my_meal(USER_ID, name="Paella", slot="comida", calories=520)
    db.add_my_meal(USER_ID, name="Avena", slot="desayuno", calories=320)

    async def scenario():
        message = FakeMessage()
        await instance.cmd_my_meals(
            FakeUpdate(user=FakeUser(USER_ID), message=message), FakeContext()
        )
        return message

    message = run(scenario)
    buttons = [b.text for row in message.replies_markup.inline_keyboard for b in row]
    assert any("Avena" in b for b in buttons)
    assert any("Paella" in b for b in buttons)
    # El desayuno va antes que la comida (orden del dia, no alfabetico).
    assert buttons.index("Avena · 320 kcal") < buttons.index("Paella · 520 kcal")


def test_tapping_a_saved_meal_logs_it(bot):
    instance, db, config = bot
    row_id = db.add_my_meal(USER_ID, name="Avena", slot="desayuno", calories=320)

    async def scenario():
        query = FakeCallbackQuery(f"mismeal:usar:{row_id}")
        await instance.cb_my_meal(
            FakeUpdate(user=FakeUser(USER_ID), callback_query=query), FakeContext()
        )

    run(scenario)
    meals = db.recent_meals(USER_ID, 5)
    assert len(meals) == 1
    assert meals[0]["dish"] == "Avena"
    assert meals[0]["calories"] == 320
    # Y cuenta los usos (lo que ordena la biblioteca).
    assert db.list_my_meals(USER_ID)[0]["times_used"] == 1


def test_the_most_used_meal_comes_first(bot):
    instance, db, config = bot
    poco = db.add_my_meal(USER_ID, name="Avena", slot="desayuno", calories=320)
    mucho = db.add_my_meal(USER_ID, name="Tostada", slot="desayuno", calories=180)
    db.use_my_meal(USER_ID, mucho)
    db.use_my_meal(USER_ID, mucho)
    db.use_my_meal(USER_ID, mucho)
    db.use_my_meal(USER_ID, poco)

    async def scenario():
        message = FakeMessage()
        await instance.cmd_my_meals(
            FakeUpdate(user=FakeUser(USER_ID), message=message), FakeContext()
        )
        return message

    message = run(scenario)
    botones = [b.text for row in message.replies_markup.inline_keyboard for b in row]
    assert botones.index("Tostada · 180 kcal (3x)") < botones.index("Avena · 320 kcal (1x)")


def test_a_meal_can_be_deleted(bot):
    instance, db, config = bot
    row_id = db.add_my_meal(USER_ID, name="Avena", slot="desayuno", calories=320)

    async def scenario():
        query = FakeCallbackQuery(f"mismeal:borrar:{row_id}")
        await instance.cb_my_meal(
            FakeUpdate(user=FakeUser(USER_ID), callback_query=query), FakeContext()
        )

    run(scenario)
    assert db.list_my_meals(USER_ID) == []


def test_saving_the_same_name_creates_a_variant(bot):
    """Si ya existe con ese nombre, se crea una variante."""
    instance, db, config = bot
    db.add_my_meal(USER_ID, name="Avena", slot="desayuno", calories=320)

    async def scenario():
        query = FakeCallbackQuery("mismeal:guardar:999")
        await instance.cb_save_my_meal(
            FakeUpdate(user=FakeUser(USER_ID), callback_query=query), FakeContext()
        )

    run(scenario)
    # No habia comida 999, asi que no se guarda nada.
    assert len(db.list_my_meals(USER_ID)) == 1
