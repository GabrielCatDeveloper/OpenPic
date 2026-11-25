"""Tests de integracion del flujo completo (sin Telegram ni GPU)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from foodbot.config import load_config
from foodbot.core.db import Database, iso, utcnow
from foodbot.core.foodr1 import FoodR1Client, parse_food_answer
from foodbot.core.tokens import TokenService
from foodbot.core.users import UserService


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "flow.db")
    yield db, UserService(db), TokenService(db, pepper="x")
    db.close()


def register(env, user_id: int = 42, plan: str = "pro"):
    db, users, tokens = env
    issued = tokens.issue(plan=plan)
    tokens.redeem(issued.code, user_id)
    db.upsert_user(
        user_id,
        username="tester",
        first_name="Test",
        plan=plan,
        token_id=issued.id,
        authorized_at=iso(),
    )
    return issued


def test_token_flow_activates_the_account(env):
    db, users, tokens = env
    issued = tokens.issue(plan="pro")
    assert not users.is_registered(42)
    assert tokens.redeem(issued.code, 42).ok
    db.upsert_user(42, plan="pro", token_id=issued.id, authorized_at=iso())
    assert users.is_registered(42)
    assert users.plan(42) == "pro"


def test_daily_target_requires_a_profile(env):
    db, users, tokens = env
    register(env)
    assert users.daily_target(42) is None

    users.update_profile(
        42, sex="male", age=30, height_cm=180, weight_kg=80, activity="moderate", goal="lose"
    )
    target = users.daily_target(42)
    assert target is not None
    assert target.daily_kcal < target.tdee
    assert target.macro.protein_g > 0


def test_meal_is_stored_and_counted_in_the_daily_summary(env):
    db, users, tokens = env
    register(env)
    users.update_profile(
        42, sex="female", age=28, height_cm=165, weight_kg=60, activity="light", goal="lose"
    )
    now = utcnow()
    db.add_meal(
        user_id=42,
        created_at=iso(now),
        eaten_at=iso(now),
        dish="Paella",
        calories=520,
        protein_g=30,
        carbs_g=60,
        fat_g=15,
    )
    summary = users.today_summary(42)
    assert summary["calories"] == 520
    assert summary["protein_g"] == 30
    assert len(summary["meals"]) == 1
    assert summary["remaining"] == summary["target"].daily_kcal - 520


def test_history_stats_aggregates_by_day(env):
    db, users, tokens = env
    register(env)
    now = utcnow()
    for kcal in (300, 450):
        db.add_meal(
            user_id=42, created_at=iso(now), eaten_at=iso(now), dish="X", calories=kcal
        )
    stats = users.history_stats(42, days=7)
    assert stats["meals"] == 2
    assert stats["total_calories"] == 750
    assert stats["avg_calories"] == 750


def test_weight_trend_and_eta(env):
    db, users, tokens = env
    register(env)
    users.update_profile(
        42, sex="male", age=30, height_cm=180, weight_kg=80, activity="moderate",
        goal="lose", target_weight_kg=75,
    )
    now = utcnow()
    for i in range(8):
        stamp = iso(now - timedelta(days=7 - i))
        db.add_weight(42, 80 - 0.1 * i, stamp)
    progress = users.weight_progress(42)
    assert progress["weekly_rate"] < -0.4
    assert progress["eta_weeks"] is not None
    assert progress["eta_weeks"] > 0


def test_streak_counts_consecutive_days(env):
    db, users, tokens = env
    register(env)
    now = utcnow()
    for days_ago in range(4):
        stamp = iso(now - timedelta(days=days_ago))
        db.add_meal(user_id=42, created_at=stamp, eaten_at=stamp, dish="X", calories=100)
    assert users.streak(42) == 4


def test_mock_provider_returns_a_parsable_answer(env):
    config = load_config()
    client = FoodR1Client(config)
    analysis = parse_food_answer(client._mock_response("test"))
    assert analysis.calories > 0
    assert analysis.dish


def test_analysis_pipeline_from_raw_text():
    """El flujo completo: texto crudo -> analysis -> fila de base de datos."""
    raw = (
        "<answer>The dish is Ensalada de pollo, and has 5 ingredients in total. "
        "They are: 150g of chicken breast, 100g of lettuce, 50g of tomato, "
        "10g of olive oil, 1g of salt. They have a total of 380 kcal.</answer>"
    )
    analysis = parse_food_answer(raw)
    assert analysis.calories == 380
    assert analysis.ingredients[0].grams == 150
    assert analysis.protein_g > 20
