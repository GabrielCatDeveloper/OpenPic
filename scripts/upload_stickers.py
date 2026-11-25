#!/usr/bin/env python3
"""Sube el set de stickers al bot y guarda los file_ids.

    python scripts/upload_stickers.py                       # usa .env
    python scripts/upload_stickers.py --images /ruta/dir

Telegram exige PNG de 512x512 con alfa y <= 256 KB (lo prepara
scripts/prepare_stickers.py). Los file_ids se guardan en
foodbot/data/stickers.json, que es lo que usa el bot al enviar el sticker.

Para subir un sticker en nombre de un usuario, ese usuario tiene que haber
hablado con el bot (el propietario del bot, normalmente).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from foodbot.config import load_config  # noqa: E402

# sticker -> (emoji para el set, texto alternativo)
STICKERS: dict[str, tuple[str, str]] = {
    "contento": ("😊", "Va bien"),
    "preocupado": ("😟", "Preocupado"),
    "triste": ("😕", "Triste"),
}
SET_TITLE = "Estado del dia"
EMOJI_BY_NAME = {"contento": "🙂", "preocupado": "😟", "triste": "😕"}


def read_token() -> str:
    config = load_config(ROOT)
    token = config.public_bot_token
    if not token:
        raise SystemExit("Falta PUBLIC_BOT_TOKEN en el .env")
    return token


def upload_file(client: httpx.Client, token: str, user_id: int, path: Path) -> str:
    with path.open("rb") as handle:
        response = client.post(
            f"https://api.telegram.org/bot{token}/uploadStickerFile",
            files={
                "sticker": (path.name, handle, "image/png"),
            },
            data={
                "user_id": str(user_id),
                "sticker_format": "static",
                "emoji_list": EMOJI_BY_NAME[path.stem],
            },
        )
    data = response.json()
    if not data.get("ok"):
        raise SystemExit(f"uploadStickerFile fallo: {data.get('description')}")
    return str(data["result"]["file_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Sube los stickers al bot")
    parser.add_argument("--images", default="/tmp/opencode/stickers/out")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--out", default=str(ROOT / "foodbot" / "data" / "stickers.json"))
    args = parser.parse_args()

    config = load_config(ROOT)
    token = read_token()
    user_id = args.user_id or (sorted(config.admin_ids)[0] if config.admin_ids else None)
    if not user_id:
        raise SystemExit("Falta el id del propietario (ADMIN_TELEGRAM_IDS)")

    images = Path(args.images)
    files = {name: images / f"{name}.png" for name in STICKERS}
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise SystemExit(f"Faltan imagenes: {', '.join(missing)}")

    with httpx.Client(timeout=60) as client:
        me = client.get(f"https://api.telegram.org/bot{token}/getMe").json()
        username = me["result"]["username"]
        set_name = f"openpic_estado_by_{username}"

        # Si el set ya existe, se borra para poder recrearlo.
        existing = client.post(
            f"https://api.telegram.org/bot{token}/deleteStickerSet",
            json={"name": set_name},
        ).json()
        if not existing.get("ok"):
            print(f"(info) no habia set previo: {existing.get('description')}")

        uploaded = {
            name: upload_file(client, token, user_id, path) for name, path in files.items()
        }

        stickers = [
            {
                "sticker": file_id,
                "emoji_list": [EMOJI_BY_NAME[name]],
                "format": "static",
            }
            for name, file_id in uploaded.items()
        ]
        created = client.post(
            f"https://api.telegram.org/bot{token}/createNewStickerSet",
            json={
                "user_id": user_id,
                "name": set_name,
                "title": SET_TITLE,
                "stickers": stickers,
                "sticker_format": "static",
            },
        ).json()
        if not created.get("ok"):
            raise SystemExit(f"createNewStickerSet fallo: {created.get('description')}")

    payload = {
        "set_name": set_name,
        "owner_user_id": user_id,
        "stickers": uploaded,
        "emojis": {name: EMOJI_BY_NAME[name] for name in STICKERS},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Set creado: {set_name}")
    print(f"file_ids guardados en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
