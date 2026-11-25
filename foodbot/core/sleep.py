"""El sueño y su relacion con la comida.

La evidencia:

- **Dormir mas, comer menos**: en un ECA (Tasali et al., *JAMA Intern Med*
  2022), alargar el sueño 1,2 h en adultos que dormian menos de 6,5 h bajo
  la ingesta diaria unos **270 kcal** y produjo balance negativo. Dormir 7-8 h
  se asocia a mas exito en las perdidas de peso.
- **Dormir poco, comer mas**: la revision de *Nature Reviews Endocrinology*
  (2022) resume que dormir poco sube el gasto ~100 kcal/dia **pero** la
  ingesta en mas de 250 kcal/dia: el balance se va al alza. Y que el exceso
  de comida al dormir poco tiene mas que ver con el control impulso y la
  recompensa que con las hormonas del hambre (la revision de 50 ECAs de 2021
  no encuentra efectos consistentes sobre leptina o grelina).
- **El horario tambien cuenta**: el desalineo circadiano reduce el gasto
  24 h ~3% (~55 kcal/dia) y empuja a elegir comida menos sana.

De ahi la idea del bot: el picuelo de la madrugada no es un fallo de
voluntad, es consecuencia del horario. Y se avisa con datos, sin culpas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Las guias clinicas piden 7 o mas horas a los adultos.
MIN_SLEEP_HOURS = 7.0
SHORT_SLEEP_HOURS = 6.0

# A partir de esta desviacion en la hora de acostarse, el horario es
# irregular (el "social jetlag" de los horarios de vida irregulares).
IRREGULAR_BEDTIME_STD = 1.5


@dataclass
class SleepNight:
    """Una noche: cuando te acostaste y cuando te levantaste."""

    bedtime: datetime
    waketime: datetime

    @property
    def hours(self) -> float:
        return round((self.waketime - self.bedtime).total_seconds() / 3600.0, 2)

    @property
    def bedtime_minutes(self) -> int:
        """La hora de acostarse en minutos (para medir la regularidad)."""
        return self.bedtime.hour * 60 + self.bedtime.minute


def sleep_quality(hours: float) -> str:
    """Como se valora la duracion: 'corta', 'justa' o 'larga'."""
    if hours < SHORT_SLEEP_HOURS:
        return "muy corta"
    if hours < MIN_SLEEP_HOURS:
        return "corta"
    if hours <= 9.0:
        return "justa"
    return "larga"


def bedtime_regularity(nights: list[SleepNight]) -> float:
    """Desviacion tipica de la hora de acostarse (en horas).

    Menos de 1,5 h es un horario estable; por encima, el ritmo circadiano
    va y viene y eso se nota en el hambre y en las elecciones de comida.
    """
    if len(nights) < 3:
        return 0.0
    minutes = [night.bedtime_minutes for night in nights]
    # La medianoche rompe la media: se trabaja en minutos circulares.
    angles = [(m * 2 * math.pi) / 1440 for m in minutes]
    sin = sum(math.sin(a) for a in angles) / len(angles)
    cos = sum(math.cos(a) for a in angles) / len(angles)
    mean_angle = math.atan2(sin, cos)
    mean_minutes = (mean_angle * 1440) / (2 * math.pi) % 1440

    def distance(value: int) -> float:
        diff = abs(value - mean_minutes) % 1440
        return min(diff, 1440 - diff)

    spread = sum(distance(m) for m in minutes) / len(minutes)
    return round(spread / 60.0, 2)


def sleep_summary(nights: list[SleepNight]) -> dict[str, Any]:
    """Resumen de las ultimas noches: duracion media y regularidad."""
    if not nights:
        return {
            "nights": 0,
            "avg_hours": 0.0,
            "quality": "sin datos",
            "regularity": 0.0,
            "irregular": False,
            "short": False,
        }
    hours = [night.hours for night in nights]
    avg = round(sum(hours) / len(hours), 2)
    regularity = bedtime_regularity(nights)
    return {
        "nights": len(nights),
        "avg_hours": avg,
        "quality": sleep_quality(avg),
        "regularity": regularity,
        "irregular": regularity > IRREGULAR_BEDTIME_STD,
        "short": avg < SHORT_SLEEP_HOURS,
    }


def late_snack_explanation(
    meals: list[dict[str, Any]],
    nights: list[SleepNight],
    tz: Any = None,
) -> str | None:
    """Explica un picuelo nocturno por el horario de sueño (si encaja).

    Devuelve un texto solo cuando tiene sentido: un piqueo a deshora despues
    de acostarse tarde.
    """
    from foodbot.core.timeutil import local_hour

    if not nights or not meals:
        return None
    last_night = max(nights, key=lambda n: n.bedtime)
    bedtime_hour = last_night.bedtime.hour + last_night.bedtime.minute / 60

    for meal in meals:
        when = meal.get("eaten_at")
        if not when:
            continue
        try:
            moment = datetime.fromisoformat(str(when))
        except ValueError:
            continue
        if tz is not None:
            moment = moment.astimezone(tz)
        hour = moment.hour + moment.minute / 60
        # Un piqueo entre la 1:00 y las 5:00 de la madrugada.
        # "Acostarse tarde" es a partir de las 23:30 o antes de las 4:00.
        late_bedtime = bedtime_hour >= 23.5 or bedtime_hour < 4
        if 1 <= hour < 5 and late_bedtime:
            return (
                f"\U0001f634 Ese piqueo de las {_hhmm(hour)} viene de acostarte "
                f"tarde (a las {_hhmm(bedtime_hour % 24)}). Con el sueño "
                "descuadrado, el hambre de madrugada es fisiología, no falta de "
                "voluntad: mover la hora de acostarse suele quitarlo."
            )
    return None


def _hhmm(hour: float) -> str:
    """23.5 -> '23:30'"""
    total = int(round(hour * 60))
    return f"{total // 60 % 24:02d}:{total % 60:02d}"
