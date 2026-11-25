"""Catalogo de productos: lo que el usuario nombra, en vez de una foto.

Hay tres niveles, de mas a menos preciso:

1. **Los productos del usuario** (los que ha dado de alta con su etiqueta): con
   el peso de la porcion, asi que son exactos.
2. **La tabla de ingredientes** del proyecto (~170 alimentos con kcal y macros
   por 100 g): sirve para lo que ya se conoce (un yogur, unas galletas).
3. **La estimacion del modelo**, si no hay nada.

Al escribir "3 magdalenas del mercadona" el bot busca el producto, calcula
(3 x peso de la porcion) x (kcal por 100 g) y lo propone. Si no lo encuentra,
lo da de alta: con la etiqueta, si la tiene, o estimado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from foodbot.data.ingredients import INGREDIENTS, normalize

# --- la frase que el usuario escribe ---------------------------------------
QUANTITIES = {
    "un": 1, "uno": 1, "una": 1, "unos": 1, "una": 1, "1": 1,
    "dos": 2, "2": 2, "tres": 3, "3": 3, "cuatro": 4, "4": 4,
    "cinco": 5, "5": 5, "seis": 6, "6": 6, "siete": 7, "7": 7,
    "ocho": 8, "8": 8, "nueve": 9, "9": 9, "diez": 10, "10": 10,
    "medio": 0.5, "media": 0.5, "1/2": 0.5, "1/4": 0.25, "3/4": 0.75,
}

# Peso tipico de una unidad de cosas que se cuentan por unidades.
TYPICAL_UNIT_WEIGHTS = {
    "huevo": 55, "huevos": 55,
    "madgalena": 55, "magdalena": 55, "magdalenas": 55,
    "galleta": 12, "galletas": 12,
    "yogur": 125, "yogures": 125,
    "natilla": 115, "natillas": 115,
    "copa": 100, "copas": 100,
    "taza": 200, "tazas": 200,
    "vaso": 200, "vasos": 200,
    "bote": 400, "botes": 400,
    "lata": 400, "latas": 400,
    "packet": 30, "sobre": 30, "sobres": 30,
    "cacao": 30,
    "barra": 35, "barritas": 35, "barrita": 35,
    "donut": 60, "donas": 60, "dona": 60,
    "croissant": 60, "cruasanes": 60, "cruasán": 60,
    "chocolatina": 25, "chocolatinas": 25,
    "plátano": 120, "platano": 120,
    "manzana": 180, "manzanas": 180,
}


@dataclass
class ProductRequest:
    """Lo que el usuario ha escrito: '3 magdalenas del mercadona'."""

    quantity: float
    name: str
    brand: str | None
    raw: str
    weight_g: float | None = None

    @property
    def search_key(self) -> str:
        return normalize(self.name)


@dataclass
class ProductMatch:
    """Un producto del catalogo que encaja con lo pedido."""

    name: str
    brand: str | None
    kcal_100g: float
    protein_100g: float = 0.0
    carbs_100g: float = 0.0
    fat_100g: float = 0.0
    portion_g: float | None = None
    source: str = "tabla"
    score: float = 0.0

    def portion_calories(self, quantity: float) -> float:
        """Las kcal de la cantidad pedida (con el peso de la porcion)."""
        if self.portion_g:
            grams = self.portion_g * quantity
        else:
            grams = 0.0
        if not grams:
            return 0.0
        return self.kcal_100g * grams / 100.0


def parse_request(text: str) -> ProductRequest | None:
    """Lee '3 magdalenas del mercadona' -> (3, 'magdalenas', 'mercadona')."""
    raw = (text or "").strip()
    if not raw:
        return None

    cleaned = raw.strip(".").strip()
    match = re.match(r"^(\d+\s*/\s*\d+|\d+(?:[.,]\d+)?|[a-zA-Zñáéíóú]+)\s+(.*)$", cleaned)
    if not match:
        return None
    qty_text, rest = match.group(1).strip().lower(), match.group(2).strip()

    if re.match(r"^\d+\s*/\s*\d+$", qty_text):
        num, _, den = qty_text.partition("/")
        quantity = float(num) / float(den)
    elif re.match(r"^\d+(?:[.,]\d+)?$", qty_text):
        quantity = float(qty_text.replace(",", "."))
    else:
        quantity = float(QUANTITIES.get(qty_text, 1))

    # "de magdalenas del mercadona" -> nombre y marca.
    rest = re.sub(r"^(de|del|la|el|los|las)\s+", "", rest, flags=re.I).strip()
    brand = None
    parts = re.split(r"\s+(?:de|del)\s+", rest, maxsplit=1, flags=re.I)
    name = parts[0].strip()
    if len(parts) > 1 and parts[1].strip():
        brand = parts[1].strip()

    if not name:
        return None
    return ProductRequest(quantity=quantity, name=name, brand=brand, raw=raw)


def _score(request: ProductRequest, name: str, brand: str | None) -> float:
    """Como encaja un producto con lo pedido."""
    key = request.search_key
    target = normalize(name)
    if not key or not target:
        return 0.0
    score = 0.0
    if key == target:
        score = 10.0
    elif target.startswith(key):
        score = 7.0
    elif key in target:
        score = 5.0
    elif target in key:
        score = 3.0
    else:
        # Coincidencia por palabras sueltas ("galletas de avena" ~ "galleta").
        words = [w for w in key.split() if len(w) > 3]
        hits = sum(1 for w in words if w in target)
        if hits and words:
            score = 2.0 * hits / len(words)
    if score and brand:
        if normalize(brand) in normalize(name):
            score += 3.0
        elif normalize(brand) in target:
            score += 1.0
    return score


def match_table(request: ProductRequest) -> ProductMatch | None:
    """Busca en la tabla de ingredientes del proyecto."""
    best: ProductMatch | None = None
    for name, (kcal, protein, carbs, fat) in INGREDIENTS.items():
        score = _score(request, name, None)
        if not score:
            continue
        candidate = ProductMatch(
            name=name,
            brand=None,
            kcal_100g=kcal,
            protein_100g=protein,
            carbs_100g=carbs,
            fat_100g=fat,
            portion_g=TYPICAL_UNIT_WEIGHTS.get(normalize(name)),
            source="tabla",
            score=score,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def match_products(
    request: ProductRequest, rows: list[dict[str, Any]]
) -> ProductMatch | None:
    """Busca entre los productos que el usuario ha dado de alta."""
    best: ProductMatch | None = None
    for row in rows:
        score = _score(request, str(row.get("name") or ""), row.get("brand"))
        if not score:
            continue
        candidate = ProductMatch(
            name=str(row.get("name") or ""),
            brand=row.get("brand"),
            kcal_100g=float(row.get("kcal_100g") or 0),
            protein_100g=float(row.get("protein_100g") or 0),
            carbs_100g=float(row.get("carbs_100g") or 0),
            fat_100g=float(row.get("fat_100g") or 0),
            portion_g=row.get("portion_g"),
            source="producto",
            score=score,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def find_product(
    request: ProductRequest, user_products: list[dict[str, Any]] | None = None
) -> ProductMatch | None:
    """Primero los productos del usuario (son mas exactos), luego la tabla."""
    match = match_products(request, user_products or [])
    table = match_table(request)
    if match and table:
        return match if match.score >= table.score else table
    return match or table


def estimate_weight(request: ProductRequest) -> float | None:
    """Peso tipico de una unidad, si se conoce."""
    key = normalize(request.name)
    if key in TYPICAL_UNIT_WEIGHTS:
        return TYPICAL_UNIT_WEIGHTS[key]
    for word in key.split():
        if word in TYPICAL_UNIT_WEIGHTS:
            return TYPICAL_UNIT_WEIGHTS[word]
    return None


def describe(match: ProductMatch, request: ProductRequest) -> dict[str, Any]:
    """El calculo listo para enseñarselo al usuario."""
    grams = None
    if match.portion_g:
        grams = match.portion_g * request.quantity
    calories = round(match.kcal_100g * grams / 100.0) if grams else 0
    return {
        "name": match.name,
        "brand": match.brand,
        "quantity": request.quantity,
        "grams": grams,
        "calories": calories,
        "protein_g": round(match.protein_100g * grams / 100.0, 1) if grams else 0,
        "carbs_g": round(match.carbs_100g * grams / 100.0, 1) if grams else 0,
        "fat_g": round(match.fat_100g * grams / 100.0, 1) if grams else 0,
        "source": match.source,
        "exact": grams is not None and match.kcal_100g > 0,
    }
