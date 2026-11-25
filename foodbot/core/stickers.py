"""Los file_ids del set de stickers del bot.

Telegram guarda los stickers en su servidor y se refers con un file_id. El
set se sube con scripts/upload_stickers.py y sus ids quedan aqui, para que el
repo no dependa de una llamada de red en cada mensaje.

Si el fichero no esta (o esta vacio), el bot cae al emoji de texto: el
usuario recibe igualmente la informacion.
"""

from __future__ import annotations

import json
from pathlib import Path

STICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "stickers.json"

_cache: dict[str, str] | None = None


def sticker_ids() -> dict[str, str]:
    """{nombre: file_id} del set de stickers (vacio si no hay)."""
    global _cache
    if _cache is not None:
        return _cache
    ids: dict[str, str] = {}
    try:
        data = json.loads(STICKER_FILE.read_text(encoding="utf-8"))
        stickers = data.get("stickers") or {}
        ids = {str(k): str(v) for k, v in stickers.items() if v}
    except (OSError, ValueError):
        ids = {}
    _cache = ids
    return ids


def sticker_for(name: str) -> str | None:
    """El file_id de un sticker concreto, o None si no esta subido."""
    return sticker_ids().get(name)
