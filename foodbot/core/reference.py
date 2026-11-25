# -*- coding: utf-8 -*-
"""Separa la fisiologia (los numeros) del trato (como te hablo).

La pregunta de la fisiologia no es 'tu sexo': es 'que referencia metabolica
te encaja'. Son cosas distintas:

- **Los numeros** (kcal, suelo de seguridad) dependen de la composicion
  corporal actual y del estado hormonal actual. El sexo asignado al
  nacimiento es solo un proxy de eso: bueno para quien es cis, malo para
  quien esta con tratamiento hormonal.
- **El trato** (como te llamo, que consejo te doy) depende de tus
  pronombres, y no tiene nada que ver con los numeros.

Y la mejor variable es el **% de grasa**: con el, la ecuacion es
Katch-McArdle (masa libre) y no mira el sexo en absoluto.
"""

from __future__ import annotations

# El campo 'sex' de la base de datos guarda la referencia metabolica:
# 'male' | 'female' | 'other' (punto medio). No es la identidad de la persona.
REFERENCE_VALUES = {
    "male": "La de un hombre (gasto algo mayor, suelo de 1.500 kcal)",
    "female": "La de una mujer (suelo de 1.200 kcal)",
    "other": "La intermedia (suelo de 1.400 kcal)",
}

REFERENCE_UNKNOWN = "other"


def reference_label(value: str | None) -> str:
    """Texto del boton de la referencia metabólica."""
    return REFERENCE_VALUES.get((value or "other").lower(), REFERENCE_VALUES["other"])


def explain_reference(value: str | None, hormone_therapy: str | None = None) -> str:
    """Por que se usa esa referencia, en una frase."""
    key = (value or "other").lower()
    hormones = (hormone_therapy or "").lower()
    if hormones in {"estrogen", "estrogeno", "testosterone", "testosterona"}:
        return (
            "Con tratamiento hormonal uso el punto medio de las referencias: "
            "no hay ecuaciones especificas y lo que manda es tu composicion "
            "corporal actual. Si me das tu % de grasa, el calculo es exacto."
        )
    if key == "male":
        return "Uso la referencia masculina para el calculo del metabolismo."
    if key == "female":
        return "Uso la referencia femenina para el calculo del metabolismo."
    return (
        "Uso el punto medio de las referencias. Si me das tu % de grasa, el "
        "calculo va por composicion corporal, que es mas preciso."
    )
