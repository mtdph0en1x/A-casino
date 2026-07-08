import logging
import os

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (injected via environment variables)
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
# Public HTTPS URL of your Container App, e.g. https://my-bot.region.azurecontainerapps.io
WEBHOOK_URL: str = os.environ["WEBHOOK_URL"]

# ---------------------------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    await update.message.reply_html(
        f"👋 Hi <b>{user.first_name}</b>!\n\n"
        "I'm your Telegram bot running on <b>Azure Container Apps</b>.\n\n"
        "Type /help to see available commands.",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "🤖 <b>Available Commands</b>\n\n"
        "/start — Welcome message\n"
        "/help  — Show this help\n\n"
        "Feel free to extend me with more commands! 🚀"
    )
    await update.message.reply_html(help_text)


# ---------------------------------------------------------------------------
# Application & FastAPI setup
# ---------------------------------------------------------------------------

# Build the PTB application (no job queue needed for webhook mode)
ptb_app = (
    Application.builder()
    .token(BOT_TOKEN)
    .updater(None)          # disable built-in polling updater
    .build()
)

# Register handlers
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(CommandHandler("help", help_command))

# FastAPI app
api = FastAPI(title="Telegram Bot Webhook")


@api.on_event("startup")
async def on_startup() -> None:
    """Initialize PTB and register the webhook with Telegram on startup."""
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
    """Gracefully stop PTB on container shutdown."""
    await ptb_app.stop()
    await ptb_app.shutdown()
    logger.info("Bot shut down gracefully.")


@api.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> Response:
    """Receive updates from Telegram."""
    if token != BOT_TOKEN:
        return Response(status_code=403)

    # Optional: verify the secret header Telegram sends
    if WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming_secret != WEBHOOK_SECRET:
            return Response(status_code=403)

    body = await request.json()
    update = Update.de_json(body, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)


@api.get("/health")
async def health() -> dict:
    """Health check endpoint used by Azure Container Apps."""
    return {"status": "ok"}
