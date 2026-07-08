"""
handlers/resolve.py — /claim, confirm/dispute callbacks, vote callbacks
"""
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import database as db
from models import BetStatus, MIN_VOTES_TO_RESOLVE, fmt, display_name

logger = logging.getLogger(__name__)


def _register(tg_user):
    return db.get_or_create_user(tg_user.id, tg_user.username, tg_user.first_name)


def _confirm_keyboard(bet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Підтверджую", callback_data=f"bc|{bet_id}"),
        InlineKeyboardButton("⚔️ Оскаржую",   callback_data=f"bdi|{bet_id}"),
    ]])


def _vote_keyboard(bet_id: int, proposer, opponent) -> InlineKeyboardMarkup:
    p_votes = db.count_votes_for(bet_id, proposer["user_id"])
    o_votes = db.count_votes_for(bet_id, opponent["user_id"])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🏆 {display_name(proposer)} ({p_votes})",
            callback_data=f"bv|{bet_id}|{proposer['user_id']}",
        ),
        InlineKeyboardButton(
            f"🏆 {display_name(opponent)} ({o_votes})",
            callback_data=f"bv|{bet_id}|{opponent['user_id']}",
        ),
    ]])


# ---------------------------------------------------------------------------
# /claim <bet_id> — declare yourself the winner
# ---------------------------------------------------------------------------

async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = _register(msg.from_user)

    if not context.args:
        await msg.reply_text("❌ Вкажи номер парі: /claim <id>")
        return

    try:
        bet_id = int(context.args[0])
    except ValueError:
        await msg.reply_text("❌ Невірний номер парі.")
        return

    bet = db.get_bet(bet_id)
    if not bet or bet["chat_id"] != msg.chat_id:
        await msg.reply_text(f"❌ Парі #{bet_id} не знайдено.")
        return

    if bet["status"] != BetStatus.ACTIVE:
        await msg.reply_text(f"❌ Парі #{bet_id} не є активним (статус: {bet['status']}).")
        return

    uid = user["user_id"]
    if uid not in (bet["proposer_id"], bet["opponent_id"]):
        await msg.reply_text("❌ Ти не учасник цього парі.")
        return

    # Determine the other party
    other_id = bet["opponent_id"] if uid == bet["proposer_id"] else bet["proposer_id"]
    other = db.get_user(other_id)

    db.update_bet(bet_id, status=BetStatus.CLAIMED, claimer_id=uid)

    text = (
        f"🏆 <b>{display_name(user)} заявляє що виграв парі #{bet_id}!</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📋 Умова: {bet['description']}\n"
        f"💰 Ставка: {fmt(bet['amount'])}\n\n"
        f"{display_name(other)}, підтверджуєш? 👀"
    )
    sent = await msg.reply_html(text, reply_markup=_confirm_keyboard(bet_id))
    db.update_bet(bet_id, claim_message_id=sent.message_id)


# ---------------------------------------------------------------------------
# Callbacks — confirm / dispute
# ---------------------------------------------------------------------------

async def resolve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    action = parts[0]
    bet_id = int(parts[1])

    bet = db.get_bet(bet_id)
    if not bet:
        await query.edit_message_text("❌ Парі не знайдено.")
        return

    if bet["status"] != BetStatus.CLAIMED:
        await query.answer("Це парі вже вирішено.", show_alert=True)
        return

    user = _register(query.from_user)
    uid = user["user_id"]

    # Only the non-claimer participant can confirm/dispute
    claimer_id = bet["claimer_id"]
    other_id = bet["opponent_id"] if claimer_id == bet["proposer_id"] else bet["proposer_id"]

    if uid != other_id:
        await query.answer("Тільки інший учасник може підтвердити або оскаржити.", show_alert=True)
        return

    claimer = db.get_user(claimer_id)
    proposer = db.get_user(bet["proposer_id"])
    opponent = db.get_user(bet["opponent_id"])
    loser_id = other_id  # the one who confirms/disputes is the loser if confirming

    if action == "bc":  # confirm — claimer wins
        winner_id = claimer_id
        loser_id = other_id
        db.transfer_balance(winner_id, loser_id, bet["amount"])
        db.update_bet(
            bet_id,
            status=BetStatus.CONFIRMED,
            resolved_at=datetime.utcnow().isoformat(),
        )
        winner = db.get_user(winner_id)
        loser = db.get_user(loser_id)
        text = (
            f"🎉 <b>Парі #{bet_id} вирішено!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📋 Умова: {bet['description']}\n\n"
            f"🏆 Переможець: <b>{display_name(winner)}</b>  +{fmt(bet['amount'])}\n"
            f"💀 Програв: {display_name(loser)}  -{fmt(bet['amount'])}\n\n"
            f"Баланс:\n"
            f"  {display_name(winner)}: {fmt(winner['balance'])}\n"
            f"  {display_name(loser)}: {fmt(loser['balance'])}"
        )
        await query.edit_message_text(text, parse_mode="HTML")

    elif action == "bdi":  # dispute — start group vote
        db.update_bet(bet_id, status=BetStatus.DISPUTED)
        text = (
            f"⚔️ <b>Суперечка в парі #{bet_id}!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚔️ {display_name(proposer)} vs {display_name(opponent)}\n"
            f"📋 Умова: {bet['description']}\n"
            f"💰 Ставка: {fmt(bet['amount'])}\n\n"
            f"Хто виграв? Голосуємо! (потрібно {MIN_VOTES_TO_RESOLVE} голоси)\n"
            f"<i>Учасники не можуть голосувати.</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_vote_keyboard(bet_id, proposer, opponent),
        )


# ---------------------------------------------------------------------------
# Vote callback
# ---------------------------------------------------------------------------

async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, bet_id_str, voted_for_str = query.data.split("|")
    bet_id = int(bet_id_str)
    voted_for_id = int(voted_for_str)

    bet = db.get_bet(bet_id)
    if not bet:
        await query.edit_message_text("❌ Парі не знайдено.")
        return

    if bet["status"] != BetStatus.DISPUTED:
        await query.answer("Голосування вже закрито.", show_alert=True)
        return

    user = _register(query.from_user)
    uid = user["user_id"]

    # Participants cannot vote in their own dispute
    if uid in (bet["proposer_id"], bet["opponent_id"]):
        await query.answer("Учасники не можуть голосувати у своїй суперечці! 😅", show_alert=True)
        return

    db.add_vote(bet_id, uid, voted_for_id)

    proposer = db.get_user(bet["proposer_id"])
    opponent = db.get_user(bet["opponent_id"])
    p_votes = db.count_votes_for(bet_id, bet["proposer_id"])
    o_votes = db.count_votes_for(bet_id, bet["opponent_id"])
    total_votes = p_votes + o_votes

    # Check if we have enough votes to resolve
    if total_votes >= MIN_VOTES_TO_RESOLVE:
        winner = proposer if p_votes >= o_votes else opponent
        loser = opponent if p_votes >= o_votes else proposer
        winner_votes = p_votes if p_votes >= o_votes else o_votes
        loser_votes = o_votes if p_votes >= o_votes else p_votes

        db.transfer_balance(winner["user_id"], loser["user_id"], bet["amount"])
        db.update_bet(
            bet_id,
            status=BetStatus.CONFIRMED,
            resolved_at=datetime.utcnow().isoformat(),
        )

        winner_fresh = db.get_user(winner["user_id"])
        loser_fresh = db.get_user(loser["user_id"])

        text = (
            f"🗳️ <b>Голосування закрито! Парі #{bet_id} вирішено!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📋 Умова: {bet['description']}\n\n"
            f"Результат: {winner_votes}:{loser_votes} на користь {display_name(winner)}\n\n"
            f"🏆 Переможець: <b>{display_name(winner)}</b>  +{fmt(bet['amount'])}\n"
            f"💀 Програв: {display_name(loser)}  -{fmt(bet['amount'])}\n\n"
            f"Баланс:\n"
            f"  {display_name(winner_fresh)}: {fmt(winner_fresh['balance'])}\n"
            f"  {display_name(loser_fresh)}: {fmt(loser_fresh['balance'])}"
        )
        await query.edit_message_text(text, parse_mode="HTML")
    else:
        # Update vote counts on the keyboard
        text = (
            f"⚔️ <b>Суперечка в парі #{bet_id}!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚔️ {display_name(proposer)} vs {display_name(opponent)}\n"
            f"📋 Умова: {bet['description']}\n"
            f"💰 Ставка: {fmt(bet['amount'])}\n\n"
            f"Хто виграв? Голосуємо! ({total_votes}/{MIN_VOTES_TO_RESOLVE} голосів)\n"
            f"<i>Учасники не можуть голосувати.</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_vote_keyboard(bet_id, proposer, opponent),
        )
