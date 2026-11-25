"""Tests de las cuotas por plan (analisis, mensajes y tamaño de foto)."""

from __future__ import annotations

import pytest

from foodbot.config import load_config
from foodbot.core.db import Database
from foodbot.core.limits import LimitService


@pytest.fixture()
def env(tmp_path):
    config = load_config(tmp_path)
    db = Database(tmp_path / "limits.db")
    yield db, config, LimitService(db, config, "UTC")
    db.close()


def test_photos_are_allowed_below_the_limit(env):
    db, config, limits = env
    limits.bump_photo(1)
    result = limits.check_photos(1, "free")
    assert result.allowed
    assert result.used == 1
    assert result.limit == config.plans["free"].daily_photo_limit
    assert result.remaining == config.plans["free"].daily_photo_limit - 1


def test_photo_limit_blocks_when_exhausted(env):
    db, config, limits = env
    limit = config.plans["free"].daily_photo_limit
    for _ in range(limit):
        limits.bump_photo(1)
    result = limits.check_photos(1, "free")
    assert not result.allowed
    assert result.exhausted
    assert str(limit) in result.reason


def test_a_higher_plan_has_a_higher_limit(env):
    db, config, limits = env
    free_limit = config.plans["free"].daily_photo_limit
    for _ in range(free_limit):
        limits.bump_photo(1)
    assert not limits.check_photos(1, "free").allowed
    assert limits.check_photos(1, "unlimited").allowed


def test_message_limit_is_enforced(env):
    db, config, limits = env
    limit = config.plans["free"].daily_message_limit
    for _ in range(limit):
        limits.bump_message(1)
    result = limits.check_messages(1, "free")
    assert not result.allowed
    assert "mensajes" in result.reason


def test_usage_is_counted_per_user(env):
    db, config, limits = env
    limits.bump_photo(1)
    limits.bump_photo(2)
    assert limits.photos_used(1) == 1
    assert limits.photos_used(2) == 1


def test_usage_does_not_carry_over_to_the_next_day(env):
    db, config, limits = env
    limits.bump_photo(1, day="2026-01-01")
    assert limits.photos_used(1, day="2026-01-01") == 1
    assert limits.photos_used(1, day="2026-01-02") == 0
    assert limits.check_photos(1, "free").allowed


def test_photo_size_limit(env):
    db, config, limits = env
    max_bytes = config.max_photo_mb * 1024 * 1024
    assert limits.check_photo_size(max_bytes).allowed
    result = limits.check_photo_size(max_bytes + 1)
    assert not result.allowed
    assert "MB" in result.reason


def test_photo_size_limit_ignores_unknown_sizes(env):
    db, config, limits = env
    assert limits.check_photo_size(None).allowed
    assert limits.check_photo_size(0).allowed


def test_snapshot_reports_what_is_left(env):
    db, config, limits = env
    limits.bump_photo(1)
    limits.bump_message(1)
    snapshot = limits.snapshot(1, "pro")
    assert snapshot["photos_used"] == 1
    assert snapshot["photos_left"] == config.plans["pro"].daily_photo_limit - 1
    assert snapshot["messages_used"] == 1
