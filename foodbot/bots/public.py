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
import base64
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
from foodbot.core.nutrition import (
    ACTIVITY_LEVELS,
    KCAL_PER_KG_FAT,
    activity_label,
    estimate_body_fat_pct,
)
from foodbot.core.privacy import PhotoStore, erase_user_data, storage_report
from foodbot.core.advice import specific_tips
from foodbot.core.feedback import meal_feedback
from foodbot.core.mealplan import build_meal_plan
from foodbot.core.label import (
    LABEL_PROMPT,
    LABEL_SYSTEM,
    WEIGHT_PROMPT,
    label_to_portion,
    parse_label,
    parse_weight,
)
from foodbot.core.products import describe, find_product, parse_request
from foodbot.core.sleep import (
    SleepNight,
    late_snack_explanation,
    sleep_summary,
)
from foodbot.core.stickers import sticker_for
from foodbot.core.text import (
    accumulated_message,
    advice_message,
    analysis_message,
    esc,
    goal_label,
    history_message,
    meal_plan_details,
    meal_plan_message,
    privacy_message,
    progress_bar,
    progress_message,
    target_line,
    today_message,
    week_line,
    weekly_message,
)
from foodbot.core.timeutil import format_local_date, local_now
from foodbot.core.tokens import TokenService
from foodbot.core.users import SEXES, UserService

log = logging.getLogger("foodbot.public")

# Estados del asistente de configuracion del perfil.
(
    NAME,
    SEX,
    HORMONES,
    PRONOUNS,
    LIKES,
    AVOID,
    GENTLE,
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
) = range(17)

PRONOUN_LABELS = {
    "male": "él",
    "female": "ella",
    "neutral": "en otro modo",
}

JOB_LABELS = {
    "sedentary": "De pie, poco movimiento",
    "light": "Trabajo a pie o activo",
    "physical": "Trabajo fisico pesado",
}

# Las preguntas del asistente, en el orden en el que se hacen. El "kind" dice
# como se responde: texto libre o boton.
PROFILE_QUESTIONS: dict[str, tuple[str, str]] = {
    "name": ("<b>\u00bfC\u00f3mo te llamas?</b>\n<i>Escribe tu nombre o el apodo que prefieras.</i>", "text"),
    "sex": (
        "<b>\u00bfCon qu\u00e9 referencia te calculo el metabolismo?</b>\n"
        "<i>Es solo para los n\u00fameros (las kcal y el suelo de seguridad). "
        "Si me das tu % de grasa, el c\u00e1lculo va por composici\u00f3n corporal, "
        "que es lo m\u00e1s preciso y no depende de esta respuesta. El c\u00f3mo "
        "te llamo ser\u00e1 como me lo pidas despu\u00e9s.</i>",
        "sex",
    ),
    "hormones": (
        "<b>\u00bfSigues alg\u00fan tratamiento hormonal?</b>\n"
        "<i>Cambia el metabolismo y la composici\u00f3n corporal, asi que lo "
        "tengo en cuenta para afinar tus n\u00fameros y tu consejo. Es "
        "opcional.</i>",
        "hormones",
    ),
    "pronouns": (
        "<b>\u00bfC\u00f3mo prefieres que te llame?</b>\n"
        "<i>Solo para darte el consejo que corresponde (hierro, calcio, "
        "prote\u00edna\u2026). Puedes saltarlo.</i>",
        "pronouns",
    ),
    "likes": (
        "<b>\u00bfQu\u00e9 te gusta?</b>\n"
        "<i>Sep\u00e1ralo con comas (ej. <code>arroz, pollo, pescado</code>). Lo "
        "uso para proponerte alternativas que te apetzcan cuando te quede "
        "hambre. Es opcional.</i>",
        "text",
    ),
    "avoid": (
        "<b>\u00bfHay algo que no te guste o no puedas comer?</b>\n"
        "<i>Sep\u00e1ralo con comas (ej. <code>lacteos, marisco, gluten</code>). "
        "No te voy a proponer nunca esas cosas. Es opcional.</i>",
        "text",
    ),
    "gentle": (
        "<b>\u00bfHay alg\u00fan motivo para que no te calcule un d\u00e9ficit?</b>\n"
        "<i>Si has tenido trastornos de la alimentaci\u00f3n, est\u00e1s "
        "embarazada, tomas medicaci\u00f3n o prefieres no contar, dime lo y "
        "te dejo en <b>modo suave</b>: sin objetivos, sin cuentas y sin "
        "estickers. Puedes cambiarlo cuando quieras.</i>",
        "gentle",
    ),
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
    "hormones": HORMONES,
    "pronouns": PRONOUNS,
    "likes": LIKES,
    "avoid": AVOID,
    "gentle": GENTLE,
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
                InlineKeyboardButton("🍽 Mis comidas", callback_data="menu:miscomidas"),
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


def hormone_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Ninguno", callback_data="perfil:hormone:none"),
                InlineKeyboardButton("Estrógenos", callback_data="perfil:hormone:estrogen"),
            ],
            [
                InlineKeyboardButton(
                    "Testosterona", callback_data="perfil:hormone:testosterone"
                ),
                InlineKeyboardButton("Otro", callback_data="perfil:hormone:other"),
            ],
            [InlineKeyboardButton("\u21e9 Saltar", callback_data="perfil:skip:pronouns")],
        ]
    )


def gentle_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "No, calc\u00falame el d\u00e9ficit", callback_data="perfil:gentle:no"
                )
            ],
            [
                InlineKeyboardButton(
                    "Prefiero solo registrar",
                    callback_data="perfil:gentle:yes",
                )
            ],
            [InlineKeyboardButton("\u21e9 Saltar", callback_data="perfil:skip:age")],
        ]
    )


def pronoun_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u00c9l", callback_data="perfil:pronoun:male"),
                InlineKeyboardButton("Ella", callback_data="perfil:pronoun:female"),
            ],
            [
                InlineKeyboardButton(
                    "En otro modo", callback_data="perfil:pronoun:neutral"
                ),
                InlineKeyboardButton("\u21e9 Saltar", callback_data="perfil:skip:age")],
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
    "🍽 Mis comidas": "miscomidas",
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
                KeyboardButton("🍽 Mis comidas"),
            ],
            [
                KeyboardButton("📊 Hoy"),
                KeyboardButton("🗓 Semana"),
            ],
            [
                KeyboardButton("⚖️ Peso"),
                KeyboardButton("⚙️ Perfil"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="📸 Envía una foto de tu plato…",
    )


def reference_keyboard() -> InlineKeyboardMarkup:
    """La referencia metabolica: para los numeros, no para identificarte."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "La de un hombre", callback_data="perfil:sex:male"
                ),
                InlineKeyboardButton(
                    "La de una mujer", callback_data="perfil:sex:female"
                ),
            ],
            [
                InlineKeyboardButton(
                    "La intermedia", callback_data="perfil:sex:other"
                ),
                InlineKeyboardButton(
                    "No lo se: usa mi % de grasa", callback_data="perfil:sex:unknown"
                ),
            ],
        ]
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


def slot_keyboard(meal_id: int) -> InlineKeyboardMarkup:
    """Elegir a que franja pertenece la comida."""
    from foodbot.core.timeutil import SLOT_LABELS

    buttons = [
        InlineKeyboardButton(
            SLOT_LABELS[slot], callback_data=f"slot:{meal_id}:{slot}"
        )
        for slot in SLOT_LABELS
    ]
    rows = [buttons[:2], buttons[2:4], buttons[4:6], buttons[6:]]
    return InlineKeyboardMarkup([row for row in rows if row])


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
                InlineKeyboardButton("🕐 ¿Qué comida es?", callback_data=f"slot:{meal_id}"),
                InlineKeyboardButton("🏷️ Producto con etiqueta", callback_data=f"etiqueta:{meal_id}"),
            ],
            [
                InlineKeyboardButton("🗑 Borrar", callback_data=f"borrar:{meal_id}"),
                InlineKeyboardButton("📊 Ver mi día", callback_data="menu:resumen"),
            ],
        ]
    )


class _EmptyLabel:
    """Etiqueta vacia (cuando el producto no venia de una etiqueta)."""

    kcal_100g = None
    protein_100g = None
    carbs_100g = None
    fat_100g = None
    net_weight_g = None
    serving_g = None


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
                HORMONES: [
                    CallbackQueryHandler(self.cb_hormone, pattern=r"^perfil:hormone:")
                ] + skip,
                PRONOUNS: [
                    CallbackQueryHandler(self.cb_pronoun, pattern=r"^perfil:pronoun:")
                ] + skip,
                LIKES: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_likes)
                ] + skip,
                AVOID: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_avoid)
                ] + skip,
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
        self.app.add_handler(CommandHandler("objetivo", self.cmd_objetivo))
        self.app.add_handler(CommandHandler("dormir", self.cmd_sleep))
        self.app.add_handler(CommandHandler("suave", self.cmd_gentle))
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
        self.app.add_handler(CallbackQueryHandler(self.cb_objetivo, pattern=r"^objetivo:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_meal_slot, pattern=r"^slot:"))
        self.app.add_handler(CallbackQueryHandler(self.cb_product, pattern=r"^producto:"))
        self.app.add_handler(CommandHandler("miscomidas", self.cmd_my_meals))
        self.app.add_handler(
            CallbackQueryHandler(
                self.cb_my_meal, pattern=r"^mismeal:(usar|borrar|ayer|copiar|nueva|gestionar)"
            )
        )
        self.app.add_handler(
            CallbackQueryHandler(self.cb_save_my_meal, pattern=r"^mismeal:guardar:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self.cb_label_confirm, pattern=r"^etiqueta:(si|no)$")
        )
        self.app.add_handler(
            CallbackQueryHandler(self.cb_label, pattern=r"^etiqueta:\d+$")
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
        lines.append(
            f"📓 Llevas <b>{streak} día(s)</b> seguidos registrando comidas. "
            "No hace falta la racha: lo que cuenta es la semana."
        )
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

        # Si estaba esperando la etiqueta de un producto, esta foto no es una
        # comida: es la etiqueta.
        pending_label = context.user_data.pop("await_label", None)
        if pending_label:
            try:
                await self._read_label(update, context, int(pending_label))
            except Exception as exc:  # noqa: BLE001
                log.warning("Fallo leyendo la etiqueta: %s", exc)
                await message.reply_text(
                    "No he podido procesar la etiqueta. Inténtalo otra vez.",
                    parse_mode=ParseMode.HTML,
                )
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

            # --- 4b. GUARDAR EN MIS COMIDAS (para no volver a hacer foto) --
            await self._safe_send(
                update,
                "\u2b50 \u00bfLa guardo en <b>Mis comidas</b> para la pr\u00f3xima vez?",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "\u2b50 Guardar en mis comidas",
                                callback_data=f"mismeal:guardar:{meal_id}",
                            ),
                            InlineKeyboardButton(
                                "\u2716 No", callback_data="mismeal:guardar:no"
                            ),
                        ]
                    ]
                ),
            )

            # --- 4c. EL STICKER: COMO VA EL DIA --------------------------
            # Sin reproches y siempre con la semana como contexto: un dia
            # tonto no es un desastre, el margen esta ahi.
            consumed_before = max(0, int(summary["calories"]) - int(analysis.calories))
            feedback = self._feedback_for(
                user.id, int(analysis.calories), consumed_before
            )
            await self._send_feedback(message, feedback)
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

        # 3b) ¿Esta guardando una comida nueva en la biblioteca?
        if context.user_data.get("await_my_meal"):
            await self.on_my_meal_text(update, context)
            return

        # 4) ¿Es un producto del catalogo? ("3 magdalenas del mercadona")
        if self._authorized(update):
            request = parse_request(text)
            if request:
                match = self._find_product(update.effective_user.id, request)
                if match:
                    await self._propose_product(update, context, request, match)
                    return
            await update.effective_message.reply_text(
                "👋 Usa el menú de abajo. <i>Para analizar una comida, "
                "envía una foto, o dime el producto: "
                "<code>3 magdalenas del mercadona</code>.</i>",
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
        elif action == "miscomidas":
            await self._send_my_meals(update)
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
        accumulated = self.users.accumulated_savings(user.id)
        await self._safe_send(
            update,
            today_message(
                summary,
                self.users.tz,
                self._quota_snapshot(user.id),
                week=week,
                accumulated=accumulated,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def _send_week(self, update: Update) -> None:
        user = update.effective_user
        week = self.users.week_summary(user.id)
        accumulated = self.users.accumulated_savings(user.id)
        text = weekly_message(week)
        block = accumulated_message(accumulated)
        if block:
            text = f"{text}\n\n{block}"
        await self._safe_send(
            update,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\U0001f3af Fijar mis kcal", callback_data="objetivo:menu"
                        ),
                        InlineKeyboardButton(
                            "\u2b05\ufe0f Men\u00fa", callback_data="menu:resumen"
                        ),
                    ]
                ]
            ),
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

    # -- objetivo manual de calorias ---------------------------------------
    async def cmd_objetivo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """El usuario fija su propio objetivo diario de calorias.

        El bot calcula y explica, pero no impone: solo se niega a bajar del
        suelo de seguridad (1200/1500/1400), que es el unico limite con
        respaldo clinico claro.
        """
        if not await self._require_auth(update):
            return
        if context.args:
            await self._propose_target(update, context, " ".join(context.args))
            return
        await self._send_target_info(update)

    async def _send_target_info(self, update: Update) -> None:
        """Muestra el mantenimiento, lo que sugiere el bot y lo que has fijado."""
        user = update.effective_user
        profile = self.users.profile(user.id)
        target = self.users.daily_target(user.id)
        if not target:
            await self._safe_send(
                update,
                "\u2699\ufe0f Completa tu perfil primero (<code>/perfil</code>).",
                parse_mode=ParseMode.HTML,
            )
            return

        manual = profile.get("daily_kcal_override")
        lines = ["<b>\U0001f3af Tus calor\u00edas</b>"]
        lines.append(
            f"Mantenimiento: <b>{target.maintenance_kcal} kcal/d\u00eda</b> "
            f"(BMR {target.bmr} \u00b7 TDEE {target.tdee})"
        )
        if manual:
            lines.append(
                f"Tu objetivo: <b>{int(manual)} kcal/d\u00eda</b> (lo has fijado t\u00fa)"
            )
            # La palabra va fuera de la f-string: dentro de las llaves no
            # puede haber barras invertidas (solo Python 3.12+ lo permite).
            direction = "menos" if target.deficit_kcal > 0 else "m\u00e1s"
            lines.append(
                f"Eso son {abs(target.deficit_kcal)} kcal {direction} que tu "
                f"mantenimiento: ~{abs(target.weekly_goal_kcal) / 7 / KCAL_PER_KG_FAT:.2f} kg/semana."
            )
        else:
            lines.append(
                f"Objetivo sugerido: <b>{target.daily_kcal} kcal/d\u00eda</b> "
                "(un deficit de 500-750 kcal, el rango que recomiendan las gu\u00edas)"
            )
        lines.append(
            "\n<i>Puedes fijar el que quieras: el bot te dir\u00e1 qu\u00e9 "
            "significa en cada caso. El \u00fanico l\u00edmite es el suelo de "
            f"seguridad ({target.floor_kcal} kcal).</i>"
        )

        buttons = [
            [
                InlineKeyboardButton(
                    "\U0001f3af Fijar mis kcal", callback_data="objetivo:menu"
                )
            ]
        ]
        if manual:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "\u21a9 Volver al autom\u00e1tico", callback_data="objetivo:auto"
                    )
                ]
            )
        buttons.append([InlineKeyboardButton("\u2b05\ufe0f Men\u00fa", callback_data="menu:resumen")])
        await self._safe_send(
            update, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
        )

    async def _propose_target(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str
    ) -> None:
        """Valida lo que el usuario escribe y explica lo que significa."""
        user = update.effective_user
        profile = self.users.profile(user.id)
        value = self._parse_number(update, raw)
        if value is None or not (600 <= value <= 6000):
            await self._safe_send(
                update,
                "No he entendido las calor\u00edas. Escribe un n\u00famero entre 600 y 6000 "
                "(ej. <code>2200</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return

        chosen = int(round(value))
        target = self.users.daily_target(user.id)
        if not target:
            await self._safe_send(
                update,
                "\u2699\ufe0f Completa tu perfil primero (<code>/perfil</code>).",
                parse_mode=ParseMode.HTML,
            )
            return

        floor = int(target.floor_kcal)
        if chosen < floor:
            await self._safe_send(
                update,
                f"\u26a0\ufe0f <b>No te dejo bajar de {floor} kcal/d\u00eda.</b>\n\n"
                f"Ese es el suelo de seguridad que recomiendan las gu\u00edas cl\u00ednicas "
                f"(Harvard Health, Alberta Health Services): por debajo hay riesgo de "
                f"d\u00e9ficit de nutrientes y de problemas de ves\u00edcula.\n\n"
                f"Si necesitas comer menos de {floor} kcal, hazlo con un profesional "
                f"sanitario. Con {floor} kcal ya estar\u00edas haciendo un d\u00e9ficit grande.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("\u2b05\ufe0f Men\u00fa", callback_data="menu:resumen")]]
                ),
            )
            return

        deficit = target.maintenance_kcal - chosen
        kg_week = abs(deficit) * 7 / KCAL_PER_KG_FAT
        if deficit > 0:
            meaning = (
                f"Eso son <b>{deficit} kcal menos</b> que tu mantenimiento "
                f"({target.maintenance_kcal} kcal): unas <b>{kg_week:.2f} kg por semana</b>."
            )
        elif deficit < 0:
            meaning = (
                f"Eso son <b>{-deficit} kcal m\u00e1s</b> que tu mantenimiento: "
                f"<b>subir\u00edas</b> de peso (~{kg_week:.2f} kg/semana)."
            )
        else:
            meaning = (
                "Eso es exactamente tu mantenimiento: <b>no cambiar\u00edas de peso</b>. "
                "Es lo que se llama una pausa de dieta, y las pausas a mantenimiento "
                "ayudan a que la perdida no se atasque."
            )

        warning = ""
        if deficit > 0 and target.maintenance_kcal and deficit > target.maintenance_kcal * 0.25:
            warning = (
                "\n\n\u26a0\ufe0f <i>Ojo: es un d\u00e9ficit de m\u00e1s del 25% de tu "
                "mantenimiento. Puede frenar la perda y es dif\u00edcil de sostener; las "
                "pausas a mantenimiento ayudan.</i>"
            )

        context.user_data["pending_kcal"] = chosen
        await self._safe_send(
            update,
            f"<b>\U0001f3af Fijar {chosen} kcal/d\u00eda</b>\n\n"
            f"{meaning}{warning}\n\n"
            "<i>\u00bfLo confirmas?</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"\u2705 S\u00ed, {chosen} kcal",
                            callback_data=f"objetivo:si:{chosen}",
                        ),
                        InlineKeyboardButton(
                            "\u274c Cancelar", callback_data="objetivo:no"
                        ),
                    ]
                ]
            ),
        )

    async def cb_objetivo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Confirma, cancela o borra el objetivo manual."""
        query = update.callback_query
        await query.answer()
        action = query.data.split(":")[1] if ":" in query.data else ""
        user = update.effective_user

        if action == "menu":
            await self._send_target_info(update)
            return

        if action == "no":
            context.user_data.pop("pending_kcal", None)
            await self._safe_send(
                update,
                "\U0001f44c Nada, dejo tu objetivo como estaba.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            return

        if action == "auto":
            self.users.update_profile(user.id, daily_kcal_override=None)
            await self._safe_send(
                update,
                "\u2705 Vuelvo al objetivo autom\u00e1tico.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            return

        if action == "si":
            raw = query.data.split(":")[-1]
            try:
                chosen = int(raw)
            except ValueError:
                chosen = int(context.user_data.pop("pending_kcal", 0) or 0)
            context.user_data.pop("pending_kcal", None)
            if not chosen:
                await self._safe_send(update, "No he guardado nada.", parse_mode=ParseMode.HTML)
                return
            self.users.update_profile(user.id, daily_kcal_override=chosen)
            target = self.users.daily_target(user.id)
            extra = ""
            if target:
                extra = (
                    f"\n\nMantenimiento: {target.maintenance_kcal} kcal/d\u00eda\n"
                    f"Tu objetivo: <b>{chosen} kcal/d\u00eda</b>\n"
                    f"Eso es ~{abs(target.weekly_goal_kcal) / 7 / KCAL_PER_KG_FAT:.2f} kg/semana."
                )
            await self._safe_send(
                update,
                f"\u2705 <b>Objetivo fijado: {chosen} kcal/d\u00eda</b>{extra}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )

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

        # Sin barras: puede ser un producto del catalogo ("3 magdalenas del
        # mercadona"), que se calcula con la etiqueta y el peso de la porcion.
        if "|" not in raw:
            request = parse_request(raw)
            if request:
                match = self._find_product(update.effective_user.id, request)
                if match:
                    await self._propose_product(update, context, request, match)
                    return
                await self._offer_new_product(update, context, request)
                return
            await update.effective_message.reply_text(
                "Uso: <code>/anotar nombre del plato | kcal</code>\n"
                "<i>O dime el producto tal cual: <code>3 magdalenas del mercadona</code> "
                "(si lo tengo en el catalogo, te calculo las kcal).</i>",
                parse_mode=ParseMode.HTML,
            )
            return
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

        # El sticker de como va el dia, igual que con las fotos.
        summary = self.users.today_summary(user.id)
        consumed_before = max(0, int(summary["calories"]) - calories)
        feedback = self._feedback_for(user.id, calories, consumed_before)
        await self._send_feedback(update.effective_message, feedback)

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

    # -- sueño ---------------------------------------------------------------
    async def cmd_sleep(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Registrar y ver como se duerme.

        El sueño manda sobre el hambre: dormir poco sube la ingesta (mas de
        250 kcal/dia segun Nature Reviews Endocrinology, 2022) y alargar el
        sueño 1,2 h la bajo unos 270 kcal (Tasali et al., JAMA Intern Med
        2022). Por eso se apunta: para entender los picuelos de madrugada.
        """
        if not await self._require_auth(update):
            return
        user = update.effective_user
        text = " ".join(context.args or []).strip()

        if not text:
            await self._send_sleep(update)
            return

        parsed = self._parse_sleep_times(text)
        if not parsed:
            await update.effective_message.reply_text(
                "No he entendido las horas. Escr\u00edbeme por ejemplo "
                "<code>23:30 - 07:15</code> (o <code>23:30 7:15</code>).",
                parse_mode=ParseMode.HTML,
            )
            return

        bedtime, waketime = parsed
        now = utcnow()
        hours = (waketime - bedtime).total_seconds() / 3600.0
        if hours <= 0:
            hours += 24
        if not (0 < hours <= 16):
            await update.effective_message.reply_text(
                "Eso no puede ser una noche. \u00bfTe acostaste a las "
                f"{bedtime.strftime('%H:%M')} y te levantaste a las "
                f"{waketime.strftime('%H:%M')}?",
                parse_mode=ParseMode.HTML,
            )
            return

        day = local_now(self.users.tz).strftime("%Y-%m-%d")
        self.db.add_sleep(
            user.id, day, iso(bedtime), iso(waketime), round(hours, 2)
        )
        await update.effective_message.reply_text(
            f"\U0001f634 Apuntado: <b>{hours:.1f} h</b> de sueño "
            f"({bedtime.strftime('%H:%M')} \u2192 {waketime.strftime('%H:%M')}).",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    def _parse_sleep_times(self, text: str):
        """'23:30 - 07:15' -> (datetime, datetime)."""
        import re
        from datetime import datetime, timedelta

        found = re.findall(r"(\d{1,2})[:.h]?(\d{2})?", text)
        if len(found) < 2:
            return None
        try:
            h1, m1 = int(found[0][0]), int(found[0][1] or 0)
            h2, m2 = int(found[1][0]), int(found[1][1] or 0)
        except ValueError:
            return None
        if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
            return None
        now = datetime.now()
        bedtime = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        waketime = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
        if waketime <= bedtime:
            waketime += timedelta(days=1)
        return bedtime, waketime

    def _sleep_nights(self, user_id: int, limit: int = 14) -> list[SleepNight]:
        """Las ultimas noches registradas (en hora local)."""
        nights: list[SleepNight] = []
        for row in self.db.recent_sleep(user_id, limit):
            try:
                from foodbot.core.nutrition import parse_iso

                bedtime = parse_iso(row["bedtime"]).astimezone(self.users.tz)
                waketime = parse_iso(row["waketime"]).astimezone(self.users.tz)
            except (KeyError, ValueError):
                continue
            nights.append(SleepNight(bedtime=bedtime, waketime=waketime))
        return nights

    async def _send_sleep(self, update: Update) -> None:
        """Como se duerme: media, regularidad y que dice de la comida."""
        user = update.effective_user
        nights = self._sleep_nights(user.id)
        summary = sleep_summary(nights)
        lines = ["<b>\U0001f634 Tu sueño</b>"]

        if not summary["nights"]:
            lines.append(
                "Todavía no has registrado ninguna noche. \U0001f4d3 "
                "<code>/dormir 23:30 - 07:15</code>"
            )
        else:
            lines.append(
                f"Últimas {summary['nights']} noches: <b>{summary['avg_hours']:.1f} h</b> "
                f"de media ({summary['quality']})."
            )
            if summary["short"]:
                lines.append(
                    "\U0001f6f1 Con menos de 6 h, la ingesta sube de media unos 250 "
                    "kcal al día (Nature Reviews Endocrinology, 2022). No es que "
                    "te falte fuerza de voluntad: es el sueño."
                )
            if summary["irregular"]:
                lines.append(
                    f"Tu hora de acostarte varía \u00b1{summary['regularity']:.1f} h. La "
                    "regularidad importa tanto como la cantidad: el cuerpo "
                    "funciona por reloj."
                )
            else:
                lines.append("\u2705 Horario bastante regular.")

        explanation = None
        try:
            summary_today = self.users.today_summary(user.id)
            explanation = late_snack_explanation(
                summary_today.get("meals") or [], nights, self.users.tz
            )
        except Exception:  # noqa: BLE001
            explanation = None
        if explanation:
            lines.append(f"\n{explanation}")

        lines.append(
            "\n<i>Apunta la noche con <code>/dormir 23:30 - 07:15</code>. "
            "Con dos semanas te digo si tu horario es el problema.</i>"
        )
        await self._safe_send(
            update, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )

    # -- catalogo de productos ----------------------------------------------
    def _find_product(self, user_id: int, request):
        """Busca el producto: primero los del usuario, luego la tabla."""
        rows = [dict(row) for row in self.db.list_products(user_id)]
        return find_product(request, rows)

    async def _propose_product(self, update, context, request, match) -> None:
        """Muestra el calculo del producto y pide confirmacion."""
        info = describe(match, request)
        from foodbot.core.products import estimate_weight

        if not info["grams"]:
            weight = estimate_weight(request)
            if weight:
                info = describe(match, request)
                info["grams"] = round(weight * request.quantity, 1)
                info["calories"] = round(match.kcal_100g * info["grams"] / 100.0)
                info["protein_g"] = round(match.protein_100g * info["grams"] / 100.0, 1)
                info["carbs_g"] = round(match.carbs_100g * info["grams"] / 100.0, 1)
                info["fat_g"] = round(match.fat_100g * info["grams"] / 100.0, 1)
                info["exact"] = True

        context.user_data["pending_product"] = {
            "name": match.name,
            "brand": match.brand,
            "quantity": request.quantity,
            "grams": info["grams"],
            "calories": info["calories"],
            "protein_g": info["protein_g"],
            "carbs_g": info["carbs_g"],
            "fat_g": info["fat_g"],
        }
        brand = f" de {match.brand}" if match.brand else ""
        where = "tu etiqueta" if match.source == "producto" else "la tabla"
        text = (
            f"\U0001f9ea <b>{request.quantity:g} x {match.name}{brand}</b>\n"
            f"Con la informacion de {where}: "
            + (
                f"<b>{info['grams']:.0f} g</b> en total -> <b>{info['calories']} kcal</b>"
                if info["grams"]
                else f"~<b>{info['calories']} kcal</b> (peso estimado)"
            )
            + "\n"
            f"\U0001f9c1 P {info['protein_g']} g \u00b7 \U0001f35a C {info['carbs_g']} g "
            f"\u00b7 \U0001f951 G {info['fat_g']} g\n\n"
            "<i>\u00bfLo anoto?</i>"
        )
        await self._safe_send(
            update,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("\u2705 S\u00ed, an\u00f3talo", callback_data="producto:si"),
                        InlineKeyboardButton("\u274c Cancelar", callback_data="producto:no"),
                    ]
                ]
            ),
        )

    async def _offer_new_product(self, update, context, request) -> None:
        """No conozco el producto: le pido la etiqueta y lo doy de alta."""
        context.user_data["pending_new_product"] = {
            "name": request.name,
            "brand": request.brand,
        }
        await self._safe_send(
            update,
            f"\U0001f9ea No tengo <b>{esc(request.name)}</b> en el catalogo.\n\n"
            "M\u00e1ndame una foto de la etiqueta (la de informaci\u00f3n nutricional) y "
            "el peso, y lo doy de alta: la pr\u00f3xima vez te saldr\u00e1 al "
            "instante.\n\n"
            "<i>Si no tienes la etiqueta, dime las kcal por 100 g y el peso de una "
            "unidad.</i>",
            parse_mode=ParseMode.HTML,
        )

    async def cb_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Confirma el producto del catalogo y lo guarda."""
        query = update.callback_query
        await query.answer()
        action = query.data.split(":")[-1]
        pending = context.user_data.pop("pending_product", None)
        if action != "si" or not pending:
            await self._safe_send(update, "Vale, no lo anoto.", parse_mode=ParseMode.HTML)
            return

        user = update.effective_user
        now = utcnow()
        dish = f"{pending['quantity']:g} x {pending['name']}"
        if pending.get("brand"):
            dish += f" de {pending['brand']}"
        self.db.add_meal(
            user_id=user.id,
            created_at=iso(now),
            eaten_at=iso(now),
            meal_slot=self._slot_for(now),
            dish=dish[:120],
            calories=int(pending["calories"]),
            protein_g=float(pending["protein_g"]),
            carbs_g=float(pending["carbs_g"]),
            fat_g=float(pending["fat_g"]),
            mass_g=pending.get("grams") or 0,
            ingredients="[]",
            raw_answer="",
            confidence=1.0,
            engine="catalogo",
        )
        await self._safe_send(
            update,
            f"\u2705 Anotado: <b>{esc(dish)}</b> — {int(pending['calories'])} kcal",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cmd_gentle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Activar o desactivar el modo suave (sin deficit ni cuentas).

        Se puede cambiar cuando quieras: no hace falta pasar por el perfil.
        """
        if not await self._require_auth(update):
            return
        user = update.effective_user
        profile = self.users.profile(user.id)
        gentle = not bool(profile.get("gentle_mode"))
        self.users.update_profile(user.id, gentle_mode=1 if gentle else 0)
        if gentle:
            await update.effective_message.reply_text(
                "\U0001f91a <b>Modo suave activado.</b>\n\n"
                "Sin deficit, sin cuentas y sin stickers que juzguen: solo se "
                "guarda lo que comes. Lo puedes cambiar cuando quieras con "
                "<code>/suave</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
        else:
            await update.effective_message.reply_text(
                "Modo suave desactivado: vuelvo a calcularte el objetivo.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )

    # -- biblioteca personal de comidas -------------------------------------
    async def cmd_my_meals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Tu biblioteca: lo que ya tienes medido, para apuntarlo sin foto."""
        if not await self._require_auth(update):
            return
        await self._send_my_meals(update)

    async def _send_my_meals(self, update: Update, manage: bool = False) -> None:
        """La lista de tus comidas guardadas, agrupadas por franja."""
        user = update.effective_user
        from foodbot.core.timeutil import SLOT_ORDER

        rows = [dict(row) for row in self.db.list_my_meals(user.id)]
        # Las que mas usas, primero; dentro de cada franja, tambien.
        rows.sort(
            key=lambda r: (
                SLOT_ORDER.index(r["slot"]) if r.get("slot") in SLOT_ORDER else 99,
                -int(r.get("times_used") or 0),
                str(r.get("name") or ""),
            )
        )
        buttons: list[list[InlineKeyboardButton]] = []

        if not rows:
            await self._safe_send(
                update,
                "<b>🍽 Mis comidas</b>\n\n"
                "Aqu\u00ed guardas lo que ya tienes medido (tu desayuno de "
                "siempre, un batido, tu cena habitual) para apuntarlo en dos "
                "toques, sin foto.\n\n"
                "<i>Cuando apuntes una comida, pulsa <b>⭐ Guardar en mis "
                "comidas</b> y la pr\u00f3xima vez estar\u00e1 aqu\u00ed.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "➕ Añadir nueva", callback_data="mismeal:nueva"
                            )
                        ],
                        [InlineKeyboardButton("⬅️ Menú", callback_data="menu:resumen")],
                    ]
                ),
            )
            return

        if manage:
            for row in rows:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🗑 {row['name']} · {int(row['calories'] or 0)} kcal",
                            callback_data=f"mismeal:borrar:{row['id']}",
                        )
                    ]
                )
            buttons.append(
                [
                    InlineKeyboardButton("✖ Volver", callback_data="menu:miscomidas"),
                    InlineKeyboardButton("➕ Añadir", callback_data="mismeal:nueva"),
                ]
            )
            await self._safe_send(
                update,
                "<b>🍽 Mis comidas</b>\n<i>Pulsa una para borrarla.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # Lo de ayer: el camino m\u00e1s r\u00e1pido para lo que se repite.
        yesterday = self._yesterday_meals(user.id)
        if yesterday:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "🔁 Lo de ayer", callback_data="mismeal:ayer"
                    )
                ]
            )

        current_slot = None
        for row in rows:
            slot = row.get("slot") or "otras"
            if slot != current_slot:
                from foodbot.core.timeutil import SLOT_LABELS

                label = SLOT_LABELS.get(slot, "Otras")
                buttons.append([InlineKeyboardButton(f"— {label} —", callback_data="mismeal:noop")])
                current_slot = slot
            used = int(row.get("times_used") or 0)
            suffix = f" ({used}x)" if used else ""
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{row['name']} · {int(row['calories'] or 0)} kcal{suffix}",
                        callback_data=f"mismeal:usar:{row['id']}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton("➕ Añadir nueva", callback_data="mismeal:nueva"),
                InlineKeyboardButton("🗑 Gestionar", callback_data="mismeal:gestionar"),
            ]
        )
        buttons.append([InlineKeyboardButton("⬅️ Menú", callback_data="menu:resumen")])

        await self._safe_send(
            update,
            "<b>🍽 Mis comidas</b>\n"
            "<i>Un toque para apuntar. Las que mas usas, primero.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    def _yesterday_meals(self, user_id: int) -> list[dict[str, Any]]:
        """Las comidas de ayer (para copiarlas de un toque)."""
        from foodbot.core.timeutil import day_bounds, local_now

        yesterday = (local_now(self.users.tz) - timedelta(days=1)).strftime("%Y-%m-%d")
        start, end, _ = day_bounds(yesterday, self.users.tz)
        return [dict(row) for row in self.db.meals_between(user_id, start, end)]

    async def cb_my_meal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Apuntar una comida de la biblioteca (o gestionarla)."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        user = update.effective_user

        if action == "gestionar":
            await self._send_my_meals(update, manage=True)
            return

        if action == "nueva":
            context.user_data["await_my_meal"] = {"step": "name"}
            await self._safe_send(
                update,
                "🍽 <b>¿Cómo se llama?</b>\n"
                "<i>Ej: Avena con leche, Batido de proteina, Tortilla de la abuela.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return

        if action == "borrar":
            try:
                meal_id = int(parts[2])
            except (IndexError, ValueError):
                return
            row = self.db.get_my_meal(user.id, meal_id)
            if row and self.db.delete_my_meal(user.id, meal_id):
                await self._safe_send(
                    update,
                    f"🗑 Borrada <b>{esc(str(row['name']))}</b>.",
                    parse_mode=ParseMode.HTML,
                )
            await self._send_my_meals(update, manage=True)
            return

        if action == "ayer":
            meals = self._yesterday_meals(user.id)
            if not meals:
                await self._safe_send(
                    update, "Ayer no registraste ninguna comida.", parse_mode=ParseMode.HTML
                )
                return
            buttons = [
                [
                    InlineKeyboardButton(
                        f"{m['dish']} · {int(m['calories'] or 0)} kcal",
                        callback_data=f"mismeal:copiar:{m['id']}",
                    )
                ]
                for m in meals[:10]
            ]
            buttons.append([InlineKeyboardButton("⬅️ Volver", callback_data="menu:miscomidas")])
            await self._safe_send(
                update,
                "🔁 <b>Lo de ayer</b>\n<i>Pulsa una para copiarla.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if action == "usar":
            try:
                meal_id = int(parts[2])
            except (IndexError, ValueError):
                return
            row = self.db.get_my_meal(user.id, meal_id)
            if not row:
                return
            await self._log_saved_meal(update, dict(row))
            return

        if action == "copiar":
            try:
                meal_id = int(parts[2])
            except (IndexError, ValueError):
                return
            meal = self.db.get_meal(user.id, meal_id)
            if not meal:
                return
            await self._log_saved_meal(
                update,
                {
                    "id": None,
                    "name": meal["dish"],
                    "calories": meal["calories"],
                    "protein_g": meal["protein_g"],
                    "carbs_g": meal["carbs_g"],
                    "fat_g": meal["fat_g"],
                    "slot": meal["meal_slot"],
                },
            )

    async def _log_saved_meal(self, update: Update, row: dict[str, Any]) -> None:
        """Apunta una comida de la biblioteca y da el feedback de siempre."""
        user = update.effective_user
        now = utcnow()
        calories = int(row.get("calories") or 0)
        summary = self.users.today_summary(user.id)
        consumed_before = max(0, int(summary["calories"]))

        self.db.add_meal(
            user_id=user.id,
            created_at=iso(now),
            eaten_at=iso(now),
            meal_slot=row.get("slot") or self._slot_for(now),
            dish=str(row.get("name") or "Comida")[:120],
            calories=calories,
            protein_g=float(row.get("protein_g") or 0),
            carbs_g=float(row.get("carbs_g") or 0),
            fat_g=float(row.get("fat_g") or 0),
            mass_g=0,
            ingredients="[]",
            raw_answer="",
            confidence=1.0,
            engine="biblioteca",
        )
        if row.get("id"):
            self.db.use_my_meal(user.id, int(row["id"]))

        await self._safe_send(
            update,
            f"✅ Anotado: <b>{esc(str(row.get('name') or 'Comida'))}</b> — "
            f"{calories} kcal",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        feedback = self._feedback_for(user.id, calories, consumed_before)
        await self._send_feedback(update.effective_message, feedback)

    async def on_my_meal_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """El alta a mano de una comida (nombre -> kcal)."""
        pending = context.user_data.get("await_my_meal") or {}
        step = pending.get("step")
        text = (update.effective_message.text or "").strip()

        if step == "name":
            if not text:
                await update.effective_message.reply_text(
                    "Necesito un nombre. Escribe el de la comida.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ForceReply(),
                )
                return ConversationHandler.END
            pending["name"] = text[:120]
            pending["step"] = "kcal"
            context.user_data["await_my_meal"] = pending
            await update.effective_message.reply_text(
                f"✅ <b>{esc(text[:120])}</b>\n¿Cuántas kcal tiene?\n"
                "<i>Escribe solo el número (ej. <code>320</code>).</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(),
            )
            return ConversationHandler.END

        if step == "kcal":
            value = self._parse_number(update, text)
            if value is None or not (0 <= value <= 5000):
                await update.effective_message.reply_text(
                    "No he entendido las calorías. Escribe un número "
                    "(ej. <code>320</code>).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ForceReply(),
                )
                return ConversationHandler.END
            user = update.effective_user
            now = utcnow()
            name = pending.get("name") or "Comida"
            existing = self.db.find_my_meal(user.id, name)
            if existing:
                name = f"{name} ({int(existing['times_used'] or 0) + 2})"
            self.db.add_my_meal(
                user.id,
                name=name,
                slot=self._slot_for(now),
                calories=int(value),
            )
            context.user_data.pop("await_my_meal", None)
            await update.effective_message.reply_text(
                f"✅ Guardada en <b>Mis comidas</b>: {esc(name)} — {int(value)} kcal",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🍽 Ver mis comidas", callback_data="menu:miscomidas")]]
                ),
            )
            return ConversationHandler.END

        return ConversationHandler.END

    async def cb_save_my_meal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Guarda la ultima comida en la biblioteca del usuario."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        user = update.effective_user
        try:
            meal_id = int(parts[2])
        except (IndexError, ValueError):
            return
        if parts[2] == "no":
            await self._safe_send(
                update, "Vale, no la guardo.", parse_mode=ParseMode.HTML, reply_markup=main_menu()
            )
            return
        meal = self.db.get_meal(user.id, meal_id)
        if not meal:
            return
        name = str(meal["dish"] or "Comida")[:120]
        existing = self.db.find_my_meal(user.id, name)
        if existing:
            # Variante: mismo nombre, otra version.
            name = f"{name} ({int(existing['times_used'] or 0) + 2})"
        self.db.add_my_meal(
            user.id,
            name=name,
            slot=meal["meal_slot"],
            calories=int(meal["calories"] or 0),
            protein_g=meal["protein_g"],
            carbs_g=meal["carbs_g"],
            fat_g=meal["fat_g"],
            photo_id=meal["photo_id"],
        )
        await self._safe_send(
            update,
            f"\u2b50 Guardada en <b>Mis comidas</b>: {esc(name)} "
            f"({int(meal['calories'] or 0)} kcal).\n"
            "<i>La pr\u00f3xima vez, un toque.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("\U0001f37d Ver mis comidas", callback_data="menu:miscomidas")]]
            ),
        )

    # -- franja de la comida ------------------------------------------------
    async def cb_meal_slot(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Elegir (o corregir) a que franja pertenece la comida."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        try:
            meal_id = int(parts[1])
        except (IndexError, ValueError):
            return

        if len(parts) > 2 and parts[2]:
            slot = parts[2]
            from foodbot.core.timeutil import SLOT_LABELS

            ok = self.db.update_meal(update.effective_user.id, meal_id, meal_slot=slot)
            if ok:
                await self._safe_send(
                    update,
                    f"\u2705 Anotado como <b>{SLOT_LABELS.get(slot, slot)}</b>.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_menu(),
                )
            else:
                await self._safe_send(
                    update, "No he podido cambiar esa comida.", parse_mode=ParseMode.HTML
                )
            return

        await self._safe_send(
            update,
            "\U0001f550 \u00bfA qu\u00e9 comida es?",
            parse_mode=ParseMode.HTML,
            reply_markup=slot_keyboard(meal_id),
        )

    # -- etiqueta de un producto envasado ------------------------------------
    async def cb_label(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Pide la etiqueta y el peso para afinar las calorias."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        try:
            meal_id = int(parts[1])
        except (IndexError, ValueError):
            return
        context.user_data["await_label"] = meal_id
        await self._safe_send(
            update,
            "\U0001f3d7\ufe0f <b>M\u00e1ndame una foto de la etiqueta</b> "
            "(la de Informaci\u00f3n nutricional) y el peso del producto.\n\n"
            "<i>En el pie de la foto escribe el peso: <code>250 g</code>. Con la "
            "etiqueta y el peso, las calor\u00edas son exactas. Yo leo lo que pone "
            "y te lo confirmo antes de guardarlo.</i>",
            parse_mode=ParseMode.HTML,
        )

    async def _read_label(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, meal_id: int
    ) -> None:
        """Transcribe la etiqueta, calcula la porcion y pide confirmacion."""
        user = update.effective_user
        message = update.effective_message
        _file_id, data = await self._download_photo(message)
        mime = sniff_mime(data)
        image_b64 = base64.b64encode(data).decode("ascii")

        note = (message.caption or "").strip()
        weight = None
        import re

        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(g|kg|ml|l)?", note, re.I)
        if match:
            weight = float(match.group(1).replace(",", "."))
            if (match.group(2) or "g").lower() in {"kg", "l"}:
                weight *= 1000

        status = await message.reply_text("\U0001f50d Leyendo la etiqueta\u2026")
        try:
            raw = await self.llm.complete(
                LABEL_SYSTEM,
                LABEL_PROMPT,
                image_b64,
                mime,
                max_tokens=160,
            )
        except Exception as exc:  # noqa: BLE001
            await status.edit_text(
                f"No he podido leer la etiqueta: {esc(str(exc)[:150])}", parse_mode=ParseMode.HTML
            )
            return

        label = parse_label(raw)
        if not label.has_values:
            await status.edit_text(
                "No he_distinguido los valores de la etiqueta. Escr\u00edbeme las "
                "calor\u00edas por 100 g y el peso, y lo calculo.",
                parse_mode=ParseMode.HTML,
            )
            return

        if weight is None:
            weight = label.net_weight_g
        if weight is None:
            # El peso no estaba en la etiqueta: se estima desde el envase.
            try:
                estimate = await self.llm.complete(
                    LABEL_SYSTEM,
                    WEIGHT_PROMPT,
                    image_b64,
                    mime,
                    max_tokens=40,
                )
            except Exception:  # noqa: BLE001
                estimate = ""
            weight = parse_weight(estimate)

        if not weight:
            await status.edit_text(
                "Necesito el peso: escr\u00edbeme los gramos (ej. <code>250 g</code>) "
                "y hago la cuenta.",
                parse_mode=ParseMode.HTML,
            )
            context.user_data["await_label_weight"] = meal_id
            return

        portion = label_to_portion(label, weight)
        context.user_data["pending_portion"] = {
            "meal_id": meal_id,
            "weight_g": weight,
            "label_data": label,
            **portion,
        }
        await status.edit_text(
            "\U0001f3d7\ufe0f Leo en la etiqueta: <b>"
            f"{int(label.kcal_100g or 0)} kcal/100 g</b>\n"
            f"Con <b>{int(weight)} g</b> son <b>{portion['calories']} kcal</b>\n"
            f"\U0001f9c1 P {portion['protein_g']} g \u00b7 \U0001f35a C {portion['carbs_g']} g "
            f"\u00b7 \U0001f951 G {portion['fat_g']} g\n\n"
            "<i>\u00bfLo guardo as\u00ed?</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\u2705 S\u00ed, gu\u00e1rdalo", callback_data="etiqueta:si"
                        ),
                        InlineKeyboardButton("\u274c Cancelar", callback_data="etiqueta:no"),
                    ]
                ]
            ),
        )

    async def cb_label_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Confirma el calculo hecho con la etiqueta."""
        query = update.callback_query
        await query.answer()
        action = query.data.split(":")[-1]
        pending = context.user_data.pop("pending_portion", None)
        if action != "si" or not pending:
            await self._safe_send(update, "Vale, lo dejo como estaba.", parse_mode=ParseMode.HTML)
            return
        user = update.effective_user
        meal_id = int(pending.get("meal_id") or 0)
        self.db.update_meal(
            user.id,
            meal_id,
            calories=int(pending.get("calories") or 0),
            protein_g=float(pending.get("protein_g") or 0),
            carbs_g=float(pending.get("carbs_g") or 0),
            fat_g=float(pending.get("fat_g") or 0),
        )

        # El producto queda en el catalogo del usuario: la proxima vez que lo
        # nombre, sale al instante y sin pedir nada.
        new_product = context.user_data.pop("pending_new_product", None)
        if new_product is None:
            # Venia del boton de etiqueta: el nombre es el del plato.
            meal = self.db.get_meal(user.id, meal_id)
            if meal and meal["dish"]:
                dish = str(meal["dish"]).strip()
                # "3 x magdalenas" -> "magdalenas"
                if " x " in dish:
                    dish = dish.split(" x ", 1)[1]
                new_product = {"name": dish, "brand": None}
        if new_product and pending.get("weight_g"):
            weight = float(pending.get("weight_g"))
            portion = label_to_portion(pending.get("label_data") or _EmptyLabel(), weight)
            self.db.add_product(
                user.id,
                name=str(new_product.get("name") or ""),
                brand=new_product.get("brand"),
                kcal_100g=(portion["calories"] / weight * 100) if weight else None,
                protein_100g=(portion["protein_g"] / weight * 100) if weight else None,
                carbs_100g=(portion["carbs_g"] / weight * 100) if weight else None,
                fat_100g=(portion["fat_g"] / weight * 100) if weight else None,
                portion_g=weight,
                source="etiqueta",
            )
        await self._safe_send(
            update,
            f"\u2705 Guardado: <b>{int(pending.get('calories') or 0)} kcal</b> "
            f"({int(pending.get('weight_g') or 0)} g de etiqueta).",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    # -- como va el dia (sticker) ------------------------------------------
    def _feedback_for(self, user_id: int, meal_kcal: int, consumed_before: int):
        """Calcula el sticker y el mensaje segun como va el dia."""
        target = self.users.daily_target(user_id)
        daily = int(target.daily_kcal) if target else 0
        week = self.users.week_summary(user_id)
        bank = week.get("bank") or {}
        return meal_feedback(
            meal_kcal=meal_kcal,
            consumed_before=consumed_before,
            daily_target=daily,
            week_saved=int(bank.get("saved") or 0),
            week_goal=int(bank.get("goal") or 0),
        )

    async def _send_feedback(self, message: Any, feedback: Any) -> None:
        """Manda el sticker con su comentario (o el emoji, si no hay sticker)."""
        file_id = sticker_for(feedback.sticker)
        if file_id:
            try:
                await message.reply_sticker(
                    sticker=file_id,
                    caption=feedback.text,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo enviar el sticker: %s", exc)
        await message.reply_text(
            f"{feedback.emoji} {feedback.text}", parse_mode=ParseMode.HTML
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
        # vuelve a generar (tampoco se gasta GPU: el plan lo hacen las reglas).
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
            await self._safe_send(
                update,
                header + esc(cached.strip()),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            return

        # El plan lo hacen las reglas: los numeros salen de la tabla de
        # ingredientes y del margen semanal, no del modelo (que devuelve 15
        # lineas en vez de 2 y tarda minutos).
        now = local_now(self.users.tz)
        plan = build_meal_plan(
            target_kcal=int(daily_allowance),
            protein_g=target.macro.protein_g,
            carbs_g=target.macro.carbs_g,
            fat_g=target.macro.fat_g,
            now_hour=now.hour + now.minute / 60.0,
            eaten_today=int(eaten),
            floor_kcal=int(target.floor_kcal),
            seed=f"{user.id}|{summary['day']}",
        )
        text = meal_plan_message(plan)
        details = meal_plan_details(plan)
        if details:
            text = f"{text}\n\n{details}"
        self.db.set_cache(cache_key, text, user_id=user.id)
        await self._safe_send(
            update,
            header + esc(text),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    async def cmd_advice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._send_advice(update)

    async def _send_advice(self, update: Update) -> None:
        """El consejo: lo especifico (con evidencia) y el del modelo.

        Los consejos especificos son deterministas: dependen del perfil
        (hormonas, pronombres, sexo, objetivo) y no del modelo, que es un
        modelo de calorias y no un dietista. El modelo aporta los tips
        genericos, que van detras.
        """
        user = update.effective_user
        profile = self.users.profile(user.id)
        target = self.users.daily_target(user.id)
        if not target:
            await update.effective_message.reply_text(
                "\u2699\ufe0f Completa tu perfil (<code>/perfil</code>) para recibir consejos personalizados.",
                parse_mode=ParseMode.HTML,
            )
            return

        specific = specific_tips(profile, target)
        summary = self.users.today_summary(user.id)
        eaten = summary["calories"]
        cache_key = f"advice:{user.id}:{summary['day']}:{eaten}"
        cached = self.db.get_cache(cache_key)

        if cached:
            await self._safe_send(
                update,
                advice_message(specific, cached),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            return

        status = await update.effective_message.reply_text(
            "\U0001f4a1 Pensando un consejo\u2026"
        )
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
            # Si el motor falla, el consejo especifico sigue sirviendo.
            await status.edit_text(
                advice_message(specific),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
            log.warning("Fallo al generar el consejo del modelo: %s", exc)
            return
        self.db.set_cache(cache_key, text.strip(), user_id=user.id)
        await status.edit_text(
            advice_message(specific, text),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

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
        hormones = profile.get("hormone_therapy")
        if hormones and hormones != "none":
            from foodbot.core.nutrition import HORMONE_OPTIONS

            lines.append(
                f"Hormonas: {HORMONE_OPTIONS.get(str(hormones), str(hormones))}"
            )
        pronouns = profile.get("pronouns")
        if pronouns:
            lines.append(f"Pronombres: {PRONOUN_LABELS.get(str(pronouns), pronouns)}")
        activity = str(profile.get("activity") or "")
        lines.append(
            f"Actividad: {activity_label(activity)}" if activity else "Actividad: -"
        )
        lines.append(f"Objetivo: {goal_label(profile.get('goal'))}")
        lines.append(f"Peso objetivo: {profile.get('target_weight_kg') or '-'} kg")
        likes = profile.get("likes")
        if likes:
            lines.append(f"Te gusta: {esc(str(likes)[:80])}")
        avoid = profile.get("avoid")
        if avoid:
            lines.append(f"Evitas: {esc(str(avoid)[:80])}")
        if profile.get("gentle_mode"):
            lines.append("Modo suave: sin deficit ni cuentas")
        manual = profile.get("daily_kcal_override")
        if manual:
            lines.append(f"Calor\u00edas al d\u00eda: <b>{int(manual)} kcal</b> (fijadas por ti)")
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
            reply_markup = reference_keyboard()
        elif kind == "activity":
            reply_markup = activity_keyboard()
        elif kind == "goal":
            reply_markup = goal_keyboard()
        elif kind == "hormones":
            reply_markup = hormone_keyboard()
        elif kind == "pronouns":
            reply_markup = pronoun_keyboard()
        elif kind == "gentle":
            reply_markup = gentle_keyboard()
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
        """La referencia metabolica: solo afecta a los numeros."""
        query = update.callback_query
        await query.answer()
        value = query.data.split(":")[-1]
        user = update.effective_user

        if value == "unknown":
            # Sin referencia clara: punto medio y composicion corporal.
            self.users.update_profile(user.id, sex="other")
            await query.edit_message_text(
                "Entendido: uso el punto medio de las referencias y, si me das tu "
                "% de grasa, el calculo va por composicion corporal.",
                parse_mode=ParseMode.HTML,
            )
            return await self._ask(update, context, "bodyfat")

        self.users.update_profile(user.id, sex=value)
        await query.edit_message_text("✅ Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "hormones")

    async def on_likes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Que le gusta: para las alternativas."""
        value = (update.effective_message.text or "").strip()[:200]
        self.users.update_profile(update.effective_user.id, likes=value)
        await update.effective_message.reply_text("\u2705 Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "avoid")

    async def on_avoid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Lo que no le gusta o no puede comer: se filtra siempre."""
        value = (update.effective_message.text or "").strip()[:200]
        self.users.update_profile(update.effective_user.id, avoid=value)
        await update.effective_message.reply_text("\u2705 Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "age")

    async def cb_gentle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Modo suave: sin deficit ni cuentas."""
        query = update.callback_query
        await query.answer()
        value = query.data.split(":")[-1]
        gentle = value == "yes"
        self.users.update_profile(update.effective_user.id, gentle_mode=1 if gentle else 0)
        if gentle:
            await query.edit_message_text(
                "\U0001f91a Modo suave activado. Sin objetivos, sin cuentas y sin "
                "estickers: solo se guarda lo que comes.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.edit_message_text("Guardado.", parse_mode=ParseMode.HTML)
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

    async def cb_hormone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Tratamiento hormonal: cambia la referencia fisiologica."""
        query = update.callback_query
        await query.answer()
        value = query.data.split(":")[-1]
        self.users.update_profile(update.effective_user.id, hormone_therapy=value)
        await query.edit_message_text("Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "pronouns")

    async def cb_pronoun(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Como quieres que te llame: decide el marco del consejo."""
        query = update.callback_query
        await query.answer()
        value = query.data.split(":")[-1]
        self.users.update_profile(update.effective_user.id, pronouns=value)
        await query.edit_message_text("Guardado.", parse_mode=ParseMode.HTML)
        return await self._ask(update, context, "likes")

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
        return await self._ask(update, context, "gentle")

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

        if action == "miscomidas":
            await self._send_my_meals(update)
        elif action == "resumen":
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
