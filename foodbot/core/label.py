"""Leer la etiqueta de un producto envasado.

El modelo transcribe la etiqueta y el bot hace la cuenta con el peso:

    450 kcal/100 g  x  250 g  =  1.125 kcal

La transcripcion **siempre se confirma** antes de guardar: la IA propone y la
persona valida. Si el modelo no entiende la etiqueta, se le pide el dato a
mano; si no hay peso, se estima y se dice que es una estimacion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --- el prompt que lee la etiqueta -----------------------------------------
LABEL_SYSTEM = (
    "Eres un lector de etiquetas de alimentos. Miras la foto de una etiqueta "
    "y transcribe solo los valores que se ven, sin inventar nada."
)

LABEL_PROMPT = (
    "Mira la foto de esta etiqueta de alimento y responde SOLO con estos "
    "cinco campos, uno por linea, sin mas texto:\n"
    "CALORIAS_100G: <numero> kcal por 100 g\n"
    "PROTEINA_100G: <numero> g por 100 g\n"
    "HIDRATOS_100G: <numero> g por 100 g\n"
    "GRASA_100G: <numero> g por 100 g\n"
    "PESO_NETO: <numero> g (el peso total del envase)\n"
    "Si un dato no aparece, escribe DESCONOCIDO en ese campo. Si los valores "
    "son por racion y no por 100 g, indica tambien RACION_GRAMOS: <numero> g."
)

# El prompt de estimacion del peso (cuando la etiqueta no lo trae).
WEIGHT_PROMPT = (
    "Mira la foto de este producto de alimentacion y estima cuanto pesa el "
    "envase o la racion. Responde SOLO con la linea:\n"
    "PESO_ESTIMADO: <numero> g\n"
    "No pongas nada mas."
)


@dataclass
class LabelData:
    """Lo que se ha leido de la etiqueta (o estimado)."""

    kcal_100g: float | None = None
    protein_100g: float | None = None
    carbs_100g: float | None = None
    fat_100g: float | None = None
    net_weight_g: float | None = None
    serving_g: float | None = None
    raw: str = ""

    @property
    def has_values(self) -> bool:
        return self.kcal_100g is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kcal_100g": self.kcal_100g,
            "protein_100g": self.protein_100g,
            "carbs_100g": self.carbs_100g,
            "fat_100g": self.fat_100g,
            "net_weight_g": self.net_weight_g,
            "serving_g": self.serving_g,
        }


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def _field(text: str, *names: str) -> str | None:
    """El valor de un campo 'NOMBRE: valor' de la transcripcion."""
    for name in names:
        pattern = rf"{name}\s*:\s*([^\n]*)"
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def _grams(value: str | None) -> float | None:
    """'250 g' -> 250.0 ; '0,5 kg' -> 500.0"""
    if not value:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(g|kg|ml|l)?", value, re.I)
    if not match:
        return None
    number = _number(match.group(1))
    if number is None:
        return None
    unit = (match.group(2) or "g").lower()
    if unit in {"kg", "l"}:
        number *= 1000
    return number


def parse_label(text: str) -> LabelData:
    """Interpreta la transcripcion del modelo (tolerante a como la escriba)."""
    text = text or ""
    data = LabelData(raw=text)

    # Primero el formato "CAMPO: valor" y, si no, la redaccion libre.
    kcal_raw = _field(text, "CALORIAS_100G", "CALORIAS", "KCAL")
    if kcal_raw:
        match = re.search(r"(\d+(?:[.,]\d+)?)", kcal_raw)
        if match:
            data.kcal_100g = _number(match.group(1))
    if data.kcal_100g is None:
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*kcal", text, re.I)
        if match:
            data.kcal_100g = _number(match.group(1))

    for attribute, names, free in (
        (
            "protein_100g",
            ("PROTEINA_100G", "PROTEINAS", "PROTEINA", "PROTEIN"),
            r"prote[i\u00ed]nas?\D{0,15}(\d+(?:[.,]\d+)?)",
        ),
        (
            "carbs_100g",
            ("HIDRATOS_100G", "HIDRATOS", "CARBOHIDRATOS", "CARBS"),
            r"hidratos\D{0,15}(\d+(?:[.,]\d+)?)|carbohidratos\D{0,15}(\d+(?:[.,]\d+)?)",
        ),
        (
            "fat_100g",
            ("GRASA_100G", "GRASAS", "GRASA", "FAT"),
            r"grasas?\D{0,15}(\d+(?:[.,]\d+)?)",
        ),
    ):
        raw = _field(text, *names)
        value = None
        if raw:
            match = re.search(r"(\d+(?:[.,]\d+)?)", raw)
            if match:
                value = _number(match.group(1))
        if value is None:
            match = re.search(free, text, re.I)
            if match:
                value = _number(next(g for g in match.groups() if g))
        if value is not None:
            setattr(data, attribute, value)

    data.net_weight_g = _grams(_field(text, "PESO_NETO", "PESO", "NET_WEIGHT"))
    data.serving_g = _grams(_field(text, "RACION_GRAMOS", "RACION", "SERVING"))
    return data


def parse_weight(text: str) -> float | None:
    """Interpreta la estimacion de peso del modelo."""
    match = re.search(r"PESO_ESTIMADO\s*:\s*(\d+(?:[.,]\d+)?)", text or "", re.I)
    if match:
        return _number(match.group(1))
    grams = _grams(text)
    return grams


def label_to_portion(data: LabelData, weight_g: float) -> dict[str, float]:
    """Convierte los valores por 100 g a la porcion que se ha comido.

    Si la transcripcion era por racion (y no por 100 g), se escala.
    """
    weight_g = max(1.0, float(weight_g))
    factor = weight_g / 100.0

    kcal = data.kcal_100g
    protein = data.protein_100g
    carbs = data.carbs_100g
    fat = data.fat_100g

    # Si vino por racion: lo pasamos a 100 g con el peso de la racion.
    if data.serving_g and data.kcal_100g:
        factor = weight_g / max(data.serving_g, 1.0)

    return {
        "calories": round((kcal or 0) * factor),
        "protein_g": round((protein or 0) * factor, 1),
        "carbs_g": round((carbs or 0) * factor, 1),
        "fat_g": round((fat or 0) * factor, 1),
    }
