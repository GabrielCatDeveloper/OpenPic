#!/usr/bin/env python3
"""Diagnóstico completo del sistema antes de usar el bot.

    python scripts/doctor.py

Comprueba configuracion, base de datos, pesos del modelo, motor de
inferencia, memoria disponible, directorio de fotos, logs y los dos bots de
Telegram. No lanza ninguna inferencia: solo habla con la API de Telegram
(getMe) y con /v1/models del servidor local.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foodbot.config import load_config  # noqa: E402
from foodbot.core.db import Database  # noqa: E402
from foodbot.core.foodr1 import FoodR1Client  # noqa: E402

OK = "\033[32m✓\033[0m"
KO = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"

problems: list[str] = []
warnings: list[str] = []


def check(cond: bool, label: str, hint: str = "") -> bool:
    if cond:
        print(f"  {OK} {label}")
    else:
        print(f"  {KO} {label}")
        if hint:
            print(f"      {hint}")
        problems.append(label)
    return cond


def warn(cond: bool, label: str, hint: str = "") -> None:
    if cond:
        print(f"  {OK} {label}")
    else:
        print(f"  {WARN} {label}")
        if hint:
            print(f"      {hint}")
        warnings.append(label)


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def free_ram() -> tuple[float, float]:
    """(RAM total, RAM libre) en bytes."""
    info = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                info[key.strip()] = int(value.strip().split()[0]) * 1024
    except OSError:
        return (0.0, 0.0)
    return (float(info.get("MemTotal", 0)), float(info.get("MemAvailable", 0)))


def check_config(config) -> None:
    print("\n== Configuracion ==")
    check(config.public_bot_configured, "Token del bot publico", "Falta PUBLIC_BOT_TOKEN")
    check(config.gate_bot_configured, "Token del bot privado", "Falta GATE_BOT_TOKEN")
    check(bool(config.admin_ids), "Id de administrador", "Falta ADMIN_TELEGRAM_IDS")
    if config.admin_ids:
        print(f"      propietario: {config.owner_id}")
    print(f"      zona horaria: {config.timezone}")
    print(f"      un solo usuario: {'si' if config.single_user_mode else 'no'}")
    print(f"      fotos en disco: {'si' if config.store_photos else 'no (solo memoria)'}")
    print(f"      concurrencia del motor: {config.llm_concurrency}")
    if config.single_user_mode and len(config.admin_ids) > 1:
        warn(
            False,
            f"Hay {len(config.admin_ids)} admins pero SINGLE_USER_MODE=true",
            f"Solo se usara el primero ({config.owner_id}).",
        )


def check_database(config) -> None:
    print("\n== Base de datos ==")
    path = config.db_path
    if not path.exists():
        warn(False, f"No existe {path}", "Se creara al arrancar el bot.")
        return
    check(True, f"Existe ({human(path.stat().st_size)})")
    try:
        db = Database(path)
    except Exception as exc:  # noqa: BLE001
        check(False, "Se puede abrir", str(exc))
        return
    users = db.scalar("SELECT COUNT(*) FROM users", default=0)
    meals = db.scalar("SELECT COUNT(*) FROM meals", default=0)
    tokens = db.scalar("SELECT COUNT(*) FROM tokens", default=0)
    print(f"      usuarios: {users} | comidas: {meals} | tokens: {tokens}")
    db.close()


def check_models(config) -> None:
    print("\n== Pesos del modelo ==")
    models = config.models_dir
    if not models.is_dir():
        check(False, f"No existe {models}", "Descarga los pesos con scripts/download_model.py")
        return
    found = sorted(models.glob("*.gguf"))
    if not found:
        check(
            False,
            "No hay ficheros .gguf",
            "python scripts/download_model.py --quant q8_0",
        )
        return
    for path in found:
        print(f"  {OK} {path.name} ({human(path.stat().st_size)})")
    if not any(p.name.startswith("mmproj") for p in found):
        warn(
            False,
            "Falta el proyector de vision",
            "Sin mmproj el modelo no ve las imagenes: "
            "python scripts/download_model.py --skip-model",
        )


def check_engine(config) -> int:
    print("\n== Motor de inferencia ==")
    client = FoodR1Client(config)

    async def scenario():
        try:
            return await client.health()
        finally:
            await client.aclose()

    ok, detail = asyncio.run(scenario())
    if ok:
        print(f"  {OK} Responde ({detail})")
    else:
        check(
            False,
            f"No responde en {config.llm_base_url} ({detail})",
            "Arranca el servidor: scripts/run_llama_server.sh q8_0",
        )
    return 0 if ok else 1


def model_is_loaded() -> bool:
    """¿Hay ya un llama-server corriendo con el modelo en memoria?"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "llama-server.*--model"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def check_memory() -> None:
    print("\n== Memoria ==")
    total, free = free_ram()
    if not total:
        print("  (no se puede leer /proc/meminfo)")
        return
    print(f"  total: {human(total)} | disponible: {human(free)}")
    if model_is_loaded():
        print(f"  {OK} El modelo ya esta cargado en memoria (llama-server corriendo)")
        return
    # El modelo q8_0 (8,7 GB) + proyector (1,1 GB) + buffers.
    needed = 11 * 1024**3
    if free < needed:
        warn(
            False,
            f"Hace falta ~{human(needed)} libres para el modelo",
            "Cierra aplicaciones o arranca el bot con el modelo ya cargado.",
        )
    else:
        print(f"  {OK} Hay hueco de sobra para el modelo q8_0")


def check_dirs(config) -> None:
    print("\n== Directorios ==")
    for label, path in (("Fotos", config.photos_dir), ("Logs", config.logs_dir)):
        if path.is_dir():
            size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            count = sum(1 for p in path.rglob("*") if p.is_file())
            print(f"  {OK} {label}: {path} ({count} ficheros, {human(size)})")
            if label == "Fotos" and count:
                warn(False, f"Hay {count} ficheros en el directorio de fotos",
                     "Se purgan al arrancar el bot (TTL).")
        else:
            print(f"  {WARN} {label}: {path} (no existe)")


def check_telegram(config) -> None:
    print("\n== Bots de Telegram ==")
    for label, token in (
        ("publico", config.public_bot_token),
        ("privado", config.gate_bot_token),
    ):
        if not token or token.startswith("PUT_"):
            print(f"  {WARN} {label}: sin token")
            continue
        try:
            import urllib.request

            url = f"https://api.telegram.org/bot{token}/getMe"
            with urllib.request.urlopen(url, timeout=10) as response:
                import json

                data = json.loads(response.read().decode("utf-8"))
            username = data.get("result", {}).get("username", "?")
            print(f"  {OK} {label}: @{username}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {KO} {label}: {exc}")


def check_llama_binary() -> None:
    print("\n== llama.cpp ==")
    server = Path(os.environ.get("LLAMA_DIR", Path.home() / "llama.cpp")) / "build/bin/llama-server"
    if server.exists():
        print(f"  {OK} {server}")
    else:
        warn(False, f"No esta compilado ({server})", "scripts/setup_llama.sh vulkan")


def main() -> int:
    config = load_config()
    print("=" * 62)
    print(" openPic · diagnostico")
    print("=" * 62)

    check_config(config)
    check_database(config)
    check_models(config)
    check_llama_binary()
    check_engine(config)
    check_memory()
    check_dirs(config)
    check_telegram(config)

    print("\n" + "=" * 62)
    if problems:
        print(f" {len(problems)} problema(s):")
        for problem in problems:
            print(f"  - {problem}")
    if warnings:
        print(f" {len(warnings)} aviso(s):")
        for warning in warnings:
            print(f"  - {warning}")
    if not problems and not warnings:
        print(" Todo listo. Arranca con: python -m foodbot.main")
    elif not problems:
        print(" Todo lo importante esta bien.")
    print("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
