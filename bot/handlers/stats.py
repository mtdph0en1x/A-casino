"""
handlers/stats.py — /balance (/ass), /leaderboard, /history
"""
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

import database as db
from models import fmt, display_name

logger = logging.getLogger(__name__)

MEDALS = ["🥇", "🥈", "🥉"]


def _register(tg_user, chat_id=None):
    return db.get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name, chat_id)


# ---------------------------------------------------------------------------
# /balance  (also /ass)
# ---------------------------------------------------------------------------

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id if update.message.chat_id != update.message.from_user.id else None
    user = _register(update.message.from_user, chat_id)
    balance = user["balance"]

    if balance > 1.5:
        mood = "🤑 Жопний магнат!"
    elif balance > 1.0:
        mood = "😏 Непогано!"
    elif balance == 1.0:
        mood = "😐 Стартова жопа."
    elif balance > 0:
        mood = "😬 Трохи залишилось..."
    elif balance == 0:
        mood = "💀 Жопи нема!"
    else:
        mood = "☠️ В мінусі! Хтось ходить без жопи."

    await update.message.reply_html(
        f"💰 <b>Баланс {display_name(user)}:</b>\n"
        f"{fmt(balance)}\n\n"
        f"{mood}"
    )


# ---------------------------------------------------------------------------
# /leaderboard
# ---------------------------------------------------------------------------

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id if update.message.chat_id != update.message.from_user.id else None
    _register(update.message.from_user, chat_id)
    users = db.get_all_users_ranked(chat_id)

    if not users:
        await update.message.reply_text("Ще нікого нема! Напиши /start щоб зареєструватись.")
        return

    lines = ["🏆 <b>Жопний рейтинг:</b>\n━━━━━━━━━━━━━━"]
    for i, user in enumerate(users):
        medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
        crown = " 👑" if i == 0 else ""
        skull = " ☠️" if user["balance"] < 0 else ""
        lines.append(
            f"{medal} {display_name(user)} — <b>{fmt(user['balance'])}</b>{crown}{skull}"
        )

    await update.message.reply_html("\n".join(lines))


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id if update.message.chat_id != update.message.from_user.id else None
    _register(update.message.from_user, chat_id)
    bets = db.get_bet_history(update.message.chat_id, limit=10)

    if not bets:
        await update.message.reply_text("📜 Ще не було вирішених парі.")
        return

    lines = ["📜 <b>Останні парі:</b>\n━━━━━━━━━━━━━━"]
    for bet in bets:
        proposer = db.get_user(bet["proposer_id"])
        opponent = db.get_user(bet["opponent_id"])
        claimer = db.get_user(bet["claimer_id"]) if bet["claimer_id"] else None
        winner_name = display_name(claimer) if claimer else "?"

        resolved = ""
        if bet["resolved_at"]:
            try:
                dt = datetime.fromisoformat(bet["resolved_at"])
                resolved = dt.strftime("%d.%m.%Y")
            except Exception:
                pass

        lines.append(
            f"<b>#{bet['id']}</b> {display_name(proposer)} vs {display_name(opponent)}"
            f" — {fmt(bet['amount'])}\n"
            f"   📋 {bet['description']}\n"
            f"   🏆 {winner_name} • {resolved}\n"
        )

    await update.message.reply_html("\n".join(lines))
