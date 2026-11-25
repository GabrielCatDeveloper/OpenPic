# -*- coding: utf-8 -*-
"""Salvaguardas: el bot no debe empujar a nadie a un deficit que le haga daño.

Los problemas de la conducta alimentaria (anorexia, bulimia, ortorexia) se
alimentan de cosas como un contador de calorías con reproches. Este bot
evita cuatro cosas:

1. **No propone deficit** a quien no debe: por debajo de IMC 18,5, por debajo
   de 18 anos, o a quien ha pedido el "modo suave" (historico de trastornos de
   la conducta alimentaria, embarazo, tratamiento por otros motivos).
2. **No pasa de un 1% del peso corporal por semana** (el ritmo que dan las
   guias clinicas): bajar mas rapido no es mas sano, es mas peligroso.
3. **No moraliza**: nunca dice "no comas", nunca juzga el plato, y el sticker
   habla del dia, no de la persona.
4. **Avisa cuando la tendencia es demasiado rapida** y deriva a un
   profesional, en vez de apretar el deficit.

Ademas, el deficit es semanal: un dia "tonto" no rompe nada, y el usuario
tiene margen real para los dias que quiera.
"""

from __future__ import annotations

# Por debajo de este IMC no se propone deficit (OMS: bajo peso < 18,5).
BMI_UNDERWEIGHT = 18.5

# Con menos de 18 anos no se calcula deficit: el cuerpo esta en desarrollo.
MIN_AGE_FOR_DEFICIT = 18

# Ritmo maximo: el 1% del peso corporal por semana.
MAX_WEEKLY_LOSS_FRACTION = 0.01

# Aviso cuando la tendencia de peso va mas rapido de lo razonable.
FAST_LOSS_WARNING = (
    "Vas mas rapido de lo previsto (mas del 1% de tu peso en una semana). "
    "Bajar asi de rapido no es mas sano: revisa las{raciones} con un "
    "profesional y no bajes mas el objetivo."
)


def body_mass_index(weight_kg: float, height_cm: float) -> float:
    if not weight_kg or not height_cm:
        return 0.0
    return weight_kg / max((height_cm / 100.0) ** 2, 1e-6)


def is_underweight(weight_kg: float, height_cm: float) -> bool:
    bmi = body_mass_index(weight_kg, height_cm)
    return 0 < bmi < BMI_UNDERWEIGHT


def max_safe_weekly_rate(weight_kg: float) -> float:
    """El 1% del peso corporal por semana."""
    return max(0.05, float(weight_kg or 0) * MAX_WEEKLY_LOSS_FRACTION)


def should_not_deficit(
    *,
    weight_kg: float,
    height_cm: float,
    age: int,
    goal: str,
    gentle_mode: bool = False,
) -> str:
    """Motivo para no proponer deficit ('' si no hay ninguno)."""
    if gentle_mode:
        return "Has pedido el modo suave: sin deficit ni cuentas, solo registro."
    if age and age < MIN_AGE_FOR_DEFICIT:
        return (
            "Con menos de 18 anos no se calculan deficits: el cuerpo esta en "
            "desarrollo y el peso lo marca el crecimiento, no la alimentacion."
        )
    if is_underweight(weight_kg, height_cm):
        return (
            "Tu peso esta por debajo del rango de salud (IMC por debajo de "
            "18,5): no te voy a marcar un deficit. Si buscas cambios, hablo "
            "con un profesional de la salud."
        )
    if str(goal or "").lower() != "lose":
        return ""
    return ""


def is_loss_too_fast(weight_kg: float, weekly_rate_kg: float) -> bool:
    """True si se esta perdiendo mas del 1% del peso por semana."""
    if weekly_rate_kg >= 0 or not weight_kg:
        return False
    return abs(weekly_rate_kg) > weight_kg * MAX_WEEKLY_LOSS_FRACTION
