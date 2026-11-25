"""Motor nutricional: BMR, TDEE, deficit, macros y tendencia de peso.

El deficit se cuenta **por semana**, no por dia: el presupuesto de 7 dias es
la referencia y lo que queda se reparte entre los dias que faltan. Asi, un dia
fuera de la raya no rompe la semana, y el dia siguiente se ajusta solo.

Reglas de seguridad aplicadas:
- Suelo calorico minimo por sexo (1200 kcal mujer / 1500 kcal hombre / 1400 resto).
- El deficit semanal nunca supera el 25% del TDEE semanal.
- La proteina se calcula por kg de peso magra aproximada (peso actual).
- 7.000 kcal ~ 1 kg de grasa (aproximacion; ver KCAL_PER_KG_FAT).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from foodbot.core.safety import (
    max_safe_weekly_rate,
    should_not_deficit,
)

# 1 kg de grasa ~ 7.000 kcal (la regla que usa el bot). Es una aproximacion:
# el tejido adiposo es ~87% lipid, asi que 1 kg de grasa pura valdria mas, y
# el peso del cuerpo no es solo grasa (agua, glucogeno, contenido intestinal,
# masa muscular). Por eso la equivalencia sirve para ver la TENDENCIA, no
# para esperar un numero exacto en la bascula.
KCAL_PER_KG_FAT = 7000.0

ACTIVITY_LEVELS: dict[str, tuple[str, float]] = {
    "sedentary": ("Sedentario (poco o nada de ejercicio)", 1.2),
    "light": ("Ligero (1-3 dias/semana)", 1.375),
    "moderate": ("Moderado (3-5 dias/semana)", 1.55),
    "active": ("Activo (6-7 dias/semana)", 1.725),
    "very_active": ("Muy activo (trabajo fisico + deporte diario)", 1.9),
}

GOALS: dict[str, str] = {
    "lose": "Perder peso",
    "maintain": "Mantener peso",
    "gain": "Ganar masa",
}

# Ritmo semanal recomendado segun agresividad (kg/semana).
RATES: dict[str, float] = {
    "mild": 0.25,
    "moderate": 0.5,
    "aggressive": 0.75,
}
GAIN_RATE = 0.25  # kg/semana al ganar masa

MIN_KCAL = {"female": 1200, "male": 1500, "midpoint": 1400, "other": 1400}

# Hormonal: los valores que se usan para el suelo y la ecuacion.
HORMONE_OPTIONS = {
    "none": "sin tratamiento hormonal",
    "estrogen": "estrogeno",
    "testosterone": "testosterona",
    "other": "otro tratamiento",
}


def physiology_reference(sex: str | None, hormone_therapy: str | None = None) -> str:
    """Que referencia se usa para la ecuacion y el suelo de seguridad.

    - Sin tratamiento hormonal: la referencia del perfil (la que corresponde
      al cuerpo).
    - Con estrogeno o testosterona: el **punto medio** de las referencias.
      No hay ecuaciones especificas para personas trans: lo que manda es la
      composicion corporal actual (Katch-McArdle, que no necesita el sexo) y,
      sin ella, el punto medio es lo mas cercano a la realidad (Frenser &
      Fischer, Int J Obes 2025; Adv Nutr 2025).
    """
    hormones = (hormone_therapy or "").strip().lower()
    if hormones in {"estrogen", "estrogeno", "testosterone", "testosterona"}:
        return "midpoint"
    value = (sex or "").strip().lower()
    return value if value in {"male", "female"} else "midpoint"

# --- Refinamiento del mantenimiento (TDEE) ---------------------------------
# El factor de actividad es la variable mas influyente (cambiar de nivel
# mueve 270-600 kcal/dia, mas que la diferencia entre ecuaciones).
# El factor de actividad ya asume una cantidad de pasos y de sesiones: los
# datos reales del usuario corrigen la diferencia.
ACTIVITY_STEPS: dict[str, int] = {
    "sedentary": 4000,
    "light": 7000,
    "moderate": 8500,
    "active": 10000,
    "very_active": 12000,
}
ACTIVITY_TRAINING: dict[str, int] = {
    "sedentary": 0,
    "light": 2,
    "moderate": 3,
    "active": 5,
    "very_active": 6,
}
# El tipo de trabajo mueve el NEAT de fondo (un trabajo de pie no es lo mismo
# que uno sentado).
JOB_ADJUST: dict[str, float] = {
    "sedentary": -0.02,
    "light": 0.02,
    "physical": 0.05,
}
KCAL_PER_STEP = 0.035  # ~35 kcal por cada 1000 pasos de diferencia
PAL_PER_TRAINING = 0.03  # ~3% del BMR por sesion de diferencia
MIN_PAL = 1.15
MAX_PAL = 1.95

# Cuantos dias de registro hacen falta para calibrar el mantenimiento con los
# datos del propio usuario (peso real + ingesta real).
CALIBRATION_MIN_DAYS = 14
CALIBRATION_MIN_WEIGHT_SPAN = 10  # dias entre el primer y el ultimo peso
CALIBRATION_MAX_DAYS = 60  # ventana maxima que se mira para calibrar


@dataclass
class MacroTarget:
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float

    def as_dict(self) -> dict[str, float]:
        return {
            "kcal": float(self.kcal),
            "protein_g": round(self.protein_g, 1),
            "carbs_g": round(self.carbs_g, 1),
            "fat_g": round(self.fat_g, 1),
        }


@dataclass
class EnergyTarget:
    bmr: int
    tdee: int
    maintenance_kcal: int
    daily_kcal: int
    deficit_kcal: int
    macro: MacroTarget
    notes: list[str]
    goal: str
    weekly_rate_kg: float
    # Objetivo de ahorro de la semana (kg/semana -> kcal/semana).
    weekly_goal_kcal: int = 0
    weekly_budget_kcal: int = 0
    floor_kcal: int = 0
    # De donde sale el mantenimiento y como se ha calculado.
    source: str = "formula"
    breakdown: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["macro"] = self.macro.as_dict()
        return data


def compute_bmr(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor: la ecuacion mas validada (Frankenfield 2005)."""
    return compute_bmr_detail(sex, weight_kg, height_cm, age)[0]


def compute_bmr_detail(
    sex: str,
    weight_kg: float,
    height_cm: float,
    age: int,
    body_fat_pct: float | None = None,
    reference: str | None = None,
) -> tuple[float, str]:
    """(BMR, ecuacion usada).

    Con % de grasa fiable (DEXA o pliegues) se usa Katch-McArdle, que escala
    con la masa libre y acierta mejor en gente musculosa. Sin ese dato (o con
    una bascula digital, que se equivoca 4-8 puntos) se usa Mifflin-St Jeor.
    """
    fat = float(body_fat_pct or 0.0)
    # Rangos plausibles por referencia: fuera de ellos el dato es ruido y es
    # mejor fiarse de Mifflin-St Jeor.
    sex_key = reference or (sex or "other").lower()
    plausible = {
        "male": (5.0, 45.0),
        "female": (8.0, 55.0),
        "midpoint": (5.0, 50.0),
        "other": (5.0, 50.0),
    }.get(sex_key, (5.0, 50.0))
    if plausible[0] <= fat <= plausible[1]:
        lean_mass = weight_kg * (1.0 - fat / 100.0)
        return 370.0 + 21.6 * lean_mass, "Katch-McArdle (masa libre)"

    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    sex = reference or (sex or "other").lower()
    if sex == "male":
        return base + 5.0, "Mifflin-St Jeor"
    if sex == "female":
        return base - 161.0, "Mifflin-St Jeor"
    return base - 78.0, "Mifflin-St Jeor"  # punto medio entre ambos


def compute_tdee(bmr: float, activity: str) -> float:
    """TDEE con el factor de actividad tal cual."""
    factor = ACTIVITY_LEVELS.get((activity or "sedentary").lower(), ACTIVITY_LEVELS["sedentary"])[1]
    return bmr * factor


def compute_tdee_detail(
    bmr: float,
    activity: str,
    training_days: int | None = None,
    daily_steps: int | None = None,
    job_activity: str | None = None,
) -> tuple[float, dict[str, Any]]:
    """(TDEE, desglose) afinando el factor de actividad con datos reales.

    El factor de actividad es una media de poblacion: se corrige con los
    pasos (NEAT) y las sesiones de ejercicio que declara el usuario, mas el
    tipo de trabajo. El resultado sigue siendo una estimacion (±10-20%); por
    eso el bot tambien puede calibrar el mantenimiento con la tendencia de
    peso del propio usuario.
    """
    level = (activity or "sedentary").lower()
    if level not in ACTIVITY_LEVELS:
        level = "sedentary"
    base_pal = ACTIVITY_LEVELS[level][1]

    steps_assumed = ACTIVITY_STEPS[level]
    steps_delta = 0
    steps_adjust = 0.0
    if daily_steps:
        steps_delta = int(daily_steps) - steps_assumed
        # +-35 kcal por cada 1000 pasos de diferencia.
        steps_adjust = steps_delta * KCAL_PER_STEP / max(bmr, 1.0)

    training_assumed = ACTIVITY_TRAINING[level]
    training_delta = 0
    training_adjust = 0.0
    if training_days is not None:
        training_delta = int(training_days) - training_assumed
        training_adjust = training_delta * PAL_PER_TRAINING

    job_adjust = JOB_ADJUST.get((job_activity or "").lower(), 0.0)

    pal = base_pal + steps_adjust + training_adjust + job_adjust
    pal = max(MIN_PAL, min(MAX_PAL, pal))

    detail = {
        "bmr": int(round(bmr)),
        "bmr_formula": "",
        "level": level,
        "level_label": ACTIVITY_LEVELS[level][0],
        "base_pal": base_pal,
        "steps_assumed": steps_assumed,
        "steps_given": int(daily_steps) if daily_steps else None,
        "steps_delta": steps_delta,
        "steps_kcal": int(round(steps_delta * KCAL_PER_STEP)),
        "training_assumed": training_assumed,
        "training_given": int(training_days) if training_days is not None else None,
        "training_delta": training_delta,
        "training_kcal": int(round(training_adjust * bmr)),
        "job_given": job_activity or None,
        "job_kcal": int(round(job_adjust * bmr)),
        "pal": round(pal, 3),
        "tdee": int(round(bmr * pal)),
    }
    return float(detail["tdee"]), detail


def estimate_body_fat_pct(
    sex: str, weight_kg: float, height_cm: float, age: int
) -> float:
    """% de grasa estimado con Deurenberg (1991).

    Es una aproximacion a partir del IMC, la edad y el sexo: sirve para
    quedarse dentro de un orden de magnitud, no para tomar decisiones.
    """
    if not weight_kg or not height_cm:
        return 25.0
    bmi = weight_kg / max((height_cm / 100.0) ** 2, 1e-6)
    male = 1 if (sex or "").lower() == "male" else 0
    value = 1.20 * bmi + 0.23 * max(int(age or 30), 12) - 10.8 * male - 5.4
    return round(max(3.0, min(60.0, value)), 1)


def protein_target_g(weight_kg: float, goal: str, override: float | None = None) -> float:
    if override:
        return round(weight_kg * override, 1)
    per_kg = {"lose": 1.8, "maintain": 1.6, "gain": 1.8}.get(goal, 1.6)
    return round(weight_kg * per_kg, 1)


def fat_target_g(kcal: int, goal: str) -> float:
    pct = {"lose": 0.27, "maintain": 0.30, "gain": 0.25}.get(goal, 0.28)
    return round(kcal * pct / 9.0, 1)


def build_target(
    *,
    sex: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity: str,
    goal: str,
    weekly_rate_kg: float | None = None,
    daily_kcal_override: int | None = None,
    protein_g_per_kg: float | None = None,
    body_fat_pct: float | None = None,
    training_days: int | None = None,
    daily_steps: int | None = None,
    job_activity: str | None = None,
    maintenance_override: int | None = None,
    calibrated_maintenance: int | None = None,
    hormone_therapy: str | None = None,
    gentle_mode: bool = False,
) -> EnergyTarget:
    """Calcula el objetivo completo con las salvaguardas aplicadas.

    El mantenimiento (kcal que el cuerpo gasta al no hacer nada especial) es
    la referencia. El objetivo diario es mantenimiento menos el deficit
    planned, y el deficit se mide **por semana**: cada kilo que se quiere
    quitar son 7.000 kcal de ahorro repartidas entre los dias.
    """
    notes: list[str] = []
    reference = physiology_reference(sex, hormone_therapy)
    bmr, bmr_formula = compute_bmr_detail(
        sex, weight_kg, height_cm, age, body_fat_pct, reference=reference
    )
    tdee, breakdown = compute_tdee_detail(
        bmr, activity, training_days, daily_steps, job_activity
    )
    breakdown["bmr_formula"] = bmr_formula
    breakdown["reference"] = reference
    goal = (goal or "maintain").lower()
    floor = MIN_KCAL.get(reference, 1400)

    # --- El mantenimiento: override manual > calibrado con datos > formula.
    maintenance = int(round(tdee))
    source = "formula"
    if maintenance_override:
        maintenance = int(maintenance_override)
        source = "manual"
        notes.append("Usando el mantenimiento que me has indicado.")
    elif calibrated_maintenance:
        maintenance = int(calibrated_maintenance)
        source = "calibrado"
        notes.append(
            "Mantenimiento calibrado con tu peso real y lo que has comido: "
            "vale mas que la formula."
        )
    breakdown["maintenance"] = maintenance
    breakdown["source"] = source

    # --- Salvaguardas: hay gente a la que NO se le propone deficit.
    blocked = should_not_deficit(
        weight_kg=weight_kg,
        height_cm=height_cm,
        age=age,
        goal=goal,
        gentle_mode=gentle_mode,
    )
    if blocked:
        notes.append(blocked)
        goal = "maintain"
        daily_kcal_override = None

    # --- El deficit se define por semana.
    rate = weekly_rate_kg if weekly_rate_kg is not None else RATES["moderate"]
    max_rate = max_safe_weekly_rate(weight_kg)
    if goal == "lose" and rate > max_rate:
        rate = max_rate
        notes.append(
            f"He limitado el ritmo a {rate:.2f} kg/semana (el 1% de tu peso): "
            "bajar mas rapido no es mas sano, es mas peligroso."
        )

    if daily_kcal_override:
        # --- OBJETIVO MANUAL: manda lo que el usuario ha elegido.
        # Se respeta su decision (dentro del suelo de seguridad) y se le
        # explica lo que significa. El bot informa, no Decide.
        chosen = int(daily_kcal_override)
        daily = max(floor, chosen)
        if daily != chosen:
            notes.append(
                f"He ajustado tu objetivo al minimo de {floor} kcal: por debajo "
                "hay riesgo de deficit de nutrientes."
            )
        deficit = int(round(maintenance - daily))
        weekly_goal = int(round(deficit * 7))
        if chosen > maintenance:
            notes.append(
                f"Tu objetivo esta {chosen - maintenance} kcal por encima de tu "
                "mantenimiento: con eso subes de peso."
            )
        else:
            notes.append(
                f"Objetivo manual: {daily} kcal. Son {abs(deficit)} kcal "
                f"{'menos' if deficit > 0 else 'mas'} que tu mantenimiento "
                f"({maintenance} kcal) -> ~{abs(weekly_goal) / 7 / KCAL_PER_KG_FAT:.2f} kg/semana."
            )
            if maintenance and abs(deficit) > maintenance * 0.25:
                notes.append(
                    "Es un deficit superior al 25% de tu mantenimiento: puede "
                    "frenar la perdida y es dificil de sostener. Con pausas a "
                    "mantenimiento (comer a tu mantenimiento unos dias) se "
                    "aguanta mejor."
                )
    elif goal == "lose":
        weekly_goal = int(round(rate * KCAL_PER_KG_FAT))
        max_weekly = int(round(maintenance * 7 * 0.25))
        if weekly_goal > max_weekly:
            weekly_goal = max_weekly
            notes.append(
                "Deficit semanal limitado al 25% de tu mantenimiento por seguridad: "
                "un deficit mayor suele frenar la perdida y es insostenible."
            )
        daily = int(round(maintenance - weekly_goal / 7.0))
        if daily < floor:
            daily = floor
            notes.append(f"Limite minimo de {floor} kcal aplicado.")
        deficit = int(round(maintenance - daily))
    elif goal == "gain":
        weekly_goal = -int(round(GAIN_RATE * KCAL_PER_KG_FAT))
        daily = int(round(maintenance - weekly_goal / 7.0))
        deficit = int(round(maintenance - daily))
    else:
        daily = int(round(maintenance))
        deficit = 0
        weekly_goal = 0

    protein = protein_target_g(weight_kg, goal, protein_g_per_kg)
    fat = fat_target_g(daily, goal)
    carbs = max(0.0, (daily - protein * 4 - fat * 9) / 4.0)

    return EnergyTarget(
        bmr=int(round(bmr)),
        tdee=int(round(tdee)),
        maintenance_kcal=int(maintenance),
        daily_kcal=int(daily),
        deficit_kcal=int(deficit),
        macro=MacroTarget(kcal=int(daily), protein_g=protein, carbs_g=round(carbs, 1), fat_g=fat),
        notes=notes,
        goal=goal,
        weekly_rate_kg=float(rate if goal == "lose" else (weekly_rate_kg or 0.0)),
        weekly_goal_kcal=int(weekly_goal),
        weekly_budget_kcal=int(round(daily * 7)),
        floor_kcal=int(floor),
        source=source,
        breakdown=breakdown,
    )


def savings_bank(
    target: EnergyTarget | None,
    consumed_kcal: int,
    logged_days: int,
    days_left: int,
) -> dict[str, Any]:
    """El banco de ahorro: lo que quitas cada dia alimenta el deficit semanal.

    - El mantenimiento diario es la referencia: comer por debajo es "ahorrar",
      comer por encima es "deuda" (y el banco la paga).
    - El objetivo de la semana es el deficit que falta para el ritmo elegido
      (0.5 kg/semana -> 3 850 kcal).
    - Lo que falta se reparte entre los dias que quedan: ese es el margen, y
      por eso un dia fuera de la raya no rompe la semana.
    """
    days_left = max(0, int(days_left))
    if not target:
        return {
            "maintenance": 0,
            "consumed": int(consumed_kcal),
            "logged_days": 0,
            "saved": 0,
            "goal": 0,
            "pending": 0,
            "days_left": days_left,
            "daily_saving_needed": 0,
            "daily_target": None,
            "floor": 0,
            "over_budget": False,
            "note": "",
        }

    maintenance = int(target.maintenance_kcal)
    consumed = int(consumed_kcal)
    logged = max(0, int(logged_days))
    floor = int(target.floor_kcal or MIN_KCAL["other"])

    saved = logged * maintenance - consumed
    goal = int(target.weekly_goal_kcal)
    pending = goal - saved

    note = ""
    over_budget = False
    daily_target: int | None = None
    daily_saving_needed = 0.0

    if goal > 0 and days_left > 0:
        daily_saving_needed = pending / days_left
        candidate = maintenance - daily_saving_needed
        if candidate < floor:
            # La semana ya no cuadra: no se propone bajar del suelo. El exceso
            # se corrige la semana siguiente, no comiendo menos de lo seguro.
            daily_target = floor
            over_budget = True
            note = (
                f"Llevas {pending} kcal de mas de las que tenias previstas. No te voy a "
                f"bajar de {floor} kcal/dia: el exceso se corrige la semana "
                "siguiente, no comiendo menos de lo seguro."
            )
        else:
            daily_target = int(round(min(candidate, maintenance)))
    elif goal > 0:
        # Ultimo dia de la semana: no queda margen para repartir.
        daily_target = int(round(max(floor, min(maintenance, maintenance - pending))))
        if pending > 0:
            note = "Ultimo dia de la semana: hoy cierra el objetivo de ahorro."

    if not over_budget and daily_saving_needed < 0:
        note = (
            f"Vas {abs(int(round(daily_saving_needed)))} kcal/dia por debajo de "
            "lo necesario: podrias comer algo mas sin pasarte."
        )

    return {
        "maintenance": maintenance,
        "consumed": consumed,
        "logged_days": logged,
        "saved": saved,
        "goal": goal,
        "pending": pending,
        "days_left": days_left,
        "daily_saving_needed": int(round(daily_saving_needed)),
        "daily_target": daily_target,
        "floor": floor,
        "over_budget": over_budget,
        "note": note,
    }


def daily_saving(maintenance_kcal: int, consumed_kcal: int) -> int:
    """Cuanto se ha ahorrado (o se ha gastado de mas) hoy."""
    return int(maintenance_kcal) - int(consumed_kcal)


# --------------------------------------------------------------------------
# Tendencia de peso
# --------------------------------------------------------------------------

def linear_trend(points: Sequence[tuple[datetime, float]]) -> float:
    """Pendiente en kg/dia de una regresion lineal sobre (fecha, peso)."""
    if len(points) < 2:
        return 0.0
    base = points[0][0]
    xs = [(dt - base).total_seconds() / 86400.0 for dt, _ in points]
    ys = [w for _, w in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 1e-9:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return num / denom


def parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def weight_trend(points: Sequence[tuple[datetime, float]], days: int = 14) -> dict[str, Any]:
    """Tendencia de peso reciente en kg/semana +diagnostico de meseta."""
    if not points:
        return {
            "weekly_rate": 0.0,
            "has_data": False,
            "plateau": False,
            "insufficient": True,
            "current": None,
            "change_kg": 0.0,
        }

    points = sorted(points, key=lambda p: p[0])
    cutoff = points[-1][0] - timedelta(days=days)
    window = [p for p in points if p[0] >= cutoff] or points[-2:]
    weekly = linear_trend(window) * 7.0
    change = points[-1][1] - window[0][1]
    return {
        "weekly_rate": round(weekly, 3),
        "has_data": len(window) >= 2,
        "insufficient": len(window) < 2,
        "plateau": abs(weekly) < 0.1 and len(window) >= 4,
        "current": round(points[-1][1], 1),
        "change_kg": round(change, 2),
        "samples": len(window),
    }


def eta_weeks(current_kg: float, target_kg: float, weekly_rate: float) -> float | None:
    """Semanas estimadas hasta el peso objetivo (None si no aplica)."""
    if weekly_rate <= 0.01:
        return None
    delta = current_kg - target_kg
    if delta <= 0:
        return 0.0
    return round(delta / weekly_rate, 1)


def projected_weight(current_kg: float, weekly_rate: float, weeks: float) -> float:
    return round(current_kg - weekly_rate * weeks, 1)


def activity_label(key: str) -> str:
    return ACTIVITY_LEVELS.get((key or "").lower(), ACTIVITY_LEVELS["sedentary"])[0]
