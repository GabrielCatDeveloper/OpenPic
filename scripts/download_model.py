#!/usr/bin/env python3
"""Descarga los pesos de Food-R1 (GGUF) desde Hugging Face.

    python scripts/download_model.py --quant bf16
    python scripts/download_model.py --quant q6_k        # recomendado para 16 GB de RAM
    python scripts/download_model.py --quant bf16 --mmproj q8_0

Los ficheros cuelgan del repositorio comunitario AKMESSI/Food-R1-GGUF,
que es una conversion no oficial de zy12123/Food-R1 (Apache-2.0).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "AKMESSI/Food-R1-GGUF"
BASE = f"https://huggingface.co/{REPO}/resolve/main"
CHECKSUMS = "checksums.sha256"

MODELS = {
    "bf16": "output/Food-R1-BF16.gguf",       # 16.4 GB - el que cabe en VRAM 16 GB
    "q8_0": "output/Food-R1-Q8_0.gguf",       # 8.7 GB
    "q6_k": "output/Food-R1-Q6_K.gguf",       # 6.7 GB - recomendado
    "q5_k_m": "output/Food-R1-Q5_K_M.gguf",   # 5.9 GB
    "q4_k_m": "output/Food-R1-Q4_K_M.gguf",   # 5.0 GB
}

MMPROJ = {
    "f16": "output/mmproj-Food-R1-F16.gguf",          # 1.16 GB
    "q8_0": "output/mmproj-Food-R1-Q8_0-mixed.gguf",  # 752 MB
}

CHUNK = 1024 * 1024


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def expected_checksums() -> dict[str, str]:
    """Lee checksums.sha256 del repo: {'output/xxx.gguf': 'sha256...'}."""
    try:
        with urllib.request.urlopen(f"{BASE}/{CHECKSUMS}", timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  (no se pudo leer checksums.sha256: {exc})", file=sys.stderr)
        return {}
    checks: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            checks[parts[-1].lstrip("*").strip()] = parts[0].strip().lower()
    return checks


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(remote_path: str, dest_dir: Path, expected: str | None) -> Path:
    """Descarga serie (con reanudacion) de un fichero."""
    dest = dest_dir / Path(remote_path).name
    url = f"{BASE}/{remote_path}?download=true"

    if dest.exists():
        print(f"  Ya existe: {dest} ({human(dest.stat().st_size)})")
        if expected and sha256_file(dest) != expected:
            print("  El checksum no coincide; se vuelve a descargar.")
            dest.unlink()
        else:
            return dest

    print(f"  Descargando {remote_path} -> {dest}")
    headers = {"User-Agent": "foodbot/1.0"}
    mode = "wb"
    existing = 0
    if dest.exists():
        existing = dest.stat().st_size
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0) + existing
            downloaded = existing
            last_report = 0.0
            started = time.monotonic()
            with dest.open(mode) as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    elapsed = now - started
                    if elapsed - last_report > 0.5:
                        last_report = elapsed
                        pct = (downloaded / total * 100) if total else 0
                        speed = (downloaded - existing) / max(elapsed, 0.001) / 1e6
                        sys.stdout.write(
                            f"\r    {human(downloaded)}/{human(total)} ({pct:.1f} %) "
                            f"{speed:.1f} MB/s   "
                        )
                        sys.stdout.flush()
        print()
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and dest.exists():
            print("  Ya estaba completo.")
            return dest
        raise

    if expected:
        actual = sha256_file(dest)
        if actual != expected:
            print(f"  !! checksum incorrecto: {actual} != {expected}", file=sys.stderr)
        else:
            print("  checksum OK")
    return dest


class _Progress:
    """Contador de bytes compartido entre hilos."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self._lock = threading.Lock()
        self._last = 0.0
        self._started = time.monotonic()

    def add(self, n: int) -> None:
        with self._lock:
            self.done += n
            now = time.monotonic()
            if now - self._last > 0.5:
                self._last = now
                elapsed = now - self._started
                pct = (self.done / self.total * 100) if self.total else 0
                speed = self.done / max(elapsed, 0.001) / 1e6
                sys.stdout.write(
                    f"\r    {human(self.done)}/{human(self.total)} ({pct:.1f} %) "
                    f"{speed:.1f} MB/s   "
                )
                sys.stdout.flush()


def _fetch_range(url: str, path: Path, start: int, end: int, progress: _Progress) -> None:
    headers = {"User-Agent": "foodbot/1.0", "Range": f"bytes={start}-{end}"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        with open(path, "r+b") as handle:
            handle.seek(start)
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                progress.add(len(chunk))


def download_parallel(
    remote_path: str, dest_dir: Path, expected: str | None, parts: int = 8
) -> Path:
    """Descarga por partes en paralelo.

    Hugging Face limita el ancho de banda POR CONEXION (~1 MB/s), asi que
    abrir N conexiones en paralelo multiplica la velocidad real.
    """
    dest = dest_dir / Path(remote_path).name
    url = f"{BASE}/{remote_path}?download=true"

    if dest.exists():
        print(f"  Ya existe: {dest} ({human(dest.stat().st_size)})")
        if expected and sha256_file(dest) != expected:
            print("  El checksum no coincide; se vuelve a descargar.")
            dest.unlink()
        else:
            return dest

    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "foodbot/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
    if not total:
        print("  (el servidor no informa de Content-Length; uso descarga serie)")
        return download(remote_path, dest_dir, expected)

    print(f"  Descargando {remote_path} -> {dest} ({human(total)} en {parts} conexiones)")
    with open(dest, "wb") as handle:
        handle.truncate(total)

    chunk_size = (total + parts - 1) // parts
    progress = _Progress(total)
    started = time.monotonic()
    ranges = [
        (i * chunk_size, min(total - 1, (i + 1) * chunk_size - 1))
        for i in range(parts)
        if i * chunk_size < total
    ]
    errors: list[BaseException] = []

    def worker(start: int, end: int) -> None:
        try:
            _fetch_range(url, dest, start, end, progress)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        list(pool.map(lambda r: worker(r[0], r[1]), ranges))
    print()

    if errors:
        for exc in errors:
            print(f"  !! error en una conexion: {exc}", file=sys.stderr)
        print("  (se puede reintentar: el fichero parcial se conserva)", file=sys.stderr)
        return dest

    elapsed = max(time.monotonic() - started, 0.001)
    speed = total / elapsed / 1e6
    print(f"  {human(total)} en {elapsed:.0f} s -> {speed:.1f} MB/s")

    if expected:
        actual = sha256_file(dest)
        if actual != expected:
            print(f"  !! checksum incorrecto: {actual} != {expected}", file=sys.stderr)
        else:
            print("  checksum OK")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga los pesos de Food-R1")
    parser.add_argument(
        "--quant",
        default="bf16",
        choices=sorted(MODELS),
        help="cuantizacion del modelo (bf16 = 16.4 GB, el que va a una VRAM de 16 GB)",
    )
    parser.add_argument(
        "--mmproj",
        default="f16",
        choices=sorted(MMPROJ),
        help="proyector de vision (obligatorio: sin el no se ven las imagenes)",
    )
    parser.add_argument("--dir", default="models", help="directorio de destino")
    parser.add_argument(
        "--connections",
        type=int,
        default=8,
        help="conexiones en paralelo (HF limita el ancho de banda por conexion)",
    )
    parser.add_argument("--skip-model", action="store_true", help="solo el proyector")
    parser.add_argument("--skip-mmproj", action="store_true", help="solo el modelo")
    args = parser.parse_args()

    dest_dir = Path(args.dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    free = shutil.disk_usage(dest_dir).free
    print(f"Destino: {dest_dir}")
    print(f"Espacio libre: {human(free)}\n")

    checksums = expected_checksums()

    if not args.skip_model:
        remote = MODELS[args.quant]
        download_parallel(remote, dest_dir, checksums.get(remote), max(1, args.connections))
    if not args.skip_mmproj:
        remote = MMPROJ[args.mmproj]
        download_parallel(remote, dest_dir, checksums.get(remote), max(1, args.connections))

    print("\nListo. Arranca el servidor con scripts/run_llama_server.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
