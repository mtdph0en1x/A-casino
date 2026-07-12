"""
handlers/events.py — /event, /beton, /resolve, and callbacks
"""
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import database as db
from models import fmt, display_name

logger = logging.getLogger(__name__)

def _register(tg_user, chat_id):
    return db.get_or_create_user(
        tg_user.id, 
        tg_user.username, 
        tg_user.first_name,
        chat_id if chat_id != tg_user.id else None
    )

def _event_keyboard(event_id: int) -> InlineKeyboardMarkup:
    # Instead of inline queries, we just use simple callback alerts telling them what to type.
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 ЗА", callback_data=f"ev|for|{event_id}"),
        InlineKeyboardButton("👎 ПРОТИ", callback_data=f"ev|against|{event_id}"),
    ]])

def _render_event_text(event: dict, bets: list, creator: dict) -> str:
    for_bets = [b for b in bets if b["side"] == "FOR"]
    against_bets = [b for b in bets if b["side"] == "AGAINST"]
    
    for_pool = sum(b["amount"] for b in for_bets)
    against_pool = sum(b["amount"] for b in against_bets)
    
    status_emoji = "🟢 ВІДКРИТО" if event["status"] == "OPEN" else f"🏁 ЗАВЕРШЕНО ({event['status']})"
    
    lines = [
        f"📢 <b>ПОДІЯ #{event['id']}</b> | {status_emoji}",
        f"━━━━━━━━━━━━━━",
        f"📋 <b>{event['description']}</b>",
        f"👤 Автор: {display_name(creator)}\n",
        f"👍 <b>ЗА (Пул: {fmt(for_pool)}):</b>"
    ]
    
    for b in for_bets:
        u = db.get_user(b["user_id"])
        lines.append(f"  • {display_name(u)}: {fmt(b['amount'])}")
    if not for_bets:
        lines.append("  • (пусто)")
        
    lines.append(f"\n👎 <b>ПРОТИ (Пул: {fmt(against_pool)}):</b>")
    for b in against_bets:
        u = db.get_user(b["user_id"])
        lines.append(f"  • {display_name(u)}: {fmt(b['amount'])}")
    if not against_bets:
        lines.append("  • (пусто)")
        
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /event — create a pool betting event
# ---------------------------------------------------------------------------
async def event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = _register(msg.from_user, msg.chat_id)

    description = " ".join(context.args).strip()
    if not description:
        await msg.reply_text("❌ Вкажи умову події.\nФормат: /event Динамо сьогодні виграє")
        return

    event_id = db.create_event(msg.chat_id, user["user_id"], description)
    event = db.get_event(event_id)
    
    text = _render_event_text(event, [], user)
    sent = await msg.reply_html(text, reply_markup=_event_keyboard(event_id))
    db.update_event(event_id, message_id=sent.message_id)


# ---------------------------------------------------------------------------
# Callbacks — click ЗА / ПРОТИ
# ---------------------------------------------------------------------------
async def event_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    side = parts[1].upper()
    event_id = int(parts[2])
    
    user = _register(query.from_user, query.message.chat_id)
    amount = 0.25

    event = db.get_event(event_id)
    if not event or event.get("chat_id") != query.message.chat_id:
        await query.answer("❌ Подія не знайдена в цьому чаті.", show_alert=True)
        return
        
    if event.get("status") != "OPEN":
        await query.answer("❌ Ставки на цю подію вже закриті.", show_alert=True)
        return

    # Check for existing bet to increment, or prevent betting on both sides
    existing_bets = db.get_event_bets(event_id)
    existing_bet = next((b for b in existing_bets if b["user_id"] == user["user_id"]), None)
    
    if existing_bet:
        if existing_bet["side"] == side:
            amount += existing_bet["amount"]
        else:
            await query.answer("❌ Ти вже поставив на іншу сторону!", show_alert=True)
            return

    db.place_event_bet(event_id, user["user_id"], side, amount)
    
    side_ua = "ЗА" if side == "FOR" else "ПРОТИ"
    await query.answer(f"✅ Твоя ставка на {side_ua} тепер {amount}!")

    # Update the event message
    bets = db.get_event_bets(event_id)
    creator = db.get_user(event["creator_id"])
    new_text = _render_event_text(event, bets, creator)
    
    try:
        await query.edit_message_text(
            text=new_text,
            parse_mode="HTML",
            reply_markup=_event_keyboard(event_id)
        )
    except Exception as e:
        logger.error(f"Failed to update event message: {e}")


# ---------------------------------------------------------------------------
# /beton <id> <for/against> <amount>
# ---------------------------------------------------------------------------
async def beton_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = _register(msg.from_user, msg.chat_id)
    
    args = context.args or []
    if len(args) < 3:
        await msg.reply_text("❌ Формат: /beton <id> <за/проти> <сума>")
        return
        
    try:
        event_id = int(args[0].lstrip("#"))
    except ValueError:
        await msg.reply_text("❌ Невірний ID події.")
        return
        
    side_str = args[1].lower()
    if side_str in ("за", "for"):
        side = "FOR"
    elif side_str in ("проти", "against"):
        side = "AGAINST"
    else:
        await msg.reply_text("❌ Сторона має бути 'за' або 'проти'.")
        return
        
    try:
        amount = float(args[2])
    except ValueError:
        await msg.reply_text("❌ Невірна сума.")
        return
        
    if amount <= 0:
        await msg.reply_text("❌ Сума повинна бути більше 0.")
        return

    event = db.get_event(event_id)
    if not event or event.get("chat_id") != msg.chat_id:
        await msg.reply_text(f"❌ Подія #{event_id} не знайдена в цьому чаті.")
        return
        
    if event.get("status") != "OPEN":
        await msg.reply_text("❌ Ставки на цю подію вже закриті.")
        return

    db.place_event_bet(event_id, user["user_id"], side, amount)
    
    # Try to delete the user's /beton command to keep chat clean
    try:
        await msg.delete()
    except Exception:
        pass
    
    # Update the event message
    bets = db.get_event_bets(event_id)
    creator = db.get_user(event["creator_id"])
    new_text = _render_event_text(event, bets, creator)
    
    try:
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=event["message_id"],
            text=new_text,
            parse_mode="HTML",
            reply_markup=_event_keyboard(event_id)
        )
    except Exception as e:
        logger.error(f"Failed to update event message: {e}")
        pass


# ---------------------------------------------------------------------------
# /resolve <id> <for/against/cancel>
# ---------------------------------------------------------------------------
async def resolve_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = _register(msg.from_user, msg.chat_id)

    args = context.args or []
    if len(args) < 2:
        await msg.reply_text("❌ Формат: /resolve <id> <за/проти/відміна>")
        return

    try:
        event_id = int(args[0].lstrip("#"))
    except ValueError:
        await msg.reply_text("❌ Невірний ID події.")
        return

    outcome_str = args[1].lower()
    if outcome_str in ("за", "for"):
        outcome = "FOR"
    elif outcome_str in ("проти", "against"):
        outcome = "AGAINST"
    elif outcome_str in ("відміна", "cancel"):
        outcome = "CANCEL"
    else:
        await msg.reply_text("❌ Результат має бути 'за', 'проти' або 'відміна'.")
        return

    event = db.get_event(event_id)
    if not event or event.get("chat_id") != msg.chat_id:
        await msg.reply_text(f"❌ Подія #{event_id} не знайдена в цьому чаті.")
        return

    if event.get("status") != "OPEN":
        await msg.reply_text("❌ Ця подія вже закрита.")
        return

    if user["user_id"] != event["creator_id"]:
        await msg.reply_text("❌ Тільки автор події може її завершити.")
        return

    result = db.resolve_event(event_id, outcome)
    
    # Update the event message one last time to remove keyboard
    updated_event = db.get_event(event_id)
    bets = db.get_event_bets(event_id)
    creator = db.get_user(event["creator_id"])
    new_text = _render_event_text(updated_event, bets, creator)
    
    try:
        await context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=event["message_id"],
            text=new_text,
            parse_mode="HTML"
        )
    except Exception:
        pass

    if outcome == "CANCEL":
        await msg.reply_text(f"❌ Подію #{event_id} скасовано. Жопи залишились на місцях.")
        return

    if result["losing_pool"] > 0 and result["winners_count"] > 0:
        await msg.reply_html(
            f"🏁 <b>Подію #{event_id} завершено!</b>\n"
            f"Перемогла сторона: <b>{'👍 ЗА' if outcome == 'FOR' else '👎 ПРОТИ'}</b>\n\n"
            f"Переможці роздерибанили <b>{fmt(result['losing_pool'])}</b> з пулу тих хто програв! 🎉"
        )
    else:
        await msg.reply_html(
            f"🏁 <b>Подію #{event_id} завершено!</b>\n"
            f"Ніхто нічого не програв і не виграв."
        )

# ---------------------------------------------------------------------------
# /events — list active events in this chat
# ---------------------------------------------------------------------------
async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = db.get_active_events(update.message.chat_id)
    if not events:
        await update.message.reply_text("🕊️ Немає відкритих подій. Створи свою: /event")
        return

    lines = ["📋 <b>Відкриті події:</b>\n"]
    keyboard = []
    
    for event in events:
        creator = db.get_user(event["creator_id"])
        bets = db.get_event_bets(event["id"])
        
        for_pool = sum(b["amount"] for b in bets if b["side"] == "FOR")
        against_pool = sum(b["amount"] for b in bets if b["side"] == "AGAINST")
        
        lines.append(
            f"🟢 <b>#{event['id']}</b> {event['description']}\n"
            f"   👤 {display_name(creator)} | Пул: {fmt(for_pool)} ЗА / {fmt(against_pool)} ПРОТИ\n"
        )
        keyboard.append([InlineKeyboardButton(f"Відкрити #{event['id']}", callback_data=f"sev|{event['id']}")])

    await update.message.reply_html("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))

# ---------------------------------------------------------------------------
# Callbacks — click Відкрити подію
# ---------------------------------------------------------------------------
async def show_event_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    event_id = int(query.data.split("|")[1])
    
    event = db.get_event(event_id)
    if not event or event.get("chat_id") != query.message.chat_id:
        await query.answer("❌ Подія не знайдена в цьому чаті.", show_alert=True)
        return
        
    bets = db.get_event_bets(event_id)
    creator = db.get_user(event["creator_id"])
    text = _render_event_text(event, bets, creator)
    
    # Send a new message with the event details and buttons
    sent = await query.message.reply_html(text, reply_markup=_event_keyboard(event_id))
    db.update_event(event_id, message_id=sent.message_id)
    
    await query.answer()


# ---------------------------------------------------------------------------
# /ehistory — list resolved events in this chat
# ---------------------------------------------------------------------------
async def ehistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = db.get_event_history(update.message.chat_id, limit=10)
    if not events:
        await update.message.reply_text("📜 Ще не було завершених подій.")
        return

    lines = ["📜 <b>Останні події:</b>\n━━━━━━━━━━━━━━"]
    for event in events:
        creator = db.get_user(event["creator_id"])
        status = event.get("status", "")
        
        resolved = ""
        if event.get("resolved_at"):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(event["resolved_at"])
                resolved = dt.strftime("%d.%m.%Y")
            except Exception:
                pass

        if status == "CANCELED":
            result = "❌ Скасовано"
        elif status == "RESOLVED_FOR":
            result = "👍 ЗА"
        elif status == "RESOLVED_AGAINST":
            result = "👎 ПРОТИ"
        else:
            result = status

        lines.append(
            f"<b>#{event['id']}</b> {event['description']}\n"
            f"   👤 Автор: {display_name(creator)}\n"
            f"   🏁 Перемогло: <b>{result}</b> • {resolved}\n"
        )

    await update.message.reply_html("\n".join(lines))
