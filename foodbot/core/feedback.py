"""Como va el dia tras cada comida: un sticker y un mensaje sin reproches.

El tono importa mas que la cifra: el objetivo es que el usuario no termine
odia la comida. Reglas:

- Nada de "no deberias", "te has pasado" en plan acusatorio ni "deberias".
- **Siempre** se recuerda que se cuenta la semana: ahi esta el margen para
  los dias tontos, y un dia tonto no es un desastre.
- El sticker反映 el estado del dia, no la moral del usuario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foodbot.core.alternatives import alternatives_text

# Emoji de reserva: si el set de stickers no esta subido (o falla), el bot
# manda el emoji de texto y el usuario sigue teniendo la informacion.
FALLBACK_EMOJI = {
    "contento": "\U0001f60a",
    "preocupado": "\U0001f61f",
    "triste": "\U0001f615",
}


@dataclass
class Feedback:
    """Lo que el bot dice tras registrar una comida."""

    sticker: str
    text: str
    emoji: str
    tone: str

    def as_dict(self) -> dict[str, Any]:
        return {"sticker": self.sticker, "text": self.text, "emoji": self.emoji, "tone": self.tone}


def _week_line(week_saved: int, week_goal: int) -> str:
    """El recordatorio de que se cuenta la semana (el margen)."""
    if not week_goal:
        return ""
    if week_saved >= 0:
        return (
            f"Llevas <b>{week_saved} kcal</b> ahorradas esta semana "
            f"de {week_goal}: hay margen para los dias tontos."
        )
    return (
        f"Esta semana vas <b>{-week_saved} kcal</b> por encima de lo previsto: "
        "los proximos dias lo recuperan sin drama."
    )


def meal_feedback(
    *,
    meal_kcal: int,
    consumed_before: int,
    daily_target: int,
    week_saved: int = 0,
    week_goal: int = 0,
    likes: str | None = None,
    avoid: str | None = None,
) -> Feedback:
    """Estado del dia tras una comida, con su sticker y su mensaje.

    Los umbrales son relativos al objetivo del dia, asi que funcionan igual
    para un objetivoAutomatico que para uno que haya fijado el usuario:

    - un plato que se come mas de la mitad del objetivo diario -> "triste"
    - el dia pasado un 30% del objetivo -> "triste"
    - el objetivo cubierto o casi -> "preocupado"
    - el resto -> "contento"
    """
    meal_kcal = max(0, int(meal_kcal))
    consumed_before = max(0, int(consumed_before))
    daily_target = max(1, int(daily_target))

    after = consumed_before + meal_kcal
    share = meal_kcal / daily_target
    ratio = after / daily_target
    remaining = daily_target - after
    week = _week_line(week_saved, week_goal)

    # 1) Un plato que se come media vida.
    if share > 0.5:
        text = (
            f"\U0001f615 Ese plato de <b>{meal_kcal} kcal</b> es el "
            f"{share * 100:.0f}% de tu objetivo de hoy.\n\n"
            "Sin drama: un plato asi no arruina nada. "
        )
        text += week or "La semana sigue contando."
        return Feedback("triste", text, FALLBACK_EMOJI["triste"], "triste")

    # 2) El dia claramente pasado.
    if ratio > 1.3:
        excess = after - daily_target
        text = (
            f"\U0001f615 Hoy llevas <b>{after} kcal</b>, unas {excess} por encima "
            "de tu objetivo.\n\n"
            "No es una catastrophe: "
        )
        text += week or "la semana sigue contando y se recupera sola."
        return Feedback("triste", text, FALLBACK_EMOJI["triste"], "triste")

    # 3) Objetivo cubierto (o casi): se ofrecen alternativas, no un veto.
    if ratio > 1.0:
        options = alternatives_text(
            likes=likes, avoid=avoid, remaining_kcal=None, limit=2
        )
        text = (
            f"\U0001f61f Con esta comida ya has cubierto el objetivo de hoy "
            f"({daily_target} kcal).\n\n"
        )
        if options:
            text += (
                f"Si te queda hambre, {options}: llenan casi sinaportarle nada "
                "a la semana. "
            )
        else:
            text += "Si te queda hambre, algo que llene sin muchas Pekerjaan. "
        text += week
        return Feedback("preocupado", text, FALLBACK_EMOJI["preocupado"], "preocupado")

    if remaining <= daily_target * 0.15:
        options = alternatives_text(
            likes=likes, avoid=avoid, remaining_kcal=max(remaining, 0), limit=2
        )
        text = (
            f"\U0001f61f Vas a <b>{after} kcal</b>: te queda muy poco para el "
            f"objetivo de hoy ({daily_target} kcal).\n\n"
        )
        if options:
            text += f"Si te entra hambre, {options}. "
        else:
            text += "Si te entra hambre, lo que elijas suma a la semana, pero cabe. "
        text += week
        return Feedback("preocupado", text, FALLBACK_EMOJI["preocupado"], "preocupado")

    # 4) Todo bien.
    text = (
        f"\U0001f60a Van <b>{after} kcal</b> y te quedan {remaining} para el "
        f"objetivo de hoy ({daily_target} kcal).\n\n"
    )
    if week:
        text += week
    else:
        text += "Si mas tarde te apetece algo mas, tambien cabe."
    return Feedback("contento", text, FALLBACK_EMOJI["contento"], "contento")
