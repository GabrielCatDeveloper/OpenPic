"""Tests del bot privado (generador de tokens).

Clave: en SINGLE_USER_MODE solo el proprietario puede usarlo; el resto recibe
"este es un bot de un solo usuario" y NO se emite ningun token.
"""

from __future__ import annotations

import pytest

from foodbot.bots.gate import GateBot, is_admin
from foodbot.config import load_config
from foodbot.core.db import Database, iso
from tests.fakes import FakeBot, FakeContext, FakeMessage, FakeUpdate, FakeUser, run

FAKE_TOKEN = "123456789:AAHfakefakefakefakefakefakefakefakefake"
OWNER_ID = 42
FRIEND_ID = 777


@pytest.fixture()
def gate(tmp_path):
    config = load_config(tmp_path)
    object.__setattr__(config, "gate_bot_token", FAKE_TOKEN)
    object.__setattr__(config, "admin_ids", frozenset({OWNER_ID}))
    object.__setattr__(config, "single_user_mode", True)
    db = Database(tmp_path / "gate.db")
    instance = GateBot(config, db)
    instance.app.bot = FakeBot()
    yield instance, db, config
    db.close()


def call(instance, handler, user_id: int, args: list[str] | None = None):
    """Ejecuta un handler del panel y devuelve el mensaje."""

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(user_id, "user"), message=message)
        await handler(update, FakeContext(args=args or []))
        return message

    return run(scenario)


# --- control de acceso -------------------------------------------------------


def test_only_the_owner_is_an_admin(gate):
    instance, db, config = gate

    def check(user_id: int) -> bool:
        update = FakeUpdate(user=FakeUser(user_id), message=FakeMessage())
        return is_admin(update, config)

    assert check(OWNER_ID) is True
    assert check(FRIEND_ID) is False


def test_a_stranger_is_told_it_is_a_single_user_bot(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_start, FRIEND_ID)
    assert "un solo usuario" in message.last_reply


def test_a_stranger_cannot_issue_a_token(gate):
    instance, db, config = gate
    call(instance, instance.cmd_token, FRIEND_ID)
    assert db.scalar("SELECT COUNT(*) FROM tokens", default=0) == 0


def test_a_stranger_cannot_see_the_users(gate):
    instance, db, config = gate
    db.upsert_user(1, plan="pro", authorized_at=iso())
    message = call(instance, instance.cmd_users, FRIEND_ID)
    assert "un solo usuario" in message.last_reply
    assert "Usuarios" not in message.last_reply


def test_the_owner_can_issue_a_token(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_token, OWNER_ID, ["pro", "30", "laura"])
    assert "Token creado" in message.last_reply
    assert db.scalar("SELECT COUNT(*) FROM tokens", default=0) == 1
    row = db.query_one("SELECT * FROM tokens")
    assert row["plan"] == "pro"
    assert "laura" in row["label"]


def test_with_multiple_admins_any_of_them_can_use_it(tmp_path):
    config = load_config(tmp_path)
    object.__setattr__(config, "gate_bot_token", FAKE_TOKEN)
    object.__setattr__(config, "admin_ids", frozenset({OWNER_ID, FRIEND_ID}))
    object.__setattr__(config, "single_user_mode", False)
    db = Database(tmp_path / "gate2.db")
    instance = GateBot(config, db)
    instance.app.bot = FakeBot()

    message = call(instance, instance.cmd_token, FRIEND_ID)
    assert "Token creado" in message.last_reply
    db.close()


# --- autorizacion directa ----------------------------------------------------


def test_authorize_activates_a_user_without_a_token(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_authorize, OWNER_ID, [str(FRIEND_ID), "pro"])
    assert "Acceso activado" in message.last_reply

    row = db.get_user(FRIEND_ID)
    assert row is not None
    assert row["authorized_at"] is not None
    assert row["plan"] == "pro"


def test_authorize_notifies_the_user(gate):
    instance, db, config = gate
    call(instance, instance.cmd_authorize, OWNER_ID, [str(FRIEND_ID)])
    sent = instance.app.bot.sent
    assert sent and sent[0][0] == FRIEND_ID
    assert "acceso" in sent[0][1].lower()


def test_authorize_accepts_an_alias(gate):
    instance, db, config = gate
    call(instance, instance.cmd_alias, OWNER_ID, ["laura", str(FRIEND_ID)])
    message = call(instance, instance.cmd_authorize, OWNER_ID, ["laura"])
    assert "Acceso activado" in message.last_reply
    assert db.get_user(FRIEND_ID)["authorized_at"] is not None


def test_authorize_with_an_unknown_alias_explains(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_authorize, OWNER_ID, ["nadie"])
    assert "No conozco" in message.last_reply
    assert db.scalar("SELECT COUNT(*) FROM users", default=0) == 0


def test_deactivate_removes_the_access(gate):
    instance, db, config = gate
    call(instance, instance.cmd_authorize, OWNER_ID, [str(FRIEND_ID)])
    message = call(instance, instance.cmd_deactivate, OWNER_ID, [str(FRIEND_ID)])
    assert "retirado" in message.last_reply
    assert db.get_user(FRIEND_ID)["authorized_at"] is None


def test_aliases_are_listed(gate):
    instance, db, config = gate
    call(instance, instance.cmd_alias, OWNER_ID, ["laura", str(FRIEND_ID)])
    message = call(instance, instance.cmd_aliases, OWNER_ID)
    assert "laura" in message.last_reply


def test_nick_creates_a_token_with_the_nickname(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_nick, OWNER_ID, ["laura", "pro", "30"])
    assert "laura" in message.last_reply
    row = db.query_one("SELECT * FROM tokens")
    assert row["plan"] == "pro"
    assert "laura" in row["label"]


def test_nick_without_arguments_shows_the_usage(gate):
    instance, db, config = gate
    message = call(instance, instance.cmd_nick, OWNER_ID, [])
    assert "Uso" in message.last_reply


def test_users_shows_the_alias_and_the_state(gate):
    instance, db, config = gate
    call(instance, instance.cmd_alias, OWNER_ID, ["laura", str(FRIEND_ID)])
    call(instance, instance.cmd_authorize, OWNER_ID, ["laura"])
    message = call(instance, instance.cmd_users, OWNER_ID)
    assert str(FRIEND_ID) in message.last_reply
    assert "laura" in message.last_reply


def test_the_plan_can_be_changed_using_an_alias(gate):
    instance, db, config = gate
    call(instance, instance.cmd_alias, OWNER_ID, ["laura", str(FRIEND_ID)])
    call(instance, instance.cmd_authorize, OWNER_ID, ["laura"])
    message = call(instance, instance.cmd_set_plan, OWNER_ID, ["laura", "unlimited"])
    assert "cambiado" in message.last_reply
    assert db.get_user(FRIEND_ID)["plan"] == "unlimited"


# --- enlace directo (deep link) ---------------------------------------------


def test_the_token_offers_a_direct_link(gate):
    instance, db, config = gate
    object.__setattr__(config, "public_bot_username", "mi_bot_nutricion")

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(OWNER_ID), message=message)
        await instance.cmd_token(update, FakeContext())
        return message

    message = run(scenario)
    markup = message.replies_markup
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.url.startswith("https://t.me/mi_bot_nutricion?start=NTR1-")


def test_without_a_username_there_is_no_link(gate):
    instance, db, config = gate
    object.__setattr__(config, "public_bot_username", "")

    async def scenario():
        message = FakeMessage()
        update = FakeUpdate(user=FakeUser(OWNER_ID), message=message)
        await instance.cmd_token(update, FakeContext())
        return message

    message = run(scenario)
    assert message.replies_markup is None
