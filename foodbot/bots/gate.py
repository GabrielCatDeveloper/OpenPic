"""Bot privado de administracion (gate bot).

Solo el propietario puede usarlo. Su unica funcion es emitir tokens de acceso
para el bot publico, revocarlos y ver estadisticas de uso.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from foodbot.config import Config
from foodbot.core.db import Database, iso
from foodbot.core.text import esc
from foodbot.core.tokens import TokenService
from foodbot.core.users import UserService

log = logging.getLogger("foodbot.gate")

HELP = """
<b>🔐 Bot de acceso (privado)</b>
Este bot emite los tokens que habilitan el bot publico de nutrition.

<b>Dos formas de dar acceso</b>
1. <b>Con token</b>: generas un codigo y el usuario lo canjea con /token.
   /token [plan] [dias] [etiqueta]
   /nick &lt;apodo&gt; [plan] [dias]   (igual, pero con apodo para reconocerlo)
2. <b>Directo</b>: activas a alguien sin codigo, con su id de Telegram.
   /autorizar &lt;id|apodo&gt; [plan]
   /alias &lt;apodo&gt; &lt;id&gt;   (registra un apodo para un id)

Comandos:
/autorizar &lt;id|apodo&gt; [plan] — activa el acceso directamente
/alias &lt;apodo&gt; &lt;id&gt; — registra un apodo para un id
/apodos — lista los apodos
/desactivar &lt;id|apodo&gt; — quita el acceso
/tokens — lista los tokens emitidos
/revocar &lt;id|codigo&gt; — invalida un token
/usuarios — lista los usuarios registrados
/plan &lt;user_id&gt; &lt;plan&gt; — cambia el plan de un usuario
/stats — estadisticas de uso
/broadcast &lt;texto&gt; — mensaje a todos los usuarios autorizados
/ayuda — este mensaje
"""


def is_admin(update: Update, config: Config) -> bool:
    """Comprueba si quien escribe es el administrador.

    En modo de un solo usuario (SINGLE_USER_MODE) solo vale el propietario:
    el resto recibe un "es un bot de un solo usuario" y no puede emitir
    tokens. El bot publico sigue abierto para cualquiera con un token valido.
    """
    user = update.effective_user
    if not user:
        return False
    if config.single_user_mode:
        return config.is_owner(user.id)
    return user.id in config.admin_ids


class GateBot:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.tokens = TokenService(db, pepper=config.llm_api_key)
        self.users = UserService(db, config.timezone)
        self.app: Application = (
            ApplicationBuilder()
            .token(config.gate_bot_token)
            .post_init(self._post_init)
            .build()
        )
        self._register_handlers()

    # -- setup ------------------------------------------------------------
    async def _post_init(self, app: Application) -> None:
        await app.bot.set_my_commands(
            [
                {"command": "token", "description": "Generar un token de acceso"},
                {"command": "tokens", "description": "Ver tokens emitidos"},
                {"command": "revocar", "description": "Revocar un token"},
                {"command": "usuarios", "description": "Ver usuarios registrados"},
                {"command": "plan", "description": "Cambiar plan de un usuario"},
                {"command": "stats", "description": "Estadisticas de uso"},
                {"command": "broadcast", "description": "Mensaje a usuarios"},
                {"command": "ayuda", "description": "Ayuda"},
            ]
        )
        log.info("Gate bot inicializado. Admins: %s", sorted(self.config.admin_ids))

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("ayuda", self.cmd_help))
        self.app.add_handler(CommandHandler("token", self.cmd_token))
        self.app.add_handler(CommandHandler("nick", self.cmd_nick))
        self.app.add_handler(CommandHandler("autorizar", self.cmd_authorize))
        self.app.add_handler(CommandHandler("alias", self.cmd_alias))
        self.app.add_handler(CommandHandler("apodos", self.cmd_aliases))
        self.app.add_handler(CommandHandler("desactivar", self.cmd_deactivate))
        self.app.add_handler(CommandHandler("tokens", self.cmd_tokens))
        self.app.add_handler(CommandHandler("revocar", self.cmd_revoke))
        self.app.add_handler(CommandHandler("usuarios", self.cmd_users))
        self.app.add_handler(CommandHandler("plan", self.cmd_set_plan))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))
        self.app.add_handler(
            MessageHandler(filters.COMMAND, self._unknown_command)
        )

    # -- utilidades -------------------------------------------------------
    async def _guard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if is_admin(update, self.config):
            return True
        user = update.effective_user
        log.warning(
            "Acceso denegado al gate bot: id=%s username=%s",
            getattr(user, "id", "?"),
            getattr(user, "username", "?"),
        )
        if update.effective_message:
            if self.config.single_user_mode:
                message = (
                    "🔒 <b>Este es un bot de un solo usuario</b>\n\n"
                    "El panel de acceso es privado: solo su propietario puede usarlo. "
                    "Si necesitas un token, pideselo al administrador del bot de "
                    "nutrición."
                )
            else:
                message = (
                    "⛔ Este bot es privado. Si necesitas acceso al bot de nutrición, "
                    "pide un token al administrador."
                )
            await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)
        return False

    async def _unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update, context):
            await update.effective_message.reply_text("Comando desconocido. Usa /ayuda.")

    # -- comandos ---------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        stats = self._stats()
        await update.effective_message.reply_text(
            "<b>🔐 Panel de acceso</b>\n\n"
            f"Tokens emitidos: {stats['tokens']}\n"
            f"Usuarios activos: {stats['users']}\n"
            f"Analisis hoy: {stats['photos_today']}\n\n"
            "Usa /token para generar un token nuevo.",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)

    async def cmd_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        plan, days, label = self._parse_access_args(context.args or [])
        issued = self.tokens.issue(
            plan=plan,
            label=label,
            days_valid=days,
            max_uses=1,
            created_by=update.effective_user.id if update.effective_user else None,
        )
        plan_obj = self.config.plan_for(issued.plan)
        expiry = issued.expires_at or "sin caducidad"
        await update.effective_message.reply_text(
            "<b>✅ Token creado</b>\n\n"
            f"<code>{issued.code}</code>\n\n"
            f"Plan: {plan_obj.label}\n"
            f"Validez: {expiry}\n"
            f"Etiqueta: {esc(label or '-')}\n\n"
            "<i>Envia este codigo al usuario. Solo se puede canjear una vez "
            "y se muestra aqui una unica vez.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._open_markup(issued.code),
        )

    def _parse_access_args(self, args: list[str]) -> tuple[str, int, str | None]:
        """Interpreta '[plan] [dias] [etiqueta]' en cualquier orden."""
        plan = "free"
        days = 0
        label_parts: list[str] = []
        for candidate in args:
            low = candidate.lower()
            if low in self.config.plans and not label_parts:
                plan = low
            elif candidate.isdigit() and not label_parts:
                days = int(candidate)
            else:
                label_parts.append(candidate)
        label = " ".join(label_parts) if label_parts else None
        return (plan, days, label)

    # -- acceso directo -----------------------------------------------------
    def _resolve_target(self, raw: str) -> int | None:
        """Resuelve un id numerico o un apodo a un id de Telegram."""
        raw = (raw or "").strip().lstrip("@")
        if not raw:
            return None
        if raw.lstrip("-").isdigit():
            return int(raw)
        row = self.db.get_alias(raw)
        return int(row["user_id"]) if row else None

    async def _notify(self, user_id: int, text: str) -> None:
        """Avisa al usuario de que le han activado el acceso (si puede)."""
        try:
            await self.app.bot.send_message(
                chat_id=user_id, text=text, parse_mode=ParseMode.HTML, reply_markup=self._open_markup()
            )
        except TelegramError as exc:
            log.info("No se pudo avisar al usuario %s: %s", user_id, exc)

    def _open_markup(self, code: str | None = None) -> InlineKeyboardMarkup | None:
        """Boton para abrir el bot publico (con el token si lo hay)."""
        username = self.config.public_bot_username
        if not username:
            return None
        url = f"https://t.me/{username}"
        label = "🚀 Empezar"
        if code:
            url = f"{url}?start={code}"
            label = "📲 Entrar con este token"
        return InlineKeyboardMarkup([[InlineKeyboardButton(label, url=url)]])

    async def cmd_nick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Crea un token con apodo: /nick laura pro 30"""
        if not await self._guard(update, context):
            return
        args = list(context.args or [])
        if not args:
            await update.effective_message.reply_text(
                "Uso: <code>/nick &lt;apodo&gt; [plan] [dias]</code>\n"
                "<i>Ej: /nick laura pro 30</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        nickname = args.pop(0)
        plan, days, label = self._parse_access_args(args)
        label = label or nickname
        issued = self.tokens.issue(
            plan=plan,
            label=f"{nickname}: {label}" if label != nickname else nickname,
            days_valid=days,
            max_uses=1,
            created_by=update.effective_user.id if update.effective_user else None,
        )
        plan_obj = self.config.plan_for(issued.plan)
        await update.effective_message.reply_text(
            f"<b>✅ Token de <i>{esc(nickname)}</i> creado</b>\n\n"
            f"<code>{issued.code}</code>\n\n"
            f"Plan: {plan_obj.label}\n"
            f"Validez: {issued.expires_at or 'sin caducidad'}\n\n"
            "<i>Mándaselo a quien corresponda: lo canjea con "
            "<code>/token NTR1-...</code> y entra directo.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._open_markup(issued.code),
        )

    async def cmd_authorize(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Activa el acceso directamente: /autorizar 123456 pro | /autorizar laura"""
        if not await self._guard(update, context):
            return
        args = list(context.args or [])
        if not args:
            await update.effective_message.reply_text(
                "Uso: <code>/autorizar &lt;id|apodo&gt; [plan]</code>\n"
                "<i>Ej: /autorizar 123456789 pro</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        target = self._resolve_target(args[0])
        if target is None:
            await update.effective_message.reply_text(
                f"❌ No conozco a <b>{esc(args[0])}</b>.\n\n"
                "Usa su id numerico de Telegram (se lo da el bot publico al escribir "
                "<code>/start</code>) o registra un apodo con "
                "<code>/alias &lt;apodo&gt; &lt;id&gt;</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        plan = "pro"
        if len(args) > 1 and args[1].lower() in self.config.plans:
            plan = args[1].lower()

        self.users.authorize(target, plan=plan)
        plan_obj = self.config.plan_for(plan)
        await update.effective_message.reply_text(
            f"✅ <b>Acceso activado</b> para <code>{target}</code>\n\n"
            f"Plan: {plan_obj.label}\n\n"
            "<i>Ya puede usar el bot publico sin necesidad de token.</i>",
            parse_mode=ParseMode.HTML,
        )
        await self._notify(
            target,
            "✅ <b>Ya tienes acceso</b> al bot de nutrición.\n\n"
            "Escríbele <code>/start</code> para empezar.",
        )

    async def cmd_alias(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Registra un apodo para un id: /alias laura 123456789"""
        if not await self._guard(update, context):
            return
        args = list(context.args or [])
        if len(args) < 2 or not args[1].lstrip("-").isdigit():
            await update.effective_message.reply_text(
                "Uso: <code>/alias &lt;apodo&gt; &lt;id de Telegram&gt;</code>\n"
                "<i>Ej: /alias laura 123456789</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        nickname = args[0].strip().lstrip("@").lower()
        user_id = int(args[1])
        note = " ".join(args[2:]) or None
        self.db.set_alias(nickname, user_id, note)
        await update.effective_message.reply_text(
            f"✅ Apodo <b>{esc(nickname)}</b> → <code>{user_id}</code>\n\n"
            "<i>Ahora puedes activar el acceso con "
            f"<code>/autorizar {esc(nickname)}</code>.</i>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_aliases(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        rows = self.db.list_aliases()
        if not rows:
            await update.effective_message.reply_text(
                "Todavia no hay apodos. Registra uno con "
                "<code>/alias &lt;apodo&gt; &lt;id&gt;</code>."
            )
            return
        lines = ["<b>Apodos</b>"]
        for row in rows:
            name = self.db.get_user(int(row["user_id"]))
            who = (name["username"] if name and name["username"] else None) or "-"
            lines.append(f"• <b>{esc(row['nickname'])}</b> → <code>{row['user_id']}</code> ({esc(who)})")
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_deactivate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Quita el acceso: /desactivar 123456789 | laura"""
        if not await self._guard(update, context):
            return
        args = list(context.args or [])
        if not args:
            await update.effective_message.reply_text(
                "Uso: <code>/desactivar &lt;id|apodo&gt;</code>", parse_mode=ParseMode.HTML
            )
            return
        target = self._resolve_target(args[0])
        if target is None:
            await update.effective_message.reply_text(
                f"❌ No conozco a <b>{esc(args[0])}</b>.", parse_mode=ParseMode.HTML
            )
            return
        ok = self.users.deactivate(target)
        await update.effective_message.reply_text(
            f"⛔ Acceso retirado a <code>{target}</code>."
            if ok
            else "Ese usuario no tenia acceso.",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_tokens(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        tokens = self.tokens.list_tokens(limit=40)
        if not tokens:
            await update.effective_message.reply_text("Todavia no hay tokens emitidos.")
            return
        now = iso()
        lines = ["<b>Tokens</b>"]
        for t in tokens:
            status = "✅"
            if int(t.get("revoked") or 0):
                status = "⛔"
            elif t.get("expires_at") and str(t["expires_at"]) < now:
                status = "⌛"
            bound = t.get("username") or (f"tg:{t['bound_user_id']}" if t.get("bound_user_id") else "-")
            lines.append(
                f"{status} <code>#{t['id']} {esc(t['token_prefix'])}</code> · {esc(t['plan'])} · "
                f"usos {t['uses']}/{t['max_uses']} · {esc(str(bound))}"
                + (f" · {esc(t['label'])}" if t.get("label") else "")
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        if not context.args:
            await update.effective_message.reply_text(
                "Uso: <code>/revocar &lt;id|codigo&gt;</code>", parse_mode=ParseMode.HTML
            )
            return
        target = context.args[0]
        if target.isdigit():
            ok = self.tokens.revoke(int(target))
        else:
            ok = self.tokens.revoke_by_code(target)
        await update.effective_message.reply_text(
            "⛔ Token revocado." if ok else "No he encontrado ese token."
        )

    async def cmd_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        rows = self.db.query(
            "SELECT u.*, (SELECT COUNT(*) FROM meals m WHERE m.user_id = u.user_id) AS meals "
            "FROM users u ORDER BY u.authorized_at DESC LIMIT 50"
        )
        if not rows:
            await update.effective_message.reply_text("No hay usuarios registrados.")
            return
        aliases = {int(r["user_id"]): r["nickname"] for r in self.db.list_aliases()}
        lines = ["<b>Usuarios</b>"]
        for r in rows:
            user_id = int(r["user_id"])
            name = esc(r["username"] or r["first_name"] or "sin nombre")
            active = bool(r["authorized_at"])
            mark = "✅" if active else "⛔"
            alias = aliases.get(user_id)
            lines.append(
                f"{mark} <code>{user_id}</code> {name} · {esc(r['plan'])} · "
                f"{r['meals']} comidas · desde {str(r['authorized_at'] or '-')[:10]}"
                + (f" · apodo: {esc(alias)}" if alias else "")
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_set_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        if len(context.args or []) < 2:
            await update.effective_message.reply_text(
                "Uso: <code>/plan &lt;user_id|apodo&gt; &lt;free|pro|unlimited&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        target = self._resolve_target(context.args[0])
        plan = context.args[1].lower()
        if target is None or plan not in self.config.plans:
            await update.effective_message.reply_text("Parametros no validos.")
            return
        self.db.touch_user(target, plan=plan)
        await update.effective_message.reply_text(
            f"✅ Plan de <code>{target}</code> cambiado a <b>{esc(plan)}</b>."
        )

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        stats = self._stats()
        await update.effective_message.reply_text(
            "<b>📊 Estadisticas</b>\n\n"
            f"Usuarios autorizados: {stats['users']}\n"
            f"Tokens emitidos: {stats['tokens']}\n"
            f"Tokens canjeados: {stats['redeemed']}\n"
            f"Comidas registradas: {stats['meals']}\n"
            f"Analisis hoy: {stats['photos_today']}\n"
            f"Analisis ultimos 7 dias: {stats['photos_week']}\n"
            f"Analisis ultimos 30 dias: {stats['photos_month']}",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        if not context.args:
            await update.effective_message.reply_text("Uso: <code>/broadcast &lt;texto&gt;</code>")
            return
        message = " ".join(context.args)
        rows = self.db.query("SELECT user_id FROM users WHERE authorized_at IS NOT NULL")
        sent = failed = 0
        for row in rows:
            try:
                await self.app.bot.send_message(
                    chat_id=int(row["user_id"]), text=f"📣 {message}"
                )
                sent += 1
                await asyncio.sleep(0.05)  # respeta los limites de Telegram
            except TelegramError:
                failed += 1
        await update.effective_message.reply_text(
            f"📣 Enviado a {sent} usuarios ({failed} fallidos)."
        )

    def _stats(self) -> dict[str, Any]:
        today = self.db.scalar("SELECT COUNT(*) FROM meals WHERE substr(eaten_at,1,10) = date('now')", default=0)
        week = self.db.scalar(
            "SELECT COUNT(*) FROM meals WHERE eaten_at >= datetime('now','-7 days')", default=0
        )
        month = self.db.scalar(
            "SELECT COUNT(*) FROM meals WHERE eaten_at >= datetime('now','-30 days')", default=0
        )
        return {
            "users": self.db.scalar(
                "SELECT COUNT(*) FROM users WHERE authorized_at IS NOT NULL", default=0
            ),
            "tokens": self.db.scalar("SELECT COUNT(*) FROM tokens", default=0),
            "redeemed": self.db.scalar(
                "SELECT COUNT(*) FROM tokens WHERE bound_user_id IS NOT NULL", default=0
            ),
            "meals": self.db.scalar("SELECT COUNT(*) FROM meals", default=0),
            "photos_today": today,
            "photos_week": week,
            "photos_month": month,
        }
