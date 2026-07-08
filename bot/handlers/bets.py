"""
handlers/bets.py — /bet, /bets, and accept/decline callbacks
"""
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import database as db
from models import BetStatus, fmt, display_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(tg_user, chat_id=None):
    return db.get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name, chat_id)


def _extract_opponent(message) -> Optional[dict]:
    """Return the first @mentioned or text_mention user from a message."""
    entities = message.entities or []
    text = message.text or ""
    for entity in entities:
        if entity.type == "text_mention" and entity.user:
            u = entity.user
            return db.get_or_create_user(u.id, u.username, u.first_name)
        if entity.type == "mention":
            username = text[entity.offset + 1: entity.offset + entity.length]
            return db.get_user_by_username(username)
    return None


def _proposal_keyboard(bet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ставлю!", callback_data=f"ba|{bet_id}"),
        InlineKeyboardButton("❌ Пас",     callback_data=f"bd|{bet_id}"),
    ]])


# ---------------------------------------------------------------------------
# /bet — propose a bet
# ---------------------------------------------------------------------------

async def bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    chat_id = msg.chat_id if msg.chat_id != msg.from_user.id else None
    user = _register(msg.from_user, chat_id)

    opponent = _extract_opponent(msg)
    if not opponent:
        await msg.reply_text(
            "❌ Не знайшов суперника.\n\n"
            "Формат: /bet @user <сума> <умова>\n"
            "Суперник повинен вже написати щось у чаті."
        )
        return

    if opponent["user_id"] == user["user_id"]:
        await msg.reply_text("❌ Не можна ставити парі самому з собою 😅")
        return

    # Parse: /bet @username <amount> <description>
    args = context.args or []
    # Skip the @username arg if present
    numeric_args = []
    desc_start = 0
    for i, arg in enumerate(args):
        try:
            numeric_args.append((i, float(arg)))
        except ValueError:
            pass

    if not numeric_args:
        await msg.reply_text("❌ Вкажи суму.\nФормат: /bet @user 0.5 Динамо виграє")
        return

    amount_idx, amount = numeric_args[0]
    if amount <= 0:
        await msg.reply_text("❌ Сума повинна бути більше 0.")
        return

    description = " ".join(args[amount_idx + 1:]).strip()
    if not description:
        await msg.reply_text("❌ Вкажи умову парі.\nФормат: /bet @user 0.5 Динамо виграє")
        return

    bet_id = db.create_bet(
        chat_id=msg.chat_id,
        proposer_id=user["user_id"],
        opponent_id=opponent["user_id"],
        amount=amount,
        description=description,
    )

    text = (
        f"🎲 <b>Новий парі #{bet_id}!</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Від: {display_name(user)}\n"
        f"🎯 Кому: {display_name(opponent)}\n"
        f"💰 Ставка: <b>{fmt(amount)}</b>\n"
        f"📋 Умова: {description}\n\n"
        f"{display_name(opponent)}, ставиш жопу? 🍑"
    )
    sent = await msg.reply_html(text, reply_markup=_proposal_keyboard(bet_id))
    db.update_bet(bet_id, proposal_message_id=sent.message_id)


# ---------------------------------------------------------------------------
# Callbacks — accept / decline
# ---------------------------------------------------------------------------

async def bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, bet_id_str = query.data.split("|")
    bet_id = int(bet_id_str)
    bet = db.get_bet(bet_id)

    if not bet:
        await query.edit_message_text("❌ Парі не знайдено.")
        return

    chat_id = query.message.chat_id if query.message.chat_id != query.from_user.id else None
    user = _register(query.from_user, chat_id)
    uid = user["user_id"]

    # Only the opponent can respond to the proposal
    if uid != bet["opponent_id"]:
        await query.answer("Це не твоє парі! 😅", show_alert=True)
        return

    if bet["status"] != BetStatus.PROPOSED:
        await query.answer("Це парі вже не очікує відповіді.", show_alert=True)
        return

    proposer = db.get_user(bet["proposer_id"])
    opponent = db.get_user(bet["opponent_id"])

    if action == "ba":  # accept
        db.update_bet(bet_id, status=BetStatus.ACTIVE)
        text = (
            f"✅ <b>Парі #{bet_id} прийнято!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚔️ {display_name(proposer)} vs {display_name(opponent)}\n"
            f"💰 Ставка: <b>{fmt(bet['amount'])}</b>\n"
            f"📋 Умова: {bet['description']}\n\n"
            f"Хай переможе найжопистіший! 🍑"
        )
        await query.edit_message_text(text, parse_mode="HTML")

    elif action == "bd":  # decline
        db.update_bet(bet_id, status=BetStatus.DECLINED,
                      resolved_at=__import__("datetime").datetime.utcnow().isoformat())
        text = (
            f"❌ <b>Парі #{bet_id} відхилено.</b>\n"
            f"{display_name(opponent)} злякався 🐔"
        )
        await query.edit_message_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /bets — list active bets in this chat
# ---------------------------------------------------------------------------

async def bets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bets = db.get_active_bets(update.message.chat_id)
    if not bets:
        await update.message.reply_text("🕊️ Немає активних парі. Ризикни жопою! /bet")
        return

    status_emoji = {
        BetStatus.PROPOSED: "⏳",
        BetStatus.ACTIVE:   "⚔️",
        BetStatus.CLAIMED:  "🏆",
        BetStatus.DISPUTED: "⚖️",
    }

    lines = ["📋 <b>Активні парі:</b>\n"]
    for bet in bets:
        proposer = db.get_user(bet["proposer_id"])
        opponent = db.get_user(bet["opponent_id"])
        emoji = status_emoji.get(bet["status"], "❓")
        lines.append(
            f"{emoji} <b>#{bet['id']}</b> {display_name(proposer)} vs {display_name(opponent)}"
            f" — {fmt(bet['amount'])}\n"
            f"   📋 {bet['description']}\n"
        )

    await update.message.reply_html("\n".join(lines))
