"""Alternativas saciantes cuando al usuario le queda hambre.

La idea: el bot nunca dice "no comas mas". Dice "si te queda hambre, con esto
no rompes nada". Las opciones:

- encajan en lo que queda del dia, si queda;
- son voluminosas (llenan sin muchas calorias);
- respetan lo que el usuario ha dicho que no le gusta o no puede comer.

Cada opcion lleva sus etiquetas (lacteo, huevo, verdura, pescado, marisco,
gluten, soja, fruta) para poder filtrar por lo que el usuario ha evitar.
"""

from __future__ import annotations

from typing import Any

# kcal por racion, y cuanto llena (1-5).
FILLING_OPTIONS: list[dict[str, Any]] = [
    {"name": "ensalada grande de verduras", "kcal": 60, "fill": 5, "tags": ("verdura",)},
    {"name": "caldo de verduras", "kcal": 45, "fill": 5, "tags": ("verdura",)},
    {"name": "pepino con yogur", "kcal": 40, "fill": 5, "tags": ("verdura", "lacteo")},
    {"name": "champinones a la plancha", "kcal": 45, "fill": 5, "tags": ("verdura",)},
    {"name": "zanahoria", "kcal": 35, "fill": 4, "tags": ("verdura",)},
    {"name": "yogur natural sin azucar", "kcal": 100, "fill": 4, "tags": ("lacteo",)},
    {"name": "batido de leche sin azucar", "kcal": 120, "fill": 4, "tags": ("lacteo",)},
    {"name": "huevo cocido", "kcal": 78, "fill": 4, "tags": ("huevo",)},
    {"name": "gambas cocidas", "kcal": 90, "fill": 4, "tags": ("marisco",)},
    {"name": "atun al natural", "kcal": 100, "fill": 4, "tags": ("pescado",)},
    {"name": "sopa de miso", "kcal": 60, "fill": 4, "tags": ("soja", "verdura")},
    {"name": "tostada integral", "kcal": 90, "fill": 3, "tags": ("gluten",)},
    {"name": "pan integral con tomate", "kcal": 120, "fill": 3, "tags": ("gluten", "verdura")},
    {"name": "manzana", "kcal": 95, "fill": 2, "tags": ("fruta",)},
    {"name": "queso fresco", "kcal": 85, "fill": 2, "tags": ("lacteo",)},
]


def _terms(raw: str | None) -> list[str]:
    """Divide una lista de preferencias en terminos normalizados."""
    if not raw:
        return []
    parts = []
    for chunk in str(raw).replace(";", ",").split(","):
        term = chunk.strip().lower()
        if term:
            parts.append(term)
    return parts


def suggest(
    *,
    likes: str | None = None,
    avoid: str | None = None,
    remaining_kcal: int | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Propuesta de alternativas que llenan sin romper el presupuesto.

    Si quedan kcal en el dia, solo se proponen las que caben en ellos. Si el
    dia ya esta cubierto, se proponen las mas voluminosas: las que menos
    pesan en la semana.
    """
    avoid_terms = _terms(avoid)
    like_terms = _terms(likes)

    candidates: list[dict[str, Any]] = []
    for option in FILLING_OPTIONS:
        tags = set(option["tags"])
        # ¿Coincide con lo que el usuario ha dicho que no quiere?
        if any(term in tag for term in avoid_terms for tag in tags):
            continue
        if any(term in option["name"] for term in avoid_terms):
            continue
        candidates.append(option)

    # Lo que le gusta sube; lo que le gusta y es voluminoso, mas.
    def score(option: dict[str, Any]) -> tuple[int, int, int]:
        liked = any(term in option["name"] for term in like_terms)
        if remaining_kcal is not None and remaining_kcal > 0:
            fits = 1 if option["kcal"] <= remaining_kcal else 0
        else:
            fits = 1
        return (liked, fits, option["fill"])

    candidates.sort(key=score, reverse=True)

    # Si queda margen, filtramos las que no caben.
    if remaining_kcal is not None and remaining_kcal > 0:
        fitting = [o for o in candidates if o["kcal"] <= remaining_kcal]
        if fitting:
            candidates = fitting

    return candidates[:limit]


def alternatives_text(
    *,
    likes: str | None = None,
    avoid: str | None = None,
    remaining_kcal: int | None = None,
    limit: int = 2,
) -> str:
    """Texto con las alternativas, o una frase si no hay ninguna."""
    options = suggest(
        likes=likes, avoid=avoid, remaining_kcal=remaining_kcal, limit=limit
    )
    if not options:
        return ""
    parts = [f"<b>{option['name']}</b> ({option['kcal']} kcal)" for option in options]
    if len(parts) == 1:
        return parts[0]
    return " o ".join([", ".join(parts[:-1]), parts[-1]])
