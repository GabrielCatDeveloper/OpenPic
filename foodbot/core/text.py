"""Helpers de formato para los mensajes de Telegram (HTML)."""

from __future__ import annotations

import html
from datetime import timedelta, tzinfo
from typing import Any, Sequence

from foodbot.core.nutrition import EnergyTarget, GOALS
from foodbot.core.timeutil import (
    SLOT_EMOJI,
    SLOT_LABELS,
    UTC,
    format_local_time,
    friendly_day,
    meal_slot,
    parse_day,
)


def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def progress_bar(current: float, total: float, width: int = 14) -> str:
    if total <= 0:
        return "▱" * width
    ratio = max(0.0, min(1.5, current / total))
    filled = int(round(ratio * width))
    filled = min(width, filled)
    bar = "▰" * filled + "▱" * (width - filled)
    if ratio > 1.0:
        bar = bar[:-1] + "!"
    return bar


def fmt_kg(value: float | None) -> str:
    return f"{value:.1f} kg" if value is not None else "sin datos"


def fmt_g(value: float | None) -> str:
    return f"{value:.0f} g" if value is not None else "-"


SCALE_NOTE = (
    "\U0001f4a7 <b>Ojo con la bascula:</b> un kilo de un dia a otro no es un "
    "kilo de grasa. El peso cambia por el agua y el glucogeno (la sal, los "
    "carbohidratos, el glucogeno que se vacia al entrenar), por el contenido "
    "intestinal, por la hora del dia y por el ciclo menstrual. M\u00e1s a\u00fan "
    "la <b>media de tus pesos de dos semanas</b> (que es justo lo que uso para "
    "la tendencia), nunca un peso suelto."
)

SCALE_NOTE_SHORT = (
    "\U0001f4a7 <i>El peso fluctua por el agua, el glucogeno, la sal y la "
    "hora del d\u00eda: mira la tendencia de dos semanas, no un d\u00eda suelto.</i>"
)


def goal_label(goal: str | None) -> str:
    return GOALS.get((goal or "").lower(), "Objetivo")


def target_line(target: EnergyTarget | None) -> str:
    if not target:
        return "Objetivo: <i>configura tu perfil con /perfil</i>"
    if target.deficit_kcal > 0:
        mode = f"deficit {abs(target.deficit_kcal)} kcal"
    elif target.deficit_kcal < 0:
        mode = f"superavit {abs(target.deficit_kcal)} kcal"
    else:
        mode = "mantenimiento"
    manual = " · fijado por ti" if target.source == "manual" else ""
    return (
        f"Objetivo: <b>{target.daily_kcal} kcal</b>"
        f"{manual} "
        f"(mantenimiento {target.maintenance_kcal} · BMR {target.bmr} · {mode})"
    )


WEEKDAY_NAMES = (
    "Lunes",
    "Martes",
    "Miercoles",
    "Jueves",
    "Viernes",
    "Sabado",
    "Domingo",
)


def week_line(summary: dict[str, Any]) -> str:
    """Linea compacta con el estado del banco de ahorro semanal."""
    bank = summary.get("bank") or {}
    target = summary.get("target")
    if not target or not bank.get("goal"):
        return ""

    saved = int(bank.get("saved") or 0)
    goal = int(bank.get("goal") or 0)
    pending = int(bank.get("pending") or 0)

    if saved >= 0:
        head = f"ahorrado <b>{saved} kcal</b> de {goal}"
    else:
        head = f"gastadas <b>{-saved} kcal</b> de más"

    if pending >= 0:
        tail = f"faltan <b>{pending} kcal</b> para el objetivo"
    else:
        tail = f"el banco va <b>{-pending} kcal</b> por encima"

    return f"🗓️ Semana: {head} · {tail}"


def week_saving_line(summary: dict[str, Any]) -> str:
    """Linea con el margen que queda para los dias que faltan."""
    bank = summary.get("bank") or {}
    target = summary.get("target")
    if not target or not bank.get("goal"):
        return ""
    days_left = int(bank.get("days_left") or 0)
    daily_target = bank.get("daily_target")
    if not days_left or not daily_target:
        return ""
    saving = int(bank.get("daily_saving_needed") or 0)
    return (
        f"🎯 Para los {days_left} días que quedan: "
        f"<b>{daily_target} kcal/día</b> "
        f"(ahorrando {saving} kcal/día)"
    )


def accumulated_message(data: dict[str, Any], width: int = 14) -> str:
    """La barra del deficit acumulado y el detalle semana a semana.

    Cada semana suma (si ahorraste) o resta (si te quedaste por encima) al
    total, asi que
    la barra sube y baja con el tiempo.
    """
    history = data.get("history") or []
    total = int(data.get("total") or 0)
    goal = data.get("goal_kcal")
    kg = float(data.get("kg_equivalent") or 0.0)

    lines = ["<b>📊 Acumulado</b>"]
    weeks_counted = data.get("weeks_counted", 0)
    if total >= 0:
        if goal:
            lines.append(f"{progress_bar(total, goal, width)}  {total}/{goal} kcal")
        else:
            lines.append(f"<b>{total} kcal</b> ahorradas en total")
        if kg:
            lines.append(
                f"Eso son <b>{kg:.2f} kg</b> de grasa ahorrados "
                f"en {weeks_counted} semanas."
            )
    else:
        # El total negativo: las semanas en las que te pasaste pesan mas.
        lines.append(f"{progress_bar(0, goal or 1, width)}  \u2212{abs(total)} kcal")
        lines.append(
            f"Vas <b>{abs(kg):.2f} kg</b> por encima: las semanas en las que "
            "te quedaste por encima restan del total."
        )

    lines.append(f"\n{SCALE_NOTE_SHORT}")

    if history:
        lines.append("\n<b>Semana a semana</b>")
        for item in history:
            saved = int(item.get("saved") or 0)
            running = int(item.get("total") or 0)
            when = _week_label(item.get("week"))
            sign = "+" if saved >= 0 else ""
            current = " <i>(en curso)</i>" if item.get("current") else ""
            color = "" if saved >= 0 else " 🔴"
            lines.append(
                f"  {when} → <b>{sign}{saved} kcal</b>{color} · "
                f"total {running} kcal{current}"
            )
        lines.append(
            "\n<i>Cada semana se suma al total lo que ahorraste y se resta lo que "
            "te quedaste por encima. La semana en curso todavia puede "
            "moverse.</i>"
        )
    return "\n".join(lines)


def _week_label(week_key: str | None) -> str:
    """'2026-11-17' -> '17/11'."""
    if not week_key:
        return "-"
    try:
        return parse_day(str(week_key)).strftime("%d/%m")
    except (TypeError, ValueError):
        return str(week_key)


def accumulated_line(data: dict[str, Any]) -> str:
    """Linea corta con el total acumulado (para /hoy)."""
    total = int(data.get("total") or 0)
    kg = float(data.get("kg_equivalent") or 0.0)
    if not total:
        return ""
    sign = "+" if total >= 0 else ""
    return (
        f"📊 Acumulado: <b>{sign}{total} kcal</b> "
        f"({abs(kg):.2f} kg) en {data.get('weeks_counted', 0)} semanas"
    )


def model_bullets(text: str, limit: int = 5) -> list[str]:
    """Convierte la respuesta del modelo en vinetas limpias.

    Food-R1 se pasa de largo con el formato (devuelve mas lineas de las
    pedidas y usa markdown), asi que se limpia el markdown y se recorta.
    """
    bullets: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "").strip()
        if line.startswith(("- ", "* ", "\u2022 ")):
            line = line[2:].strip()
        if not line:
            continue
        bullets.append(line)
    return bullets[:limit]


def advice_message(specific: list[str], model_text: str = "") -> str:
    """El consejo: primero lo especifico (con evidencia), despues el modelo."""
    lines = ["<b>\U0001f4a1 Consejo de hoy</b>"]
    if specific:
        lines.append("\n".join(f"\u2022 {esc(tip)}" for tip in specific))
    bullets = model_bullets(model_text)
    if bullets:
        lines.append("<b>\U0001f4ac Del modelo</b>")
        lines.append("\n".join(f"\u2022 {esc(bullet)}" for bullet in bullets))
    if not specific and not bullets:
        lines.append("<i>No tengo nada especial que decirte hoy.</i>")
    return "\n".join(lines)


def meal_plan_message(plan: list[dict[str, Any]]) -> str:
    """El plan en el formato que se pidio siempre:
    '- COMIDA: plato | 480 kcal | P 32 g | C 45 g | G 18 g'
    """
    if not plan:
        return "<i>Hoy ya has cubierto tu objetivo: no hace falta plan.</i>"
    lines = []
    for dish in plan:
        lines.append(
            f"- COMIDA: {esc(str(dish.get('name') or 'Plato'))} | "
            f"{int(dish.get('kcal') or 0)} kcal | "
            f"P {float(dish.get('protein_g') or 0):.0f} g | "
            f"C {float(dish.get('carbs_g') or 0):.0f} g | "
            f"G {float(dish.get('fat_g') or 0):.0f} g"
        )
    return "\n".join(lines)


def meal_plan_details(plan: list[dict[str, Any]]) -> str:
    """El detalle de cada plato (para quien lo quiera ver)."""
    if not plan:
        return ""
    lines = []
    for dish in plan:
        items = ", ".join(
            f"{item['name']} {int(item['grams'])} g" for item in dish.get("items", [])
        )
        lines.append(f"<i>{esc(items)}</i>")
    return "\n".join(lines)


def weekly_message(summary: dict[str, Any]) -> str:
    """Mensaje completo de la semana: presupuesto, margen y dia a dia."""
    target = summary.get("target")
    lines = ["<b>🗓️ Tu semana</b>"]

    if not target:
        lines.append("<i>Configura tu perfil con /perfil para ver tus objetivos.</i>")
        return "\n".join(lines)

    bank = summary.get("bank") or {}
    maintenance = int(bank.get("maintenance") or target.maintenance_kcal)
    consumed = int(bank.get("consumed") or 0)
    saved = int(bank.get("saved") or 0)
    goal = int(bank.get("goal") or 0)
    pending = int(bank.get("pending") or 0)
    days_left = int(bank.get("days_left") or 0)
    daily_target = bank.get("daily_target")

    lines.append(
        f"Mantenimiento: <b>{maintenance} kcal/día</b> "
        f"(BMR {target.bmr} · TDEE {target.tdee})"
    )
    if goal > 0:
        lines.append(
            f"Objetivo de la semana: ahorrar <b>{goal} kcal</b>"
            + (
                f" (~{abs(target.weekly_rate_kg):.2f} kg)"
                if target.weekly_rate_kg
                else ""
            )
        )
    else:
        lines.append("Semana de mantenimiento: no hay deficit que repartir.")

    lines.append(
        f"Comido desde el lunes: <b>{consumed} kcal</b> "
        f"en {summary.get('meals', 0)} comidas "
        f"({summary.get('logged_days', 0)} días con registro)"
    )
    if saved >= 0:
        lines.append(f"Ahorrado: <b>{saved} kcal</b>")
    else:
        lines.append(
            f"Vas <b>{-saved} kcal</b> por encima de tu mantenimiento esta semana"
        )
    lines.append(
        f"Te faltan <b>{pending} kcal</b> para el objetivo de la semana"
        if pending >= 0
        else f"El banco va <b>{-pending} kcal</b> por encima del objetivo"
    )

    if daily_target and days_left:
        lines.append(
            f"Para los {days_left} días que quedan: <b>{daily_target} kcal/día</b>"
            + (
                f" (suelo de seguridad: {bank.get('floor')} kcal)"
                if bank.get("floor")
                else ""
            )
        )

    for target_note in target.notes:
        lines.append(f"<i>⚠️ {esc(target_note)}</i>")

    note = bank.get("note")
    if note:
        lines.append(f"<i>⚠️ {esc(note)}</i>")

    by_day = summary.get("by_day") or {}
    week_start = summary.get("week")
    elapsed = int(summary.get("days_elapsed") or 0)
    if by_day and week_start:
        lines.append("\n<b>Dia a dia</b>")
        start = parse_day(week_start)
        for index in range(7):
            key = (start + timedelta(days=index)).strftime("%Y-%m-%d")
            name = WEEKDAY_NAMES[index]
            bucket = by_day.get(key)
            if bucket:
                lines.append(
                    f"  {name}: <b>{bucket['calories']} kcal</b> "
                    f"({bucket['meals']} comidas)"
                )
            elif index < elapsed:
                lines.append(f"  {name}: <i>sin registro</i>")
            else:
                lines.append(f"  {name}: <i>pendiente</i>")

    lines.append(
        "\n<i>Tu mantenimiento es la referencia: lo que comes por debajo se "
        "ahorra en el deficit de la semana y lo que te pasas se gasta de ahi. "
        "Un dia fuera de la raya no rompe la semana. Los dias sin registro no "
        "cuentan: sin datos no se inventa nada.</i>"
    )
    return "\n".join(lines)


def macro_line(target: EnergyTarget | None) -> str:
    if not target:
        return ""
    return (
        f"🥩 {target.macro.protein_g:.0f} g  ·  🍚 {target.macro.carbs_g:.0f} g  ·  "
        f"🥑 {target.macro.fat_g:.0f} g"
    )


def meal_line(meal: dict[str, Any] | Any, tz: tzinfo = UTC) -> str:
    """Una linea resumida de una comida, en hora local.

    La franja es la que el usuario ha elegido; si no la ha corregido, se
    deduce de la hora.
    """
    hour = format_local_time(meal["eaten_at"], tz)
    slot = None
    try:
        slot = meal["meal_slot"]
    except (KeyError, TypeError, IndexError):
        slot = None
    if not slot:
        slot = (
            meal_slot(None, tz)
            if not meal["eaten_at"]
            else meal_slot(_as_dt(meal["eaten_at"]), tz)
        )
    emoji = SLOT_EMOJI.get(slot, "🍽")
    label = SLOT_LABELS.get(slot, "")
    dish = esc(meal["dish"] or "Plato")
    kcal = int(meal["calories"] or 0)
    edited = " ✏️" if int(meal["edited"] or 0) else ""
    suffix = f" · {label}" if label else ""
    return f"{emoji} <code>{hour}</code>{suffix} {dish} — <b>{kcal} kcal</b>{edited}"


def _as_dt(value: Any):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def confidence_label(confidence: float | None) -> str:
    if confidence is None:
        return "?"
    if confidence >= 0.8:
        return "alta"
    if confidence >= 0.55:
        return "media"
    return "baja"


def analysis_message(analysis: Any) -> str:
    """Mensaje con el resultado del analisis de una foto."""
    parts: list[str] = []
    parts.append(f"🍽 <b>{esc(analysis.dish)}</b>")
    parts.append(f"🔥 <b>{analysis.calories} kcal</b>")
    parts.append(
        f"🥩 {analysis.protein_g:.1f} g  ·  🍚 {analysis.carbs_g:.1f} g  ·  "
        f"🥑 {analysis.fat_g:.1f} g"
    )
    if analysis.mass_g:
        parts.append(f"⚖️ Peso estimado: {analysis.mass_g:.0f} g")

    if analysis.ingredients:
        lines = []
        for ing in analysis.ingredients[:10]:
            grams = f"{ing.grams:.0f} g" if ing.grams else ""
            kcal = f"{ing.kcal:.0f} kcal" if ing.kcal else ""
            parts_txt = " · ".join(p for p in (grams, kcal) if p)
            lines.append(f"  - {esc(ing.name)} <i>({parts_txt})</i>")
        parts.append("\n<b>Ingredientes detectados</b>")
        parts.extend(lines)

    parts.append(
        f"\n<i>Confianza {confidence_label(analysis.confidence)} ({analysis.confidence:.0%})</i>"
    )
    for warning in analysis.warnings:
        parts.append(f"⚠️ {esc(warning)}")
    parts.append(
        "\n<i>¿No es exacto? Pulsa <b>✏️ Corregir</b> y ajusta las calorías: "
        "tu historial manda sobre la estimación.</i>"
    )
    return "\n".join(parts)


def today_message(
    summary: dict[str, Any],
    tz: tzinfo = UTC,
    quota: dict[str, int] | None = None,
    week: dict[str, Any] | None = None,
    accumulated: dict[str, Any] | None = None,
) -> str:
    target = summary.get("target")
    kcal = summary.get("calories", 0)
    lines: list[str] = ["<b>📊 Tu día</b>"]

    if target:
        goal_kcal = target.daily_kcal
        lines.append(f"{progress_bar(kcal, goal_kcal)}  {kcal}/{goal_kcal} kcal")
        remaining = summary.get("remaining")
        if remaining is not None:
            if remaining >= 0:
                lines.append(f"Te quedan <b>{remaining} kcal</b>.")
            else:
                lines.append(
                    f"Vas <b>{-remaining} kcal</b> por encima del objetivo de hoy. "
                    "La semana las absorbe: mañana sigue igual."
                )
        lines.append(
            f"🥩 {summary['protein_g']:.0f}/{target.macro.protein_g:.0f} g  ·  "
            f"🍚 {summary['carbs_g']:.0f}/{target.macro.carbs_g:.0f} g  ·  "
            f"🥑 {summary['fat_g']:.0f}/{target.macro.fat_g:.0f} g"
        )
        lines.append(target_line(target))
        if target.maintenance_kcal and summary.get("meals"):
            # Sin registro no se sabe que se ha comido: no se inventa.
            saved_today = target.maintenance_kcal - kcal
            if saved_today >= 0:
                lines.append(
                    f"💰 Hoy has ahorrado <b>{saved_today} kcal</b> "
                    "respecto a tu mantenimiento."
                )
            else:
                lines.append(
                    f"💴 Hoy vas <b>{-saved_today} kcal</b> por encima de "
                    "tu mantenimiento: lo paga el banco de la semana."
                )
        if week:
            weekly = week_line(week)
            if weekly:
                lines.append(weekly)
        if accumulated:
            line = accumulated_line(accumulated)
            if line:
                lines.append(line)
    else:
        lines.append("<i>Configura tu perfil con /perfil para ver tus objetivos.</i>")

    meals = summary.get("meals") or []
    if meals:
        lines.append(f"\n<b>Comidas registradas ({len(meals)})</b>")
        lines.extend(meal_line(m, tz) for m in meals)
    else:
        lines.append("\n<i>Todavía no has registrado nada hoy. Envíame una foto de tu plato.</i>")

    if quota:
        left = quota.get("photos_left")
        if left is not None:
            if left > 0:
                lines.append(f"\n<i>📸 Te quedan {left} análisis hoy.</i>")
            else:
                lines.append(
                    "\n<i>📸 Ya has usado los análisis de hoy; mañana se renuevan.</i>"
                )
    return "\n".join(lines)


def progress_message(progress: dict[str, Any]) -> str:
    target = progress.get("target")
    lines = ["<b>🎯 Tu progreso</b>"]

    current = progress.get("current")
    goal_weight = progress.get("goal_weight")
    lines.append(f"Peso actual: <b>{fmt_kg(current)}</b>")
    if goal_weight:
        lines.append(f"Peso objetivo: <b>{fmt_kg(goal_weight)}</b>")

    rate = progress.get("weekly_rate") or 0.0
    trend = progress.get("trend") or {}
    if trend.get("has_data"):
        direction = "bajando" if rate < 0 else "subiendo" if rate > 0 else "estable"
        lines.append(f"Tendencia 14 días: <b>{rate:+.2f} kg/semana</b> ({direction})")
    else:
        lines.append("<i>Registra tu peso con /peso para ver la tendencia.</i>")

    eta = progress.get("eta_weeks")
    if eta is not None:
        lines.append(f"ETA al objetivo: <b>~{eta:.0f} semanas</b>")
        projected = progress.get("projected_4w")
        if projected:
            lines.append(f"Proyección a 4 semanas: <b>{fmt_kg(projected)}</b>")

    if target:
        lines.append(target_line(target))
        lines.append(macro_line(target))

    week = progress.get("week")
    if week:
        weekly = week_line(week)
        if weekly:
            lines.append(weekly)

    if progress.get("plateau"):
        lines.append(f"\n🟡 {esc(progress.get('advice', ''))}")
    elif progress.get("advice"):
        lines.append(f"\n💡 {esc(progress['advice'])}")

    lines.append(f"\n{SCALE_NOTE}")
    return "\n".join(lines)


def history_message(stats: dict[str, Any], tz: tzinfo = UTC, limit: int = 15) -> str:
    today = stats.get("today")
    lines = [f"<b>📋 Historial (últimos {stats['days']} días)</b>"]
    lines.append(
        f"Comidas: {stats['meals']}  ·  Total: {stats['total_calories']} kcal  ·  "
        f"Media por día con registro: {stats['avg_calories']} kcal"
    )
    if stats.get("avg_per_day") is not None:
        lines.append(f"Media del periodo: {stats['avg_per_day']} kcal/día")
    by_day = stats.get("by_day") or {}
    if by_day:
        lines.append("")
        for day in sorted(by_day.keys(), reverse=True)[:limit]:
            bucket = by_day[day]
            label = friendly_day(day, today) if today else day
            lines.append(
                f"• <code>{label}</code> — {bucket['calories']} kcal "
                f"({bucket['meals']} comidas)"
            )
    else:
        lines.append("\n<i>Todavía no hay comidas registradas.</i>")
    return "\n".join(lines)


def privacy_message(report: dict[str, int], photo_bytes: int = 0) -> str:
    lines = [
        "<b>🔐 Tus datos</b>",
        "",
        "Esto es <b>lo único</b> que guardo de ti:",
        f"• 🍽 {report.get('meals', 0)} comidas (plato, calorías y macros)",
        f"• ⚖️ {report.get('weights', 0)} registros de peso",
        f"• 🎯 {report.get('targets', 0)} objetivos diarios calculados",
        "",
        "<b>Lo que NO guardo</b>:",
        "• 🚫 Las fotos se analizan en memoria y se borran al terminar.",
        "• 🚫 No veo ni guardo tus conversaciones de Telegram.",
        "• 🚫 Tus datos no se comparten con nadie.",
    ]
    if photo_bytes:
        lines.append(f"\n<i>Copias locales de fotos en tu disco: {photo_bytes} bytes.</i>")
    lines.append(
        "\n<i>Pulsa <b>🗑 Borrar todos mis datos</b> para eliminarlo todo y "
        "revocar tu acceso.</i>"
    )
    return "\n".join(lines)


def escape_all(items: Sequence[str]) -> str:
    return ", ".join(esc(i) for i in items)
