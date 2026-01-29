import logging
import os
import psycopg2
import random
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
        return row and row[0] and row[0] > datetime.utcnow()
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
        if row and row[0]:
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
        if row and row[0] and row[0] >= 3:
            cur.execute(
                "UPDATE users SET banned=true WHERE user_id=%s",
                (user_id,)
            )
            return True
        return False
    except Exception:
        return False

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
            await bot.send_message(user1, "❌ Chat ended.", reply_markup=main_menu)
        except: pass
    
    if notify_user2:
        try:
            await bot.send_message(user2, "❌ Chat ended.", reply_markup=main_menu)
        except: pass

async def connect_users(user1, user2):
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
        await bot.send_message(user1, "✅ Match found! Start chatting...", reply_markup=chat_kb)
        await bot.send_message(user2, "✅ Match found! Start chatting...", reply_markup=chat_kb)
    except Exception:
        # If a user blocked the bot, force disconnect
        await end_chat(user1, user2)

# ================= MENUS =================

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add("🔍 Find Chat")
main_menu.add("👨 Find a Man", "👩 Find a Woman")
main_menu.add("🎯 Find by Interests", "🏙 Find in My City")
main_menu.add("⭐ Premium", "👤 Profile")
main_menu.add("🎁 Invite & Earn", "📜 Rules")
main_menu.add("⚙ Settings", "🔁 Reconnect")

chat_kb = ReplyKeyboardMarkup(resize_keyboard=True)
chat_kb.add("🚫 Block", "🚨 Report")
chat_kb.add("⛔ Stop", "⏭ Next")

# ================= HANDLERS =================

@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    username = message.from_user.username

    cur.execute("SELECT age FROM users WHERE user_id=%s", (uid,))
    user = cur.fetchone()

    if user and user[0] is not None:
        await message.answer(
            "👋 Welcome back to *Chatogram*",
            reply_markup=main_menu,
            parse_mode="Markdown"
        )
        return

    # New user → insert
    cur.execute("""
        INSERT INTO users (user_id, username)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO NOTHING
    """, (uid, username))

    onboarding_state[uid] = "age"
    await message.answer(
        "👋 Welcome to *Chatogram*\n\nLet’s set up your profile.\n\n📌 Enter your *age*:",
        parse_mode="Markdown"
    )

@dp.message_handler(text="⚙ Settings")
@dp.message_handler(commands=["settings"])
async def settings_menu(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✏ Edit Profile", "✏ Edit Interests")
    kb.add("⬅ Back")

    await message.answer(
        "⚙ *Settings*\nChoose an option:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message_handler(text="⬅ Back")
async def back_to_main(message: types.Message):
    await message.answer(
        "⬅ Back to main menu",
        reply_markup=main_menu
    )

@dp.message_handler(commands=["profile"])
@dp.message_handler(text="👤 Profile")
async def profile(message: types.Message):
    uid = message.from_user.id

    cur.execute("""
        SELECT age, gender, city, country, interests, premium_until
        FROM users WHERE user_id = %s
    """, (uid,))
    row = cur.fetchone()

    if not row:
        await message.answer("❌ Profile not found.")
        return

    age, gender, city, country, interests, premium_until = row

    interests_text = interests if interests else "Not set"
    premium_badge = " ⭐" if premium_until and premium_until > datetime.utcnow() else ""

    await message.answer(
        f"👤 *Your Profile{premium_badge}*\n\n"
        f"🎂 Age: {age}\n"
        f"⚧ Gender: {gender}\n"
        f"🏙 City: {city}\n"
        f"🌍 Country: {country}\n"
        f"🎯 Interests: {interests_text}",
        parse_mode="Markdown"
    )

@dp.message_handler(text="✏ Edit Profile")
async def edit_profile_entry(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✏ Edit Age", "✏ Edit Gender")
    kb.add("✏ Edit City", "✏ Edit Country")
    kb.add("⬅ Back")

    await message.answer(
        "✏ *Edit Profile*\nWhat do you want to change?",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# --- Profile Text Edits ---
@dp.message_handler(text="✏ Edit Age")
async def edit_age(message: types.Message):
    user_edit_state[message.from_user.id] = "age"
    await message.answer("Enter your age:")

@dp.message_handler(text="✏ Edit Gender")
async def edit_gender(message: types.Message):
    user_edit_state[message.from_user.id] = "gender"
    await message.answer("Enter your gender (Male/Female):")

@dp.message_handler(text="✏ Edit City")
async def edit_city(message: types.Message):
    user_edit_state[message.from_user.id] = "city"
    await message.answer("Enter your city:")

@dp.message_handler(text="✏ Edit Country")
async def edit_country(message: types.Message):
    user_edit_state[message.from_user.id] = "country"
    await message.answer("Enter your country:")

# --- Interest System ---

def get_interest_kb(selected_interests):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = []
    for intr in AVAILABLE_INTERESTS:
        text = f"✅ {intr}" if intr in selected_interests else intr
        buttons.append(InlineKeyboardButton(text, callback_data=f"intr_{intr}"))
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("💾 Save", callback_data="intr_save"))
    return kb

@dp.message_handler(text="✏ Edit Interests")
async def edit_interests(message: types.Message):
    uid = message.from_user.id
    cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    current = row[0].split(",") if row and row[0] else []
    current = [x.strip() for x in current if x.strip()]
    
    await message.answer(
        "🏷 Select your interests:",
        reply_markup=get_interest_kb(current)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("intr_"))
async def interest_callback(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    uid = callback.from_user.id
    
    cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    current = row[0].split(",") if row and row[0] else []
    current = [x.strip() for x in current if x.strip()]

    if action == "save":
        if uid in onboarding_state and onboarding_state[uid] == "interests":
            del onboarding_state[uid]
            await callback.message.answer("✅ Profile setup complete! You can now start chatting.", reply_markup=main_menu)
            await callback.message.delete()
        else:
            await callback.message.answer("✅ Interests saved!", reply_markup=main_menu)
            await callback.message.delete()
        return

    # Toggle logic
    interest = action
    if interest in current:
        current.remove(interest)
    else:
        limit = 100 if is_premium(uid) else 3
        if len(current) >= limit:
            await callback.answer(f"🔒 Free limit reached ({limit}). Upgrade for more!", show_alert=True)
            return
        current.append(interest)
    
    new_interests_str = ",".join(current)
    cur.execute("UPDATE users SET interests=%s WHERE user_id=%s", (new_interests_str, uid))
    
    await callback.message.edit_reply_markup(reply_markup=get_interest_kb(current))

# ================= MATCHING LOGIC =================

async def find_match_generic(message: types.Message, query_condition, query_params, allow_queue=True):
    uid = message.from_user.id

    # FIX 1: Set is_online=true BEFORE DB matching
    try:
        cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))
    except Exception:
        pass

    # 1. CHECK WAITING QUEUE FIRST
    # Only use queue for generic searches to ensure specific filters are respected
    if not query_condition:
        for waiting_user in list(waiting_queue):
            if waiting_user != uid:
                waiting_queue.remove(waiting_user)
                return await connect_users(uid, waiting_user)
    
    # 2. CHECK BLOCKING AND DB MATCH
    final_query = f"""
        SELECT user_id FROM users
        WHERE user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND NOT (%s = ANY(COALESCE(blocked_users, '{{}}')))
        {query_condition}
        ORDER BY RANDOM()
        LIMIT 1
    """
    
    # Update params: uid for !=, uid for blocked_users check, then query params
    full_params = (uid, uid) + query_params

    try:
        cur.execute(final_query, full_params)
        partner = cur.fetchone()
    except Exception as e:
        logging.error(f"Match Query Error: {e}")
        partner = None

    if not partner:
        # FIX 2: Add allow_queue flag to prevent filtered searches entering waiting_queue
        if allow_queue and not query_condition:
            waiting_queue.add(uid)
            
        return await message.answer(
            "❌ No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="🔍 Find Chat")
@dp.message_handler(commands=["find"])
async def find_chat(message: types.Message):
    await find_match_generic(message, "", (), allow_queue=True)

@dp.message_handler(text="👨 Find a Man")
async def find_man(message: types.Message):
    if not is_premium(message.from_user.id):
        return await message.answer("🔒 Subscribe to Premium to use gender matching.")
    await find_match_generic(message, "AND gender ILIKE 'male'", (), allow_queue=False)

@dp.message_handler(text="👩 Find a Woman")
async def find_woman(message: types.Message):
    if not is_premium(message.from_user.id):
        return await message.answer("🔒 Subscribe to Premium to use gender matching.")
    await find_match_generic(message, "AND gender ILIKE 'female'", (), allow_queue=False)

@dp.message_handler(text="🏙 Find in My City")
async def city_menu(message: types.Message):
    if not is_premium(message.from_user.id):
        return await message.answer("🔒 Subscribe to Premium to use city matching.")
    
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🏙👨 Men in My City", "🏙👩 Women in My City")
    kb.add("⬅ Back")
    await message.answer("Choose who you want to chat with:", reply_markup=kb)

@dp.message_handler(lambda m: m.text in ["🏙👨 Men in My City", "🏙👩 Women in My City"])
async def find_city_gender(message: types.Message):
    uid = message.from_user.id
    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium.")

    cur.execute("SELECT city FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if not row or not row[0]:
        return await message.answer("❌ Please set your city in Settings first.")
    
    city = row[0]
    gender_target = 'male' if 'Men' in message.text else 'female'
    
    await find_match_generic(
        message, 
        "AND city ILIKE %s AND gender ILIKE %s", 
        (city, gender_target),
        allow_queue=False
    )

@dp.message_handler(text="🎯 Find by Interests")
async def find_by_interests(message: types.Message):
    uid = message.from_user.id
    
    cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    if not row or not row[0]:
        return await message.answer("❌ Please set your interests in Settings first.")
    
    user_interests = [x.strip() for x in row[0].split(",") if x.strip()]
    if not user_interests:
        return await message.answer("❌ You have no interests selected.")

    # Construct dynamic SQL for interest matching (at least one common interest)
    # FIX 3: Prevent self-matching is handled by 'WHERE user_id != %s' in generic query
    conditions = []
    params = []
    for interest in user_interests:
        conditions.append("interests ILIKE %s")
        params.append(f"%{interest}%")
    
    sql_condition = "AND (" + " OR ".join(conditions) + ")"
    
    await find_match_generic(message, sql_condition, tuple(params), allow_queue=False)

@dp.message_handler(text="🔁 Reconnect")
async def reconnect_last_chat(message: types.Message):
    uid = message.from_user.id
    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium to reconnect.")

    cur.execute("SELECT last_chat_user_id FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()

    if not row or not row[0]:
        return await message.answer("❌ No previous chat found.")
    
    partner_id = row[0]
    
    # Check if user is available
    cur.execute("SELECT user_id FROM users WHERE user_id=%s AND (banned IS NULL OR banned=false)", (partner_id,))
    if not cur.fetchone():
        return await message.answer("❌ User is no longer available.")
    
    # Check if blocked
    blocked_list = get_blocked_users(uid)
    if partner_id in blocked_list:
         return await message.answer("❌ You have blocked this user.")

    await connect_users(uid, partner_id)

# ================= CHAT CONTROLS =================

@dp.message_handler(text="⛔ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        partner = active_chats[uid]
        await end_chat(uid, partner)
    else:
        await message.answer("❌ You are not in a chat.", reply_markup=main_menu)

@dp.message_handler(text="⏭ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        partner = active_chats[uid]
        # Silently end current chat
        try:
            cur.execute("UPDATE users SET is_online=false WHERE user_id IN (%s, %s)", (uid, partner))
        except Exception: 
            pass
            
        if uid in active_chats: del active_chats[uid]
        if partner in active_chats: del active_chats[partner]
        
        try:
            await bot.send_message(partner, "❌ Partner skipped. Chat ended.", reply_markup=main_menu)
        except: pass

    # Find new chat immediately
    await find_chat(message)

@dp.message_handler(text="🚫 Block")
async def block_user(message: types.Message):
    uid = message.from_user.id
    
    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.")

    partner_id = active_chats[uid]
    
    try:
        cur.execute("""
            UPDATE users 
            SET blocked_users = array_append(COALESCE(blocked_users, '{}'), %s)
            WHERE user_id = %s
        """, (partner_id, uid))
    except Exception as e:
        logging.error(f"Block error: {e}")
    
    await message.answer("🚫 User blocked.")
    await end_chat(uid, partner_id)

@dp.message_handler(text="🚨 Report")
async def start_report(message: types.Message):
    uid = message.from_user.id
    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.")

    partner = active_chats[uid]
    report_state[uid] = partner
    await message.answer("Please describe the issue (spam, abuse, etc):")

@dp.message_handler(lambda m: m.from_user.id in report_state)
async def process_report(message: types.Message):
    reporter = message.from_user.id
    reported = report_state.pop(reporter)
    reason = message.text
    
    try:
        cur.execute("""
            INSERT INTO reports (reporter_id, reported_id, reason)
            VALUES (%s, %s, %s)
        """, (reporter, reported, reason))
    except: pass

    try:
        cur.execute("UPDATE users SET report_count = COALESCE(report_count, 0) + 1 WHERE user_id=%s", (reported,))
        check_and_auto_ban(reported)
    except Exception:
        pass

    # FIX 5: Avoid duplicate messages by not notifying reporter in end_chat
    await end_chat(reporter, reported, notify_user1=False, notify_user2=True)

    await message.answer("🚨 Report submitted. Thank you.", reply_markup=main_menu)

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
        provider_token="", # Empty for Telegram Stars
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
    
    await message.answer(f"⭐ Premium activated for {days} days!")

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
        await message.answer(f"✅ {field.capitalize()} updated!", reply_markup=main_menu)
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
    # FIX 4: Ensure chat_relay ignores slash commands
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
    ])

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)