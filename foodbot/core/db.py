"""Capa de persistencia (SQLite) del bot.

Un unico modulo con el esquema, las consultas y las funciones de alto nivel.
SQLite en modo WAL soporta bien las escrituras concurrentes del bot.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash    TEXT    NOT NULL UNIQUE,
    token_prefix  TEXT    NOT NULL,
    plan          TEXT    NOT NULL DEFAULT 'free',
    label         TEXT,
    max_uses      INTEGER NOT NULL DEFAULT 1,
    uses          INTEGER NOT NULL DEFAULT 0,
    expires_at    TEXT,
    created_at    TEXT    NOT NULL,
    created_by    INTEGER,
    revoked       INTEGER NOT NULL DEFAULT 0,
    bound_user_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(bound_user_id);

CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    first_name     TEXT,
    language       TEXT    NOT NULL DEFAULT 'es',
    plan           TEXT    NOT NULL DEFAULT 'free',
    token_id       INTEGER REFERENCES tokens(id) ON DELETE SET NULL,
    authorized_at  TEXT,
    last_seen      TEXT,
    nickname       TEXT,
    sex            TEXT,
    age            INTEGER,
    height_cm      REAL,
    weight_kg      REAL,
    target_weight_kg REAL,
    activity       TEXT,
    goal           TEXT,
    weekly_rate_kg REAL,
    daily_kcal_override INTEGER,
    protein_g_per_kg REAL,
    -- Datos extra para afinar el mantenimiento (TDEE):
    --   body_fat_pct     % de grasa (DEXA o pliegues; una báscula digital
    --                    se equivoca 4-8 puntos y no sirve)
    --   training_days    sesiones de ejercicio por semana
    --   daily_steps      pasos medios al dia (NEAT)
    --   job_activity     tipo de trabajo: sedentary | light | physical
    --   maintenance_kcal mantenimiento manual (si el usuario lo conoce)
    body_fat_pct   REAL,
    hormone_therapy TEXT,   -- none | estrogen | testosterone | other
    pronouns       TEXT,    -- male | female | neutral (como te llamamos)
    training_days  INTEGER,
    daily_steps    INTEGER,
    job_activity   TEXT,
    maintenance_kcal INTEGER,
    onboarded      INTEGER NOT NULL DEFAULT 0,
    onboard_step   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_token ON users(token_id);

CREATE TABLE IF NOT EXISTS meals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    eaten_at     TEXT    NOT NULL,
    meal_slot    TEXT,
    photo_id     TEXT,
    caption      TEXT,
    dish         TEXT,
    calories     INTEGER,
    protein_g    REAL,
    carbs_g      REAL,
    fat_g        REAL,
    mass_g       REAL,
    ingredients  TEXT,
    raw_answer   TEXT,
    confidence   REAL,
    engine       TEXT,
    edited       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(user_id, eaten_at DESC);

CREATE TABLE IF NOT EXISTS weights (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    weight_kg  REAL NOT NULL,
    logged_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weights_user ON weights(user_id, logged_at DESC);

CREATE TABLE IF NOT EXISTS targets (
    user_id        INTEGER NOT NULL,
    day            TEXT    NOT NULL,
    kcal_target    INTEGER,
    protein_target REAL,
    carbs_target   REAL,
    fat_target     REAL,
    bmr            INTEGER,
    tdee           INTEGER,
    deficit_kcal   INTEGER,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS usage (
    user_id  INTEGER NOT NULL,
    day      TEXT    NOT NULL,
    photos   INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- Respuestas generadas por el modelo que se reutilizan (plan y consejo del
-- dia). Guardarlas evita volver a gastar GPU en la misma pregunta.
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    user_id    INTEGER,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cache_user ON cache(user_id);
CREATE INDEX IF NOT EXISTS idx_cache_expiry ON cache(expires_at);

-- Cierre semanal del banco de ahorro: lo que cada semana sumo (o resto) al
-- total acumulado. Se guarda al cerrar la semana para no recalcularlo con el
-- mantenimiento de hoy (que ya no es el de entonces).
CREATE TABLE IF NOT EXISTS weekly_savings (
    user_id      INTEGER NOT NULL,
    week         TEXT    NOT NULL,   -- lunes de la semana ('YYYY-MM-DD')
    saved        INTEGER NOT NULL,   -- kcal ahorradas esa semana (+ o -)
    consumed     INTEGER NOT NULL,
    logged_days  INTEGER NOT NULL,
    maintenance  INTEGER NOT NULL,   -- mantenimiento medio de esa semana
    closed_at    TEXT    NOT NULL,
    PRIMARY KEY (user_id, week)
);
CREATE INDEX IF NOT EXISTS idx_weekly_savings_user ON weekly_savings(user_id, week);

-- Apodos que usa el administrador para autorizar a alguien sin recordar su
-- id numerico de Telegram: 'laura' -> 123456789
CREATE TABLE IF NOT EXISTS aliases (
    nickname   TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);
"""

# Columnas anadidas despues del primer release. SQLite no las anade solo con
# CREATE TABLE IF NOT EXISTS, asi que se migran al abrir la base de datos.
MIGRATIONS: dict[str, str] = {
    "nickname": "TEXT",
    "hormone_therapy": "TEXT",
    "pronouns": "TEXT",
    "body_fat_pct": "REAL",
    "training_days": "INTEGER",
    "daily_steps": "INTEGER",
    "job_activity": "TEXT",
    "maintenance_kcal": "INTEGER",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: Any) -> Any:
    """Normaliza una marca de tiempo a ISO en UTC.

    Las comparaciones de rango en SQL son textuales: si una fila guarda
    "+01:00" y la ventana de la consulta "+00:00", el rango se rompe. Todo lo
    que se guarda se guarda en UTC.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


class Database:
    """Envoltura fina y thread-safe sobre sqlite3."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Anade las columnas que falten (BDs creadas por versiones previas)."""
        rows = self._conn.execute("PRAGMA table_info(users)").fetchall()
        existing = {row[1] for row in rows}
        for column, decl in MIGRATIONS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE users ADD COLUMN {column} {decl}")

    # -- primitivas -------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    # -- usuarios ---------------------------------------------------------
    def get_user(self, user_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    def upsert_user(self, user_id: int, **fields: Any) -> None:
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        values = list(fields.values())
        self.execute(
            f"INSERT INTO users (user_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in fields)}",
            [user_id, *values],
        )

    def touch_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE users SET {assignments} WHERE user_id = ?",
            [*fields.values(), user_id],
        )

    # -- comidas ----------------------------------------------------------
    def add_meal(self, **fields: Any) -> int:
        # sqlite3 no admite listas/dicts: se guardan como JSON.
        encoded = {
            key: (json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value)
            for key, value in fields.items()
        }
        # Las marcas de tiempo siempre en UTC (ver to_utc_iso).
        for stamp in ("eaten_at", "created_at"):
            if stamp in encoded:
                encoded[stamp] = to_utc_iso(encoded[stamp])
        columns = ", ".join(encoded)
        placeholders = ", ".join("?" for _ in encoded)
        cur = self.execute(
            f"INSERT INTO meals ({columns}) VALUES ({placeholders})", list(encoded.values())
        )
        return int(cur.lastrowid or 0)

    def meals_between(self, user_id: int, start: str, end: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM meals WHERE user_id = ? AND eaten_at >= ? AND eaten_at < ? "
            "ORDER BY eaten_at ASC",
            (user_id, start, end),
        )

    def recent_meals(self, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM meals WHERE user_id = ? ORDER BY eaten_at DESC LIMIT ?",
            (user_id, limit),
        )

    def delete_meal(self, user_id: int, meal_id: int) -> bool:
        cur = self.execute(
            "DELETE FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id)
        )
        return cur.rowcount > 0

    def get_meal(self, user_id: int, meal_id: int) -> sqlite3.Row | None:
        """Solo devuelve la comida si pertenece a ese usuario."""
        return self.query_one(
            "SELECT * FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id)
        )

    def update_meal(self, user_id: int, meal_id: int, **fields: Any) -> bool:
        """Corrige una comida (p. ej. las calorias). Solo toca la del usuario."""
        if not fields:
            return False
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = self.execute(
            f"UPDATE meals SET {assignments} WHERE id = ? AND user_id = ?",
            [*fields.values(), meal_id, user_id],
        )
        return cur.rowcount > 0

    def last_meal(self, user_id: int) -> sqlite3.Row | None:
        return self.query_one(
            "SELECT * FROM meals WHERE user_id = ? ORDER BY eaten_at DESC, id DESC LIMIT 1",
            (user_id,),
        )

    # -- pesos ------------------------------------------------------------
    def add_weight(self, user_id: int, weight_kg: float, logged_at: str | None = None) -> int:
        cur = self.execute(
            "INSERT INTO weights (user_id, weight_kg, logged_at) VALUES (?, ?, ?)",
            (user_id, weight_kg, to_utc_iso(logged_at) if logged_at else iso()),
        )
        return int(cur.lastrowid or 0)

    # -- cierre semanal del banco -----------------------------------------
    def save_week_savings(self, user_id: int, week: str, **fields: Any) -> None:
        """Guarda el resultado de una semana ya cerrada."""
        self.execute(
            "INSERT INTO weekly_savings "
            "(user_id, week, saved, consumed, logged_days, maintenance, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, week) DO UPDATE SET "
            "saved=excluded.saved, consumed=excluded.consumed, "
            "logged_days=excluded.logged_days, maintenance=excluded.maintenance, "
            "closed_at=excluded.closed_at",
            [
                user_id,
                week,
                int(fields.get("saved") or 0),
                int(fields.get("consumed") or 0),
                int(fields.get("logged_days") or 0),
                int(fields.get("maintenance") or 0),
                fields.get("closed_at") or iso(),
            ],
        )

    def list_week_savings(self, user_id: int) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM weekly_savings WHERE user_id = ? ORDER BY week ASC",
            (user_id,),
        )

    def weights_between(self, user_id: int, start: str, end: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM weights WHERE user_id = ? AND logged_at >= ? AND logged_at < ? "
            "ORDER BY logged_at ASC",
            (user_id, start, end),
        )

    def last_weights(self, user_id: int, limit: int = 14) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM weights WHERE user_id = ? ORDER BY logged_at DESC LIMIT ?",
            (user_id, limit),
        )

    def latest_weight(self, user_id: int) -> float | None:
        row = self.query_one(
            "SELECT weight_kg FROM weights WHERE user_id = ? ORDER BY logged_at DESC LIMIT 1",
            (user_id,),
        )
        return float(row["weight_kg"]) if row else None

    # -- uso / limites ----------------------------------------------------
    def usage_today(self, user_id: int, day: str) -> tuple[int, int]:
        row = self.query_one(
            "SELECT photos, messages FROM usage WHERE user_id = ? AND day = ?", (user_id, day)
        )
        if row is None:
            return (0, 0)
        return (int(row["photos"]), int(row["messages"]))

    def bump_usage(self, user_id: int, day: str, photos: int = 0, messages: int = 0) -> None:
        self.execute(
            "INSERT INTO usage (user_id, day, photos, messages) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "photos = photos + excluded.photos, messages = messages + excluded.messages",
            (user_id, day, photos, messages),
        )

    # -- objetivos diarios ------------------------------------------------
    def save_target(self, user_id: int, day: str, **fields: Any) -> None:
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        self.execute(
            f"INSERT INTO targets (user_id, day, {columns}) VALUES (?, ?, {placeholders}) "
            f"ON CONFLICT(user_id, day) DO UPDATE SET "
            f"{', '.join(f'{k}=excluded.{k}' for k in fields)}",
            [user_id, day, *fields.values()],
        )

    # -- apodos (aliases) -------------------------------------------------
    def set_alias(self, nickname: str, user_id: int, note: str | None = None) -> None:
        self.execute(
            "INSERT INTO aliases (nickname, user_id, note, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(nickname) DO UPDATE SET user_id = excluded.user_id, "
            "note = excluded.note",
            (nickname.strip().lower(), user_id, note, iso()),
        )

    def get_alias(self, nickname: str) -> sqlite3.Row | None:
        return self.query_one(
            "SELECT * FROM aliases WHERE nickname = ?", (nickname.strip().lower(),)
        )

    def list_aliases(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM aliases ORDER BY nickname")

    def delete_alias(self, nickname: str) -> bool:
        cur = self.execute(
            "DELETE FROM aliases WHERE nickname = ?", (nickname.strip().lower(),)
        )
        return cur.rowcount > 0

    # -- cache de respuestas del modelo -----------------------------------
    def get_cache(self, key: str, now: str | None = None) -> str | None:
        """Valor cacheado, o None si no existe o ha caducado."""
        row = self.query_one(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        )
        if row is None:
            return None
        expires_at = row["expires_at"]
        if expires_at:
            reference = now or iso()
            if str(expires_at) <= reference:
                self.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
        return str(row["value"])

    def set_cache(
        self, key: str, value: str, user_id: int | None = None, expires_at: str | None = None
    ) -> None:
        self.execute(
            "INSERT INTO cache (key, user_id, value, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "created_at = excluded.created_at, expires_at = excluded.expires_at",
            (key, user_id, value, iso(), expires_at),
        )

    def purge_expired_cache(self, now: str | None = None) -> int:
        reference = now or iso()
        cur = self.execute("DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                           (reference,))
        return cur.rowcount

    # -- utilerias --------------------------------------------------------
    def json_field(self, row: sqlite3.Row, name: str, default: Any = None) -> Any:
        raw = row[name] if name in row.keys() else None
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def close(self) -> None:
        with self._lock:
            self._conn.close()
