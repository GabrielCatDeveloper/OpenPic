#!/usr/bin/env python3
"""Prepara las imagenes del set de stickers para Telegram.

Telegram exige: PNG de 512x512, con canal alfa y <= 256 KB.

Los ficheros que genera el modelo de imagen vienen con el fondo en tablero
de ajedrez DIBUJADO (no es transparencia real), asi que aqui se quita: se
queda con los pixeles saturados (la cara: amarillo y naranja) y con los muy
oscuros (el contorno), y el resto se vuelve transparente. El recorte se
hace sobre la mascara binaria (no sobre el alfa suavizado) para que los
artefactos del JPEG no agranden la caja y la cara quede grande.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

SIZE = 512
MAX_BYTES = 256 * 1024
MARGIN = 0.05


def face_mask(image: Image.Image) -> Image.Image:
    """Mascara binaria de la cara: saturado o muy oscuro."""
    hsv = image.convert("HSV")
    saturation = hsv.getchannel("S").point(lambda x: 255 if x > 76 else 0)
    darkness = hsv.getchannel("V").point(lambda x: 255 if x < 120 else 0)
    return ImageChops.lighter(saturation, darkness)


def trim_and_center(image: Image.Image) -> Image.Image:
    """Recorta a la cara y la centra en un lienzo 512x512 transparente."""
    mask = face_mask(image)
    bbox = mask.getbbox()
    if not bbox:
        return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Un poco de aire alrededor del recorte.
    pad = int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.02)
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(image.width, bbox[2] + pad)
    bottom = min(image.height, bbox[3] + pad)
    cropped = image.crop((left, top, right, bottom))

    # El alfa sale de la mascara suavizada: bordes limpios sin dientes.
    alpha = mask.crop((left, top, right, bottom)).filter(ImageFilter.GaussianBlur(2))
    rgba = cropped.convert("RGBA")
    rgba.putalpha(alpha)

    inner = int(SIZE * (1 - 2 * MARGIN))
    scale = min(inner / rgba.width, inner / rgba.height)
    new_size = (max(1, int(rgba.width * scale)), max(1, int(rgba.height * scale)))
    resized = rgba.resize(new_size, Image.LANCZOS)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(resized, ((SIZE - new_size[0]) // 2, (SIZE - new_size[1]) // 2), resized)
    return canvas


def save_telegram_ready(image: Image.Image, path: Path) -> int:
    """Guarda el PNG optimizando el tamano hasta caber en 256 KB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    for colors in (None, 128, 64, 32):
        candidate = image
        if colors:
            alpha = candidate.getchannel("A")
            quantized = candidate.convert("RGB").quantize(
                colors=colors, method=Image.MEDIANCUT
            ).convert("RGBA")
            quantized.putalpha(alpha)
            candidate = quantized
        candidate.save(path, "PNG", optimize=True)
        size = path.stat().st_size
        if size <= MAX_BYTES:
            break
    return size


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/stickers")
    target = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/opencode/stickers/out")
    if not source.is_dir():
        print(f"No existe {source}")
        return 1

    for path in sorted(source.glob("*.jpg")) + sorted(source.glob("*.png")):
        if target in path.parents:
            continue
        image = Image.open(path).convert("RGBA")
        image = trim_and_center(image)
        out = target / f"{path.stem}.png"
        size = save_telegram_ready(image, out)
        bbox = image.getchannel("A").getbbox()
        coverage = (bbox[2] - bbox[0]) if bbox else 0
        print(
            f"{path.name} -> {out.name}  {image.size[0]}x{image.size[1]}  "
            f"{size / 1024:.0f} KB  cara={coverage}px"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
