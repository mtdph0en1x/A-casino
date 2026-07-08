import logging
import os

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
from handlers.bets import bet_command, bet_callback, bets_command
from handlers.resolve import claim_command, resolve_callback, vote_callback
from handlers.stats import balance_command, leaderboard_command, history_command
from handlers.events import (
    event_command, beton_command, resolve_event_command, event_callback,
    events_command, ehistory_command
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_URL: str = os.environ["WEBHOOK_URL"]

# ---------------------------------------------------------------------------
# General command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = db.get_or_create_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name,
    )
    await update.message.reply_html(
        f"👋 Привіт, <b>{update.effective_user.first_name}</b>!\n\n"
        f"Ласкаво просимо до <b>ЖопоБанку</b> 🍑\n\n"
        f"Твій стартовий баланс: <b>1.0 🍑</b>\n\n"
        f"Постав жопу на парі та доведи всім хто тут головний!\n\n"
        f"/help — список команд"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "🍑 <b>ЖопоБанк — команди:</b>\n"
        "━━━━━━━━━━━━━━\n"
        "<b>Парі:</b>\n"
        "/bet @user 0.5 умова — запропонувати парі\n"
        "/bets — активні парі\n\n"
        "<b>Вирішення:</b>\n"
        "/claim &lt;id&gt; — заявити що ти виграв\n\n"
        "<b>Групові події (Пул):</b>\n"
        "/event умова — створити подію\n"
        "/beton &lt;id&gt; за 0.5 — поставити\n"
        "/resolve &lt;id&gt; за — завершити (автор)\n\n"
        "<b>Статистика:</b>\n"
        "/balance — твій баланс\n"
        "/ass — теж баланс 😄\n"
        "/leaderboard — рейтинг\n"
        "/history — минулі парі\n\n"
        "<b>Як це працює:</b>\n"
        "1️⃣ /bet @friend 0.5 Динамо виграє\n"
        "2️⃣ Друг натискає ✅ Ставлю!\n"
        "3️⃣ Після події переможець пише /claim\n"
        "4️⃣ Інший підтверджує або оскаржує\n"
        "5️⃣ При суперечці — голосує чат 🗳️"
    )


_seen_users = {}

async def auto_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently register any user who writes in the chat."""
    u = update.effective_user
    if not u:
        return

    # Skip DB call if we already verified this user recently
    cached = _seen_users.get(u.id)
    if cached == (u.username, u.first_name):
        return

    db.get_or_create_user(u.id, u.username, u.first_name)
    _seen_users[u.id] = (u.username, u.first_name)


# ---------------------------------------------------------------------------
# PTB Application
# ---------------------------------------------------------------------------

ptb_app = (
    Application.builder()
    .token(BOT_TOKEN)
    .updater(None)
    .build()
)

# General
ptb_app.add_handler(CommandHandler("start",       start))
ptb_app.add_handler(CommandHandler("help",        help_command))

# Bets
ptb_app.add_handler(CommandHandler("bet",         bet_command))
ptb_app.add_handler(CommandHandler("bets",        bets_command))

# Group Events
ptb_app.add_handler(CommandHandler("event",       event_command))
ptb_app.add_handler(CommandHandler("events",      events_command))
ptb_app.add_handler(CommandHandler("ehistory",    ehistory_command))
ptb_app.add_handler(CommandHandler("beton",       beton_command))
ptb_app.add_handler(CommandHandler("resolve",     resolve_event_command))

# Resolve
ptb_app.add_handler(CommandHandler("claim",       claim_command))

# Stats
ptb_app.add_handler(CommandHandler("balance",     balance_command))
ptb_app.add_handler(CommandHandler("ass",         balance_command))
ptb_app.add_handler(CommandHandler("leaderboard", leaderboard_command))
ptb_app.add_handler(CommandHandler("history",     history_command))

# Callbacks from inline keyboards
ptb_app.add_handler(CallbackQueryHandler(bet_callback,     pattern=r"^b[ad]\|"))
ptb_app.add_handler(CallbackQueryHandler(resolve_callback, pattern=r"^b(c|di)\|"))
ptb_app.add_handler(CallbackQueryHandler(vote_callback,    pattern=r"^bv\|"))
ptb_app.add_handler(CallbackQueryHandler(event_callback,   pattern=r"^ev\|"))

# Auto-register users who chat (so @mentions work)
ptb_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, auto_register)
)

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

api = FastAPI(title="ЖопоБанк Bot")


@api.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    await ptb_app.initialize()
    await ptb_app.start()

    webhook_path = f"/webhook/{BOT_TOKEN}"
    full_url = WEBHOOK_URL.rstrip("/") + webhook_path

    await ptb_app.bot.set_webhook(
        url=full_url,
        secret_token=WEBHOOK_SECRET or None,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info("Webhook registered: %s", full_url)


@api.on_event("shutdown")
async def on_shutdown() -> None:
    await ptb_app.stop()
    await ptb_app.shutdown()
    logger.info("Bot shut down gracefully.")


@api.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> Response:
    if token != BOT_TOKEN:
        return Response(status_code=403)

    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != WEBHOOK_SECRET:
            return Response(status_code=403)

    body = await request.json()
    update = Update.de_json(body, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}
