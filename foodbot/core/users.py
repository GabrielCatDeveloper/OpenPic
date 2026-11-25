"""Servicio de usuarios: perfil, objetivos diarios, resumen y progreso."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from foodbot.core.db import Database, iso
from foodbot.core.nutrition import (
    CALIBRATION_MAX_DAYS,
    CALIBRATION_MIN_DAYS,
    CALIBRATION_MIN_WEIGHT_SPAN,
    KCAL_PER_KG_FAT,
    EnergyTarget,
    activity_label,
    build_target,
    compute_bmr,
    eta_weeks,
    parse_iso,
    projected_weight,
    savings_bank,
    weight_trend,
)
from foodbot.core.timeutil import (
    day_bounds as _day_bounds,
    day_key,
    local_now,
    resolve_tz,
    week_bounds as _week_bounds,
    week_progress as _week_progress,
)

SEXES = {"male": "Hombre", "female": "Mujer", "other": "Otro"}


class UserService:
    def __init__(self, db: Database, timezone_name: str | None = None):
        self.db = db
        self.tz = resolve_tz(timezone_name)

    # -- perfil -----------------------------------------------------------
    def get(self, user_id: int) -> dict[str, Any] | None:
        row = self.db.get_user(user_id)
        return dict(row) if row else None

    def profile(self, user_id: int) -> dict[str, Any]:
        return self.get(user_id) or {}

    def is_registered(self, user_id: int) -> bool:
        row = self.db.get_user(user_id)
        return bool(row and row["authorized_at"])

    def is_onboarded(self, user_id: int) -> bool:
        row = self.db.get_user(user_id)
        return bool(row and int(row["onboarded"] or 0))

    def plan(self, user_id: int) -> str:
        row = self.db.get_user(user_id)
        return str(row["plan"]) if row and row["plan"] else "free"

    def update_profile(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        self.db.touch_user(user_id, **fields)

    # -- autorizacion ------------------------------------------------------
    def authorize(
        self,
        user_id: int,
        plan: str = "free",
        username: str | None = None,
        first_name: str | None = None,
        token_id: int | None = None,
    ) -> None:
        """Da de alta a un usuario sin pasar por un token.

        Lo usa el panel de acceso cuando el administrador decide activar a
        alguien directamente (por id de Telegram o por apodo).
        """
        self.db.upsert_user(
            user_id,
            username=username,
            first_name=first_name,
            plan=plan,
            token_id=token_id,
            authorized_at=iso(),
            last_seen=iso(),
        )

    def deactivate(self, user_id: int) -> bool:
        """Quita el acceso al bot a un usuario (deja su historial intacto)."""
        row = self.db.get_user(user_id)
        if not row or not row["authorized_at"]:
            return False
        self.db.touch_user(user_id, authorized_at=None)
        return True

    def aliases(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.list_aliases()]

    def alias_user_id(self, nickname: str) -> int | None:
        row = self.db.get_alias(nickname)
        return int(row["user_id"]) if row else None

    def set_alias(self, nickname: str, user_id: int, note: str | None = None) -> None:
        self.db.set_alias(nickname, user_id, note)

    def profile_complete(self, profile: dict[str, Any]) -> list[str]:
        missing = []
        if not profile.get("sex"):
            missing.append("sexo")
        if not profile.get("age"):
            missing.append("edad")
        if not profile.get("height_cm"):
            missing.append("altura")
        if not profile.get("weight_kg"):
            missing.append("peso")
        if not profile.get("activity"):
            missing.append("actividad")
        if not profile.get("goal"):
            missing.append("objetivo")
        return missing

    def describe_profile(self, profile: dict[str, Any]) -> str:
        parts = []
        if profile.get("sex"):
            parts.append(SEXES.get(str(profile["sex"]).lower(), profile["sex"]))
        if profile.get("age"):
            parts.append(f"{int(profile['age'])} anos")
        if profile.get("height_cm"):
            parts.append(f"{float(profile['height_cm']):.0f} cm")
        if profile.get("weight_kg"):
            parts.append(f"{float(profile['weight_kg']):.1f} kg")
        if profile.get("activity"):
            parts.append(activity_label(str(profile["activity"])))
        return ", ".join(parts) if parts else "perfil incompleto"

    # -- objetivos --------------------------------------------------------
    def daily_target(self, user_id: int, day: str | None = None) -> EnergyTarget | None:
        """Calcula (y cachea) el objetivo energetico del dia."""
        day = day or self.today_key()
        profile = self.profile(user_id)
        if not profile:
            return None
        weight = profile.get("weight_kg")
        if not weight:
            latest = self.db.latest_weight(user_id)
            if not latest:
                return None
            weight = latest

        target = build_target(
            sex=str(profile.get("sex") or "other"),
            age=int(profile.get("age") or 30),
            height_cm=float(profile.get("height_cm") or 170),
            weight_kg=float(weight),
            activity=str(profile.get("activity") or "sedentary"),
            goal=str(profile.get("goal") or "maintain"),
            weekly_rate_kg=profile.get("weekly_rate_kg"),
            daily_kcal_override=profile.get("daily_kcal_override"),
            protein_g_per_kg=profile.get("protein_g_per_kg"),
            body_fat_pct=profile.get("body_fat_pct"),
            training_days=profile.get("training_days"),
            daily_steps=profile.get("daily_steps"),
            job_activity=profile.get("job_activity"),
            maintenance_override=profile.get("maintenance_kcal"),
            calibrated_maintenance=self.calibrated_maintenance(user_id),
        )
        self.db.save_target(
            user_id,
            day,
            kcal_target=target.daily_kcal,
            protein_target=target.macro.protein_g,
            carbs_target=target.macro.carbs_g,
            fat_target=target.macro.fat_g,
            bmr=target.bmr,
            tdee=target.maintenance_kcal,
            deficit_kcal=target.deficit_kcal,
        )
        return target

    # -- calibracion con los datos reales ---------------------------------
    def calibrated_maintenance(self, user_id: int) -> int | None:
        """Mantenimiento real deducido de lo que ha comido y como pesa.

        Las formulas se equivocan en un +-10-20%. El unico dato que no miente
        es la tendencia de peso: si promedia X kcal/dia y baja 0.3 kg/semana,
        su mantenimiento son X + 330 kcal. Necesita al menos dos semanas de
        registros y dos pesos separados por 10 dias.
        """
        since = (local_now(self.tz) - timedelta(days=CALIBRATION_MAX_DAYS)).strftime(
            "%Y-%m-%d"
        )
        # Ventana completa: desde 'since' hasta el final de hoy.
        start, _, _ = self.day_bounds(since)
        _, end, _ = self.day_bounds(None)
        meals = self.db.meals_between(user_id, start, end)
        if not meals:
            return None

        by_day: dict[str, int] = {}
        for meal in meals:
            key = day_key(parse_iso(meal["eaten_at"]), self.tz)
            by_day[key] = by_day.get(key, 0) + int(meal["calories"] or 0)
        days = len(by_day)
        if days < CALIBRATION_MIN_DAYS:
            return None
        avg_intake = sum(by_day.values()) / days

        rows = self.db.last_weights(user_id, CALIBRATION_MAX_DAYS)
        if len(rows) < 2:
            return None
        points = sorted(
            (parse_iso(row["logged_at"]), float(row["weight_kg"])) for row in rows
        )
        span_days = (points[-1][0] - points[0][0]).days
        if span_days < CALIBRATION_MIN_WEIGHT_SPAN:
            return None

        trend = weight_trend(points, days=CALIBRATION_MAX_DAYS)
        kg_per_day = float(trend.get("weekly_rate") or 0.0) / 7.0
        # Mantenimiento = ingesta media - energia que se esta gastando en el
        # peso (negativo si pierde peso, se suma).
        maintenance = avg_intake - kg_per_day * KCAL_PER_KG_FAT
        if maintenance <= 0:
            return None

        # Solo se acepta si no se va de las ramas (un error de registro
        # tipico son 500 kcal de media).
        profile = self.profile(user_id)
        weight = profile.get("weight_kg")
        if weight:
            bmr = compute_bmr(
                str(profile.get("sex") or "other"),
                float(weight),
                float(profile.get("height_cm") or 170),
                int(profile.get("age") or 30),
            )
            plausible = bmr * 1.1 <= maintenance <= bmr * 2.2
            if not plausible:
                return None
        return int(round(maintenance))

    # -- resumenes --------------------------------------------------------
    def day_bounds(self, day: str | None = None) -> tuple[str, str, str]:
        """(fecha ISO inicio, fecha ISO fin, clave dia) en la zona del usuario."""
        return _day_bounds(day, self.tz)

    def today_key(self) -> str:
        return local_now(self.tz).strftime("%Y-%m-%d")

    def today_summary(self, user_id: int) -> dict[str, Any]:
        start, end, key = self.day_bounds()
        meals = self.db.meals_between(user_id, start, end)
        kcal = sum(int(m["calories"] or 0) for m in meals)
        protein = sum(float(m["protein_g"] or 0) for m in meals)
        carbs = sum(float(m["carbs_g"] or 0) for m in meals)
        fat = sum(float(m["fat_g"] or 0) for m in meals)
        target = self.daily_target(user_id, key)
        return {
            "day": key,
            "meals": [dict(m) for m in meals],
            "calories": kcal,
            "protein_g": round(protein, 1),
            "carbs_g": round(carbs, 1),
            "fat_g": round(fat, 1),
            "target": target,
            "remaining": (target.daily_kcal - kcal) if target else None,
        }

    def week_summary(self, user_id: int) -> dict[str, Any]:
        """Resumen de la semana: mantenimiento, banco de ahorro y margen.

        El deficit es semanal: cada dia que comes por debajo de tu
        mantenimiento ahorras kcal que se acumulan en el banco, y cada dia
        que te pasas lo gastas de ahi. Un dia de mas no rompe la semana.
        """
        start, end, week_key = _week_bounds(None, self.tz)
        meals = self.db.meals_between(user_id, start, end)
        kcal = sum(int(m["calories"] or 0) for m in meals)

        by_day: dict[str, dict[str, Any]] = {}
        for meal in meals:
            key = day_key(parse_iso(meal["eaten_at"]), self.tz)
            bucket = by_day.setdefault(key, {"calories": 0, "meals": 0})
            bucket["calories"] += int(meal["calories"] or 0)
            bucket["meals"] += 1

        target = self.daily_target(user_id)
        _, elapsed, days_left = _week_progress(None, self.tz)
        bank = savings_bank(target, kcal, len(by_day), days_left)

        return {
            "week": week_key,
            "days_elapsed": elapsed,
            "days_left": days_left,
            "calories": kcal,
            "meals": len(meals),
            "logged_days": len(by_day),
            "by_day": by_day,
            "target": target,
            "bank": bank,
        }

    def savings_history(self, user_id: int, days: int = 30) -> dict[str, Any]:
        """Historial del banco: cuanto se ha ahorrado por dia.

        El mantenimiento se recalcula con el peso actual, asi que es una
        aproximacion: sirve para ver la tendencia, no para hacer contabilidad.
        """
        days = max(1, min(365, int(days)))
        target = self.daily_target(user_id)
        maintenance = int(target.maintenance_kcal) if target else 0
        now = local_now(self.tz)
        start_day = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        # La ventana va desde el primer dia hasta el final de hoy.
        start, _, _ = self.day_bounds(start_day)
        _, end, _ = self.day_bounds(None)
        meals = self.db.meals_between(user_id, start, end)

        by_day: dict[str, int] = {}
        for meal in meals:
            key = day_key(parse_iso(meal["eaten_at"]), self.tz)
            by_day[key] = by_day.get(key, 0) + int(meal["calories"] or 0)

        ledger = [
            {"day": key, "calories": value, "saved": maintenance - value}
            for key, value in sorted(by_day.items())
        ]
        total_saved = sum(item["saved"] for item in ledger)
        return {
            "days": days,
            "maintenance": maintenance,
            "ledger": ledger,
            "total_saved": total_saved,
            "logged_days": len(ledger),
            "kg_equivalent": round(total_saved / KCAL_PER_KG_FAT, 2),
        }

    def history_stats(self, user_id: int, days: int = 7) -> dict[str, Any]:
        days = max(1, min(365, int(days)))
        now = local_now(self.tz)
        start_day = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        start, _, _ = _day_bounds(start_day, self.tz)
        _, end, today = _day_bounds(None, self.tz)
        meals = self.db.meals_between(user_id, start, end)
        by_day: dict[str, dict[str, Any]] = {}
        for meal in meals:
            key = day_key(parse_iso(meal["eaten_at"]), self.tz)
            bucket = by_day.setdefault(key, {"calories": 0, "meals": 0, "protein_g": 0.0})
            bucket["calories"] += int(meal["calories"] or 0)
            bucket["protein_g"] += float(meal["protein_g"] or 0)
            bucket["meals"] += 1
        total_kcal = sum(b["calories"] for b in by_day.values())
        return {
            "days": days,
            "today": today,
            "meals": len(meals),
            "total_calories": total_kcal,
            "avg_calories": int(total_kcal / len(by_day)) if by_day else 0,
            "avg_per_day": int(total_kcal / days),
            "active_days": len(by_day),
            "by_day": by_day,
        }

    # -- peso y progreso --------------------------------------------------
    def weight_progress(self, user_id: int) -> dict[str, Any]:
        rows = self.db.last_weights(user_id, 30)
        points = [(parse_iso(r["logged_at"]), float(r["weight_kg"])) for r in rows]
        points.sort(key=lambda p: p[0])
        profile = self.profile(user_id)
        target = self.daily_target(user_id)

        trend = weight_trend(points, days=14)
        current = trend.get("current")
        if current is None:
            current = profile.get("weight_kg")
        goal_weight = profile.get("target_weight_kg")
        weekly_rate = float(trend.get("weekly_rate") or 0.0)
        goal = str(profile.get("goal") or "maintain")

        expected = 0.0
        if goal == "lose":
            expected = -0.5
        elif goal == "gain":
            expected = 0.25

        plateau = False
        advice = ""
        if trend.get("has_data") and not trend.get("insufficient"):
            if goal == "lose" and weekly_rate > -0.1:
                plateau = True
                advice = (
                    "Tu peso no se ha movido en los ultimos dias. Revisa las raciones "
                    "de aceites y las porciones: suelen ser el error mas comun."
                )
            elif goal == "lose" and weekly_rate < -1.2:
                advice = (
                    "Estas bajando demasiado rapido (mas de 1 kg/semana). "
                    "Sube un poco las calorias para no perder muscle."
                )

        eta = None
        projected = None
        if current is not None and goal_weight:
            if goal == "lose" and weekly_rate < -0.05:
                eta = eta_weeks(float(current), float(goal_weight), abs(weekly_rate))
                projected = projected_weight(float(current), abs(weekly_rate), 4)
            elif goal == "gain" and weekly_rate > 0.05:
                eta = eta_weeks(float(goal_weight), float(current), weekly_rate)
                projected = projected_weight(float(current), weekly_rate, 4)

        return {
            "current": current,
            "goal_weight": goal_weight,
            "weekly_rate": weekly_rate,
            "trend": trend,
            "eta_weeks": eta,
            "projected_4w": projected,
            "plateau": plateau,
            "advice": advice,
            "expected_rate": expected,
            "target": target,
            "week": self.week_summary(user_id),
        }

    def streak(self, user_id: int) -> int:
        """Dias consecutivos con al menos un registro de comida.

        Los dias se cuentan en la zona horaria del usuario. Si el ultimo
        registro es de hace dos o mas dias, la racha esta rota y vale 0 (aunque
        el usuario no haya registrado nada hoy, la racha de ayer sigue viva).
        """
        rows = self.db.query(
            "SELECT eaten_at FROM meals WHERE user_id = ? ORDER BY eaten_at DESC LIMIT 400",
            (user_id,),
        )
        if not rows:
            return 0
        days: list[str] = []
        seen: set[str] = set()
        for row in rows:
            key = day_key(parse_iso(row["eaten_at"]), self.tz)
            if key not in seen:
                seen.add(key)
                days.append(key)
        if not days:
            return 0

        today = local_now(self.tz).date()
        try:
            first = datetime.fromisoformat(days[0]).date()
        except ValueError:
            return 0
        if (today - first).days > 1:
            return 0

        streak = 1
        current = first
        for key in days[1:]:
            try:
                previous = datetime.fromisoformat(key).date()
            except ValueError:
                break
            if (current - previous).days == 1:
                streak += 1
                current = previous
            else:
                break
        return streak
