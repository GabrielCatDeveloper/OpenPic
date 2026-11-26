"""Tests del bot publico: flujos de usuario, cuotas y correcciones.

Se prueba con el proveedor `mock` (no hace falta GPU) y con dobles de los
objetos de Telegram.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from foodbot.bots.public import PublicBot
from foodbot.config import load_config
from foodbot.core.db import Database, iso, utcnow
from foodbot.core.tokens import TokenService
from tests.fakes import FakeContext, FakeMessage, FakeUpdate, FakeUser, run

FAKE_TOKEN = "123456789:AAHfakefakefakefakefakefakefakefakefake"
OWNER_ID = 42
USER_ID = 777


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


def register(db, user_id: int = USER_ID, plan: str = "pro") -> None:
    db.upsert_user(
        user_id,
        username="tester",
        first_name="Test",
        plan=plan,
        authorized_at=iso(),
    )


def photo_message(size: int = 1024) -> FakeMessage:
    photo = SimpleNamespace(file_id="file-1", file_size=size)
    return FakeMessage(photo=[photo])


# --- acceso -----------------------------------------------------------------


def test_start_asks_for_a_token_and_shows_the_user_id(bot):
    instance, db, config = bot

    async def scenario():
        message = FakeMessage(text="/start")
        update = FakeUpdate(user=FakeUser(USER_ID, "tester"), message=message)
        await instance.cmd_start(update, FakeContext())
        return message

    message = run(scenario)
    assert "NTR1" in message.last_reply
    assert str(USER_ID) in message.last_reply


def test_the_owner_is_activated_without_a_token(bot):
    instance, db, config = bot

    async def scenario():
        message = FakeMessage(text="/start")
        update = FakeUpdate(user=FakeUser(OWNER_ID, "owner"), message=message)
        await instance.cmd_start(update, FakeContext())
        return message

    message = run(scenario)
    assert db.get_user(OWNER_ID) is not None
    assert db.get_user(OWNER_ID)["authorized_at"] is not None
    assert db.get_user(OWNER_ID)["plan"] == config.owner_plan
    # Como no tiene perfil, el bot le pide completarlo.
    assert "Cuenta activada" in message.last_reply
    assert "/perfil" in message.last_reply


def test_a_valid_token_activates_the_account(bot):
    instance, db, config = bot
    tokens = TokenService(db, pepper=config.llm_api_key)
    issued = tokens.issue(plan="pro")

    async def scenario():
        message = FakeMessage(text=f"/token {issued.code}")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_token(update, FakeContext(args=[issued.code]))
        return message

    message = run(scenario)
    assert "Acceso activado" in message.last_reply
    assert db.get_user(USER_ID)["plan"] == "pro"


def test_a_token_bound_to_another_account_does_not_activate(bot):
    instance, db, config = bot
    tokens = TokenService(db, pepper=config.llm_api_key)
    issued = tokens.issue(plan="pro")
    tokens.redeem(issued.code, OWNER_ID)

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_token(update, FakeContext(args=[issued.code]))
        return message

    message = run(scenario)
    assert "no válido" in message.last_reply
    assert db.get_user(USER_ID) is None


def test_an_unknown_token_is_rejected(bot):
    instance, db, config = bot

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_token(update, FakeContext(args=["NTR1-AAAA-BBBB-CCCC"]))
        return message

    message = run(scenario)
    assert "no válido" in message.last_reply
    assert db.get_user(USER_ID) is None


# --- analisis de fotos -------------------------------------------------------


def test_a_photo_is_analyzed_and_stored(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    message = run(scenario)
    assert "kcal" in message.last_edit

    meals = db.recent_meals(USER_ID, 5)
    assert len(meals) == 1
    assert meals[0]["calories"] == 530
    assert meals[0]["dish"]
    assert db.usage_today(USER_ID, utcnow().strftime("%Y-%m-%d"))[0] == 1


def test_the_analysis_offers_correct_and_retry_buttons(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    run(scenario)
    meal_id = db.recent_meals(USER_ID, 1)[0]["id"]
    from foodbot.bots.public import meal_actions_keyboard

    markup = meal_actions_keyboard(meal_id)
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"corregir:{meal_id}" in data
    assert f"reintentar:{meal_id}" in data
    assert f"borrar:{meal_id}" in data


def test_a_too_big_photo_is_rejected_before_downloading(bot):
    instance, db, config = bot
    register(db)
    big = config.max_photo_mb * 1024 * 1024 + 1

    async def scenario():
        message = photo_message(size=big)
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    message = run(scenario)
    assert "MB" in message.last_reply
    assert db.recent_meals(USER_ID, 5) == []


def test_the_photo_quota_blocks_the_analysis(bot):
    instance, db, config = bot
    register(db, plan="free")
    limit = config.plans["free"].daily_photo_limit
    for _ in range(limit):
        instance.limits.bump_photo(USER_ID)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    message = run(scenario)
    assert "límite" in message.last_reply or "limite" in message.last_reply
    # No se analiza ni se guarda ninguna comida nueva.
    assert db.recent_meals(USER_ID, 5) == []
    assert db.usage_today(USER_ID, utcnow().strftime("%Y-%m-%d"))[0] == limit


# --- correcciones ------------------------------------------------------------


def test_the_calories_can_be_corrected(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        meal_id = db.recent_meals(USER_ID, 1)[0]["id"]
        correction = FakeMessage(text="420")
        update = FakeUpdate(user=FakeUser(USER_ID), message=correction)
        await instance.on_text(update, FakeContext(user_data={"await_kcal": meal_id}))
        return correction

    message = run(scenario)
    assert "420" in message.last_reply
    meal = db.recent_meals(USER_ID, 1)[0]
    assert meal["calories"] == 420
    assert meal["edited"] == 1


def test_a_user_cannot_correct_another_users_meal(bot):
    instance, db, config = bot
    register(db, USER_ID)
    register(db, 999, plan="free")

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        meal_id = db.recent_meals(USER_ID, 1)[0]["id"]

        correction = FakeMessage(text="1")
        update = FakeUpdate(user=FakeUser(999), message=correction)
        await instance.on_text(update, FakeContext(user_data={"await_kcal": meal_id}))
        return correction

    run(scenario)
    assert db.recent_meals(USER_ID, 1)[0]["calories"] == 530


def test_an_invalid_correction_is_ignored(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        meal_id = db.recent_meals(USER_ID, 1)[0]["id"]

        correction = FakeMessage(text="muchas")
        update = FakeUpdate(user=FakeUser(USER_ID), message=correction)
        await instance.on_text(update, FakeContext(user_data={"await_kcal": meal_id}))
        return correction

    message = run(scenario)
    assert "No he entendido" in message.last_reply
    assert db.recent_meals(USER_ID, 1)[0]["calories"] == 530


# --- consultas y privacidad ---------------------------------------------------


def test_today_shows_the_summary_and_the_remaining_quota(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        summary_message = FakeMessage(text="/hoy")
        update = FakeUpdate(user=FakeUser(USER_ID), message=summary_message)
        await instance.cmd_hoy(update, FakeContext())
        return summary_message

    message = run(scenario)
    assert "Tu día" in message.last_reply
    assert "530" in message.last_reply
    assert "análisis hoy" in message.last_reply


def test_the_history_can_be_filtered(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        history = FakeMessage(text="/historial 30")
        update = FakeUpdate(user=FakeUser(USER_ID), message=history)
        await instance.cmd_historial(update, FakeContext(args=["30"]))
        return history

    message = run(scenario)
    assert "Historial" in message.last_reply
    assert "30 días" in message.last_reply


def test_privacy_reports_what_is_stored(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        privacy = FakeMessage(text="/privacidad")
        update = FakeUpdate(user=FakeUser(USER_ID), message=privacy)
        await instance.cmd_privacy(update, FakeContext())
        return privacy

    message = run(scenario)
    assert "Tus datos" in message.last_reply
    assert "1 comidas" in message.last_reply
    assert "fotos se analizan en memoria" in message.last_reply


def test_forgetting_deletes_the_user_data(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        forget = FakeMessage(text="/olvidarme")
        update = FakeUpdate(user=FakeUser(USER_ID), message=forget)
        await instance.cmd_forget(update, FakeContext())
        return forget

    message = run(scenario)
    assert "Datos borrados" in message.last_reply
    assert db.get_user(USER_ID) is None
    assert db.recent_meals(USER_ID, 5) == []


# --- funciones inteligentes ---------------------------------------------------


def test_the_plan_is_cached_and_the_model_is_not_called_twice(bot):
    instance, db, config = bot
    register(db)
    db.upsert_user(
        USER_ID,
        plan="pro",
        authorized_at=iso(),
        sex="male",
        age=30,
        height_cm=180,
        weight_kg=80,
        activity="moderate",
        goal="lose",
    )
    calls = {"n": 0}
    original = instance.llm.make_plan

    async def counting_plan(**kwargs):
        calls["n"] += 1
        return await original(**kwargs)

    instance.llm.make_plan = counting_plan

    async def scenario():
        first = FakeMessage(text="/plan")
        update = FakeUpdate(user=FakeUser(USER_ID), message=first)
        await instance.cmd_plan(update, FakeContext())

        second = FakeMessage(text="/plan")
        update = FakeUpdate(user=FakeUser(USER_ID), message=second)
        await instance.cmd_plan(update, FakeContext())
        return (first, second)

    first, second = run(scenario)
    # El plan lo generan las reglas (foodbot/core/mealplan.py): el modelo no
    # se llama ni una vez, asi que tarda segundos y no se corta.
    assert calls["n"] == 0
    assert "Plan para el resto del día" in first.everything
    assert "Plan para el resto del día" in second.everything
    # El formato que se pide: "- COMIDA: plato | 480 kcal | P 32 g | C 45 g | G 18 g"
    assert "COMIDA:" in first.everything
    assert "kcal |" in first.everything


def test_the_advice_is_cached(bot):
    instance, db, config = bot
    register(db)
    db.upsert_user(
        USER_ID,
        plan="pro",
        authorized_at=iso(),
        sex="female",
        age=28,
        height_cm=165,
        weight_kg=60,
        activity="light",
        goal="lose",
    )
    calls = {"n": 0}
    original = instance.llm.make_advice

    async def counting(**kwargs):
        calls["n"] += 1
        return await original(**kwargs)

    instance.llm.make_advice = counting

    async def scenario():
        for _ in range(2):
            message = FakeMessage(text="/consejo")
            update = FakeUpdate(user=FakeUser(USER_ID), message=message)
            await instance.cmd_advice(update, FakeContext())

    run(scenario)
    assert calls["n"] == 1


def test_the_free_plan_cannot_use_the_plan_feature(bot):
    instance, db, config = bot
    register(db, plan="free")

    async def scenario():
        message = FakeMessage(text="/plan")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_plan(update, FakeContext())
        return message

    message = run(scenario)
    assert "plan Pro" in message.last_reply


# --- peso --------------------------------------------------------------------


def test_the_weight_is_registered_and_tracked(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="/peso 78.4")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_peso(update, FakeContext(args=["78.4"]))
        return message

    message = run(scenario)
    assert "78.4" in message.last_reply
    assert db.latest_weight(USER_ID) == 78.4


def test_an_invalid_weight_is_rejected(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = FakeMessage(text="/peso 900")
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.cmd_peso(update, FakeContext(args=["900"]))
        return message

    message = run(scenario)
    assert "no parece válido" in message.last_reply
    assert db.latest_weight(USER_ID) is None


def test_a_meal_can_be_deleted(bot):
    instance, db, config = bot
    register(db)

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())

        delete = FakeMessage(text="/borrar")
        update = FakeUpdate(user=FakeUser(USER_ID), message=delete)
        await instance.cmd_delete(update, FakeContext())
        return delete

    message = run(scenario)
    assert "Borrado" in message.last_reply
    assert db.recent_meals(USER_ID, 5) == []


# --- limpieza periodica ------------------------------------------------------


# --- pipeline: recibir -> guardar -> analizar -> enviar -> borrar -----------


def test_the_photo_pipeline_saves_analyses_sends_and_deletes(bot, tmp_path):
    """1. recibir · 2. guardar en disco · 3. modelo · 4. enviar · 5. borrar."""
    instance, db, config = bot
    register(db)

    from foodbot.core.privacy import PhotoStore

    store = PhotoStore(tmp_path / "fotos", enabled=True, ttl_hours=1)
    instance.photos = store

    seen: dict[str, object] = {}

    async def fake_analyze(image_bytes, mime="image/jpeg", note=None):
        # 3. Durante el analisis la foto TIENE que estar en el disco.
        files = list(store.directory.iterdir()) if store.directory.is_dir() else []
        seen["files_during_analysis"] = [p.name for p in files]
        seen["bytes_match_disk"] = bool(files) and files[0].read_bytes() == image_bytes
        return await original(image_bytes, mime, note)

    original = instance.llm.analyze_photo
    instance.llm.analyze_photo = fake_analyze

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    message = run(scenario)

    # 2. La foto se guardo en disco...
    assert seen["files_during_analysis"], "la foto no estaba en disco durante el analisis"
    assert seen["bytes_match_disk"] is True
    # 4. Se envio el resultado...
    assert "kcal" in message.last_edit
    assert db.recent_meals(USER_ID, 1)[0]["calories"] == 530
    # 5. Y la foto se elimino.
    assert list(store.directory.iterdir()) == []


def test_the_photo_is_deleted_even_if_the_model_fails(bot, tmp_path):
    instance, db, config = bot
    register(db)

    from foodbot.core.foodr1 import InferenceError
    from foodbot.core.privacy import PhotoStore

    store = PhotoStore(tmp_path / "fotos", enabled=True, ttl_hours=1)
    instance.photos = store

    async def boom(image_bytes, mime="image/jpeg", note=None):
        raise InferenceError("el motor esta caido")

    instance.llm.analyze_photo = boom

    async def scenario():
        message = photo_message()
        update = FakeUpdate(user=FakeUser(USER_ID), message=message)
        await instance.on_photo(update, FakeContext())
        return message

    message = run(scenario)
    assert "no ha respondido" in message.last_edit
    # La foto no se queda tirada por ahi.
    assert list(store.directory.iterdir()) == []
    assert db.recent_meals(USER_ID, 5) == []


def test_the_housekeeping_purges_photos_and_cache(bot, tmp_path):
    """Las fotos caducan aunque no llegue ninguna foto nueva."""
    instance, db, config = bot
    register(db)

    async def scenario():
        import os
        import time

        from foodbot.core.privacy import PhotoStore

        store = PhotoStore(tmp_path / "fotos", enabled=True, ttl_hours=1)
        path = store.save(USER_ID, b"foto")
        assert path is not None and path.exists()

        # Envejecemos el fichero mas alla del TTL.
        old = time.time() - 7200
        os.utime(path, (old, old))

        # La cache caducada tambien se limpia.
        db.set_cache("k", "v", expires_at="2000-01-01T00:00:00+00:00")

        instance.photos = store
        await instance._housekeeping(FakeContext())
        return path

    path = run(scenario)
    assert not path.exists()
    assert db.get_cache("k") is None
