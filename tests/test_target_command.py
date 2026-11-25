# -*- coding: utf-8 -*-
"""Tests del comando /objetivo: el usuario fija sus propias kcal."""
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

FAKE_TOKEN = "123456789:AAHfakefakefakefakefakefakefakefake"
USER_ID = 5150


@pytest.fixture()
def bot(tmp_path):
    config = load_config(tmp_path)
    object.__setattr__(config, "public_bot_token", FAKE_TOKEN)
    object.__setattr__(config, "provider", "mock")
    object.__setattr__(config, "single_user_mode", True)
    object.__setattr__(config, "admin_ids", frozenset({42}))
    db = Database(tmp_path / "bot.db")
    instance = PublicBot(config, db)
    yield instance, db, config
    db.close()


def register(db, **fields):
    data = dict(
        user_id=USER_ID,
        username="tester",
        first_name="Test",
        plan="pro",
        authorized_at=iso(),
        sex="male",
        age=32,
        height_cm=178,
        weight_kg=80.0,
        activity="moderate",
        goal="lose",
        weekly_rate_kg=0.5,
    )
    data.update(fields)
    db.upsert_user(**data)


def test_the_target_info_explains_the_maintenance(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_objetivo(update, FakeContext())
        return message

    message = run(scenario)
    text = message.everything
    assert "Mantenimiento" in text
    assert "suelo de seguridad" in text


def test_a_manual_target_is_proposed_with_its_meaning(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_objetivo(update, FakeContext(args=["2200"]))
        return message

    message = run(scenario)
    text = message.everything
    assert "2200" in text
    assert "mantenimiento" in text
    assert "kg por semana" in text
    assert "¿Lo confirmas?" in text


def test_a_target_below_the_floor_is_refused(bot):
    """El suelo de seguridad es la unica linea que el bot no cruza."""
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_objetivo(update, FakeContext(args=["1000"]))
        return message

    message = run(scenario)
    text = message.everything
    assert "No te dejo bajar de 1500" in text
    assert "Harvard" in text
    assert db.get_user(USER_ID)["daily_kcal_override"] is None


def test_confirming_saves_the_target(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        query = FakeCallbackQuery("objetivo:si:2200")
        update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
        await instance.cb_objetivo(update, FakeContext())
        return query

    query = run(scenario)
    assert db.get_user(USER_ID)["daily_kcal_override"] == 2200
    assert "2200" in query.edits[-1]


def test_cancelling_saves_nothing(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        query = FakeCallbackQuery("objetivo:no")
        update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
        await instance.cb_objetivo(update, FakeContext())
        return query

    run(scenario)
    assert db.get_user(USER_ID)["daily_kcal_override"] is None


def test_going_back_to_the_automatic_target(bot):
    instance, db, config = bot
    register(db, daily_kcal_override=2200)

    async def scenario():
        query = FakeCallbackQuery("objetivo:auto")
        update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
        await instance.cb_objetivo(update, FakeContext())
        return query

    run(scenario)
    assert db.get_user(USER_ID)["daily_kcal_override"] is None


def test_the_manual_target_changes_the_daily_goal(bot):
    """Con 2200 fijados, el objetivo diario pasa a ser 2200 (no el automatico)."""
    instance, db, config = bot
    register(db, daily_kcal_override=2200)
    target = instance.users.daily_target(USER_ID)
    assert target.daily_kcal == 2200
    assert target.deficit_kcal == target.maintenance_kcal - 2200


def test_the_weekly_bank_follows_the_manual_target(bot):
    instance, db, config = bot
    register(db, daily_kcal_override=2200)
    bank = instance.users.week_summary(USER_ID)["bank"]
    assert bank["goal"] == (bank["maintenance"] - 2200) * 7
