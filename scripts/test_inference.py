#!/usr/bin/env python3
"""Prueba el motor de inferencia con una imagen real.

    python scripts/test_inference.py --image ~/Imagens/comida.jpg
    python scripts/test_inference.py --image foto.jpg --base-url http://127.0.0.1:8080/v1

Mide el tiempo total y el tiempo de generacion para saber si el hardware
da para produccion.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foodbot.core.foodr1 import (  # noqa: E402
    CALORIE_PROMPT,
    CALORIE_SYSTEM,
    FoodR1Client,
    parse_food_answer,
)
from foodbot.config import load_config  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--note", default=None, help="Contexto extra (p. ej. '150 g de pechuga')")
    args = parser.parse_args()

    path = Path(args.image).expanduser()
    if not path.is_file():
        print(f"No existe: {path}", file=sys.stderr)
        return 1

    config = load_config()
    if args.base_url:
        object.__setattr__(config, "llm_base_url", args.base_url.rstrip("/"))
    if args.model:
        object.__setattr__(config, "llm_model", args.model)

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = path.read_bytes()
    client = FoodR1Client(config)

    ok, detail = await client.health()
    print(f"Motor: {config.llm_base_url} | {detail}")
    if not ok:
        print("El motor no responde. Arranca scripts/run_llama_server.sh", file=sys.stderr)
        return 2

    print(f"Imagen: {path.name} ({len(data) / 1024:.0f} KB)")

    start = time.monotonic()
    image_b64 = base64.b64encode(data).decode("ascii")
    prompt = CALORIE_PROMPT
    if args.note:
        prompt += f"\n Additional context from the user: {note}"
    raw = await client.complete(CALORIE_SYSTEM, prompt, image_b64, mime)
    elapsed = time.monotonic() - start

    print(f"\n--- Respuesta cruda ({elapsed:.1f} s) ---")
    print(raw.strip())

    analysis = parse_food_answer(raw)
    print("\n--- Analisis parseado ---")
    print(f"Plato      : {analysis.dish}")
    print(f"Calorias   : {analysis.calories} kcal")
    print(
        f"Macros     : P {analysis.protein_g:.1f} g | C {analysis.carbs_g:.1f} g | "
        f"G {analysis.fat_g:.1f} g"
    )
    print(f"Masa       : {analysis.mass_g:.0f} g")
    print(f"Confianza  : {analysis.confidence:.0%}")
    if analysis.ingredients:
        print("\nIngredientes:")
        for ing in analysis.ingredients:
            print(f"  - {ing.name:<28} {ing.grams:>6.0f} g  {ing.kcal:>6.0f} kcal")
    for warning in analysis.warnings:
        print(f"AVISO: {warning}")
    return 0


async def await_health(client: FoodR1Client):
    return await client.health()


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
