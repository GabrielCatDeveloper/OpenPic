"""Bot publico de nutrition.

Flujo de acceso:
1. El usuario escribe /start.
2. Si no esta autorizado, se le pide un token (codigo emitido por el bot privado).
3. Con el token valido, se activa su cuenta y puede usar todas las funciones.

Funciones: analisis de fotos, historial, peso, objetivo, plan de comidas y consejo.

Reglas de privacidad que aplica este modulo:
- Las fotos se analizan EN MEMORIA y no se guardan en disco (salvo que se active
  STORE_PHOTOS, y aun asi se purgan solas).
- Cada usuario solo puede ver y tocar sus propios datos: todas las consultas
  van filtradas por user_id y el borrado tambien.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import timedelta
from typing import Any

from telegram import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from foodbot.config import Config
from foodbot.core.db import Database, iso, utcnow
from foodbot.core.foodr1 import FoodR1Client, InferenceError, sniff_mime
from foodbot.core.limits import LimitService
from foodbot.core.nutrition import ACTIVITY_LEVELS, activity_label, estimate_body_fat_pct
from foodbot.core.privacy import PhotoStore, erase_user_data, storage_report
from foodbot.core.text import (
    analysis_message,
    esc,
    goal_label,
    history_message,
    privacy_message,
    progress_bar,
    progress_message,
    target_line,
    today_message,
    week_line,
    weekly_message,
)
from foodbot.core.timeutil import format_local_date
from foodbot.core.tokens import TokenService
from foodbot.core.users import SEXES, UserService

log = logging.getLogger("foodbot.public")

# Estados del asistente de configuracion del perfil.
(
    NAME,
    SEX,
    AGE,
    HEIGHT,
    WEIGHT,
    BODYFAT,
    TRAINING,
    STEPS,
    JOB,
    ACTIVITY,
    GOAL,
    TARGET_WEIGHT,
) = range(12)

JOB_LABELS = {
    "sedentary": "De pie, poco movimiento",
    "light": "Trabajo a pie o activo",
    "physical": "Trabajo fisico pesado",
}

# Las preguntas del asistente, en el orden en el que se hacen. El "kind" dice
# como se responde: texto libre o boton.
PROFILE_QUESTIONS: dict[str, tuple[str, str]] = {
    "name": ("<b>\u00bfC\u00f3mo te llamas?</b>\n<i>Escribe tu nombre o el apodo que prefieras.</i>", "text"),
    "sex": ("<b>\u00bfCu\u00e1l es tu sexo?</b>\n<i>Lo necesito para calcular tu metabolismo.</i>", "sex"),
    "age": ("<b>\u00bfCu\u00e1l es tu edad?</b>\n<i>Solo el n\u00famero.</i>", "text"),
    "height": ("<b>\u00bfCu\u00e1l es tu altura en cm?</b>\n<i>Ejemplo: <code>175</code></i>", "text"),
    "weight": ("<b>\u00bfCu\u00e1l es tu peso actual en kg?</b>\n<i>Ejemplo: <code>78.4</code></i>", "text"),
    "bodyfat": (
        "<b>\u00bfCu\u00e1l es tu porcentaje de grasa?</b>\n"
        "<i>Con eso (Katch-McArdle) el mantenimiento se calcula mucho mejor. "
        "Si no lo sabes, usa el estimado: es una aproximaci\u00f3n.</i>",
        "bodyfat",
    ),
    "training": (
        "<b>\u00bfCu\u00e1ntas veces por semana entrenas?</b>\n"
        "<i>Las sesiones de fuerza gastan m\u00e1s que caminar.</i>",
        "training",
    ),
    "steps": (
        "<b>\u00bfCu\u00e1ntos pasos das al d\u00eda de media?</b>\n"
        "<i>El movimiento de fondo (NEAT) es la variable que m\u00e1s se "
        "olvida y puede mover 2.000 kcal al d\u00eda entre personas iguales.</i>",
        "steps",
    ),
    "job": (
        "<b>\u00bfQu\u00e9 tipo de trabajo tienes?</b>\n"
        "<i>Un trabajo de pie no es lo mismo que uno sentado.</i>",
        "job",
    ),
    "activity": (
        "<b>\u00bfCu\u00e1l es tu actividad f\u00edsica en general?</b>\n"
        "<i>El trabajo, los pasos y el ejercicio juntos.</i>",
        "activity",
    ),
    "goal": ("<b>\u00bfCu\u00e1l es tu objetivo?</b>", "goal"),
    "target": (
        "<b>\u00bfCu\u00e1l es tu peso objetivo?</b>\n"
        "<i>Si no lo sabes, escribe <code>0</code> y lo calculamos despu\u00e9s.</i>",
        "text",
    ),
}

# Los datos que son opcionales se pueden saltar: el bot no bloquea al
# usuario por no saber su % de grasa o sus pasos.
PROFILE_STEPS: dict[str, int] = {
    "name": NAME,
    "sex": SEX,
    "age": AGE,
    "height": HEIGHT,
    "weight": WEIGHT,
    "bodyfat": BODYFAT,
    "training": TRAINING,
    "steps": STEPS,
    "job": JOB,
    "activity": ACTIVITY,
    "goal": GOAL,
    "target": TARGET_WEIGHT,
}

# Ventanas del historial ofrecidas con un boton.
HISTORY_RANGES = (7, 30, 90)


def main_menu() -> InlineKeyboardMarkup:
    """Menu principal: todo se hace con botones, sin escribir comandos."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📸 Analizar plato", callback_data="menu:foto"),
                InlineKeyboardButton("📊 Mi día", callback_data="menu:resumen"),
            ],
            [
                InlineKeyboardButton("🗓 Mi semana", callback_data="menu:semana"),
                InlineKeyboardButton("🧭 Plan", callback_data="menu:plan"),
            ],
            [
                InlineKeyboardButton("💡 Consejo", callback_data="menu:consejo"),
                InlineKeyboardButton("🗂 Historial", callback_data="menu:historial"),
            ],
            [
                InlineKeyboardButton("⚖️ Mi peso", callback_data="menu:peso"),
                InlineKeyboardButton("🎯 Mi meta", callback_data="menu:meta"),
            ],
            [
                InlineKeyboardButton("🍽 Registrar a mano", callback_data="menu:anotar"),
                InlineKeyboardButton("⚙️ Mi perfil", callback_data="menu:perfil"),
            ],
            [
                InlineKeyboardButton("🔐 Mis datos", callback_data="menu:privacidad"),
                InlineKeyboardButton("💾 Exportar CSV", callback_data="menu:exportar"),
            ],
        ]
    )


def skip_keyboard(extra: InlineKeyboardButton | None = None) -> InlineKeyboardMarkup:
    """Teclado con 'Saltar' (para los datos opcionales del perfil)."""
    row = [extra] if extra else []
    row.append(InlineKeyboardButton("⇩ Saltar", callback_data="perfil:skip"))
    return InlineKeyboardMarkup([row])


def skip_to(next_step: str) -> InlineKeyboardMarkup:
    """Boton de 'Saltar' que lleva al siguiente paso del asistente."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏭ Saltar", callback_data=f"perfil:skip:{next_step}")]]
    )


def bodyfat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "No lo sé", callback_data="perfil:bodyfat:skip"
                ),
                InlineKeyboardButton(
                    "Usar estimado", callback_data="perfil:bodyfat:estimate"
                ),
            ]
        ]
    )


def training_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{n}", callback_data=f"perfil:training:{n}")
                for n in (0, 1, 2, 3)
            ],
            [
                InlineKeyboardButton(f"{n}", callback_data=f"perfil:training:{n}")
                for n in (4, 5, 6)
            ],
            [InlineKeyboardButton("⇩ Saltar", callback_data="perfil:skip:steps")],
        ]
    )


def steps_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Menos de 5 000", callback_data="perfil:steps:4000"
                ),
                InlineKeyboardButton(
                    "5 000 - 8 000", callback_data="perfil:steps:6500"
                ),
            ],
            [
                InlineKeyboardButton(
                    "8 000 - 10 000", callback_data="perfil:steps:9000"
                ),
                InlineKeyboardButton(
                    "Más de 10 000", callback_data="perfil:steps:12000"
                ),
            ],
            [InlineKeyboardButton("⇩ Saltar", callback_data="perfil:skip:job")],
        ]
    )


def job_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "De pie, poco movimiento", callback_data="perfil:job:sedentary"
                )
            ],
            [
                InlineKeyboardButton(
                    "Trabajo a pie o activo", callback_data="perfil:job:light"
                )
            ],
            [
                InlineKeyboardButton(
                    "Trabajo fisico pesado", callback_data="perfil:job:physical"
                )
            ],
            [InlineKeyboardButton("⇩ Saltar", callback_data="perfil:skip:activity")],
        ]
    )


# Botones del teclado persistente (la "app" del bot: no hay comandos).
MENU_LABELS: dict[str, str] = {
    "📸 Analizar": "foto",
    "📊 Hoy": "resumen",
    "🗓 Semana": "semana",
    "⚖️ Peso": "peso",
    "⚙️ Perfil": "perfil",
}


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Teclado fijo bajo el campo de texto: el menu siempre a mano.

    Es la mejor UI que ofrece Telegram para un bot de este tipo: el usuario
    no tiene que recordar ni escribir un solo comando.
    """
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("📸 Analizar"),
                KeyboardButton("📊 Hoy"),
            ],
            [
                KeyboardButton("🗓 Semana"),
                KeyboardButton("⚖️ Peso"),
            ],
            [KeyboardButton("⚙️ Perfil")],
        ],
        resize_keyboard=True,
        input_field_placeholder="📸 Envía una foto de tu plato…",
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Hombre", callback_data="perfil:sex:male"),
                InlineKeyboardButton("Mujer", callback_data="perfil:sex:female"),
                InlineKeyboardButton("Otro", callback_data="perfil:sex:other"),
            ]
        ]
    )


def activity_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"perfil:act:{key}")]
        for key, (label, _) in ACTIVITY_LEVELS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Perder peso", callback_data="perfil:goal:lose"),
                InlineKeyboardButton("Mantener", callback_data="perfil:goal:maintain"),
                InlineKeyboardButton("Ganar masa", callback_data="perfil:goal:gain"),
            ]
        ]
    )


def history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{days} días", callback_data=f"historial:{days}")
                for days in HISTORY_RANGES
            ]
        ]
    )


def meal_actions_keyboard(meal_id: int, can_retry: bool = True) -> InlineKeyboardMarkup:
    """Botones sobre el resultado de un analisis: corregir, reanalizar, borrar."""
    row = [
        InlineKeyboardButton("✏️ Corregir", callback_data=f"corregir:{meal_id}"),
    ]
    if can_retry:
        row.append(InlineKeyboardButton("🔄 Reanalizar", callback_data=f"reintentar:{meal_id}"))
    return InlineKeyboardMarkup(
        [
            row,
            [
                InlineKeyboardButton("🗑 Borrar", callback_data=f"borrar:{meal_id}"),
                InlineKeyboardButton("📊 Ver mi día", callback_data="menu:resumen"),
            ],
        ]
    )


class PublicBot:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.users = UserService(db, config.timezone)
        self.tokens = TokenService(db, pepper=config.llm_api_key)
        self.limits = LimitService(db, config, config.timezone)
        self.photos = PhotoStore(
            config.photos_dir,
            enabled=config.store_photos,
            ttl_hours=config.photo_ttl_hours,
        )
        self.llm = FoodR1Client(config)
        self.app: Application = (
            ApplicationBuilder()
            .token(config.public_bot_token)
            .post_init(self._post_init)
            .build()
        )
        # Red de seguridad: si un boton edita un mensaje con el mismo texto,
        # Telegram lanza BadRequest y el usuario ve un error.
        self.app.add_error_handler(self._on_error)
        self._register_handlers()

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Errores no controlados.

        Telegram lanza BadRequest ("message is not modified") cuando se edita
        un mensaje con el mismo texto. Es inocuo: se ignora. El resto se
        registra.
        """
        error = context.error
        if isinstance(error, BadRequest) and "not modified" in str(error).lower():
            return
        log.error("Error no controlado: %s", error, exc_info=error)

    # -- setup ------------------------------------------------------------
    async def _post_init(self, app: Application) -> None:
        await app.bot.set_my_commands(
            [
                {"command": "start", "description": "Empezar / activar acceso"},
                {"command": "foto", "description": "Analizar una foto de comida"},
                {"command": "hoy", "description": "Resumen del dia"},
                {"command": "semana", "description": "Presupuesto semanal y margen"},
                {"command": "historial", "description": "Historial de comidas"},
                {"command": "peso", "description": "Registrar peso"},
                {"command": "meta", "description": "Ver mi objetivo"},
                {"command": "perfil", "description": "Configurar perfil"},
                {"command": "plan", "description": "Plan de comidas"},
                {"command": "consejo", "description": "Consejo del dia"},
                {"command": "anotar", "description": "Anotar comida manualmente"},
                {"command": "corregir", "description": "Corregir las calorias de un plato"},
                {"command": "borrar", "description": "Borrar la ultima comida"},
                {"command": "privacidad", "description": "Mis datos y privacidad"},
                {"command": "exportar", "description": "Descarga el historial (CSV)"},
                {"command": "ayuda", "description": "Ayuda"},
            ]
        )
        # Limpieza periodica: las fotos caducan por TTL aunque el bot se
        # quede sin recibir nuevas fotos, y la cache de planes caduca sola.
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                self._housekeeping, interval=1800, first=30, name="limpieza"
            )

    async def _housekeeping(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Tarea periodica: purga fotos caducadas y respuestas cacheadas."""
        removed = self.photos.purge_expired()
        cache = self.db.purge_expired_cache()
        if removed or cache:
            log.info("Limpieza: %d fotos y %d respuestas cacheadas", removed, cache)

    def _register_handlers(self) -> None:
        # "Saltar" esta disponible en todos los pasos del asistente.
        skip = [CallbackQueryHandler(self.cb_skip, pattern=r"^perfil:skip:")]
        wizard = ConversationHandler(
            entry_points=[
                CommandHandler("perfil", self.cmd_profile),
                CallbackQueryHandler(self.cmd_profile, pattern=r"^menu:perfil$"),
            ],
            states={
                NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_name)] + skip,
                SEX: [CallbackQueryHandler(self.cb_sex, pattern=r"^perfil:sex:")] + skip,
                AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_age)] + skip,
                HEIGHT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_height)
                ] + skip,
                WEIGHT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_weight)
                ] + skip,
                BODYFAT: [
                    CallbackQueryHandler(self.cb_bodyfat, pattern=r"^perfil:bodyfat:"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_bodyfat),
                ] + skip,
                TRAINING: [
                    CallbackQueryHandler(self.cb_training, pattern=r"^perfil:training:")
                ] + skip,
                STEPS: [
                    CallbackQueryHandler(self.cb_steps, pattern=r"^perfil:steps:")
                ] + skip,
                JOB: [CallbackQueryHandler(self.cb_job, pattern=r"^perfil:job:")] + skip,
                ACTIVITY: [
                    CallbackQueryHandler(self.cb_activity, pattern=r"^perfil:act:")
                ] + skip,
                GOAL: [CallbackQueryHandler(self.cb_goal, pattern=r"^perfil:goal:")] + skip,
                TARGET_WEIGHT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_target_weight)
                ],
            },
            fallbacks=[CommandHandler("cancelar", self.cmd_cancel)],
            per_user=True,
            per_chat=True,
            # per_message=False es intencionado: el asistente pide datos por
            # teclado (callback) y luego por texto (edad, altura, peso) y ambos
            # deben compartir el mismo estado de conversacion.
            per_message=False,
        )
        self.app.add_handler(wizard)
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("ayuda", self.cmd_help))
        self.app.add_handler(CommandHandler("token", self.cmd_token))
        self.app.add_handler(CommandHandler("foto", self.cmd_foto))
        self.app.add_handler(CommandHandler("hoy", self.cmd_hoy))
        self.app.add_handler(CommandHandler("resumen", self.cmd_hoy))
        self.app.add_handler(CommandHandler("semana", self.cmd_semana))
        self.app.add_handler(CommandHandler("historial", self.cmd_historial))
        self.app.add_handler(CommandHandler("peso", self.cmd_peso))
        self.app.add_handler(CommandHandler("meta", self.cmd_meta))
        self.app.add_handler(CommandHandler("plan", self.cmd_plan))
        self.app.add_handler(CommandHandler("consejo", self.cmd_advice))
        self.app.add_handler(CommandHandler("anotar", self.cmd_manual))
        self.app.add_handler(CommandHandler("corregir", self.cmd_correct))
        self.app.add_handler(CommandHandler("borrar", self.cmd_delete))
        self.app.add_handler(CommandHandler("privacidad", self.cmd_privacy))
        self.app.add_handler(CommandHandler("olvidarme", self.cmd_forget))
        self.app.add_handler(CommandHandler("exportar", self.cmd_export))
        self.app.add_handler(CommandHandler("cancelar", self.cmd_cancel))
        self.app.add_handler(CallbackQueryHandler(self.cb_menu, pattern=r"^menu:"))
        self.app.add_handler(
            CallbackQueryHandler(self.cb_register_weight, pattern=r"^peso:registrar$")
        )
        self.app.add_handler(CallbackQueryHandler(self.cb_delete_meal, pattern=r"^borrar:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_correct, pattern=r"^corregir:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_retry, pattern=r"^reintentar:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_history, pattern=r"^historial:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_privacy, pattern=r"^privacidad:"))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        self.app.add_handler(MessageHandler(filters.Document.IMAGE, self.on_photo))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

    # -- helpers ----------------------------------------------------------
    def _auto_activate_owner(self, user: Any) -> bool:
        """Activa la cuenta del propietario sin necesidad de token.

        En un bot personal no tiene sentido pedirle un token al dueño; el
        resto de usuarios si lo necesitan.
        """
        if user is None or not self.config.is_owner(getattr(user, "id", None)):
            return False
        if self.users.is_registered(user.id):
            return False
        self.db.upsert_user(
            user.id,
            username=user.username,
            first_name=user.first_name,
            plan=self.config.plan_for(self.config.owner_plan).name,
            authorized_at=iso(),
            last_seen=iso(),
        )
        return True

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        if self.users.is_registered(user.id):
            self.db.touch_user(user.id, last_seen=iso())
            return True
        return False

    def _id_hint(self, user: Any) -> str:
        """Muestra tu id de Telegram para que te activen sin token."""
        return (
            "\n\n<i>Tu id de Telegram es "
            f"<code>{getattr(user, 'id', '?')}</code>: si prefieres que te activen "
            "sin token, pásaselo al administrador.</i>"
        )

    async def _require_auth(self, update: Update) -> bool:
        """Comprueba el acceso y la cuota de mensajes del plan."""
        user = update.effective_user
        if not self._authorized(update):
            # El propietario se activa solo: no le hace falta un token.
            if self._auto_activate_owner(user):
                return True
            await update.effective_message.reply_text(
                "🔒 Este bot es de acceso restringido.\n\n"
                "Para activarlo necesitas un token. Escribe:\n"
                "<code>/token NTR1-XXXXX-XXXXX-XXXXX</code>\n\n"
                "El token te lo entrega el administrador del bot."
                + self._id_hint(user),
                parse_mode=ParseMode.HTML,
            )
            return False

        plan = self.users.plan(user.id)
        check = self.limits.check_messages(user.id, plan)
        if not check.allowed:
            await update.effective_message.reply_text(
                f"⏳ {esc(check.reason)}", parse_mode=ParseMode.HTML
            )
            return False
        self.limits.bump_message(user.id)
        return True

    def _plan_limits(self, user_id: int) -> tuple[int, int]:
        plan = self.config.plan_for(self.users.plan(user_id))
        return (plan.daily_photo_limit, plan.daily_message_limit)

    def _check_photo_quota(self, user_id: int) -> tuple[bool, str]:
        check = self.limits.check_photos(user_id, self.users.plan(user_id))
        if not check.allowed:
            return (False, check.reason)
        return (True, "")

    def _quota_snapshot(self, user_id: int) -> dict[str, int]:
        return self.limits.snapshot(user_id, self.users.plan(user_id))

    # -- acceso -----------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return

        if not self.users.is_registered(user.id):
            # El propietario no necesita token: se activa solo.
            self._auto_activate_owner(user)
            token = (context.args or [None])[0]
            if token and await self._try_token(update, token):
                return
            if not self.users.is_registered(user.id):
                await update.effective_message.reply_text(
                    "👋 <b>Bienvenido a tu asistente de nutrición</b>\n\n"
                    "Envíame una foto de cualquier plato y te digo cuántas calorías tiene, "
                    "lo guardo en tu historial y te ayudo a cumplir tu objetivo.\n\n"
                    "🔒 <b>Acceso restringido</b>: necesito un token para activarte la cuenta.\n"
                    "Escribe <code>/token NTR1-XXXXX-XXXXX-XXXXX</code> con el código que te "
                    "haya dado el administrador."
                    + self._id_hint(user),
                    parse_mode=ParseMode.HTML,
                )
                return

        profile = self.users.profile(user.id)
        missing = self.users.profile_complete(profile)
        if missing:
            await update.effective_message.reply_text(
                "👋 <b>Cuenta activada</b> ✅\n\n"
                "Para calcular tus objetivos necesito completar el perfil "
                f"({', '.join(missing)}).\n\n"
                "Pulsa <b>⚙️ Perfil</b> o escribe <code>/perfil</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            return

        summary = self.users.today_summary(user.id)
        streak = self.users.streak(user.id)
        quota = self._quota_snapshot(user.id)
        target = summary.get("target")
        lines = [
            "👋 <b>Bienvenido de nuevo</b>",
            "",
            f"🔥 Hoy: <b>{summary['calories']} kcal</b>"
            + (f" de {target.daily_kcal}" if target else ""),
        ]
        if target:
            lines.append(progress_bar(summary["calories"], target.daily_kcal))
        lines.append(f"🔥 Racha: <b>{streak} día(s)</b> seguidos registrando comidas.")
        lines.append(f"📸 Te quedan <b>{quota['photos_left']}</b> análisis hoy.")
        lines.append("\nEnvíame una foto cuando quieras.")
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_keyboard(),
        )

    async def cmd_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_message.reply_text(
                "Uso: <code>/token NTR1-XXXXX-XXXXX-XXXXX</code>", parse_mode=ParseMode.HTML
            )
            return
        await self._try_token(update, context.args[0])

    async def _try_token(self, update: Update, code: str) -> bool:
        user = update.effective_user
        if not user:
            return False
        if self.users.is_registered(user.id):
            await update.effective_message.reply_text(
                "✅ Tu cuenta ya está activada.", reply_markup=main_menu()
            )
            return True

        check = self.tokens.check(code)
        if not check.ok:
            await update.effective_message.reply_text(
                f"❌ Token no válido: {esc(check.reason)}", parse_mode=ParseMode.HTML
            )
            return False

        # Canjear puede fallar aunque `check` haya pasado (p. ej. el token ya
        # esta vinculado a otra cuenta): hay que mirar el resultado.
        redeem = self.tokens.redeem(code, user.id)
        if not redeem.ok:
            await update.effective_message.reply_text(
                f"❌ Token no válido: {esc(redeem.reason)}", parse_mode=ParseMode.HTML
            )
            return False

        plan = check.plan or "free"
        self.db.upsert_user(
            user.id,
            username=user.username,
            first_name=user.first_name,
            plan=plan,
            token_id=check.token_id,
            authorized_at=iso(),
            last_seen=iso(),
        )
        plan_obj = self.config.plan_for(plan)
        await update.effective_message.reply_text(
            "✅ <b>¡Acceso activado!</b>\n\n"
            f"Plan: <b>{plan_obj.label}</b>\n"
            f"Análisis por día: <b>{plan_obj.daily_photo_limit}</b>\n\n"
            "Ahora completa tu perfil con <code>/perfil</code> y empieza a enviar fotos "
            "de tus platos.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return True

    # -- analisis de fotos -------------------------------------------------
    async def cmd_foto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        note = (context.args or [None])[0]
        await update.effective_message.reply_text(
            "📸 Envíame una foto del plato.\n"
            + (f"Contexto: <i>{esc(note)}</i>\n" if note else "")
            + "\n<i>Consejo: añade en el pie de foto el peso o la porción si la sabes "
            "(ej. <code>150 g de pechuga</code>); mejora mucho la precisión.</i>",
            parse_mode=ParseMode.HTML,
        )

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return

        allowed, reason = self._check_photo_quota(user.id)
        if not allowed:
            await message.reply_text(f"⏳ {esc(reason)}", parse_mode=ParseMode.HTML)
            return

        # El tamano se comprueba antes de descargar: una foto de 20 MB no debe
        # llenar la RAM del servidor.
        size = self._photo_size(message)
        size_check = self.limits.check_photo_size(size)
        if not size_check.allowed:
            await message.reply_text(f"📏 {esc(size_check.reason)}", parse_mode=ParseMode.HTML)
            return

        note = message.caption or None
        status = await message.reply_text("🔍 Analizando tu plato…")

        # --- 1. RECIBIR --------------------------------------------------
        try:
            file_id, bytes_data = await self._download_photo(message)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo descargar la foto: %s", exc)
            await status.edit_text("❌ No he podido descargar la imagen. Inténtalo otra vez.")
            return

        # --- 2. GUARDAR EN DISCO ------------------------------------------
        # La foto se escribe en disco antes de analizarla: si el proceso se
        # cae a mitad del analisis, el fichero queda y la tarea periodica
        # lo recoge. En cuanto se envia el resultado, se borra.
        saved_path = self.photos.save(user.id, bytes_data)
        if saved_path:
            log.debug("Foto guardada en %s", saved_path)

        try:
            # --- 3. EL MODELO DETERMINA LAS KCAL --------------------------
            # Se lee del disco: asi se comprueba que el fichero se guardo bien.
            payload = saved_path.read_bytes() if saved_path else bytes_data
            analysis = await self.llm.analyze_photo(
                payload, sniff_mime(payload), note
            )

            now = utcnow()
            meal_id = self.db.add_meal(
                user_id=user.id,
                created_at=iso(now),
                eaten_at=iso(now),
                meal_slot=self._slot_for(now),
                photo_id=file_id,
                caption=note,
                dish=analysis.dish,
                calories=analysis.calories,
                protein_g=analysis.protein_g,
                carbs_g=analysis.carbs_g,
                fat_g=analysis.fat_g,
                mass_g=analysis.mass_g,
                ingredients=analysis.as_row()["ingredients"],
                raw_answer=analysis.raw[:4000],
                confidence=analysis.confidence,
                engine=analysis.engine,
            )
            self.limits.bump_photo(user.id)

            summary = self.users.today_summary(user.id)
            target = summary.get("target")
            extra = ""
            if target:
                extra = (
                    f"\n\n<b>Hoy:</b> {summary['calories']}/{target.daily_kcal} kcal "
                    f"{progress_bar(summary['calories'], target.daily_kcal)}"
                )

            # --- 4. ENVIAR EL RESULTADO ----------------------------------
            await status.edit_text(
                analysis_message(analysis) + extra,
                parse_mode=ParseMode.HTML,
                reply_markup=meal_actions_keyboard(meal_id),
            )
        except InferenceError as exc:
            log.warning("Fallo de inferencia: %s", exc)
            context.user_data["last_failed_photo"] = file_id
            await status.edit_text(
                "❌ <b>El motor de análisis no ha respondido.</b>\n\n"
                f"<i>{esc(str(exc)[:200])}</i>\n\n"
                "Espera un momento y vuelve a intentarlo.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Reintentar", callback_data="reintentar:ultimo"
                            )
                        ]
                    ]
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Fallo inesperado en inferencia")
            await status.edit_text(f"❌ Error inesperado: {esc(str(exc)[:200])}")
        finally:
            # --- 5. ELIMINAR LA FOTO -------------------------------------
            # Siempre, incluso si el modelo fallo: en Telegram sigue
            # estando (por eso existe el boton de reanalizar).
            if self.photos.delete(saved_path):
                log.debug("Foto eliminada: %s", saved_path)

    def _slot_for(self, moment) -> str:
        from foodbot.core.timeutil import meal_slot

        return meal_slot(moment, self.users.tz)

    @staticmethod
    def _photo_size(message: Any) -> int | None:
        """Tamaño en bytes de la foto, si Telegram lo indica."""
        if getattr(message, "photo", None):
            return int(message.photo[-1].file_size or 0) or None
        document = getattr(message, "document", None)
        if document is not None:
            return int(document.file_size or 0) or None
        return None

    async def _download_photo(self, message: Any) -> tuple[str, bytes]:
        if message.photo:
            file = await message.get_bot().get_file(message.photo[-1].file_id)
        elif message.document:
            file = await message.get_bot().get_file(message.document.file_id)
        else:
            raise ValueError("El mensaje no contiene una imagen")
        data = await file.download_as_bytearray()
        return (file.file_id or "", bytes(data))

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Texto suelto: puede ser un token, el nuevo valor deunas calorías, etc."""
        text = (update.effective_message.text or "").strip()

        # 1) ¿Estamos esperando las calorías corregidas?
        pending = context.user_data.pop("await_kcal", None)
        if pending:
            await self._apply_kcal(update, int(pending), text)
            return

        # 2) ¿Está esperando el peso (botón "Registrar peso ahora")?
        if context.user_data.pop("await_weight", None):
            await self._register_weight(update, text)
            return

        # 3) Botones del teclado persistente
        action = MENU_LABELS.get(text)
        if action:
            await self._menu_action(update, action)
            return

        # 4) ¿El usuario está activo?
        if self._authorized(update):
            await update.effective_message.reply_text(
                "👋 Usa el menú de abajo. <i>Para analizar una comida, "
                "envía una foto.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_keyboard(),
            )
            return

        # 3) ¿Es un token?
        if text.upper().replace(" ", "").startswith("NTR1"):
            await self._try_token(update, text)
            return

        await update.effective_message.reply_text(
            "🔒 Este bot es de acceso restringido: necesito un token para activarte.\n"
            "Escribe <code>/token NTR1-XXXXX-XXXXX-XXXXX</code>." + self._id_hint(user),
            parse_mode=ParseMode.HTML,
        )

    async def _menu_action(self, update: Update, action: str) -> None:
        """Acciones del teclado persistente (los botones de abajo)."""
        if action == "foto":
            await self._send_foto_prompt(update)
        elif action == "resumen":
            await self._send_today(update)
        elif action == "semana":
            await self._send_week(update)
        elif action == "peso":
            await self._send_weight(update)
        elif action == "perfil":
            await self.cmd_profile(update, ContextTypes.DEFAULT_TYPE)

    # -- correccion de calorías --------------------------------------------
    async def cmd_correct(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Corrige las calorías del último plato: /corregir 520"""
        if not await self._require_auth(update):
            return
        user = update.effective_user
        if context.args:
            await self._apply_kcal(update, None, " ".join(context.args))
            return
        meal = self.db.last_meal(user.id)
        if not meal:
            await update.effective_message.reply_text("No hay platos que corregir.")
            return
        await self._ask_kcal(update, context, int(meal["id"]))

    async def cb_correct(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        meal_id = int(query.data.split(":")[-1])
        # Solo se puede corregir un plato propio.
        meal = self.db.get_meal(user.id, meal_id)
        if not meal:
            await self._safe_send(update, "Ese plato no existe o no es tuyo.")
            return
        await self._safe_send(
            update,
            "✏️ ¿Cuántas calorías tiene de verdad el plato?\n"
            f"<i>Estimación del bot: {int(meal['calories'] or 0)} kcal. "
            "Escribe el número.</i>"
        )
        context.user_data["await_kcal"] = meal_id

    async def _ask_kcal(self, update: Update, context: ContextTypes.DEFAULT_TYPE, meal_id: int) -> None:
        context.user_data["await_kcal"] = meal_id
        await update.effective_message.reply_text(
            "✏️ ¿Cuántas calorías tiene el plato? Escribe solo el número.",
            parse_mode=ParseMode.HTML,
        )

    async def _apply_kcal(self, update: Update, meal_id: int | None, text: str) -> None:
        user = update.effective_user
        if not user:
            return
        value = self._parse_number(update, text)
        if value is None or not (0 <= value <= 5000):
            await update.effective_message.reply_text(
                "No he entendido las calorías. Escribe un número entre 0 y 5000.",
                parse_mode=ParseMode.HTML,
            )
            return
        if meal_id is None:
            meal = self.db.last_meal(user.id)
            if not meal:
                await update.effective_message.reply_text("No hay platos que corregir.")
                return
            meal_id = int(meal["id"])
        # Solo se toca una comida del propio usuario.
        if not self.db.update_meal(user.id, meal_id, calories=int(round(value)), edited=1):
            await update.effective_message.reply_text("No he encontrado ese plato.")
            return
        meal = self.db.get_meal(user.id, meal_id)
        dish = esc(meal["dish"] or "Plato") if meal else "el plato"
        await update.effective_message.reply_text(
            f"✅ Anotado: <b>{dish}</b> — <b>{int(round(value))} kcal</b>.\n"
            "<i>He actualizado tu historial con tu valor.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cb_retry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reanaliza una foto: vuelve a descargarla de Telegram y repetir la llamada."""
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        raw = query.data.split(":")[-1]
        if raw == "ultimo":
            file_id = context.user_data.get("last_failed_photo") or ""
        else:
            meal = self.db.get_meal(user.id, int(raw))
            file_id = str(meal["photo_id"] or "") if meal else ""
        if not file_id:
            await query.edit_message_text(
                "No tengo la foto guardada. Vuelve a enviármela y la analizo."
            )
            return

        allowed, reason = self._check_photo_quota(user.id)
        if not allowed:
            await query.edit_message_text(f"⏳ {esc(reason)}", parse_mode=ParseMode.HTML)
            return

        await query.edit_message_text("🔍 Analizando tu plato…")
        saved_path = None
        try:
            # 1. RECIBIR (de nuevo, desde Telegram)
            file = await self.app.bot.get_file(file_id)
            data = bytes(await file.download_as_bytearray())
            # 2. GUARDAR EN DISCO
            saved_path = self.photos.save(user.id, data)
            payload = saved_path.read_bytes() if saved_path else data
            # 3. EL MODELO
            analysis = await self.llm.analyze_photo(payload, sniff_mime(payload), None)

            if raw != "ultimo":
                meal_id = int(raw)
                self.db.update_meal(
                    user.id,
                    meal_id,
                    dish=analysis.dish,
                    calories=analysis.calories,
                    protein_g=analysis.protein_g,
                    carbs_g=analysis.carbs_g,
                    fat_g=analysis.fat_g,
                    mass_g=analysis.mass_g,
                    ingredients=analysis.as_row()["ingredients"],
                    raw_answer=analysis.raw[:4000],
                    confidence=analysis.confidence,
                    edited=0,
                )
            else:
                now = utcnow()
                meal_id = self.db.add_meal(
                    user_id=user.id,
                    created_at=iso(now),
                    eaten_at=iso(now),
                    meal_slot=self._slot_for(now),
                    photo_id=file_id,
                    dish=analysis.dish,
                    calories=analysis.calories,
                    protein_g=analysis.protein_g,
                    carbs_g=analysis.carbs_g,
                    fat_g=analysis.fat_g,
                    mass_g=analysis.mass_g,
                    ingredients=analysis.as_row()["ingredients"],
                    raw_answer=analysis.raw[:4000],
                    confidence=analysis.confidence,
                    engine=analysis.engine,
                )
                self.limits.bump_photo(user.id)

            # 4. ENVIAR EL RESULTADO
            await query.edit_message_text(
                analysis_message(analysis),
                parse_mode=ParseMode.HTML,
                reply_markup=meal_actions_keyboard(meal_id),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Reanalisis fallido: %s", exc)
            await query.edit_message_text(
                "❌ No he podido volver a analizar la foto. Inténtalo más tarde.",
                parse_mode=ParseMode.HTML,
            )
        finally:
            # 5. ELIMINAR LA FOTO (siempre)
            self.photos.delete(saved_path)

    # -- envio unificado: los comandos y los botones hacen lo mismo --------
    def _reply_target(self, update: Update):
        """(mensaje, como responder) segun venga de un comando o de un boton."""
        query = getattr(update, "callback_query", None)
        if query is not None and query.message is not None:
            return query.message, query.edit_message_text
        message = update.effective_message
        return message, message.reply_text

    async def _safe_send(self, update: Update, text: str, **kwargs: Any) -> None:
        """Edita el mensaje del boton; si no se puede, responde con uno nuevo."""
        query = getattr(update, "callback_query", None)
        if query is not None and query.message is not None:
            try:
                await query.edit_message_text(text, **kwargs)
                return
            except BadRequest:
                pass  # el texto es identico: mejor responder aparte
        await update.effective_message.reply_text(text, **kwargs)

    async def _typing_loop(self, chat_id: int, stop: asyncio.Event) -> None:
        """Mantiene el indicador de 'escribiendo...' del chat."""
        send = getattr(getattr(self.app, "bot", None), "send_chat_action", None)
        if send is None:
            return
        while not stop.is_set():
            try:
                await send(chat_id=chat_id, action="typing")
            except Exception:  # noqa: BLE001
                return
            await asyncio.sleep(4.5)

    async def _run_with_typing(self, chat_id: int | None, coro):
        """Ejecuta una inferencia mostrando que el bot esta escribiendo."""
        if not chat_id:
            return await coro
        stop = asyncio.Event()
        task = asyncio.create_task(self._typing_loop(chat_id, stop))
        try:
            return await coro
        finally:
            stop.set()
            task.cancel()

    # -- consultas ---------------------------------------------------------
    async def _send_today(self, update: Update) -> None:
        user = update.effective_user
        summary = self.users.today_summary(user.id)
        week = self.users.week_summary(user.id)
        await self._safe_send(
            update,
            today_message(
                summary,
                self.users.tz,
                self._quota_snapshot(user.id),
                week=week,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def _send_week(self, update: Update) -> None:
        user = update.effective_user
        week = self.users.week_summary(user.id)
        await self._safe_send(
            update,
            weekly_message(week),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def _send_foto_prompt(self, update: Update) -> None:
        await self._safe_send(
            update,
            "📸 <b>Envíame una foto del plato</b>\n\n"
            "<i>Consejo: añade en el pie de foto el peso o la porción si la "
            "sabes (ej. <code>150 g de pechuga</code>); mejora mucho la precisión.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def _send_manual_prompt(self, update: Update) -> None:
        await self._safe_send(
            update,
            "🍽 <b>Registrar a mano</b>\n\n"
            "Escribe: <code>Arroz con pollo | 520</code>\n"
            "<i>Nombre del plato, una barra vertical y las calorías.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def _send_weight(self, update: Update) -> None:
        user = update.effective_user
        current = self.db.latest_weight(user.id)
        rows = self.db.last_weights(user.id, 7)
        lines = [
            f"\u2022 <code>{format_local_date(r['logged_at'], self.users.tz)}</code> — "
            f"{float(r['weight_kg']):.1f} kg"
            for r in reversed(rows)
        ]
        await self._safe_send(
            update,
            "<b>⚖️ Mi peso</b>\n"
            + (f"Último: <b>{current:.1f} kg</b>\n" if current else "")
            + ("\n".join(lines) if lines else "<i>Sin registros.</i>"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📝 Registrar peso ahora", callback_data="peso:registrar"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🗓 Mi semana", callback_data="menu:semana"
                        ),
                        InlineKeyboardButton(
                            "📊 Mi día", callback_data="menu:resumen"
                        ),
                    ],
                ]
            ),
        )

    async def _send_progress(self, update: Update) -> None:
        progress = self.users.weight_progress(update.effective_user.id)
        await self._safe_send(
            update,
            progress_message(progress),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cmd_hoy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_today(update)

    async def cmd_semana(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Estado del banco de ahorro semanal y margen que queda."""
        if not await self._require_auth(update):
            return
        await self._send_week(update)

    async def cmd_historial(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        days = 7
        if context.args:
            try:
                days = max(1, min(90, int(context.args[0])))
            except ValueError:
                pass
        await self._send_history(update, days)

    async def _send_history(self, update: Update, days: int) -> None:
        stats = self.users.history_stats(update.effective_user.id, days)
        await self._safe_send(
            update,
            history_message(stats, self.users.tz),
            parse_mode=ParseMode.HTML,
            reply_markup=history_keyboard(),
        )

    async def cb_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        days = 7
        raw = query.data.split(":")[-1]
        if raw.isdigit():
            days = max(1, min(365, int(raw)))
        stats = self.users.history_stats(update.effective_user.id, days)
        await self._safe_send(
            update,
            history_message(stats, self.users.tz),
            parse_mode=ParseMode.HTML,
            reply_markup=history_keyboard(),
        )

    async def cmd_meta(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_progress(update)

    async def cmd_peso(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        if not context.args:
            await self._send_weight(update)
            return
        await self._register_weight(update, context.args[0])

    async def _register_weight(self, update: Update, raw: str) -> None:
        """Guarda un peso y devuelve la tendencia."""
        user = update.effective_user
        try:
            weight = float(str(raw).strip().replace(",", "."))
        except ValueError:
            await update.effective_message.reply_text(
                "No he entendido el peso. Escribe los kilos (ej. <code>78.4</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return
        if not (30 <= weight <= 300):
            await update.effective_message.reply_text(
                "Ese peso no parece válido (debe estar entre 30 y 300 kg).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return

        now = utcnow()
        self.db.add_weight(user.id, weight, iso(now))
        self.db.touch_user(user.id, weight_kg=weight)
        progress = self.users.weight_progress(user.id)
        rate = progress.get("weekly_rate") or 0.0
        trend_line = (
            f"Tendencia: <b>{rate:+.2f} kg/semana</b>\n"
            if (progress.get("trend") or {}).get("has_data")
            else "\n<i>Registra pesos durante una semana para ver la tendencia.</i>\n"
        )
        advice = progress.get("advice")
        await update.effective_message.reply_text(
            f"✅ Peso registrado: <b>{weight:.1f} kg</b>\n"
            + trend_line
            + (f"\n💡 {esc(advice)}" if advice else ""),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cb_register_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Boton 'Registrar peso ahora': Telegram enfoca el campo de texto."""
        query = update.callback_query
        await query.answer("Escribe tu peso 👇", show_alert=False)
        await query.message.reply_text(
            "⚖️ <b>¿Cuánto pesas ahora?</b>\n<i>Escribe los kilos (ej. <code>78.4</code>)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(),
        )
        context.user_data["await_weight"] = True


    async def cmd_manual(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Registro manual: /anotar Arroz con pollo | 520"""
        if not await self._require_auth(update):
            return
        raw = " ".join(context.args or [])
        if "|" not in raw:
            await update.effective_message.reply_text(
                "Uso: <code>/anotar nombre del plato | kcal</code>", parse_mode=ParseMode.HTML
            )
            return
        dish, _, kcal_text = raw.partition("|")
        try:
            calories = int(round(float(kcal_text.strip().replace(",", "."))))
        except ValueError:
            await update.effective_message.reply_text(
                "No he entendido las calorías.", parse_mode=ParseMode.HTML
            )
            return
        if not (0 <= calories <= 5000):
            await update.effective_message.reply_text(
                "Ese número de calorías no parece válido (entre 0 y 5000).",
                parse_mode=ParseMode.HTML,
            )
            return
        user = update.effective_user
        now = utcnow()
        self.db.add_meal(
            user_id=user.id,
            created_at=iso(now),
            eaten_at=iso(now),
            meal_slot=self._slot_for(now),
            dish=dish.strip()[:120],
            calories=calories,
            protein_g=0,
            carbs_g=0,
            fat_g=0,
            engine="manual",
        )
        await update.effective_message.reply_text(
            f"✅ Anotado: <b>{esc(dish.strip())}</b> — {calories} kcal",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        user = update.effective_user
        meals = self.db.recent_meals(user.id, 1)
        if not meals:
            await update.effective_message.reply_text("No hay nada que borrar.")
            return
        meal = meals[0]
        ok = self.db.delete_meal(user.id, int(meal["id"]))
        await update.effective_message.reply_text(
            ("🗑 Borrado: " + esc(meal["dish"] or "comida")) if ok else "No he podido borrarlo.",
            parse_mode=ParseMode.HTML,
        )

    async def cb_delete_meal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        meal_id = int(query.data.split(":")[-1])
        user = update.effective_user
        ok = self.db.delete_meal(user.id, meal_id)
        await self._safe_send(
            update,
            "🗑 Plato borrado." if ok else "No he podido borrar ese plato.",
        )

    async def cmd_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_export(update)

    async def _send_export(self, update: Update) -> None:
        plan = self.config.plan_for(self.users.plan(update.effective_user.id))
        if "export" not in plan.features:
            await update.effective_message.reply_text(
                "🔒 La exportación de datos es una función del plan Pro. "
                "Contacta con el administrador para ampliarla.",
                parse_mode=ParseMode.HTML,
            )
            return
        user = update.effective_user
        now = utcnow()
        start = iso(now - timedelta(days=90))
        meals = self.db.meals_between(user.id, start, iso(now + timedelta(seconds=1)))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["fecha", "plato", "kcal", "proteina_g", "carbohidratos_g", "grasa_g", "confianza"]
        )
        for meal in meals:
            writer.writerow(
                [
                    format_local_date(meal["eaten_at"], self.users.tz),
                    meal["dish"] or "",
                    meal["calories"] or 0,
                    meal["protein_g"] or 0,
                    meal["carbs_g"] or 0,
                    meal["fat_g"] or 0,
                    meal["confidence"] or "",
                ]
            )
        buffer.seek(0)
        await update.effective_message.reply_document(
            document=buffer.getvalue().encode("utf-8"),
            filename="historial_nutrition.csv",
            caption="📤 Tu historial de comidas (últimos 90 días).",
        )

    # -- funciones inteligentes -------------------------------------------
    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_plan(update)

    async def _send_plan(self, update: Update) -> None:
        user = update.effective_user
        plan = self.config.plan_for(self.users.plan(user.id))
        if "plan" not in plan.features:
            await update.effective_message.reply_text(
                "🔒 El plan de comidas es una función del plan Pro. "
                "Contacta con el administrador para activarlo.",
                parse_mode=ParseMode.HTML,
            )
            return

        profile = self.users.profile(user.id)
        target = self.users.daily_target(user.id)
        if not target:
            await update.effective_message.reply_text(
                "⚙️ Completa tu perfil primero (<code>/perfil</code>) para que el plan "
                "se ajuste a tus objetivos.",
                parse_mode=ParseMode.HTML,
            )
            return
        summary = self.users.today_summary(user.id)
        eaten = summary["calories"]

        # El deficit es semanal: el plan se ajusta a lo que queda de la
        # semana, no a un objetivo diario rigido. Asi, un dia de mas se
        # compensa con los siguientes sin que la semana se rompa.
        week = self.users.week_summary(user.id)
        allowance = week.get("allowance") or {}
        daily_allowance = allowance.get("daily_allowance") or target.daily_kcal
        week_context = ""
        if allowance.get("budget") and allowance.get("days_left"):
            week_context = (
                f"Mi presupuesto semanal es de {allowance['budget']} kcal y llevo "
                f"{allowance['consumed']} kcal; me quedan {allowance['remaining']} kcal "
                f"para los {allowance['days_left']} dias que quedan de la semana. "
            )

        # Cache por dia: si ya se genero un plan para lo que has comido, no se
        # vuelve a gastar GPU en la misma pregunta.
        cache_key = f"plan:{user.id}:{summary['day']}:{eaten}:{daily_allowance}"
        cached = self.db.get_cache(cache_key)
        remaining = summary.get("remaining")
        header = (
            "<b>🧭 Plan para el resto del día</b>\n"
            f"{target_line(target)}\n"
            f"{esc(self.users.describe_profile(profile))}\n"
            f"Consumido: {eaten} kcal"
            + (f" · Quedan {remaining} kcal" if remaining is not None else "")
            + "\n\n"
        )
        if cached:
            await update.effective_message.reply_text(
                header + esc(cached.strip()), parse_mode=ParseMode.HTML
            )
            return

        status = await update.effective_message.reply_text("🧭 Preparando tu plan…")
        try:
            text = await self.llm.make_plan(
                profile=self.users.describe_profile(profile),
                kcal=int(daily_allowance),
                protein=target.macro.protein_g,
                carbs=target.macro.carbs_g,
                fat=target.macro.fat_g,
                eaten=eaten,
                week=week_context,
            )
        except InferenceError as exc:
            await status.edit_text(f"❌ No he podido generar el plan: {esc(str(exc)[:200])}")
            return
        self.db.set_cache(cache_key, text.strip(), user_id=user.id)
        await status.edit_text(header + esc(text.strip()), parse_mode=ParseMode.HTML)

    async def cmd_advice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_advice(update)

    async def _send_advice(self, update: Update) -> None:
        user = update.effective_user
        profile = self.users.profile(user.id)
        target = self.users.daily_target(user.id)
        if not target:
            await update.effective_message.reply_text(
                "⚙️ Completa tu perfil (<code>/perfil</code>) para recibir consejos personalizados.",
                parse_mode=ParseMode.HTML,
            )
            return
        summary = self.users.today_summary(user.id)
        eaten = summary["calories"]
        cache_key = f"advice:{user.id}:{summary['day']}:{eaten}"
        cached = self.db.get_cache(cache_key)
        header = "<b>💡 Consejo de hoy</b>\n"
        if cached:
            await update.effective_message.reply_text(
                header + esc(cached.strip()), parse_mode=ParseMode.HTML
            )
            return

        status = await update.effective_message.reply_text("💡 Pensando un consejo…")
        try:
            text = await self.llm.make_advice(
                profile=self.users.describe_profile(profile),
                goal=goal_label(profile.get("goal")),
                kcal=target.daily_kcal,
                protein=target.macro.protein_g,
                carbs=target.macro.carbs_g,
                fat=target.macro.fat_g,
                eaten=eaten,
            )
        except InferenceError as exc:
            await status.edit_text(f"❌ No he podido generar el consejo: {esc(str(exc)[:200])}")
            return
        self.db.set_cache(cache_key, text.strip(), user_id=user.id)
        await status.edit_text(header + esc(text.strip()), parse_mode=ParseMode.HTML)

    # -- privacidad --------------------------------------------------------
    async def cmd_privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_privacy(update)

    async def _send_privacy(self, update: Update) -> None:
        user = update.effective_user
        report = storage_report(self.db, user.id)
        await update.effective_message.reply_text(
            privacy_message(report, self.photos.total_bytes()),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗑 Borrar todos mis datos", callback_data="privacidad:borrar"
                        )
                    ],
                    [InlineKeyboardButton("⬅️ Menú", callback_data="menu:resumen")],
                ]
            ),
        )

    async def cb_privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        action = query.data.split(":")[-1]
        user = update.effective_user
        if action == "borrar":
            await query.edit_message_text(
                "⚠️ <b>¿Seguro?</b>\n\n"
                "Se borrarán tus comidas, pesos y objetivos, y tu acceso al bot "
                "quedará revocado. No se puede deshacer.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Sí, borra todo", callback_data="privacidad:confirmar"
                            ),
                            InlineKeyboardButton("Cancelar", callback_data="privacidad:cancelar"),
                        ]
                    ]
                ),
            )
            return
        if action == "confirmar":
            counts = erase_user_data(self.db, user.id)
            self.photos.purge_user(user.id)
            total = sum(counts.values())
            await query.edit_message_text(
                "🗑 <b>Datos borrados.</b>\n\n"
                f"Se han eliminado {total} registros y tu acceso ha quedado revocado.\n\n"
                "Si quieres volver a usar el bot, pide un token nuevo.",
                parse_mode=ParseMode.HTML,
            )
            return
        if action == "cancelar":
            await query.edit_message_text("No se ha borrado nada. 👍")
            return
        await query.edit_message_text("Opción no reconocida.")

    async def cmd_forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Alias de texto de /privacidad: borra todo lo del usuario."""
        if not await self._require_auth(update):
            return
        user = update.effective_user
        counts = erase_user_data(self.db, user.id)
        self.photos.purge_user(user.id)
        await update.effective_message.reply_text(
            "🗑 <b>Datos borrados.</b>\n\n"
            f"Se han eliminado {sum(counts.values())} registros y tu acceso ha quedado "
            "revocado.",
            parse_mode=ParseMode.HTML,
        )

    # -- configuracion del perfil -----------------------------------------
    # -- asistente de perfil ----------------------------------------------
    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """El bot va preguntando los datos, uno a uno, y los guarda."""
        if not await self._require_auth(update):
            return ConversationHandler.END
        profile = self.users.profile(update.effective_user.id)
        await update.effective_message.reply_text(
            self._profile_summary(profile) + "\nVamos a completarlo paso a paso.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_keyboard(),
        )
        return await self._ask(update, context, "name")

    def _profile_summary(self, profile: dict[str, Any]) -> str:
        """Resumen de todo lo que sabe el bot del usuario."""
        lines = ["<b>⚙️ Tu perfil</b>"]
        nickname = profile.get("nickname")
        if nickname:
            lines.append(f"Nombre: {esc(str(nickname))}")
        lines.append(f"Sexo: {SEXES.get(str(profile.get('sex') or '').lower(), 'sin definir')}")
        lines.append(f"Edad: {profile.get('age') or '-'}")
        height = profile.get("height_cm")
        lines.append(f"Altura: {height:.0f} cm" if height else "Altura: -")
        weight = profile.get("weight_kg")
        lines.append(f"Peso: {weight:.1f} kg" if weight else "Peso: -")
        fat = profile.get("body_fat_pct")
        lines.append(f"% grasa: {fat:.1f} %" if fat else "% grasa: sin definir")
        training = profile.get("training_days")
        lines.append(
            f"Entrenamiento: {training} d\u00edas/semana" if training is not None else
            "Entrenamiento: sin definir"
        )
        steps = profile.get("daily_steps")
        lines.append(f"Pasos: {steps:,}/d\u00eda".replace(",", ".") if steps else "Pasos: sin definir")
        job = profile.get("job_activity")
        lines.append(f"Trabajo: {JOB_LABELS.get(str(job or ''), 'sin definir')}")
        activity = str(profile.get("activity") or "")
        lines.append(
            f"Actividad: {activity_label(activity)}" if activity else "Actividad: -"
        )
        lines.append(f"Objetivo: {goal_label(profile.get('goal'))}")
        lines.append(f"Peso objetivo: {profile.get('target_weight_kg') or '-'} kg")
        return "\n".join(lines) + "\n"

    async def _ask(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        step: str,
    ) -> int:
        """Hace la siguiente pregunta del asistente con su teclado."""
        question, kind = PROFILE_QUESTIONS[step]
        context.user_data["perfil_paso"] = step
        reply_markup: Any = ForceReply()
        if kind == "sex":
            reply_markup = profile_keyboard()
        elif kind == "activity":
            reply_markup = activity_keyboard()
        elif kind == "goal":
            reply_markup = goal_keyboard()
        elif kind == "bodyfat":
            reply_markup = bodyfat_keyboard()
        elif kind == "training":
            reply_markup = training_keyboard()
        elif kind == "steps":
            reply_markup = steps_keyboard()
        elif kind == "job":
            reply_markup = job_keyboard()
        await update.effective_message.reply_text(
            question, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
        return PROFILE_STEPS[step]

    async def cb_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Saltar un dato opcional (no bloquea el perfil)."""
        query = update.callback_query
        await query.answer()
        target = query.data.split(":")[-1]
        if target not in PROFILE_STEPS:
            target = "activity"
        await query.edit_message_text("👍 Perfecto, seguimos.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, target)

    async def on_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        name = (update.effective_message.text or "").strip()[:40]
        if not name:
            await update.effective_message.reply_text(
                "Necesito un nombre para llamarte. Escribe lo que quieras.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return NAME
        self.users.update_profile(update.effective_user.id, nickname=name)
        await update.effective_message.reply_text(
            f"✅ Hola, <b>{esc(name)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return await self._ask(update, context, "sex")

    async def cb_sex(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        sex = query.data.split(":")[-1]
        self.users.update_profile(update.effective_user.id, sex=sex)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "age")

    async def on_age(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        age = self._parse_number(update, update.effective_message.text or "")
        if age is None or not (12 <= age <= 100):
            await update.effective_message.reply_text(
                "Esa edad no parece válida. Escribe un número entre 12 y 100.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return AGE
        self.users.update_profile(update.effective_user.id, age=int(age))
        await update.effective_message.reply_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "height")

    async def on_height(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        height = self._parse_number(update, update.effective_message.text or "")
        if height is None or not (120 <= height <= 230):
            await update.effective_message.reply_text(
                "Esa altura no parece válida. Escribe los centímetros (ej. <code>175</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return HEIGHT
        self.users.update_profile(update.effective_user.id, height_cm=float(height))
        await update.effective_message.reply_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "weight")

    async def on_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        weight = self._parse_number(update, update.effective_message.text or "")
        if weight is None or not (30 <= weight <= 300):
            await update.effective_message.reply_text(
                "Ese peso no parece válido. Escribe los kilos (ej. <code>78.4</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return WEIGHT
        user = update.effective_user
        self.users.update_profile(user.id, weight_kg=float(weight))
        self.db.add_weight(user.id, float(weight), iso(utcnow()))
        await update.effective_message.reply_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "bodyfat")

    async def on_bodyfat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.effective_message.text or "").strip().replace(",", ".")
        value = self._parse_number(update, raw)
        if value is not None and 3 <= value <= 60:
            self.users.update_profile(update.effective_user.id, body_fat_pct=float(value))
            await update.effective_message.reply_text(
                "✅ Guardado. Con el % de grasa afino tu mantenimiento (Katch-McArdle).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.effective_message.reply_text(
                "No he entendido el porcentaje. Escribe un número (ej. <code>18</code>) "
                "o vuelve a la pregunta.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return BODYFAT
        return await self._ask(update, context, "training")

    async def cb_bodyfat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """'No lo sé' -> estimacion; 'Usar estimado' -> estimacion."""
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        profile = self.users.profile(user.id)
        estimate = estimate_body_fat_pct(
            str(profile.get("sex") or "other"),
            float(profile.get("weight_kg") or 0),
            float(profile.get("height_cm") or 0),
            int(profile.get("age") or 30),
        )
        self.users.update_profile(user.id, body_fat_pct=estimate)
        await query.edit_message_text(
            f"✅ Estimado: <b>{estimate:.1f} %</b> de grasa. "
            "Es una aproximación; si consigues una medida real (DEXA o pliegues), "
            "me la puedes dar luego.",
            parse_mode=ParseMode.HTML,
        )
        return await self._ask(update, context, "training")

    async def cb_training(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        days = int(query.data.split(":")[-1])
        self.users.update_profile(update.effective_user.id, training_days=days)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "steps")

    async def cb_steps(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        steps = int(query.data.split(":")[-1])
        self.users.update_profile(update.effective_user.id, daily_steps=steps)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "job")

    async def cb_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        job = query.data.split(":")[-1]
        self.users.update_profile(update.effective_user.id, job_activity=job)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "activity")

    async def cb_activity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        activity = query.data.split(":")[-1]
        self.users.update_profile(update.effective_user.id, activity=activity)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "goal")

    async def cb_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        goal = query.data.split(":")[-1]
        user = update.effective_user
        self.users.update_profile(user.id, goal=goal)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "target")

    async def on_target_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = self._parse_number(update, update.effective_message.text or "")
        user = update.effective_user
        if value and 30 <= value <= 300:
            self.users.update_profile(user.id, target_weight_kg=float(value))
        self.users.update_profile(user.id, onboarded=1, onboard_step=None)
        profile = self.users.profile(user.id)
        target = self.users.daily_target(user.id)
        text = "✅ <b>Perfil completo</b>\n\n"
        text += self._profile_summary(profile) + "\n"
        if target:
            text += (
                f"<b>Mantenimiento: {target.maintenance_kcal} kcal/d\u00eda</b>\n"
                f"Objetivo diario: <b>{target.daily_kcal} kcal</b> "
                f"(deficit de {target.deficit_kcal} kcal)\n"
                f"\U0001f9c1 {target.macro.protein_g:.0f} g · \U0001f35a {target.macro.carbs_g:.0f} g · "
                f"\U0001f951 {target.macro.fat_g:.0f} g\n"
            )
            for note in target.notes:
                text += f"\n\u26a0\ufe0f {esc(note)}"
        text += (
            "\n\n<b>Siguiente paso:</b> env\u00edame una foto de tu plato y te dir\u00e9 "
            "sus calor\u00edas."
        )
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )
        return ConversationHandler.END

    def _parse_number(self, update: Update, text: str | None = None) -> float | None:
        raw = (text if text is not None else (update.effective_message.text or ""))
        raw = str(raw).strip().replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            return None

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text(
            "Configuración cancelada. Puedes retomarla con <code>/perfil</code>.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    # -- menu -------------------------------------------------------------
    async def cb_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Menu de botones: cada boton hace lo mismo que su comando."""
        query = update.callback_query
        await query.answer()
        action = query.data.split(":")[-1]
        user = update.effective_user
        if not user or not self.users.is_registered(user.id):
            await query.edit_message_text(
                "🔒 Necesitas un token para usar el bot. Escribe <code>/start</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "resumen":
            await self._send_today(update)
        elif action == "semana":
            await self._send_week(update)
        elif action == "historial":
            await self._send_history(update, 7)
        elif action == "peso":
            await self._send_weight(update)
        elif action == "meta":
            await self._send_progress(update)
        elif action == "plan":
            await self._send_plan(update)
        elif action == "consejo":
            await self._send_advice(update)
        elif action == "foto":
            await self._send_foto_prompt(update)
        elif action == "anotar":
            await self._send_manual_prompt(update)
        elif action == "privacidad":
            await self._send_privacy(update)
        elif action == "exportar":
            await self._send_export(update)
        elif action == "perfil":
            await self.cmd_profile(update, context)
        else:
            await query.edit_message_text(
                "No se qué hacer eso.", parse_mode=ParseMode.HTML
            )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "<b>🥗 Cómo usar el bot</b>\n\n"
            "1️⃣ Activa tu cuenta con <code>/token NTR1-...</code>\n"
            "2️⃣ Completa tu perfil con <code>/perfil</code>\n"
            "3️⃣ Envía una foto de cada plato\n\n"
            "<b>Comandos</b>\n"
            "/foto — instrucciones para analizar\n"
            "/hoy — resumen del día y macros\n"
            "/historial [días] — últimas comidas\n"
            "/peso [kg] — registra y consulta tu peso\n"
            "/meta — progreso hacia tu objetivo\n"
            "/plan — plan de comidas del día\n"
            "/consejo — consejo nutricional\n"
            "/anotar plato | kcal — registro manual\n"
            "/corregir [kcal] — corrige las calorías del último plato\n"
            "/borrar — elimina la última comida\n"
            "/privacidad — qué datos guardo de ti\n"
            "/exportar — descarga tu historial (CSV)\n"
            "/ayuda — este mensaje",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
