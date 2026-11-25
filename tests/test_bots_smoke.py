"""Test de humo: los bots se construyen y registran sus handlers."""

from __future__ import annotations

import pytest

from foodbot.bots.gate import GateBot
from foodbot.bots.public import PublicBot
from foodbot.config import load_config
from foodbot.core.db import Database

FAKE_TOKEN = "123456789:AAHfakefakefakefakefakefakefakefakefake"


@pytest.fixture()
def config(tmp_path):
    cfg = load_config(tmp_path)
    object.__setattr__(cfg, "public_bot_token", FAKE_TOKEN)
    object.__setattr__(cfg, "gate_bot_token", FAKE_TOKEN)
    object.__setattr__(cfg, "admin_ids", frozenset({42}))
    return cfg


def test_public_bot_builds(config, tmp_path):
    from telegram.ext import ConversationHandler

    db = Database(tmp_path / "bots.db")
    bot = PublicBot(config, db)

    # `app.handlers` es {grupo: [handlers]}; aplanamos para inspeccionarlo.
    handlers = [h for group in bot.app.handlers.values() for h in group]

    # El asistente de configuracion del perfil debe estar registrado.
    conversations = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert conversations

    # "perfil" se entra desde el ConversationHandler.
    entry_commands = {
        entry
        for conversation in conversations
        for handler in conversation.entry_points
        for entry in getattr(handler, "commands", []) or []
    }
    assert "perfil" in entry_commands

    # Y deben existir los comandos principales del bot publico.
    commands = set()
    for handler in handlers:
        for entry in getattr(handler, "commands", []) or []:
            commands.add(entry)
    assert {"start", "token", "peso", "meta", "plan", "historial", "hoy"} <= commands
    db.close()


def test_gate_bot_builds(config, tmp_path):
    db = Database(tmp_path / "gate.db")
    bot = GateBot(config, db)
    assert bot.app is not None
    db.close()


def test_menu_keyboards_are_consistent():
    from foodbot.bots.public import activity_keyboard, goal_keyboard, main_menu, profile_keyboard

    for markup in (main_menu(), profile_keyboard(), activity_keyboard(), goal_keyboard()):
        assert markup.inline_keyboard
        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data or button.text


def test_config_plans_have_limits():
    cfg = load_config()
    for plan in cfg.plans.values():
        assert plan.daily_photo_limit > 0
        assert plan.daily_message_limit > 0
    assert "plan" in cfg.plans["pro"].features
    assert "plan" not in cfg.plans["free"].features
