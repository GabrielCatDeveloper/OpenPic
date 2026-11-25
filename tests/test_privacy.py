"""Tests de privacidad: las fotos no se quedan en el disco y los datos son
borrables por el usuario.
"""

from __future__ import annotations

import time

import pytest

from foodbot.core.db import Database, iso, utcnow
from foodbot.core.privacy import (
    PhotoStore,
    erase_user_data,
    reset_photos_directory,
    storage_report,
)


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "privacy.db")
    yield database
    database.close()


def make_meal(db, user_id: int, dish: str = "Paella", kcal: int = 500) -> int:
    now = utcnow()
    return db.add_meal(
        user_id=user_id,
        created_at=iso(now),
        eaten_at=iso(now),
        dish=dish,
        calories=kcal,
    )


# --- retencion de fotos ----------------------------------------------------


def test_photos_are_not_written_when_the_store_is_disabled(tmp_path):
    store = PhotoStore(tmp_path / "photos", enabled=False, ttl_hours=1)
    path = store.save(1, b"\xff\xd8\xffdatos")
    assert path is None
    assert not (tmp_path / "photos").exists()


def test_photos_are_written_and_purged_by_ttl(tmp_path):
    directory = tmp_path / "photos"
    store = PhotoStore(directory, enabled=True, ttl_hours=1)
    path = store.save(7, b"\xff\xd8\xffdatos")
    assert path is not None and path.exists()

    # Pasada una hora, la foto se borra sola.
    removed = store.purge_expired(now=time.time() + 7200)
    assert removed == 1
    assert not path.exists()


def test_fresh_photos_are_not_purged(tmp_path):
    store = PhotoStore(tmp_path / "photos", enabled=True, ttl_hours=24)
    store.save(7, b"datos")
    assert store.purge_expired() == 0


def test_purge_user_only_removes_that_user(tmp_path):
    store = PhotoStore(tmp_path / "photos", enabled=True, ttl_hours=24)
    mine = store.save(1, b"uno")
    other = store.save(2, b"dos")
    assert store.purge_user(1) == 1
    assert not mine.exists()
    assert other.exists()


def test_reset_only_removes_files_created_by_the_bot(tmp_path):
    directory = tmp_path / "photos"
    directory.mkdir()
    stranger = directory / "documentos-importantes.txt"
    stranger.write_text("no tocar")
    ours = directory / "42_1700000000.jpg"
    ours.write_bytes(b"foto")

    removed = reset_photos_directory(directory)

    assert removed == 1
    assert stranger.exists()
    assert not ours.exists()


def test_total_bytes_ignores_foreign_files(tmp_path):
    directory = tmp_path / "photos"
    directory.mkdir()
    (directory / "42_1700000000.jpg").write_bytes(b"x" * 100)
    (directory / "nota.txt").write_bytes(b"y" * 500)
    store = PhotoStore(directory, enabled=True)
    assert store.total_bytes() == 100


# --- borrado de datos -------------------------------------------------------


def test_erase_user_data_removes_only_that_user(db):
    make_meal(db, 1)
    make_meal(db, 2, dish="Tortilla")
    db.add_weight(1, 80.0)
    db.add_weight(2, 70.0)
    db.save_target(1, "2026-01-01", kcal_target=2000)
    db.save_target(2, "2026-01-01", kcal_target=2100)
    db.bump_usage(1, "2026-01-01", photos=1)
    db.bump_usage(2, "2026-01-01", photos=1)

    counts = erase_user_data(db, 1)

    assert counts["meals"] == 1
    assert counts["weights"] == 1
    assert counts["targets"] == 1
    assert counts["usage"] == 1
    # El usuario 2 no se toca.
    assert db.scalar("SELECT COUNT(*) FROM meals WHERE user_id = 2", default=0) == 1
    assert db.scalar("SELECT COUNT(*) FROM weights WHERE user_id = 2", default=0) == 1


def test_erase_user_data_removes_the_user_row(db):
    db.upsert_user(5, plan="pro", authorized_at=iso())
    counts = erase_user_data(db, 5)
    assert counts["user"] == 1
    assert db.get_user(5) is None


def test_erase_user_data_revokes_the_token(db):
    from foodbot.core.tokens import TokenService

    tokens = TokenService(db, pepper="pepper")
    issued = tokens.issue(plan="pro")
    tokens.redeem(issued.code, 9)
    db.upsert_user(9, plan="pro", token_id=issued.id, authorized_at=iso())

    erase_user_data(db, 9)

    row = tokens.get(issued.id)
    assert int(row["revoked"]) == 1
    assert row["bound_user_id"] is None


def test_erase_user_data_is_idempotent(db):
    make_meal(db, 1)
    erase_user_data(db, 1)
    counts = erase_user_data(db, 1)
    assert sum(counts.values()) == 0


def test_storage_report_counts_what_is_stored(db):
    make_meal(db, 1)
    make_meal(db, 1)
    db.add_weight(1, 80.0)
    report = storage_report(db, 1)
    assert report["meals"] == 2
    assert report["weights"] == 1
    assert storage_report(db, 2)["meals"] == 0
