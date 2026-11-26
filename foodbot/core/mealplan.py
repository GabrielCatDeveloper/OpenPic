"""El plan de comidas: lo eligen las reglas, no el modelo.

Food-R1 esta afinado para el formato `<answer>...</answer>` de las calorias,
no para estructurar un plan: pide 2 comidas y devuelve 15 (la ultima, cortada
a media), tarda minutos en CPU y se cachea entero para todo el dia.

Aqui los numeros salen de la tabla de ingredientes (~265 alimentos con kcal
y macros por 100 g) y de las reglas del bot: el margen semanal, el suelo de
seguridad y la hora. El modelo, si acaso, solo pondria nombre a los platos;
mientras tanto, los nombres salen de la propia tabla.
"""

from __future__ import annotations

import hashlib
from typing import Any

from foodbot.data.ingredients import INGREDIENTS

# Las comidas del dia y la parte del objetivo diario que se le lleva cada una.
MEAL_SLOTS_PLAN: tuple[tuple[str, float, int, int], ...] = (
    # (franja, parte del dia, hora desde, hora hasta)
    ("desayuno", 0.25, 5, 10),
    ("comida", 0.35, 12, 15),
    ("merienda", 0.15, 16, 17),
    ("cena", 0.25, 20, 23),
)

# Candidatos por franja: un principal (con proteina) y un acompanamiento.
CANDIDATES: dict[str, dict[str, list[str]]] = {
    "desayuno": {
        "principal": [
            "huevos", "yogur natural", "yogur griego", "tofu", "leche",
            "queso fresco", "tortilla francesa", "huevo frito",
        ],
        "acompanamiento": [
            "copos de avena", "pan integral", "tostada con tomate",
            "pan con tomate", "cereales", "muesli",
        ],
        "extra": ["platano", "manzana", "fresas", "miel", "cafe"],
    },
    "comida": {
        "principal": [
            "pechuga de pollo", "pollo", "filete de ternera", "bistec",
            "atun en lata", "salmon", "merluza", "bacalao", "gambas",
            "calamar a la plancha", "tofu", "tempeh", "huevos",
        ],
        "acompanamiento": [
            "arroz integral", "arroz blanco", "pasta", "quinoa", "patata",
            "boniato", "lentejas", "garbanzos", "ensalada",
        ],
        "extra": ["judías verdes", "brocoli", "zanahoria", "tomate"],
    },
    "merienda": {
        "principal": [
            "yogur griego", "yogur natural", "queso fresco", "leche",
            "leche de avena", "crepe", "tortilla de crepe",
        ],
        "acompanamiento": ["manzana", "plátano", "galletas", "tostada", "hummus"],
        "extra": ["nueces", "almendra", "chocolate negro", "fresas", "cafe"],
    },
    "cena": {
        "principal": [
            "merluza", "salmon", "bacalao", "calamar a la plancha", "gambas",
            "tortilla francesa", "huevos", "tofu", "pechuga de pollo",
        ],
        "acompanamiento": [
            "sopa de verduras", "crema de calabaza", "gazpacho", "ensalada",
            "salmorejo", "verduras", "champinones",
        ],
        "extra": ["aceite de oliva", "tomate", "lechuga", "pepino"],
    },
}

# Porciones (gramos). El acompanamiento y el extra tienen una porcion
# realista fija; el principal es el que se ajusta a las kcal del plato.
MAIN_PORTION = (60, 250)
SIDE_PORTION = (80, 250)
EXTRA_PORTION = (10, 40)
# A partir de estas kcal/100 g, una racion "pequena" (15 g) ya aporta mucho.
DENSE_KCAL_100G = 250


def _kcal(name: str) -> float:
    for key, values in INGREDIENTS.items():
        if key.lower() == name.lower():
            return float(values[0])
    return 0.0


def _macros(name: str, grams: float) -> tuple[float, float, float]:
    """(proteina, carbohidratos, grasa) de 'grams' de ese alimento."""
    for key, values in INGREDIENTS.items():
        if key.lower() == name.lower():
            factor = grams / 100.0
            return (
                round(values[1] * factor, 1),
                round(values[2] * factor, 1),
                round(values[3] * factor, 1),
            )
    return (0.0, 0.0, 0.0)


def _offset(seed: str, size: int) -> int:
    """Un desplazamiento estable por dia, para no repetir siempre lo mismo."""
    if size <= 0:
        return 0
    digest = hashlib.sha256((seed or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % size


def _pick(candidates: list[str], index: int, used: set[str]) -> str | None:
    """El candidato que toca, saltando los ya usados."""
    if not candidates:
        return None
    for step in range(len(candidates)):
        name = candidates[(index + step) % len(candidates)]
        if name not in used:
            return name
    return None


def _build_dish(
    slot: str, target_kcal: float, index: int, used: set[str]
) -> dict[str, Any] | None:
    """Un plato: un principal, un acompanamiento y un extra, con sus gramos."""
    pool = CANDIDATES.get(slot)
    if not pool:
        return None

    main = _pick(pool["principal"], index, used)
    if not main:
        return None
    used.add(main)

    # El principal se lleva el 60% de las kcal, el acompanamiento el 30% y
    # el extra el 10%.
    main_kcal = _kcal(main)
    if not main_kcal:
        used.discard(main)
        return None

    # El acompanamiento y el extra llevan su porcion realista; el principal
    # se ajusta a las kcal que quedan.
    side = _pick(pool["acompanamiento"], index, used)
    side_g = 0.0
    if side and _kcal(side):
        used.add(side)
        side_g = min(SIDE_PORTION[1], max(SIDE_PORTION[0], SIDE_PORTION[0]))

    extra = _pick(pool.get("extra", []), index, used)
    extra_g = 0.0
    if extra and _kcal(extra):
        used.add(extra)
        if _kcal(extra) >= DENSE_KCAL_100G:
            # Chocolate, nuts, queso curado: una racion corta y basta.
            extra_g = 15
        else:
            extra_g = 30

    fixed_kcal = _kcal(side) * side_g / 100.0 if side else 0.0
    fixed_kcal += _kcal(extra) * extra_g / 100.0 if extra else 0.0
    main_g = (target_kcal - fixed_kcal) / main_kcal * 100.0
    main_g = max(MAIN_PORTION[0], min(MAIN_PORTION[1], main_g))

    items = []
    for name, grams in ((main, main_g), (side, side_g), (extra, extra_g)):
        if not name or grams <= 0:
            continue
        items.append(
            {
                "name": name,
                "grams": int(round(grams / 5) * 5),
                "kcal": int(round(_kcal(name) * grams / 100.0)),
            }
        )
    if not items:
        return None

    total_kcal = sum(item["kcal"] for item in items)
    protein = carb = fat = 0.0
    for item in items:
        p, c, f = _macros(item["name"], item["grams"])
        protein += p
        carb += c
        fat += f

    nombre = " con ".join(item["name"].capitalize() for item in items[:2])
    if len(items) > 2:
        nombre += f" y {items[2]['name']}"

    return {
        "slot": slot,
        "name": nombre,
        "kcal": total_kcal,
        "protein_g": round(protein, 1),
        "carbs_g": round(carb, 1),
        "fat_g": round(fat, 1),
        "items": items,
    }


def build_meal_plan(
    *,
    target_kcal: int,
    protein_g: float = 0.0,
    carbs_g: float = 0.0,
    fat_g: float = 0.0,
    now_hour: float = 12.0,
    eaten_today: int = 0,
    floor_kcal: int = 1200,
    seed: str = "",
) -> list[dict[str, Any]]:
    """El plan del resto del dia, con las reglas del bot.

    - Reparte lo que queda del objetivo entre las comidas que aun no han
      pasado (segun la hora local del usuario).
    - Cada comida se compone con la tabla de ingredientes (los numeros son
      exactos: salen de la tabla, no de una estimacion del modelo).
    - Nunca baja del suelo de seguridad.
    """
    remaining = max(0, int(target_kcal) - int(eaten_today))

    # Las comidas que aun quedan hoy.
    pending = []
    for slot, share, start, end in MEAL_SLOTS_PLAN:
        if now_hour < start:
            pending.append((slot, share))
        elif now_hour < end:
            pending.append((slot, share * 0.5))  # la que esta en curso, a medias
    if not pending:
        return []

    total_share = sum(share for _, share in pending) or 1.0
    used: set[str] = set()
    plan: list[dict[str, Any]] = []

    # El suelo de seguridad es DIARIO: si lo que queda por debajo del suelo,
    # se reparte entre las comidas que faltan (nunca menos de lo seguro al
    # final del dia).
    if remaining < floor_kcal:
        remaining = floor_kcal

    for position, (slot, share) in enumerate(pending):
        budget = remaining * share / total_share
        index = _offset(
            f"{seed}|{slot}",
            max(1, len(CANDIDATES.get(slot, {}).get("principal", []))),
        )
        dish = _build_dish(slot, budget, index, used)
        if not dish:
            continue
        plan.append(dish)

    return plan
