"""Privacidad: retencion de fotos y borrado de datos del usuario.

Dos garantias:

1. **Las fotos no se quedan en el disco.** El analisis se hace en memoria: la
   imagen se descarga de Telegram, se procesa y se tira. Si se activa
   `STORE_PHOTOS` (por defecto desactivado) se guarda una copia para
   depuracion, pero se purga sola pasado `PHOTO_TTL_HOURS`.

2. **Cada usuario puede borrar sus datos.** `erase_user_data` elimina comidas,
   pesos, objetivos, uso y respuestas cacheadas de ese usuario, y revoca el
   token que lo/unio al bot. No toca los datos de nadie mas.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from foodbot.core.db import Database

# Nombre de las fotos que guarda el bot: <user_id>_<timestamp>.<ext>
PHOTO_NAME_RE = re.compile(r"^\d+_\d+\.[A-Za-z0-9]+$")


@dataclass
class PhotoStore:
    """Copias locales de las fotos, con caducidad automatica.

    Por defecto esta desactivado (`enabled=False`): las fotos se analizan en
    memoria y nunca tocan el disco.
    """

    directory: Path
    enabled: bool = False
    ttl_hours: int = 1

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def ttl_seconds(self) -> int:
        return max(0, int(self.ttl_hours)) * 3600

    def save(self, user_id: int, data: bytes, suffix: str = ".jpg") -> Path | None:
        """Guarda la foto y devuelve su ruta (None si el almacen esta apagado)."""
        if not self.enabled:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{user_id}_{int(time.time())}{suffix}"
        path.write_bytes(data)
        self.purge_expired()
        return path

    def delete(self, path: Path | None) -> bool:
        """Borra una foto concreta. Devuelve True si existia."""
        if path is None:
            return False
        try:
            Path(path).unlink()
            return True
        except (OSError, FileNotFoundError):
            return False

    def purge_expired(self, now: float | None = None) -> int:
        """Borra las fotos mas antiguas que el TTL. Devuelve cuantas ha borrado."""
        if not self.directory.is_dir():
            return 0
        current = now if now is not None else time.time()
        removed = 0
        for path in self.directory.iterdir():
            if not path.is_file() or not PHOTO_NAME_RE.match(path.name):
                continue
            try:
                age = current - path.stat().st_mtime
            except OSError:
                continue
            if age > self.ttl_seconds:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def purge_user(self, user_id: int) -> int:
        """Borra todas las fotos de un usuario concreto."""
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.glob(f"{user_id}_*"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def purge_all(self) -> int:
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.iterdir():
            if path.is_file() and PHOTO_NAME_RE.match(path.name):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def total_bytes(self) -> int:
        if not self.directory.is_dir():
            return 0
        return sum(
            p.stat().st_size
            for p in self.directory.iterdir()
            if p.is_file() and PHOTO_NAME_RE.match(p.name)
        )


def erase_user_data(db: Database, user_id: int) -> dict[str, int]:
    """Borra todos los datos personales de un usuario.

    Devuelve un recuento por tabla de lo eliminado. Es idempotente: si el
    usuario no tiene datos, devuelve todo a cero.
    """
    counts: dict[str, int] = {}
    counts["meals"] = db.execute("DELETE FROM meals WHERE user_id = ?", (user_id,)).rowcount
    counts["weights"] = db.execute("DELETE FROM weights WHERE user_id = ?", (user_id,)).rowcount
    counts["targets"] = db.execute("DELETE FROM targets WHERE user_id = ?", (user_id,)).rowcount
    counts["usage"] = db.execute("DELETE FROM usage WHERE user_id = ?", (user_id,)).rowcount
    counts["cache"] = db.execute("DELETE FROM cache WHERE user_id = ?", (user_id,)).rowcount
    # El token deja de estar vinculado: el usuario ya no tiene acceso al bot.
    counts["tokens"] = db.execute(
        "UPDATE tokens SET revoked = 1, bound_user_id = NULL WHERE bound_user_id = ?",
        (user_id,),
    ).rowcount
    counts["user"] = db.execute("DELETE FROM users WHERE user_id = ?", (user_id,)).rowcount
    return counts


def storage_report(db: Database, user_id: int) -> dict[str, int]:
    """Que datos tiene guardados este usuario (para el comando /privacidad)."""
    return {
        "meals": int(db.scalar("SELECT COUNT(*) FROM meals WHERE user_id = ?", (user_id,), 0)),
        "weights": int(db.scalar("SELECT COUNT(*) FROM weights WHERE user_id = ?", (user_id,), 0)),
        "targets": int(db.scalar("SELECT COUNT(*) FROM targets WHERE user_id = ?", (user_id,), 0)),
        "usage": int(db.scalar("SELECT COUNT(*) FROM usage WHERE user_id = ?", (user_id,), 0)),
        "cache": int(db.scalar("SELECT COUNT(*) FROM cache WHERE user_id = ?", (user_id,), 0)),
    }


def reset_photos_directory(directory: Path) -> int:
    """Vacia las fotos guardadas por el bot (solo los ficheros que el bot crea).

    Borra unicamente ficheros con el patron `<user_id>_<timestamp>.<ext>`: si
    PHOTOS_DIR apunta por error a otra carpeta, no se toca nada ajeno.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.iterdir():
        if path.is_file() and PHOTO_NAME_RE.match(path.name):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
