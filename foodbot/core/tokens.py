"""Gestion de tokens de acceso.

Un token se entrega al usuario final y se canjea una sola vez por su id de
Telegram (salvo tokens multi-uso). En base de datos solo se guarda el hash
SHA-256: el codigo en claro se muestra una unica vez al crear el token.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from foodbot.core.db import Database, iso, utcnow

TOKEN_PREFIX = "NTR1"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I -> evita errores al dictar
GROUP_LEN = 4
GROUPS = 3


@dataclass
class IssuedToken:
    id: int
    code: str  # codigo en claro (solo se devuelve al crearlo)
    plan: str
    label: str | None
    max_uses: int
    expires_at: str | None


@dataclass
class TokenCheck:
    ok: bool
    reason: str = ""
    token_id: int | None = None
    plan: str | None = None


def generate_code() -> str:
    groups = [
        "".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS)
    ]
    return f"{TOKEN_PREFIX}-" + "-".join(groups)


def hash_code(code: str, pepper: str = "") -> str:
    normalized = code.strip().upper().replace(" ", "")
    return hashlib.sha256(f"{pepper}{normalized}".encode("utf-8")).hexdigest()


def code_prefix(code: str) -> str:
    """Prefijo visible para identificar un token sin exponerlo: 'NTR1-ABCD'."""
    parts = code.strip().upper().split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return code.strip().upper()[:9]


class TokenService:
    def __init__(self, db: Database, pepper: str = ""):
        self.db = db
        self.pepper = pepper

    def issue(
        self,
        *,
        plan: str = "free",
        label: str | None = None,
        days_valid: int | None = None,
        max_uses: int = 1,
        created_by: int | None = None,
    ) -> IssuedToken:
        code = generate_code()
        expires_at = None
        if days_valid:
            # days_valid negativo = token ya caducado (util en tests).
            expires_at = iso(utcnow() + timedelta(days=days_valid))
        cur = self.db.execute(
            "INSERT INTO tokens (token_hash, token_prefix, plan, label, max_uses, "
            "expires_at, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hash_code(code, self.pepper),
                code_prefix(code),
                plan,
                label,
                max_uses,
                expires_at,
                iso(),
                created_by,
            ),
        )
        return IssuedToken(
            id=int(cur.lastrowid or 0),
            code=code,
            plan=plan,
            label=label,
            max_uses=max_uses,
            expires_at=expires_at,
        )

    def check(self, code: str) -> TokenCheck:
        code = (code or "").strip()
        if not code:
            return TokenCheck(False, "Token vacio.")
        normalized = code.upper().replace(" ", "")
        if not normalized.startswith(f"{TOKEN_PREFIX}-"):
            return TokenCheck(False, "El token no tiene el formato esperado.")

        row = self.db.query_one(
            "SELECT * FROM tokens WHERE token_hash = ?", (hash_code(code, self.pepper),)
        )
        if row is None:
            return TokenCheck(False, "Token desconocido.")
        if int(row["revoked"]):
            return TokenCheck(False, "Ese token ha sido revocado.")
        if row["expires_at"]:
            try:
                expiry = datetime.fromisoformat(row["expires_at"])
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry < utcnow():
                    return TokenCheck(False, "Ese token ha caducado.")
            except ValueError:
                pass
        uses = int(row["uses"])
        max_uses = int(row["max_uses"])
        bound_user = row["bound_user_id"]
        if max_uses > 0 and uses >= max_uses and not bound_user:
            return TokenCheck(False, "Ese token ya se ha canjeado.")

        return TokenCheck(True, token_id=int(row["id"]), plan=str(row["plan"]))

    def redeem(self, code: str, user_id: int) -> TokenCheck:
        """Canjea el token y lo vincula al usuario de Telegram."""
        check = self.check(code)
        if not check.ok or check.token_id is None:
            return check

        row = self.db.query_one("SELECT * FROM tokens WHERE id = ?", (check.token_id,))
        if row is None:
            return TokenCheck(False, "Token desconocido.")

        bound = row["bound_user_id"]
        if bound is not None and int(bound) != user_id:
            return TokenCheck(False, "Ese token ya esta vinculado a otra cuenta.")

        self.db.execute(
            "UPDATE tokens SET uses = uses + 1, bound_user_id = ? WHERE id = ?",
            (user_id, check.token_id),
        )
        return check

    def revoke(self, token_id: int) -> bool:
        cur = self.db.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (token_id,))
        return cur.rowcount > 0

    def revoke_by_code(self, code: str) -> bool:
        row = self.db.query_one(
            "SELECT id FROM tokens WHERE token_hash = ?", (hash_code(code, self.pepper),)
        )
        if row is None:
            return False
        return self.revoke(int(row["id"]))

    def list_tokens(self, limit: int = 50) -> list[dict]:
        rows = self.db.query(
            "SELECT t.*, u.username, u.first_name FROM tokens t "
            "LEFT JOIN users u ON u.token_id = t.id "
            "ORDER BY t.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def get(self, token_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM tokens WHERE id = ?", (token_id,))
        return dict(row) if row else None
