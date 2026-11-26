"""Tests de la capa de datos: cache, alias y serializacion."""

from __future__ import annotations

import pytest

from datetime import timedelta

from foodbot.core.db import Database, iso, utcnow


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "db.db")
    yield database
    database.close()


# --- cache ------------------------------------------------------------------


def test_cache_stores_and_returns_the_value(db):
    db.set_cache("plan:1:2026-01-01", "un plan", user_id=1)
    assert db.get_cache("plan:1:2026-01-01") == "un plan"


def test_cache_miss_returns_none(db):
    assert db.get_cache("nada") is None


def test_cache_expires(db):
    db.set_cache("k", "v", expires_at="2000-01-01T00:00:00+00:00")
    assert db.get_cache("k") is None


def test_cache_without_expiry_never_expires(db):
    db.set_cache("k", "v")
    assert db.get_cache("k") == "v"


def test_cache_overwrites_the_same_key(db):
    db.set_cache("k", "v1")
    db.set_cache("k", "v2")
    assert db.get_cache("k") == "v2"


def test_purge_expired_cache_only_removes_expired_rows(db):
    db.set_cache("viejo", "v", expires_at="2000-01-01T00:00:00+00:00")
    db.set_cache("nuevo", "v")
    removed = db.purge_expired_cache()
    assert removed == 1
    assert db.get_cache("nuevo") == "v"


# --- comidas ----------------------------------------------------------------


def test_add_meal_serialises_lists_as_json(db):
    meal_id = db.add_meal(
        user_id=1,
        created_at=iso(),
        eaten_at=iso(),
        dish="Paella",
        calories=500,
        ingredients=[{"name": "arroz", "grams": 120}],
    )
    row = db.get_meal(1, meal_id)
    assert db.json_field(row, "ingredients")[0]["name"] == "arroz"


def test_update_meal_marks_the_meal_as_edited(db):
    now = iso()
    meal_id = db.add_meal(user_id=1, created_at=now, eaten_at=now, dish="X", calories=500)
    assert db.update_meal(1, meal_id, calories=420, edited=1)
    row = db.get_meal(1, meal_id)
    assert row["calories"] == 420
    assert row["edited"] == 1


def test_last_meal_is_the_most_recent_one(db):
    # Ayer y ahora: si se usara "hoy a la 01:00" comomeal antiguo, el test
    # fallaria entre las 00:00 y la 01:00 UTC (la "antigua" seria futura).
    old = iso(utcnow() - timedelta(days=1))
    new = iso(utcnow())
    db.add_meal(user_id=1, created_at=old, eaten_at=old, dish="Viejo", calories=100)
    db.add_meal(user_id=1, created_at=new, eaten_at=new, dish="Nuevo", calories=200)
    assert db.last_meal(1)["dish"] == "Nuevo"


# --- alias ------------------------------------------------------------------


def test_alias_is_created_and_resolved(db):
    db.set_alias("laura", 1234, "la hermana")
    row = db.get_alias("laura")
    assert row["user_id"] == 1234
    assert row["note"] == "la hermana"


def test_alias_is_case_insensitive(db):
    db.set_alias("Laura", 1234)
    assert db.get_alias("laura")["user_id"] == 1234


def test_alias_can_be_updated_and_deleted(db):
    db.set_alias("laura", 1)
    db.set_alias("laura", 2)
    assert db.get_alias("laura")["user_id"] == 2
    assert db.delete_alias("laura")
    assert db.get_alias("laura") is None


def test_list_aliases(db):
    db.set_alias("b", 2)
    db.set_alias("a", 1)
    assert [r["nickname"] for r in db.list_aliases()] == ["a", "b"]
