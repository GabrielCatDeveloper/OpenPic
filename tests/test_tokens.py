"""Tests del ciclo de vida de los tokens de acceso."""

from __future__ import annotations

import pytest

from foodbot.core.db import Database
from foodbot.core.tokens import TokenService, code_prefix, generate_code


@pytest.fixture()
def service(tmp_path):
    db = Database(tmp_path / "test.db")
    yield TokenService(db, pepper="pepper")
    db.close()


def test_generated_codes_have_the_expected_shape(service):
    for _ in range(20):
        code = generate_code()
        assert code.startswith("NTR1-")
        assert len(code) == len("NTR1-XXXX-XXXX-XXXX")
        # La parte aleatoria no usa caracteres ambiguos al dictar.
        random_part = code.split("-", 1)[1]
        assert not set(random_part) & set("01IO")


def test_prefix_hides_the_secret(service):
    code = generate_code()
    prefix = code_prefix(code)
    assert prefix.startswith("NTR1-")
    assert code not in prefix


def test_issue_and_check(service):
    issued = service.issue(plan="pro", label="test", days_valid=30)
    check = service.check(issued.code)
    assert check.ok
    assert check.plan == "pro"
    assert check.token_id == issued.id


def test_unknown_token_is_rejected(service):
    assert not service.check("NTR1-AAAA-BBBB-CCCC").ok
    assert not service.check("basura").ok
    assert not service.check("").ok


def test_token_can_be_redeemed_only_once(service):
    issued = service.issue()
    assert service.redeem(issued.code, 111).ok
    # Mismo usuario, segunda vez: ya esta vinculado, no es un error.
    assert service.redeem(issued.code, 111).ok
    # Otro usuario: rechazado.
    second = service.redeem(issued.code, 222)
    assert not second.ok
    assert "vinculado" in second.reason


def test_expired_token_is_rejected(service):
    issued = service.issue(days_valid=-1)
    check = service.check(issued.code)
    assert not check.ok
    assert "caducado" in check.reason


def test_revoked_token_is_rejected(service):
    issued = service.issue()
    assert service.revoke(issued.id)
    assert not service.check(issued.code).ok


def test_code_is_not_stored_in_plain_text(service, tmp_path):
    issued = service.issue()
    db = service.db
    raw = (tmp_path / "test.db").read_bytes()
    assert issued.code.encode() not in raw
    row = db.query_one("SELECT token_hash, token_prefix FROM tokens WHERE id = ?", (issued.id,))
    assert issued.code not in row["token_hash"]
    assert row["token_prefix"].startswith("NTR1-")
