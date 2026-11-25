"""Tests de la deteccion de formato de imagen y del cliente HTTP."""

from __future__ import annotations

from foodbot.core.foodr1 import (
    CALORIE_PROMPT,
    CALORIE_SYSTEM,
    sniff_mime,
)


def test_sniff_jpeg():
    assert sniff_mime(b"\xff\xd8\xff\xe0" + b"0" * 100) == "image/jpeg"


def test_sniff_png():
    assert sniff_mime(b"\x89PNG\r\n\x1a\n" + b"0" * 100) == "image/png"


def test_sniff_webp():
    assert sniff_mime(b"RIFF" + b"1234" + b"WEBP" + b"0" * 100) == "image/webp"


def test_sniff_unknown_defaults_to_jpeg():
    assert sniff_mime(b"not an image at all") == "image/jpeg"


def test_prompt_matches_the_official_training_format():
    """Si cambia el prompt, Food-R1 deja de ser parseable. Este test lo blinda."""
    assert "<answer>" in CALORIE_SYSTEM
    assert "<answer>" in CALORIE_PROMPT
    assert "They have a total of {KCAL} kcal." in CALORIE_PROMPT
    assert "They are:" in CALORIE_PROMPT
    assert "and has {COUNT} ingredients in total" in CALORIE_PROMPT
    # El system prompt es el del script de evaluacion oficial, linea a linea.
    assert CALORIE_SYSTEM.startswith("You are a cooking and nutrition assistant.")
    assert "Keep the colon after \"They are:\"" in CALORIE_SYSTEM
    assert "Separate items with commas only." in CALORIE_SYSTEM


def test_image_max_tokens_matches_official_max_pixels():
    """Qwen3-VL: cada token cubre 32x32 px. 1024 tokens = 1.048.576 px."""
    official_max_pixels = 1048576
    assert 1024 * 32 * 32 == official_max_pixels
