"""Tests de la configuracion (lectura del .env y valores por defecto)."""

from __future__ import annotations

import pytest

from foodbot.config import load_config


@pytest.fixture()
def config(tmp_path):
    return load_config(tmp_path)


def test_default_values(config):
    assert config.provider == "openai_compat"
    assert config.llm_base_url == "http://127.0.0.1:8080/v1"
    assert config.timezone == "UTC"
    # El pipeline por defecto es: guardar en disco -> analizar -> borrar.
    assert config.store_photos is True
    assert config.photo_ttl_hours == 1
    assert config.single_user_mode is True
    assert config.max_photo_mb >= 1
    assert config.llm_concurrency >= 1


def test_the_env_file_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "PUBLIC_BOT_TOKEN=123:ABC\n"
        "ADMIN_TELEGRAM_IDS=555\n"
        "TIMEZONE=Europe/Madrid\n"
        "STORE_PHOTOS=true\n"
        "PHOTO_TTL_HOURS=3\n"
        "MAX_PHOTO_MB=20\n"
        "LLM_CONCURRENCY=2\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.public_bot_token == "123:ABC"
    assert cfg.admin_ids == frozenset({555})
    assert cfg.timezone == "Europe/Madrid"
    assert cfg.store_photos is True
    assert cfg.photo_ttl_hours == 3
    assert cfg.max_photo_mb == 20
    assert cfg.llm_concurrency == 2


def test_exported_variables_win_over_the_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BOT_TOKEN", "del-entorno")
    (tmp_path / ".env").write_text("PUBLIC_BOT_TOKEN=del-fichero\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.public_bot_token == "del-entorno"


def test_the_bots_are_detected_as_configured(config):
    assert config.public_bot_configured is False
    assert config.gate_bot_configured is False
    object.__setattr__(config, "public_bot_token", "123:ABC")
    assert config.public_bot_configured is True


def test_unknown_plans_fall_back_to_free(config):
    assert config.plan_for("no-existe").name == "free"
    assert config.plan_for(None).name == "free"
    assert config.plan_for("PRO").name == "pro"


def test_the_owner_is_the_first_admin(config):
    object.__setattr__(config, "admin_ids", frozenset({42, 7}))
    assert config.owner_id == 7
    assert config.is_owner(7) is True
    assert config.is_owner(42) is False
    assert config.is_owner(None) is False


def test_in_single_user_mode_only_the_owner_passes(config):
    object.__setattr__(config, "admin_ids", frozenset({42, 7}))
    object.__setattr__(config, "single_user_mode", True)
    assert config.is_owner(7) is True
    assert config.is_owner(42) is False


def test_without_single_user_mode_every_admin_passes(config):
    object.__setattr__(config, "admin_ids", frozenset({42, 7}))
    object.__setattr__(config, "single_user_mode", False)
    assert config.is_owner(7) is True
    assert config.is_owner(42) is True


def test_the_plans_have_sensible_limits(config):
    free = config.plan_for("free")
    pro = config.plan_for("pro")
    unlimited = config.plan_for("unlimited")
    assert free.daily_photo_limit < pro.daily_photo_limit < unlimited.daily_photo_limit
    assert "export" not in free.features
    assert "export" in pro.features
    assert "plan" in pro.features
