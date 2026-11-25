"""Punto de entrada: arranca el bot publico y el bot privado a la vez.

    python -m foodbot.main            # arranca los dos bots
    python -m foodbot.main --check    # comprueba configuracion y motor
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from foodbot.bots.gate import GateBot
from foodbot.bots.public import PublicBot
from foodbot.config import Config, load_config
from foodbot.core.db import Database
from foodbot.core.foodr1 import FoodR1Client
from foodbot.core.privacy import reset_photos_directory

log = logging.getLogger("foodbot")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def check(config: Config) -> int:
    """Comprueba la configuracion sin arrancar nada."""
    problems: list[str] = []

    print("== Configuracion ==")
    print(f"Base de datos   : {config.db_path}")
    print(f"Proveedor       : {config.provider}")
    print(f"Endpoint        : {config.llm_base_url}")
    print(f"Modelo          : {config.llm_model}")
    print(f"Bot publico     : {'configurado' if config.public_bot_configured else 'FALTA PUBLIC_BOT_TOKEN'}")
    print(f"Bot privado     : {'configurado' if config.gate_bot_configured else 'FALTA GATE_BOT_TOKEN'}")
    print(f"Admins          : {sorted(config.admin_ids) or 'FALTA ADMIN_TELEGRAM_IDS'}")
    print(f"Propietario     : {config.owner_id if config.owner_id else '-'}")
    print(f"Zona horaria    : {config.timezone}")
    print(f"Un solo usuario : {'si' if config.single_user_mode else 'no'}")
    print(f"Fotos en disco  : {'si (TTL %dh)' % config.photo_ttl_hours if config.store_photos else 'no (solo memoria)'}")

    if not config.public_bot_configured:
        problems.append("Falta PUBLIC_BOT_TOKEN en .env")
    if not config.gate_bot_configured:
        problems.append("Falta GATE_BOT_TOKEN en .env")
    if not config.admin_ids:
        problems.append("Falta ADMIN_TELEGRAM_IDS en .env (id numerico de Telegram)")
    if config.single_user_mode and len(config.admin_ids) > 1:
        problems.append(
            "SINGLE_USER_MODE=true pero hay "
            f"{len(config.admin_ids)} ids en ADMIN_TELEGRAM_IDS: solo se usara el "
            f"primero ({config.owner_id}). Deja uno solo."
        )
    if config.llm_concurrency < 1:
        problems.append("LLM_CONCURRENCY debe ser >= 1")

    db = Database(config.db_path)
    print(f"Base de datos   : OK ({db.scalar('SELECT COUNT(*) FROM users', default=0)} usuarios)")
    db.close()

    print("\n== Planes ==")
    for plan in config.plans.values():
        print(f"  {plan.name:<10} {plan.label:<10} fotos/dia={plan.daily_photo_limit}")

    if problems:
        print("\n== Problemas ==")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print("\nTodo correcto.")
    return 0


async def check_engine(config: Config) -> bool:
    """Comprueba que el motor de inferencia responde."""
    client = FoodR1Client(config)
    try:
        ok, detail = await client.health()
    finally:
        await client.aclose()
    print("\n== Motor de inferencia ==")
    if ok:
        print(f"  OK ({detail})")
    else:
        print(f"  ! No responde: {detail}")
        print(
            "    Arranca el servidor con:  scripts/run_llama_server.sh q8_0\n"
            "    (o pon LLM_PROVIDER=mock para probar el bot sin modelo)"
        )
    return ok


async def run(config: Config) -> None:
    db = Database(config.db_path)

    # Privacidad: las fotos que hubiera de una sesion anterior no se quedan
    # en el disco (se analizan en memoria y se borran solas).
    removed = reset_photos_directory(config.photos_dir)
    if removed:
        log.info("Fotos antiguas borradas: %d", removed)

    public = PublicBot(config, db)
    gate = GateBot(config, db) if config.gate_bot_configured else None

    apps = [public.app] + ([gate.app] if gate else [])

    for app in apps:
        await app.initialize()
    for app in apps:
        await app.start()
    for app in apps:
        await app.updater.start_polling(
            # No se descartan los mensajes pendientes: si el bot estaba parado
            # cuando el usuario envio una foto, no se pierde.
            drop_pending_updates=False,
            allowed_updates=["message", "callback_query"],
        )

    log.info("Bots arrancados. Ctrl+C para parar.")
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Parando...")
    finally:
        for app in apps:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:  # noqa: BLE001
                log.debug("Fallo al cerrar una app", exc_info=True)
        await public.llm.aclose()
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot de nutrition con Food-R1")
    parser.add_argument("--check", action="store_true", help="Comprueba la configuracion")
    parser.add_argument("--verbose", action="store_true", help="Log detallado")
    args = parser.parse_args()

    setup_logging(args.verbose)
    config = load_config()

    if args.check:
        status = check(config)
        asyncio.run(check_engine(config))
        return status

    if not config.public_bot_configured:
        log.error("Falta PUBLIC_BOT_TOKEN. Copia .env.example a .env y rellenalo.")
        return 1
    if not config.admin_ids:
        log.error(
            "Falta ADMIN_TELEGRAM_IDS: define tu id numerico de Telegram "
            "(escribe /start al @userinfobot) para poder usar el bot privado."
        )
        return 1

    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        log.info("Parado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
