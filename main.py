import logging
import os
import psycopg2
import random
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, ContentType
)
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG ========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID environment variable is required")

ADMIN_ID = int(ADMIN_ID)

# Global States
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

user_edit_state = {}    # For text input edits
onboarding_state = {}   # For registration flow
active_chats = {}       # {user_id: partner_id} (Bidirectional)
waiting_queue = set()   # Users waiting for random match
report_state = {}       # {reporter_id: reported_id}
share_profile_state = {}  # {user_id: "awaiting_confirmation"} - for /shareprofile flow

upsell_shown = set()      # {user_id} - Track upsells
expiry_reminded = set()   # {user_id} - Track reminders

upsell_kb = ReplyKeyboardMarkup(resize_keyboard=True)
upsell_kb.add("⭐ Buy Premium", "⬅ Back to Menu")

# Predefined Interests for Selection
AVAILABLE_INTERESTS = [
    "Music", "Movies", "Sports", "Gaming", "Travel", 
    "Reading", "Food", "Tech", "Art", "Anime"
]

logging.basicConfig(level=logging.INFO)

# ================= DB CONNECTION =====================

try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
except Exception as e:
    logging.error(f"Database connection failed: {e}")
    exit(1)

# ================= HELPERS ===========================

def is_premium(user_id):
    try:
        cur.execute(
            "SELECT premium_until FROM users WHERE user_id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        return row and row[0] and row[0] > datetime.now()
    except Exception:
        return False

def get_blocked_users(user_id):
    """Get list of blocked users for a given user_id, handles NULL safely."""
    try:
        cur.execute(
            "SELECT blocked_users FROM users WHERE user_id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return row[0]
        return []
    except Exception:
        return []

def check_and_auto_ban(user_id):
    """Check report_count and auto-ban if threshold reached"""
    try:
        cur.execute(
            "SELECT report_count FROM users WHERE user_id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        # FIX: Explicit None check for safer handling
        if row and row[0] is not None and row[0] >= 3:
            cur.execute(
                "UPDATE users SET banned=true WHERE user_id=%s",
                (user_id,)
            )
            return True
        return False
    except Exception:
        return False

async def queue_timeout(uid):
    await asyncio.sleep(60)
    if uid in waiting_queue:
        waiting_queue.discard(uid)
        try:
            await bot.send_message(uid, "❌ No users active right now. Please try again later.", reply_markup=get_main_menu(uid))
        except Exception:
            pass

async def end_chat(user1, user2, notify_user1=True, notify_user2=True):
    """Safely disconnect two users and notify them."""
    # Update DB status with safety check
    try:
        cur.execute("UPDATE users SET is_online=false WHERE user_id IN (%s, %s)", (user1, user2))
    except Exception as e:
        logging.error(f"DB Error ending chat: {e}")

    # Remove from active chats
    if user1 in active_chats: del active_chats[user1]
    if user2 in active_chats: del active_chats[user2]

    # Notify users
    if notify_user1:
        try:
            await bot.send_message(user1, "❌ Chat ended.", reply_markup=get_main_menu(user1))
        except: pass
    
    if notify_user2:
        try:
            await bot.send_message(user2, "❌ Chat ended.", reply_markup=get_main_menu(user2))
        except: pass

async def connect_users(user1, user2):
    # FIX: Ensure symmetric state by ending existing chats first
    if user1 in active_chats:
        await end_chat(user1, active_chats[user1], notify_user1=True, notify_user2=True)
    if user2 in active_chats:
        await end_chat(user2, active_chats[user2], notify_user1=True, notify_user2=True)

    active_chats[user1] = user2
    active_chats[user2] = user1
    
    # Remove from waiting queue if present
    waiting_queue.discard(user1)
    waiting_queue.discard(user2)

    # Save last_chat_user_id for reconnect and set online
    try:
        cur.execute("""
            UPDATE users
            SET last_chat_user_id = %s, is_online = true
            WHERE user_id = %s
        """, (user2, user1))

        cur.execute("""
            UPDATE users
            SET last_chat_user_id = %s, is_online = true
            WHERE user_id = %s
        """, (user1, user2))
    except Exception as e:
        logging.error(f"Error connecting users DB: {e}")
    
    # Notify both users
    try:
        p1_badge = " (⭐ Premium User)" if is_premium(user1) else ""
        p2_badge = " (⭐ Premium User)" if is_premium(user2) else ""
        
        await bot.send_message(user1, f"✅ Match found! Start chatting...{p1_badge}", reply_markup=chat_kb)
        await bot.send_message(user2, f"✅ Match found! Start chatting...{p2_badge}", reply_markup=chat_kb)
    except Exception:
        # If a user blocked the bot, force disconnect
        await end_chat(user1, user2)
    
    # Premium feature: Show partner details to premium user
    try:
        # Check if user1 is premium
        if is_premium(user1):
            cur.execute("""
                SELECT age, gender, city, interests
                FROM users WHERE user_id = %s
            """, (user2,))
            partner_row = cur.fetchone()
            if partner_row:
                p_age, p_gender, p_city, p_interests = partner_row
                p_interests_text = p_interests if p_interests else "Not set"
                details_msg = (
                    f"ℹ️ *Partner Details* (Premium)\n\n"
                    f"🎂 Age: {p_age}\n"
                    f"⚧ Gender: {p_gender}\n"
                    f"🏙 City: {p_city}\n"
                    f"🎯 Interests: {p_interests_text}"
                )
                await bot.send_message(user1, details_msg, parse_mode="Markdown")
        else:
            await bot.send_message(user1, "🔒 Partner details hidden.\nUpgrade to Premium to see Age, Gender, City, and Interests.")
        
        # Check if user2 is premium
        if is_premium(user2):
            cur.execute("""
                SELECT age, gender, city, interests
                FROM users WHERE user_id = %s
            """, (user1,))
            partner_row = cur.fetchone()
            if partner_row:
                p_age, p_gender, p_city, p_interests = partner_row
                p_interests_text = p_interests if p_interests else "Not set"
                details_msg = (
                    f"ℹ️ *Partner Details* (Premium)\n\n"
                    f"🎂 Age: {p_age}\n"
                    f"⚧ Gender: {p_gender}\n"
                    f"🏙 City: {p_city}\n"
                    f"🎯 Interests: {p_interests_text}"
                )
                await bot.send_message(user2, details_msg, parse_mode="Markdown")
        else:
            await bot.send_message(user2, "🔒 Partner details hidden.\nUpgrade to Premium to see Age, Gender, City, and Interests.")
    except Exception as e:
        logging.error(f"Error showing partner details: {e}")

# ================= MENUS =================

premium_submenu = ReplyKeyboardMarkup(resize_keyboard=True)
premium_submenu.add("👨 Find a Man", "👩 Find a Woman")
premium_submenu.add("🎯 Find by Interests", "🏙 Find in My City")
premium_submenu.add("⬅ Back to Menu")

def get_main_menu(uid):
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    menu.add("🔍 Find Chat")
    if is_premium(uid):
        menu.add("💎 Premium Search")
    menu.add("⭐ Premium", "👤 Profile")
    menu.add("🎁 Invite & Earn", "📜 Rules")
    menu.add("⚙ Settings", "🔁 Reconnect")
    return menu

@dp.message_handler(text="💎 Premium Search")
async def open_premium_menu(message: types.Message):
    uid = message.from_user.id
    
    if uid in active_chats or uid in waiting_queue:
        return await message.answer("❌ Finish your current chat/search first.")

    if not is_premium(uid):
        if uid not in upsell_shown:
            upsell_shown.add(uid)
            await message.answer(
                "⭐ *Unlock Premium Logic*\n\n"
                "• Find by Gender (Man/Woman)\n"
                "• Find by Interests & City\n"
                "• See Partner Details\n"
                "• No Ads & Priority Support",
                parse_mode="Markdown",
                reply_markup=upsell_kb
            )
            return
        return await message.answer("⭐ This feature requires Premium.")
    await message.answer("💎 Choose an option:", reply_markup=premium_submenu)

@dp.message_handler(text="⬅ Back to Menu")
async def back_to_main_menu(message: types.Message):
    await message.answer("🏠 Main Menu", reply_markup=get_main_menu(message.from_user.id))

chat_kb = ReplyKeyboardMarkup(resize_keyboard=True)
chat_kb.add("🚫 Block", "🚨 Report")
chat_kb.add("⛔ Stop", "➡ Next")

def get_interest_kb(selected_interests):
    kb = InlineKeyboardMarkup(row_width=2)
    for interest in AVAILABLE_INTERESTS:
        prefix = "✅ " if interest in selected_interests else ""
        kb.insert(
            InlineKeyboardButton(
                f"{prefix}{interest}",
                callback_data=f"toggle_interest:{interest}"
            )
        )
    kb.add(InlineKeyboardButton("✔️ Done", callback_data="interests_done"))
    return kb

# ================= START & REGISTRATION =================

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = message.from_user.id
    cur.execute("SELECT age, banned FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()

    if row and row[1]:
        return await message.answer("🚫 You have been banned from using this bot.")

    if not row:
        cur.execute("""
            INSERT INTO users (user_id, username, age, gender, city, country, interests, blocked_users, premium_until)
            VALUES (%s, %s, 0, '', '', '', '', '{}', NOW() + INTERVAL '2 hours')
        """, (uid, message.from_user.username or ""))
        
        onboarding_state[uid] = "age"
        return await message.answer("Welcome! Let's set up your profile.\n\n🎂 Enter your age:")
    
    # Premium Expiry Reminder
    try:
        cur.execute("SELECT premium_until FROM users WHERE user_id=%s", (uid,))
        p_row = cur.fetchone()
        if p_row and p_row[0] and p_row[0] > datetime.now():
            time_left = p_row[0] - datetime.now()
            if time_left < timedelta(hours=24) and uid not in expiry_reminded:
                expiry_reminded.add(uid)
                await message.answer("⚠️ Your Premium expires in less than 24 hours! Renew now to keep benefits.")
    except Exception:
        pass

    await message.answer("Welcome back!", reply_markup=get_main_menu(uid))

# ================= PROFILE MENU =================

@dp.message_handler(text="👤 Profile")
@dp.message_handler(commands=["profile"])
async def profile(message: types.Message):
    uid = message.from_user.id
    
    try:
        cur.execute("""
            SELECT age, gender, city, country, interests, premium_until
            FROM users WHERE user_id=%s
        """, (uid,))
        row = cur.fetchone()
        
        if not row:
            return await message.answer("❌ No profile found. Please /start again.")
        
        age, gender, city, country, interests, premium_until = row
        
        premium_text = "⭐ Premium User" if premium_until and premium_until > datetime.now() else "❌ Not Active"
        
        # Premium Expiry Reminder
        if premium_until and premium_until > datetime.now():
            time_left = premium_until - datetime.now()
            if time_left < timedelta(hours=24) and uid not in expiry_reminded:
                expiry_reminded.add(uid)
                await message.answer("⚠️ Your Premium expires in less than 24 hours! Renew now to keep benefits.")
        interests_text = interests if interests else "Not set"
        
        profile_text = (
            f"👤 *Your Profile*\n\n"
            f"🎂 Age: {age}\n"
            f"⚧ Gender: {gender}\n"
            f"🏙 City: {city}\n"
            f"🌍 Country: {country}\n"
            f"🎯 Interests: {interests_text}\n"
            f"⭐ Premium: {premium_text}"
        )
        
        await message.answer(profile_text, parse_mode="Markdown", reply_markup=get_main_menu(uid))
    except Exception as e:
        logging.error(f"Profile error: {e}")
        await message.answer("❌ Error loading profile.")

# ================= SETTINGS =================

@dp.message_handler(text="⚙ Settings")
@dp.message_handler(commands=["settings"])
async def settings(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎂 Edit Age", callback_data="edit_age"))
    kb.add(InlineKeyboardButton("⚧ Edit Gender", callback_data="edit_gender"))
    kb.add(InlineKeyboardButton("🏙 Edit City", callback_data="edit_city"))
    kb.add(InlineKeyboardButton("🌍 Edit Country", callback_data="edit_country"))
    kb.add(InlineKeyboardButton("🎯 Edit Interests", callback_data="edit_interests"))
    await message.answer("⚙ Profile Settings:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_field(callback: types.CallbackQuery):
    field = callback.data.split("_")[1]
    
    if field == "interests":
        uid = callback.from_user.id
        try:
            cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
            row = cur.fetchone()
            selected = row[0].split(", ") if row and row[0] else []
            await callback.message.answer("🏷 Select your interests:", reply_markup=get_interest_kb(selected))
        except Exception as e:
            logging.error(f"Interests edit error: {e}")
            await callback.message.answer("❌ Error loading interests.")
    else:
        user_edit_state[callback.from_user.id] = field
        await callback.message.answer(f"Enter new value for *{field}*:", parse_mode="Markdown")
    
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("toggle_interest:"))
async def toggle_interest(callback: types.CallbackQuery):
    interest = callback.data.split(":")[1]
    uid = callback.from_user.id
    
    try:
        cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        selected = row[0].split(", ") if row and row[0] else []
        
        if interest in selected:
            selected.remove(interest)
        else:
            if not is_premium(uid) and len(selected) >= 3:
                await callback.answer("❌ Free users can select up to 3 interests.", show_alert=True)
                return
            selected.append(interest)
        
        await callback.message.edit_reply_markup(reply_markup=get_interest_kb(selected))
    except Exception as e:
        logging.error(f"Toggle interest error: {e}")
    
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "interests_done")
async def interests_done(callback: types.CallbackQuery):
    uid = callback.from_user.id
    
    try:
        cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        selected = row[0].split(", ") if row and row[0] else []
        interests_str = ", ".join(selected)
        
        cur.execute("UPDATE users SET interests=%s WHERE user_id=%s", (interests_str, uid))
        
        if uid in onboarding_state:
            del onboarding_state[uid]
            await callback.message.answer("✅ Profile complete!", reply_markup=get_main_menu(uid))
        else:
            await callback.message.answer(f"✅ Interests updated!\n\n🎯 {interests_str}", reply_markup=get_main_menu(uid))
    except Exception as e:
        logging.error(f"Interests done error: {e}")
        await callback.message.answer("❌ Error saving interests.")
    
    await callback.answer()

# ================= MATCHING =================

@dp.message_handler(text="🔍 Find Chat")
@dp.message_handler(commands=["find"])
async def find_chat(message: types.Message):
    uid = message.from_user.id
    
    cur.execute("SELECT banned FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0]:
        return await message.answer("🚫 You have been banned.")
    
    if uid in active_chats:
        return await message.answer("❌ You are already in a chat. Use ⛔ Stop to end it first.")
    
    if uid in waiting_queue:
        return await message.answer("⏳ Already searching...")
    
    blocked_users = get_blocked_users(uid)
    
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
          AND user_id NOT IN %s
          AND user_id NOT IN (
              SELECT unnest(blocked_users) FROM users WHERE user_id = %s
          )
          AND banned = false
    """, (uid, tuple(blocked_users) if blocked_users else (0,), uid))
    
    available = [r[0] for r in cur.fetchall() if r[0] in waiting_queue]
    
    if available:
        partner = random.choice(available)
        waiting_queue.discard(partner)
        await connect_users(uid, partner)
    else:
        waiting_queue.add(uid)
        await message.answer("🔄 Matching with a partner...", reply_markup=types.ReplyKeyboardRemove())
        asyncio.create_task(queue_timeout(uid))

@dp.message_handler(text="👨 Find a Man")
async def find_man(message: types.Message):
    uid = message.from_user.id
    
    if not is_premium(uid):
        if uid not in upsell_shown:
            upsell_shown.add(uid)
            await message.answer(
                "⭐ *Unlock Premium Logic*\n\n"
                "• Find by Gender (Man/Woman)\n"
                "• Find by Interests & City\n"
                "• See Partner Details\n"
                "• No Ads & Priority Support",
                parse_mode="Markdown",
                reply_markup=upsell_kb
            )
            return
        return await message.answer("⭐ This feature requires Premium.\nType /premium to upgrade.")
    
    cur.execute("SELECT banned FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0]:
        return await message.answer("🚫 You have been banned.")
    
    if uid in active_chats:
        return await message.answer("❌ Already in a chat.")
    
    if uid in waiting_queue:
        return await message.answer("⏳ Already searching...")
    
    blocked_users = get_blocked_users(uid)
    
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
          AND gender = 'Male'
          AND user_id NOT IN %s
          AND user_id NOT IN (
              SELECT unnest(blocked_users) FROM users WHERE user_id = %s
          )
          AND banned = false
    """, (uid, tuple(blocked_users) if blocked_users else (0,), uid))
    
    available = [r[0] for r in cur.fetchall() if r[0] in waiting_queue]
    
    if available:
        partner = random.choice(available)
        waiting_queue.discard(partner)
        await connect_users(uid, partner)
    else:
        waiting_queue.add(uid)
        await message.answer("🔄 Matching with a partner...", reply_markup=types.ReplyKeyboardRemove())
        asyncio.create_task(queue_timeout(uid))

@dp.message_handler(text="👩 Find a Woman")
async def find_woman(message: types.Message):
    uid = message.from_user.id
    
    if not is_premium(uid):
        if uid not in upsell_shown:
            upsell_shown.add(uid)
            await message.answer(
                "⭐ *Unlock Premium Logic*\n\n"
                "• Find by Gender (Man/Woman)\n"
                "• Find by Interests & City\n"
                "• See Partner Details\n"
                "• No Ads & Priority Support",
                parse_mode="Markdown",
                reply_markup=upsell_kb
            )
            return
        return await message.answer("⭐ This feature requires Premium.\nType /premium to upgrade.")
    
    cur.execute("SELECT banned FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0]:
        return await message.answer("🚫 You have been banned.")
    
    if uid in active_chats:
        return await message.answer("❌ Already in a chat.")
    
    if uid in waiting_queue:
        return await message.answer("⏳ Already searching...")
    
    blocked_users = get_blocked_users(uid)
    
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
          AND gender = 'Female'
          AND user_id NOT IN %s
          AND user_id NOT IN (
              SELECT unnest(blocked_users) FROM users WHERE user_id = %s
          )
          AND banned = false
    """, (uid, tuple(blocked_users) if blocked_users else (0,), uid))
    
    available = [r[0] for r in cur.fetchall() if r[0] in waiting_queue]
    
    if available:
        partner = random.choice(available)
        waiting_queue.discard(partner)
        await connect_users(uid, partner)
    else:
        waiting_queue.add(uid)
        await message.answer("🔄 Matching with a partner...", reply_markup=types.ReplyKeyboardRemove())
        asyncio.create_task(queue_timeout(uid))

@dp.message_handler(text="🎯 Find by Interests")
async def find_interests(message: types.Message):
    uid = message.from_user.id
    
    if not is_premium(uid):
        if uid not in upsell_shown:
            upsell_shown.add(uid)
            await message.answer(
                "⭐ *Unlock Premium Logic*\n\n"
                "• Find by Gender (Man/Woman)\n"
                "• Find by Interests & City\n"
                "• See Partner Details\n"
                "• No Ads & Priority Support",
                parse_mode="Markdown",
                reply_markup=upsell_kb
            )
            return
        return await message.answer("⭐ This feature requires Premium.\nType /premium to upgrade.")
    
    cur.execute("SELECT banned, interests FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0]:
        return await message.answer("🚫 You have been banned.")
    
    my_interests = row[1] if row and row[1] else ""
    if not my_interests:
        return await message.answer("❌ Set your interests first in ⚙ Settings.")
    
    if uid in active_chats:
        return await message.answer("❌ Already in a chat.")
    
    if uid in waiting_queue:
        return await message.answer("⏳ Already searching...")
    
    blocked_users = get_blocked_users(uid)
    
    cur.execute("""
        SELECT user_id, interests FROM users
        WHERE user_id != %s
          AND interests IS NOT NULL
          AND interests != ''
          AND user_id NOT IN %s
          AND user_id NOT IN (
              SELECT unnest(blocked_users) FROM users WHERE user_id = %s
          )
          AND banned = false
    """, (uid, tuple(blocked_users) if blocked_users else (0,), uid))
    
    my_set = set(my_interests.split(", "))
    candidates = []
    for r in cur.fetchall():
        partner_id, partner_interests = r
        if partner_id in waiting_queue:
            partner_set = set(partner_interests.split(", "))
            if my_set & partner_set:
                candidates.append(partner_id)
    
    if candidates:
        partner = random.choice(candidates)
        waiting_queue.discard(partner)
        await connect_users(uid, partner)
    else:
        waiting_queue.add(uid)
        await message.answer("🔄 Matching with a partner...", reply_markup=types.ReplyKeyboardRemove())
        asyncio.create_task(queue_timeout(uid))

@dp.message_handler(text="🏙 Find in My City")
async def find_city(message: types.Message):
    uid = message.from_user.id
    
    if not is_premium(uid):
        if uid not in upsell_shown:
            upsell_shown.add(uid)
            await message.answer(
                "⭐ *Unlock Premium Logic*\n\n"
                "• Find by Gender (Man/Woman)\n"
                "• Find by Interests & City\n"
                "• See Partner Details\n"
                "• No Ads & Priority Support",
                parse_mode="Markdown",
                reply_markup=upsell_kb
            )
            return
        return await message.answer("⭐ This feature requires Premium.\nType /premium to upgrade.")
    
    cur.execute("SELECT banned, city FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0]:
        return await message.answer("🚫 You have been banned.")
    
    my_city = row[1] if row and row[1] else ""
    if not my_city:
        return await message.answer("❌ Set your city first in ⚙ Settings.")
    
    if uid in active_chats:
        return await message.answer("❌ Already in a chat.")
    
    if uid in waiting_queue:
        return await message.answer("⏳ Already searching...")
    
    blocked_users = get_blocked_users(uid)
    
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
          AND city = %s
          AND user_id NOT IN %s
          AND user_id NOT IN (
              SELECT unnest(blocked_users) FROM users WHERE user_id = %s
          )
          AND banned = false
    """, (uid, my_city, tuple(blocked_users) if blocked_users else (0,), uid))
    
    available = [r[0] for r in cur.fetchall() if r[0] in waiting_queue]
    
    if available:
        partner = random.choice(available)
        waiting_queue.discard(partner)
        await connect_users(uid, partner)
    else:
        waiting_queue.add(uid)
        await message.answer("🔄 Matching with a partner...", reply_markup=types.ReplyKeyboardRemove())
        asyncio.create_task(queue_timeout(uid))

# ================= RECONNECT =================

@dp.message_handler(text="🔁 Reconnect")
@dp.message_handler(commands=["reconnect"])
async def reconnect(message: types.Message):
    uid = message.from_user.id
    
    if uid in active_chats:
        return await message.answer("❌ You are already in a chat.")
    
    if uid in waiting_queue:
        return await message.answer("❌ You are already searching.")
    
    try:
        cur.execute("SELECT last_chat_user_id FROM users WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        
        if not row or not row[0]:
            return await message.answer("❌ No previous chat found.")
        
        partner_id = row[0]
        
        if partner_id in active_chats or partner_id in waiting_queue:
            return await message.answer("❌ Partner is busy.")
        
        blocked_users = get_blocked_users(uid)
        if partner_id in blocked_users:
            return await message.answer("❌ Cannot reconnect with blocked user.")
        
        partner_blocked = get_blocked_users(partner_id)
        if uid in partner_blocked:
            return await message.answer("❌ Cannot reconnect.")
        
        await connect_users(uid, partner_id)
    
    except Exception as e:
        logging.error(f"Reconnect error: {e}")
        await message.answer("❌ Reconnect failed.")

# ================= CHAT ACTIONS =================

@dp.message_handler(text="⛔ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    
    if uid in active_chats:
        partner = active_chats[uid]
        await end_chat(uid, partner)
    else:
        await message.answer("❌ You are not in a chat.", reply_markup=get_main_menu(uid))

@dp.message_handler(text="➡ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.", reply_markup=get_main_menu(uid))
    
    partner = active_chats[uid]
    await end_chat(uid, partner)
    
    await find_chat(message)

@dp.message_handler(text="🚫 Block")
async def block_user(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ No active chat to block.")
    
    partner = active_chats[uid]
    
    try:
        cur.execute("""
            UPDATE users
            SET blocked_users = array_append(blocked_users, %s)
            WHERE user_id = %s AND NOT (%s = ANY(blocked_users))
        """, (partner, uid, partner))
        
        await end_chat(uid, partner)
        await message.answer("🚫 User blocked.", reply_markup=get_main_menu(uid))
    except Exception as e:
        logging.error(f"Block error: {e}")
        await message.answer("❌ Error blocking user.")

@dp.message_handler(text="🚨 Report")
async def report_init(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ No active chat to report.")
    
    partner = active_chats[uid]
    report_state[uid] = partner
    
    await message.answer(
        "🚨 *Report User*\n\n"
        "Choose a reason:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("Spam", callback_data="report_spam"),
            InlineKeyboardButton("Harassment", callback_data="report_harassment"),
            InlineKeyboardButton("Inappropriate Content", callback_data="report_inappropriate")
        )
    )

@dp.callback_query_handler(lambda c: c.data.startswith("report_"))
async def report_submit(callback: types.CallbackQuery):
    uid = callback.from_user.id
    
    if uid not in report_state:
        return await callback.answer("❌ Report expired.", show_alert=True)
    
    partner = report_state.pop(uid)
    
    try:
        cur.execute("""
            UPDATE users
            SET report_count = COALESCE(report_count, 0) + 1
            WHERE user_id = %s
        """, (partner,))
        
        if check_and_auto_ban(partner):
            await bot.send_message(partner, "🚫 You have been banned due to multiple reports.")
        
        await callback.message.answer("✅ Report submitted. Thank you.", reply_markup=get_main_menu(uid))
    except Exception as e:
        logging.error(f"Report error: {e}")
        await callback.message.answer("❌ Error submitting report.")
    
    await callback.answer()

# ================= /STOP COMMAND =================

@dp.message_handler(commands=["stop"])
async def stop_command(message: types.Message):
    uid = message.from_user.id
    
    if uid in active_chats:
        partner = active_chats[uid]
        await end_chat(uid, partner)
    elif uid in waiting_queue:
        waiting_queue.discard(uid)
        await message.answer("❌ Search cancelled.", reply_markup=get_main_menu(uid))
    else:
        await message.answer("❌ You are not in a chat or searching.", reply_markup=get_main_menu(uid))

# ================= /NEXT COMMAND =================

@dp.message_handler(commands=["next"])
async def next_command(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.", reply_markup=get_main_menu(uid))
    
    partner = active_chats[uid]
    await end_chat(uid, partner)
    
    await find_chat(message)

# ================= /SHAREPROFILE COMMAND =================

@dp.message_handler(commands=["shareprofile"])
async def shareprofile_init(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ You can only share your profile during an active chat.")
    
    share_profile_state[uid] = "awaiting_confirmation"
    
    await message.answer(
        "⚠️ *Warning*\n\n"
        "Sharing your profile may reveal personal details. "
        "Proceed only if you trust the other user.\n\n"
        "Type *YES* to confirm sharing your profile, or anything else to cancel.",
        parse_mode="Markdown"
    )

@dp.message_handler(lambda m: m.from_user.id in share_profile_state and share_profile_state[m.from_user.id] == "awaiting_confirmation")
async def shareprofile_confirm(message: types.Message):
    uid = message.from_user.id
    response = message.text.strip().upper()
    
    del share_profile_state[uid]
    
    if uid not in active_chats:
        return await message.answer("❌ Chat ended. Profile sharing cancelled.")
    
    if response != "YES":
        return await message.answer("❌ Profile sharing cancelled.")
    
    partner_id = active_chats[uid]
    
    try:
        cur.execute("""
            SELECT age, gender, city, interests
            FROM users WHERE user_id = %s
        """, (uid,))
        row = cur.fetchone()
        
        if not row:
            return await message.answer("❌ Profile data not found.")
        
        age, gender, city, interests = row
        interests_text = interests if interests else "Not set"
        
        shared_msg = (
            f"📤 *Partner shared their profile:*\n\n"
            f"🎂 Age: {age}\n"
            f"⚧ Gender: {gender}\n"
            f"🏙 City: {city}\n"
            f"🎯 Interests: {interests_text}"
        )
        
        await bot.send_message(partner_id, shared_msg, parse_mode="Markdown")
        await message.answer("✅ Your profile has been shared with your chat partner.")
        
    except Exception as e:
        logging.error(f"Profile sharing error: {e}")
        await message.answer("❌ Error sharing profile.")

# ================= PREMIUM & PAYMENTS =================

@dp.message_handler(text="⭐ Premium")
@dp.message_handler(commands=["premium"])
async def premium_menu(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⭐ 7 Days – 30 Stars", callback_data="buy_7"),
        InlineKeyboardButton("⭐ 30 Days – 120 Stars", callback_data="buy_30")
    )
    await message.answer("Upgrade to Premium", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy_callback(callback: types.CallbackQuery):
    days = 7 if callback.data == "buy_7" else 30
    stars = 30 if days == 7 else 120

    await bot.send_invoice(
        callback.message.chat.id,
        title="Chatogram Premium ⭐",
        description=f"Premium access for {days} days",
        payload=f"premium_{days}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Premium", stars)]
    )

@dp.pre_checkout_query_handler(lambda q: True)
async def pre_checkout(q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message_handler(content_types=ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    days = 7 if payload == "premium_7" else 30
    
    cur.execute("""
        UPDATE users
        SET premium_until = COALESCE(premium_until, NOW()) + INTERVAL '%s days'
        WHERE user_id = %s
    """, (days, message.from_user.id))
    
    await message.answer(f"⭐ Premium activated for {days} days!", reply_markup=get_main_menu(message.from_user.id))

# ================= ADMIN =================

@dp.message_handler(lambda m: m.from_user.id in user_edit_state)
async def save_profile_edit(message: types.Message):
    field = user_edit_state.pop(message.from_user.id)
    value = message.text.strip()
    
    try:
        cur.execute(
            f"UPDATE users SET {field}=%s WHERE user_id=%s",
            (value, message.from_user.id)
        )
        await message.answer(f"✅ {field.capitalize()} updated!", reply_markup=get_main_menu(message.from_user.id))
    except Exception as e:
        await message.answer("❌ Error updating profile.")

@dp.message_handler(commands=["addpremium"])
async def add_premium_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        uid = int(parts[1])
        days = int(parts[2])
        cur.execute("""
            UPDATE users
            SET premium_until = COALESCE(premium_until, NOW()) + INTERVAL '%s days'
            WHERE user_id = %s
        """, (days, uid))
        await message.answer(f"⭐ Added {days} days to {uid}")
    except:
        await message.answer("Usage: /addpremium <uid> <days>")

@dp.message_handler(commands=["ban"])
async def ban_user_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid = int(message.get_args())
        cur.execute("UPDATE users SET banned=true WHERE user_id=%s", (uid,))
        await message.answer(f"🚫 User {uid} banned.")
    except:
        await message.answer("Usage: /ban <uid>")

# ================= ONBOARDING =================

@dp.message_handler(lambda m: m.from_user.id in onboarding_state)
async def onboarding_handler(message: types.Message):
    uid = message.from_user.id
    step = onboarding_state[uid]
    text = message.text.strip()

    if step == "age":
        if not text.isdigit() or not (13 <= int(text) <= 80):
            return await message.answer("❌ Enter a valid age (13–80):")
        cur.execute("UPDATE users SET age=%s WHERE user_id=%s", (int(text), uid))
        onboarding_state[uid] = "gender"
        return await message.answer("👤 Enter your gender (Male/Female):")

    elif step == "gender":
        cur.execute("UPDATE users SET gender=%s WHERE user_id=%s", (text, uid))
        onboarding_state[uid] = "city"
        return await message.answer("🏙 Enter your city:")

    elif step == "city":
        cur.execute("UPDATE users SET city=%s WHERE user_id=%s", (text, uid))
        onboarding_state[uid] = "country"
        return await message.answer("🌍 Enter your country:")

    elif step == "country":
        cur.execute("UPDATE users SET country=%s WHERE user_id=%s", (text, uid))
        onboarding_state[uid] = "interests"
        
        cur.execute("UPDATE users SET interests='' WHERE user_id=%s", (uid,))
        await message.answer("🏷 Now select your interests!", reply_markup=get_interest_kb([]))

# ================= OTHER =================

@dp.message_handler(text="🎁 Invite & Earn")
@dp.message_handler(commands=["invite"])
async def invite(message: types.Message):
    link = f"https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}"
    await message.answer(f"Invite friends:\n{link}\n\n🎁 Referral rewards coming soon.")

@dp.message_handler(text="📜 Rules")
@dp.message_handler(commands=["rules"])
async def rules(message: types.Message):
    await message.answer("1️⃣ No abuse\n2️⃣ No spam\n3️⃣ No illegal content\n4️⃣ Respect privacy")

# Catch-all for active chat messages
@dp.message_handler(content_types=ContentType.ANY)
async def chat_relay(message: types.Message):
    if message.text and message.text.startswith('/'):
        return

    uid = message.from_user.id
    if uid in active_chats:
        partner = active_chats[uid]
        try:
            await message.copy_to(partner)
        except Exception:
            await end_chat(uid, partner)

async def on_startup(dp):
    await bot.set_my_commands([
        types.BotCommand("start", "Start/Restart"),
        types.BotCommand("find", "Random Chat"),
        types.BotCommand("profile", "My Profile"),
        types.BotCommand("settings", "Edit Profile"),
        types.BotCommand("premium", "Get Premium"),
        types.BotCommand("rules", "Read Rules"),
        types.BotCommand("stop", "Stop Current Chat"),
        types.BotCommand("next", "Next Chat"),
        types.BotCommand("shareprofile", "Share Your Profile"),
    ])

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)