"""Tests de aislamiento: un usuario NUNCA ve ni toca los datos de otro."""

from __future__ import annotations

import pytest

from foodbot.core.db import Database, iso, utcnow
from foodbot.core.tokens import TokenService
from foodbot.core.users import UserService


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "isolation.db")
    users = UserService(db, "UTC")
    tokens = TokenService(db, pepper="pepper")
    for uid in (1, 2):
        db.upsert_user(uid, plan="pro", authorized_at=iso())
    yield db, users, tokens
    db.close()


def add_meal(db, user_id: int, dish: str, kcal: int) -> int:
    now = utcnow()
    return db.add_meal(
        user_id=user_id,
        created_at=iso(now),
        eaten_at=iso(now),
        dish=dish,
        calories=kcal,
        protein_g=10,
        carbs_g=20,
        fat_g=5,
    )


def test_each_user_sees_only_their_own_meals(env):
    db, users, tokens = env
    add_meal(db, 1, "Paella", 500)
    add_meal(db, 1, "Ensalada", 200)
    add_meal(db, 2, "Tortilla", 300)

    assert len(db.meals_between(1, "1970-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00")) == 2
    assert len(db.recent_meals(1, 10)) == 2
    assert db.recent_meals(1, 10)[0]["dish"] in {"Paella", "Ensalada"}


def test_summary_and_history_only_count_the_owner_meals(env):
    db, users, tokens = env
    add_meal(db, 1, "Paella", 500)
    add_meal(db, 2, "Tortilla", 900)

    summary = users.today_summary(1)
    assert summary["calories"] == 500
    assert len(summary["meals"]) == 1

    stats = users.history_stats(1, days=7)
    assert stats["meals"] == 1
    assert stats["total_calories"] == 500


def test_streak_only_counts_the_owner_meals(env):
    db, users, tokens = env
    now = utcnow()
    for days_ago in range(3):
        stamp = iso(now - __import__("datetime").timedelta(days=days_ago))
        db.add_meal(user_id=1, created_at=stamp, eaten_at=stamp, dish="X", calories=100)
    assert users.streak(1) == 3
    assert users.streak(2) == 0


def test_weight_progress_only_uses_the_owner_weights(env):
    db, users, tokens = env
    db.add_weight(1, 80.0, iso(utcnow()))
    db.add_weight(2, 60.0, iso(utcnow()))
    progress = users.weight_progress(1)
    assert progress["current"] == 80.0


def test_get_meal_hides_other_users_meals(env):
    db, users, tokens = env
    meal_id = add_meal(db, 1, "Paella", 500)
    assert db.get_meal(1, meal_id) is not None
    assert db.get_meal(2, meal_id) is None


def test_update_meal_cannot_touch_another_users_meal(env):
    db, users, tokens = env
    meal_id = add_meal(db, 1, "Paella", 500)
    assert db.update_meal(2, meal_id, calories=1) is False
    assert db.get_meal(1, meal_id)["calories"] == 500


def test_delete_meal_cannot_delete_another_users_meal(env):
    db, users, tokens = env
    meal_id = add_meal(db, 1, "Paella", 500)
    assert db.delete_meal(2, meal_id) is False
    assert db.get_meal(1, meal_id) is not None
    assert db.delete_meal(1, meal_id) is True


def test_last_meal_is_scoped_to_the_user(env):
    db, users, tokens = env
    add_meal(db, 2, "Tortilla", 300)
    assert db.last_meal(1) is None
    assert db.last_meal(2)["dish"] == "Tortilla"


def test_usage_is_scoped_to_the_user(env):
    db, users, tokens = env
    db.bump_usage(1, "2026-01-01", photos=3)
    assert db.usage_today(1, "2026-01-01") == (3, 0)
    assert db.usage_today(2, "2026-01-01") == (0, 0)


def test_targets_are_scoped_to_the_user(env):
    db, users, tokens = env
    db.save_target(1, "2026-01-01", kcal_target=2000)
    db.save_target(2, "2026-01-01", kcal_target=2400)
    row = db.query_one(
        "SELECT kcal_target FROM targets WHERE user_id = 1 AND day = '2026-01-01'"
    )
    assert row["kcal_target"] == 2000


# --- tokens -----------------------------------------------------------------


def test_a_token_cannot_be_stolen_by_another_user(env):
    db, users, tokens = env
    issued = tokens.issue(plan="pro")
    assert tokens.redeem(issued.code, 1).ok

    # El mismo codigo no sirve para otra cuenta.
    second = tokens.redeem(issued.code, 2)
    assert not second.ok
    assert "otra cuenta" in second.reason


def test_check_reports_a_token_bound_to_someone_else(env):
    db, users, tokens = env
    issued = tokens.issue(plan="pro")
    tokens.redeem(issued.code, 1)
    check = tokens.check(issued.code)
    # El canje esta ligado al usuario 1: el 2 no debe poder activarse con el.
    assert tokens.redeem(issued.code, 2).ok is False


def test_revoking_a_token_blocks_it(env):
    db, users, tokens = env
    issued = tokens.issue(plan="free")
    assert tokens.revoke(issued.id)
    assert not tokens.check(issued.code).ok
