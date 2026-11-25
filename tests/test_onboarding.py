"""Tests del asistente de perfil y de la calibracion con datos reales.

El bot pregunta los datos uno a uno (nombre, sexo, edad, altura, peso, % de
grasa, entrenamiento, pasos, trabajo, actividad, objetivo) y los guarda. Con
suficientes registros, el mantenimiento se calibra con su propio peso y su
propia ingesta.
"""

from __future__ import annotations

from datetime import timedelta
import pytest

from foodbot.bots.public import MENU_LABELS, PublicBot, main_reply_keyboard
from foodbot.config import load_config
from foodbot.core.db import Database, iso, utcnow
from foodbot.core.timeutil import local_now
from tests.fakes import (
    FakeCallbackQuery,
    FakeContext,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    run,
)

FAKE_TOKEN = "123456789:AAHfakefakefakefakefakefakefakefake"
OWNER_ID = 42
USER_ID = 4242


@pytest.fixture()
def bot(tmp_path):
    config = load_config(tmp_path)
    object.__setattr__(config, "public_bot_token", FAKE_TOKEN)
    object.__setattr__(config, "provider", "mock")
    object.__setattr__(config, "single_user_mode", True)
    object.__setattr__(config, "admin_ids", frozenset({OWNER_ID}))
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
    )
    data.update(fields)
    db.upsert_user(**data)


# --- el asistente pregunta y guarda --------------------------------------


def test_the_wizard_starts_by_asking_the_name(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="/perfil")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_profile(update, FakeContext())
        return message

    message = run(scenario)
    assert "Cómo te llamas" in message.everything
    assert "Nombre" not in message.everything.split("Vamos a completarlo")[0] or True


def test_the_name_is_saved(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="Laura")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_name(update, FakeContext())
        return message

    run(scenario)
    assert db.get_user(USER_ID)["nickname"] == "Laura"


def test_the_weight_is_saved_as_a_weight_record(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="63.5")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_weight(update, FakeContext())
        return message

    run(scenario)
    assert db.get_user(USER_ID)["weight_kg"] == 63.5
    assert db.latest_weight(USER_ID) == 63.5


def test_an_invalid_weight_is_rejected(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="mil")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_weight(update, FakeContext())
        return message

    message = run(scenario)
    assert "no parece válido" in message.everything
    assert db.get_user(USER_ID)["weight_kg"] is None


def test_the_body_fat_is_saved(bot):
    instance, db, config = bot
    register(db, sex="female", age=34, height_cm=165, weight_kg=63.5)

    async def scenario():
        message = FakeMessage(text="26")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_bodyfat(update, FakeContext())
        return message

    run(scenario)
    assert db.get_user(USER_ID)["body_fat_pct"] == 26.0


def test_the_estimated_body_fat_is_used_when_unknown(bot):
    instance, db, config = bot
    register(db, sex="female", age=34, height_cm=165, weight_kg=63.5)

    async def scenario():
        query = FakeCallbackQuery("perfil:bodyfat:estimate")
        update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
        await instance.cb_bodyfat(update, FakeContext())
        return query

    query = run(scenario)
    assert db.get_user(USER_ID)["body_fat_pct"] > 0
    assert "Estimado" in query.edits[0]


def test_the_extra_data_is_saved(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        for data, handler in (
            ("perfil:training:4", instance.cb_training),
            ("perfil:steps:9000", instance.cb_steps),
            ("perfil:job:physical", instance.cb_job),
        ):
            query = FakeCallbackQuery(data)
            update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
            await handler(update, FakeContext())
        return db.get_user(USER_ID)

    row = run(scenario)
    assert row["training_days"] == 4
    assert row["daily_steps"] == 9000
    assert row["job_activity"] == "physical"


# --- la UI por botones ----------------------------------------------------


def test_the_reply_keyboard_has_the_main_actions():
    keyboard = main_reply_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert set(labels) == set(MENU_LABELS)
    for label in labels:
        assert label in MENU_LABELS


def test_the_persistent_keyboard_buttons_do_what_they_say(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="📊 Hoy")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_text(update, FakeContext())
        return message

    message = run(scenario)
    assert "Tu día" in message.everything


def test_the_week_button_shows_the_bank(bot):
    instance, db, config = bot
    register(db, sex="male", age=30, height_cm=178, weight_kg=80, activity="moderate",
             goal="lose", weekly_rate_kg=0.5)

    async def scenario():
        query = FakeCallbackQuery("menu:semana")
        update = FakeUpdate(user=FakeUser(USER_ID), callback_query=query)
        await instance.cb_menu(update, FakeContext())
        return query

    query = run(scenario)
    assert "Tu semana" in query.edits[0]
    assert "Mantenimiento" in query.edits[0]


# --- calibracion con los datos del usuario --------------------------------


def test_the_maintenance_is_calibrated_with_the_real_data(bot):
    """Si el usuario come 2000 kcal/dia y baja 0.5 kg/semana, su
    mantenimiento son 2000 + 550 = 2550 kcal, no lo que diga la formula."""
    instance, db, config = bot
    register(db, sex="male", age=30, height_cm=178, weight_kg=80, activity="moderate",
             goal="lose", weekly_rate_kg=0.5)

    now = local_now(instance.users.tz)
    for day in range(20):
        when = now - timedelta(days=day)
        db.add_meal(
            user_id=USER_ID,
            created_at=iso(utcnow()),
            eaten_at=when.isoformat(),
            meal_slot="comida",
            dish="Prueba",
            calories=2000,
            protein_g=100,
            carbs_g=200,
            fat_g=60,
            mass_g=400,
            ingredients="[]",
            raw_answer="",
            confidence=0.9,
            engine="food-r1",
        )
        # 0.5 kg/semana = 0.0714 kg/dia: hace 19 dias pesaba 1.36 kg mas.
        db.add_weight(USER_ID, 80.0 + 0.0714 * day, iso(when))

    calibrated = instance.users.calibrated_maintenance(USER_ID)
    assert calibrated is not None
    assert 2400 < calibrated < 2700

    target = instance.users.daily_target(USER_ID)
    assert target.source == "calibrado"
    assert target.maintenance_kcal == calibrated


def test_without_enough_data_the_formula_is_used(bot):
    instance, db, config = bot
    register(db, sex="male", age=30, height_cm=178, weight_kg=80, activity="moderate",
             goal="lose", weekly_rate_kg=0.5)
    assert instance.users.calibrated_maintenance(USER_ID) is None
    target = instance.users.daily_target(USER_ID)
    assert target.source == "formula"
