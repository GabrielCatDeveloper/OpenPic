"""Cliente de inferencia para Food-R1 (Qwen3-VL-8B).

Formatos de prompt tomados del script de evaluacion oficial
(`eval/infer_caloriebench80k.py` del repo hustvl/Food-R1). El modelo fue
entrenado con ese esquema `<answer>...</answer>`, asi que deviate de el
provoca salidas no parseables.

Los macros no se le preguntan al modelo: se derivan de la tabla de ingredientes
para que las cifras mostradas sean coherentes entre si.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from foodbot.config import Config
from foodbot.data.ingredients import match_ingredient

# --------------------------------------------------------------------------
# Prompts (verbatim del repo oficial para el calculo de calorias)
# --------------------------------------------------------------------------

CALORIE_SYSTEM = (
    "You are a cooking and nutrition assistant.\n"
    "Produce exactly one block wrapped in <answer>...</answer> and nothing else.\n\n"
    "<answer>The dish is <DishName>, and has <N> ingredients in total. "
    "They are: <item1>, <item2>, ... <itemN>. "
    "They have a total of <TotalKcal> kcal.</answer>\n\n"
    "Rules:\n"
    "1. Keep the colon after \"They are:\".\n"
    "2. Separate items with commas only.\n"
    "3. Each item is \"<qty><unit> of <ingredient>\", or just \"<ingredient>\" if quantity is unknown.\n"
    "4. The last sentence must present the total calories exactly as \"<TotalKcal> kcal.\""
)

CALORIE_PROMPT = (
    "You are a food and nutrition expert. Using the image only, produce exactly one response "
    "wrapped in <answer>...</answer> with this template (replace placeholders): "
    "\"The dish is {DISH}, and has {COUNT} ingredients in total. They are: {INGREDIENTS}. "
    "They have a total of {KCAL} kcal.\" Requirements: infer the dish from the image; count "
    "the ingredients; list ingredients as a comma-separated list (include amounts if visible; "
    "omit amounts if not readable); provide the total energy in kcal; do not add explanations, "
    "steps, or any text outside the <answer> tag."
)

NUTRITION_SYSTEM = (
    "You are a registered dietitian. Answer only with a single JSON object, no markdown, "
    "no extra text. Keys: \"dish\" (string), \"calories\" (number, kcal), \"mass_g\" (number), "
    "\"protein_g\" (number), \"carbs_g\" (number), \"fat_g\" (number)."
)

NUTRITION_PROMPT = (
    "Analyse the food in the image and return ONLY a JSON object with the nutritional "
    "information of the whole dish: {\"dish\": string, \"calories\": number, \"mass_g\": number, "
    "\"protein_g\": number, \"carbs_g\": number, \"fat_g\": number}. "
    "Estimate realistic restaurant/home portions. No explanation, JSON only."
)

PLAN_SYSTEM = (
    "You are a nutritionist. Answer strictly in Spanish, using short lines. "
    "Never use markdown headers or code blocks. Start each line with '- '."
)

PLAN_PROMPT = (
    "Tengo este perfil: {profile}. Objetivo diario: {kcal} kcal "
    "({protein} g de proteina, {carbs} g de carbohidratos, {fat} g de grasa). "
    "Ya he comido hoy: {eaten} kcal. "
    "{week}"
    "Propone un plan de comida para el resto del dia de hoy, en espanol, con el formato exacto:\n"
    "- COMIDA: nombre del plato | {{calories}} kcal | P {{protein}} g | "
    "C {{carbs}} g | G {{fat}} g\n"
    "- COMIDA: nombre del plato | {{calories}} kcal | P {{protein}} g | "
    "C {{carbs}} g | G {{fat}} g\n"
    "Escribe una linea breve de consejo al final. Usa solo lineas que empiecen por '- '."
)

ADVICE_SYSTEM = (
    "You are a nutritionist. Answer in Spanish, in 3 to 5 short bullet points starting with '- '. "
    "No preamble, no markdown headers."
)

ADVICE_PROMPT = (
    "Perfil: {profile}. Objetivo: {goal}. Hoy llevo {eaten} kcal de un objetivo de {kcal} kcal "
    "(proteina {protein} g, carboidratos {carbs} g, grasa {fat} g). "
    "Dame 3-5 consejos practicos y especificos de hoy en espanol, sin repetir lo obvio."
)

# --------------------------------------------------------------------------
# Modelos de datos
# --------------------------------------------------------------------------


@dataclass
class ParsedIngredient:
    raw: str
    name: str
    quantity: float | None
    unit: str | None
    grams: float
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    matched: bool


@dataclass
class FoodAnalysis:
    dish: str
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    mass_g: float
    ingredients: list[ParsedIngredient] = field(default_factory=list)
    confidence: float = 0.0
    raw: str = ""
    engine: str = ""
    parsed: bool = True
    warnings: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {
            "dish": self.dish,
            "calories": self.calories,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "mass_g": self.mass_g,
            "ingredients": [
                {
                    "name": ing.name,
                    "raw": ing.raw,
                    "quantity": ing.quantity,
                    "unit": ing.unit,
                    "grams": ing.grams,
                    "kcal": ing.kcal,
                    "protein_g": ing.protein_g,
                    "carbs_g": ing.carbs_g,
                    "fat_g": ing.fat_g,
                    "matched": ing.matched,
                }
                for ing in self.ingredients
            ],
        }


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
_DISH_RE = re.compile(
    r"the\s+dish\s+is\s+(?P<dish>.+?)[,\.]?\s+and\s+has\s+(?P<count>\d+)\s+ingredients?",
    re.I | re.S,
)
_INGREDIENTS_RE = re.compile(
    r"they\s+are\s*:\s*(?P<items>.+?)\.\s*they\s+have\s+a\s+total\s+of", re.I | re.S
)
_KCAL_RE = re.compile(
    r"(?:total\s+of|total)\s*:?\s*(?P<kcal>\d{1,4}(?:[.,]\d+)?)\s*kcal", re.I
)
_QTY_OF_RE = re.compile(
    r"^(?P<qty>\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?)\s*(?P<unit>[a-zA-Z°]{1,12})?\s+of\s+(?P<name>.+?)$",
    re.I,
)
_QTY_ONLY_RE = re.compile(r"^(?P<qty>\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?)\s*(?P<name>.+?)$")


def extract_answer(text: str) -> str:
    """Devuelve el contenido del primer bloque <answer>...</answer>."""
    if not text:
        return ""
    match = _ANSWER_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        pass
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            denominator = float(den.strip())
            return float(num.strip()) / denominator if denominator else None
        except (ValueError, ZeroDivisionError):
            return None
    return None


def parse_ingredient_item(item: str) -> tuple[str, float | None, str | None]:
    """'150g of chicken breast' -> ('chicken breast', 150.0, 'g').

    Formatos admitidos por Food-R1:
      - '<cantidad><unidad> de <ingrediente>'  -> '150g of chicken breast'
      - '<cantidad> <ingrediente>'              -> '2 eggs'
      - '<ingrediente>'                        -> 'olive oil'
    """
    cleaned = item.strip().strip(".").strip()
    if not cleaned:
        return ("", None, None)

    match = _QTY_OF_RE.match(cleaned)
    if match:
        name = (match.group("name") or "").strip()
        qty = _parse_number(match.group("qty"))
        unit = (match.group("unit") or "").strip().lower() or None
        if name:
            return (name, qty, unit)

    match = _QTY_ONLY_RE.match(cleaned)
    if match:
        name = (match.group("name") or "").strip()
        qty = _parse_number(match.group("qty"))
        if name:
            return (name, qty, None)

    return (cleaned, None, None)


def parse_food_answer(text: str) -> FoodAnalysis:
    """Convierte la respuesta cruda de Food-R1 en un FoodAnalysis."""
    answer = extract_answer(text)
    warnings: list[str] = []
    parsed = bool(_ANSWER_RE.search(text or ""))

    dish = ""
    count = 0
    dish_match = _DISH_RE.search(answer)
    if dish_match:
        dish = dish_match.group("dish").strip().strip(",.")
        count = int(dish_match.group("count") or 0)

    kcal_match = _KCAL_RE.search(answer) or _KCAL_RE.search(text or "")
    calories = int(round(_parse_number(kcal_match.group("kcal")))) if kcal_match else 0
    if not kcal_match:
        warnings.append("El modelo no devolvio las calorias en el formato esperado.")

    items: list[str] = []
    ing_match = _INGREDIENTS_RE.search(answer)
    if ing_match:
        raw_items = ing_match.group("items")
        items = [i.strip() for i in raw_items.split(",") if i.strip()]
    elif count:
        warnings.append("No se pudo aislar la lista de ingredientes.")

    parsed_ingredients: list[ParsedIngredient] = []
    for raw_item in items:
        name, qty, unit = parse_ingredient_item(raw_item)
        if not name:
            continue
        match = match_ingredient(name, qty, unit)
        parsed_ingredients.append(
            ParsedIngredient(
                raw=raw_item,
                name=name,
                quantity=qty,
                unit=unit,
                grams=match.grams,
                kcal=match.kcal,
                protein_g=match.protein,
                carbs_g=match.carbs,
                fat_g=match.fat,
                matched=match.matched,
            )
        )

    table_kcal = sum(i.kcal for i in parsed_ingredients)
    table_protein = sum(i.protein_g for i in parsed_ingredients)
    table_carbs = sum(i.carbs_g for i in parsed_ingredients)
    table_fat = sum(i.fat_g for i in parsed_ingredients)
    mass = sum(i.grams for i in parsed_ingredients)

    coverage = (
        sum(1 for i in parsed_ingredients if i.matched) / len(parsed_ingredients)
        if parsed_ingredients
        else 0.0
    )

    # Las calorias del modelo mandan; los macros se escalan para que cuadren
    # con ellas (si no, el usuario veria macros que no suman las kcal mostradas).
    protein, carbs, fat = table_protein, table_carbs, table_fat
    if calories and table_kcal > 0:
        deviation = abs(table_kcal - calories) / max(calories, 1)
        if deviation > 0.08 and coverage >= 0.5:
            factor = calories / table_kcal
            protein *= factor
            carbs *= factor
            fat *= factor
            if deviation > 0.25:
                warnings.append(
                    "Las calorias del modelo y el calculo por ingredientes difieren; "
                    "he ajustado los macros para que cuadren con las calorias."
                )
    elif calories:
        # Sin tabla: reparto por defecto con reparto proteico 30/carb 45/grasa 25.
        protein = calories * 0.30 / 4
        carbs = calories * 0.45 / 4
        fat = calories * 0.25 / 9
        if not parsed_ingredients:
            mass = 0.0

    if not calories and table_kcal:
        calories = int(round(table_kcal))
        warnings.append("Calorias calculadas a partir de la tabla de ingredientes.")

    # Cierre contable: 4P + 4C + 9G debe sumar exactamente las calorias.
    # La tabla de alimentos no cumple la identidad al 100% (redondeos de la
    # base de datos), asi que se ajusta el carboidrato, que es el macro mas
    # flexible, para que las cifras mostradas cuadren entre si.
    if calories > 0:
        from_macros = protein * 4 + carbs * 4 + fat * 9
        gap = calories - from_macros
        if abs(gap) > 1.0:
            carbs = max(0.0, carbs + gap / 4.0)

    confidence = 0.5
    if parsed:
        confidence += 0.15
    if kcal_match and dish:
        confidence += 0.10
    confidence += 0.20 * coverage
    if calories <= 0:
        confidence -= 0.4
    elif calories > 3000:
        confidence -= 0.25
        warnings.append("El total calórico parece sobestimado; revisa la porcion.")
    if not parsed_ingredients:
        confidence -= 0.2
    confidence = max(0.05, min(0.98, confidence))

    if not dish:
        dish = "Plato sin identificar"

    return FoodAnalysis(
        dish=dish[:120],
        calories=int(calories),
        protein_g=round(protein, 1),
        carbs_g=round(carbs, 1),
        fat_g=round(fat, 1),
        mass_g=round(mass, 1),
        ingredients=parsed_ingredients,
        confidence=round(confidence, 2),
        raw=text or "",
        parsed=parsed,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Proveedores de inferencia
# --------------------------------------------------------------------------


class InferenceError(RuntimeError):
    pass


def sniff_mime(data: bytes) -> str:
    """Detecta el tipo real de la imagen a partir de sus bytes magic.

    Telegram entrega fotos en JPEG y documentos en PNG/WebP. Enviar un PNG
    etiquetado como JPEG hace que el decodificador del servidor falle.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


class FoodR1Client:
    """Envoltura sobre el endpoint de inferencia (llama.cpp / Ollama / mock)."""

    def __init__(self, config: Config):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        # Cola de inferencia: llama.cpp en local va con --parallel 1, asi que
        # solo se admite una peticion a la vez. Las demas esperan su turno en
        # lugar de saturar el servidor (o la RAM).
        self._semaphore = asyncio.Semaphore(max(1, config.llm_concurrency))

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.config.llm_timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # -- llamadas ---------------------------------------------------------
    async def complete(
        self,
        system: str,
        prompt: str,
        image_b64: str | None = None,
        image_mime: str = "image/jpeg",
        max_tokens: int | None = None,
    ) -> str:
        provider = self.config.provider
        if provider == "mock":
            return self._mock_response(prompt)
        async with self._semaphore:
            if provider == "ollama":
                return await self._ollama(system, prompt, image_b64)
            return await self._openai_compat(system, prompt, image_b64, image_mime, max_tokens)

    async def _openai_compat(
        self,
        system: str,
        prompt: str,
        image_b64: str | None,
        image_mime: str,
        max_tokens: int | None,
    ) -> str:
        url = f"{self.config.llm_base_url}/chat/completions"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_b64:
            content.insert(
                0,
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                },
            )
        body: dict[str, Any] = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": self.config.llm_temperature,
            "max_tokens": max_tokens or self.config.llm_max_tokens,
            "stream": False,
        }
        if self.config.llm_extra_body:
            try:
                body.update(json.loads(self.config.llm_extra_body))
            except json.JSONDecodeError:
                pass

        headers = {"Content-Type": "application/json"}
        if self.config.llm_api_key:
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"

        client = await self.client()
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise InferenceError(f"No se pudo contactar con el motor: {exc}") from exc
        if response.status_code >= 400:
            raise InferenceError(
                f"El motor devolvio {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise InferenceError("Respuesta no JSON del motor") from exc

        try:
            message = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceError(f"Respuesta inesperada del motor: {str(data)[:300]}") from exc
        if isinstance(message, list):
            message = "".join(
                part.get("text", "") for part in message if isinstance(part, dict)
            )
        return str(message)

    async def _ollama(self, system: str, prompt: str, image_b64: str | None) -> str:
        base = self.config.llm_base_url
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        url = f"{base}/api/chat"
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if image_b64:
            message["images"] = [image_b64]
        body = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                message,
            ],
            "stream": False,
            "options": {
                "temperature": self.config.llm_temperature,
                "num_predict": self.config.llm_max_tokens,
            },
        }
        client = await self.client()
        try:
            response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise InferenceError(f"No se pudo contactar con Ollama: {exc}") from exc
        if response.status_code >= 400:
            raise InferenceError(
                f"Ollama devolvio {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
            return str(data["message"]["content"])
        except (ValueError, KeyError) as exc:
            raise InferenceError("Respuesta inesperada de Ollama") from exc

    def _mock_response(self, prompt: str) -> str:
        """Respuesta simulada para probar el bot sin modelo descargado.

        Mantiene la coherencia entre ingredientes y TOTAL calórico, igual que
        hace el modelo real (que razona ingrediente a ingrediente).
        """
        return (
            "<answer>The dish is Arroz con pollo y verduras, and has 6 ingredients in total. "
            "They are: 120g of rice, 150g of chicken breast, 100g of tomato, "
            "50g of onion, 10g of olive oil, 1g of salt. "
            "They have a total of 530 kcal.</answer>"
        )

    # -- API de alto nivel ------------------------------------------------
    async def analyze_photo(
        self,
        image_bytes: bytes,
        mime: str = "image/jpeg",
        note: str | None = None,
    ) -> FoodAnalysis:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = CALORIE_PROMPT
        if note:
            prompt += f"\n Additional context from the user: {note}"
        raw = await self.complete(CALORIE_SYSTEM, prompt, image_b64, mime)
        analysis = parse_food_answer(raw)
        analysis.engine = self.config.llm_model
        return analysis

    async def analyze_photo_nutrition(
        self, image_bytes: bytes, mime: str = "image/jpeg"
    ) -> dict[str, Any]:
        """Segunda pasada opcional con salida JSON de macros."""
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        raw = await self.complete(
            NUTRITION_SYSTEM, NUTRITION_PROMPT, image_b64, mime, max_tokens=256
        )
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    async def make_plan(
        self,
        *,
        profile: str,
        kcal: int,
        protein: float,
        carbs: float,
        fat: float,
        eaten: int,
        week: str = "",
    ) -> str:
        prompt = PLAN_PROMPT.format(
            profile=profile,
            kcal=kcal,
            protein=protein,
            carbs=carbs,
            fat=fat,
            eaten=eaten,
            week=week,
        )
        return await self.complete(PLAN_SYSTEM, prompt)

    async def make_advice(
        self, *, profile: str, goal: str, kcal: int, protein: float, carbs: float,
        fat: float, eaten: int,
    ) -> str:
        prompt = ADVICE_PROMPT.format(
            profile=profile,
            goal=goal,
            kcal=kcal,
            protein=protein,
            carbs=carbs,
            fat=fat,
            eaten=eaten,
        )
        return await self.complete(ADVICE_SYSTEM, prompt)

    async def health(self) -> tuple[bool, str]:
        """Comprueba que el motor responde."""
        try:
            if self.config.provider == "mock":
                return (True, "mock (sin modelo real)")
            if self.config.provider == "ollama":
                base = self.config.llm_base_url
                if base.endswith("/v1"):
                    base = base[:-3]
                client = await self.client()
                response = await client.get(f"{base}/api/tags", timeout=10)
                return (response.status_code == 200, f"ollama {response.status_code}")
            client = await self.client()
            response = await client.get(f"{self.config.llm_base_url}/models", timeout=10)
            return (response.status_code == 200, f"http {response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])
