# -*- coding: utf-8 -*-
"""Consejos nutritionales especificos de la fisiologia del usuario.

Por que son deterministas y no los genera el modelo: Food-R1 esta afinado
para calcular calorias de un plato, no para dar consejo clinico. Estas
pautas salen de las referencias nutritionales y no cambian con el modelo.

La evidencia que hay detras:

- **Hierro**: 18 mg/dia en mujeres premenopausicas (19-50) y 8 mg en
  hombres y mujeres posmenopausicas (IOM/NIH; EFSA: PRI 16 mg y 11 mg).
  La causa es la perdida menstrual, no el tamano corporal.
- **Calcio**: 1000 mg/dia en mujeres menores de 50 y 1300 mg a partir de los
  50 (RDI); vitamina D 800-1000 UI. En deficit, la perdida de hueso acelera.
- **Proteina**: 1.6-2.2 g/kg para preservar masa muscular en deficit, pero por
  encima de 1.5 g/kg aumenta el riesgo de fractura (revision de menopausia,
  2023), asi que mas no es mejor.
- **Personas trans**: con testosterona sube la masa muscular y baja la grasa
  (el peso puede subir mientras la grasa baja); con estrogeno ocurre lo
  contrario. **No hay ecuaciones especificas**: lo que manda es la composicion
  corporal actual (Katch-McArdle) y, sin ella, el punto medio de las
  referencias (Frenser & Fischer, Int J Obes 2025; Adv Nutr 2025).
- La AND (Academy of Nutrition and Dietetics, 2024) recomienda usar un
  **rango** entre los valores masculino y femenino cuando la cifra depende
  del sexo, y tratar siempre a la persona por su nombre y pronombres.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# Referencias
# --------------------------------------------------------------------------

IRON_FEMALE_PREMENOPAUSIC = 18
IRON_MALE_OR_POSTMENOPAUSIC = 8
CALCIUM_UNDER_50 = 1000
CALCIUM_OVER_50 = 1300
VITAMIN_D_IU = "800-1000 UI"
PROTEIN_DEFICIT = "1,6-2,2 g/kg"


def _sex(profile: dict[str, Any]) -> str:
    return str(profile.get("sex") or "other").lower()


def _hormones(profile: dict[str, Any]) -> str:
    return str(profile.get("hormone_therapy") or "none").lower()


def _pronouns(profile: dict[str, Any]) -> str:
    """male | female | neutral (por defecto, el sexo del perfil)."""
    value = str(profile.get("pronouns") or "").lower()
    if value in {"male", "female", "neutral"}:
        return value
    sex = _sex(profile)
    return sex if sex in {"male", "female"} else "neutral"


def _age(profile: dict[str, Any]) -> int:
    try:
        return int(profile.get("age") or 30)
    except (TypeError, ValueError):
        return 30


def _on_ht(profile: dict[str, Any]) -> bool:
    return _hormones(profile) in {"estrogen", "estrogeno", "testosterone", "testosterona"}


def specific_tips(profile: dict[str, Any], target: Any = None) -> list[str]:
    """Consejos concretos segun la fisiologia y la situacion del usuario.

    Quien decide el marco del consejo son los **pronombres** que ha elegido
    el usuario; el sexo del perfil solo se usa para la ecuacion y el suelo.
    Asi una mujer trans recibe el consejo de hierro y calcio, un hombre trans
    el de proteina y preservacion muscular, y quien prefiera un marco
    neutro no recibe ninguno de los dos.
    """
    tips: list[str] = []
    sex = _sex(profile)
    pronouns = _pronouns(profile)
    hormones = _hormones(profile)
    age = _age(profile)
    goal = str(profile.get("goal") or "maintain").lower()
    deficit = int(getattr(target, "deficit_kcal", 0) or 0) if target else 0
    protein = float(getattr(getattr(target, "macro", None), "protein_g", 0) or 0) if target else 0
    weight = float(profile.get("weight_kg") or 0)
    in_deficit = deficit > 0

    # --- deficit: la proteina es lo que preserva el musculo ---------------
    if in_deficit and weight:
        per_kg = protein / weight if weight else 0
        if per_kg and per_kg < 1.6:
            tips.append(
                f"📉 Con deficit, la proteina importa mas que las calorías: "
                f"vas a {per_kg:.1f} g/kg y el rango para no perder musculo es "
                f"{PROTEIN_DEFICIT}. Repartela entre las comidas."
            )

    # --- personas trans ----------------------------------------------------
    if hormones in {"testosterone", "testosterona"}:
        tips.append(
            "💊 Con testosterona, la masa muscular sube y la grasa baja: el peso "
            "puede subir mientras la grasa baja. Mide tambien el porcentaje de "
            "grasa o la cintura, no solo la bascula."
        )
        tips.append(
            "🩸 La testosterona sube el hemoglobino: tu cuerpo necesita mas "
            "hierro. El hierro de la carne se absorbe mejor, y con vitamina C "
            "a la hora de comer."
        )
        tips.append(
            "📅 Tu metabolismo cambia durante el primer año de tratamiento: "
            "vuelve a revisar tus datos de mantenimiento a los 6 meses."
        )
    elif hormones in {"estrogen", "estrogeno"}:
        tips.append(
            "💊 Con estrogeno, la grasa se redistribuye (cadera y muslo) y la "
            "masa muscular baja un poco: el peso puede subir aunque no estés "
            "ganando grasa. Mira el porcentaje de grasa y la cintura."
        )
        tips.append(
            "🦴 El estrogeno protege el hueso: al lowest, el hueso necesita mas "
            f"calcio (1.000-1.200 mg al dia), vitamina D ({VITAMIN_D_IU}) y "
            "ejercicio de fuerza."
        )
    elif _on_ht(profile):
        tips.append(
            "💊 Tu tratamiento hormonal cambia el gasto y la composición "
            "corporal: recalibra el mantenimiento con tus propios datos de peso "
            "y comida (el bot lo hace solo cuando tiene registros suficientes)."
        )

    # --- TIPOS POR MARCO (pronombres) -------------------------------------
    if pronouns == "female" and age < 50:
        tips.append(
            f"🩸 Si tienes la menstruación, necesitas del orden de "
            f"{IRON_FEMALE_PREMENOPAUSIC} mg de hierro al dia (el doble que sin "
            "perdidas menstruales), por esa perdida. El hierro de la carne se "
            "absorbe mejor; acompanialo con vitamina C y deja el te y el cafe "
            "para despues de comer."
        )
        # El hierro tambien importa con testosterona: sube el hemoglobino.
        tips.append(
            "🩸 Si estas cansada o sin fuerzas, el deficit no es lo primero: "
            "cuando hay perdidas menstruales, la deficiencia de hierro es una "
            "causa muy frecuente de fatiga. Un analisis de ferritina lo dice."
        )
    elif pronouns == "female":
        tips.append(
            f"🦴 Con la menopausia, el hueso pierde entre un 1% y un 2% al "
            f"año: apunta a {CALCIUM_OVER_50} mg de calcio al dia, "
            f"vitamina D ({VITAMIN_D_IU}) y una sesion de fuerza por semana."
        )
        tips.append(
            "🦴 En deficit el hueso tambien paga: sin suficiente calcio ni "
            "proteina, la masa osea baja mas de lo que bajaria el peso."
        )
    elif pronouns == "male":
        tips.append(
            "💪 Tu musculo es lo que sostiene tu metabolismo: en deficit, "
            f"mantener {PROTEIN_DEFICIT} de proteina y una sesion de fuerza "
            "por semana es lo que evita que bajes musculo en vez de grasa."
        )
        tips.append(
            f"🩸 Sin perdidas menstruales, tu referencia de hierro es del orden "
            f"de {IRON_MALE_OR_POSTMENOPAUSIC} mg al dia (la mitad que con "
            "menstruacion). Suma mas si entrenas a diario: el sudor y el "
            "sangrado de las plantas del pie tambien cuentan."
        )
        tips.append(
            "🍺 El alcohol suma rapido: unas pocas cervezas son el equivalente "
            "a un paseo de una hora. Si bebes, quitalo los dias de deficit."
        )

    # --- comun -------------------------------------------------------------
    if in_deficit:
        tips.append(
            "🍽 Come siempre algo de proteina en cada comida principal: es lo "
            "que hace que el deficit queme grasa y no musculo, y lo que te "
            "sostiene cuando el hambre aprieta."
        )
    tips.append(
        "📊 Pesa siempre en las mismas condiciones (por la manana, en ayunas) "
        "y mira la media de dos semanas: un kilo de un dia a otro es agua, no "
        "grasa."
    )

    return tips
