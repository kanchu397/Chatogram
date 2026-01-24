import logging
import os
import psycopg2
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, ContentType
)
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================

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
user_edit_state = {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
onboarding_state = {}
active_chats = {}
waiting_queue = set()  # users waiting for a match

# 🔐 Temporary states
report_state = {}  # {reporter_id: reported_id} - users currently reporting
block_state = set()  # users currently blocking
edit_interest_state = set()

logging.basicConfig(level=logging.INFO)

# ================= DB =====================

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

# ================= HELPERS =================

def is_premium(user_id):
    cur.execute(
        "SELECT premium_until FROM users WHERE user_id=%s",
        (user_id,)
    )
    row = cur.fetchone()
    return row and row[0] and row[0] > datetime.utcnow()

def get_blocked_users(user_id):
    """Get list of blocked users for a given user_id"""
    cur.execute(
        "SELECT blocked_users FROM users WHERE user_id=%s",
        (user_id,)
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return []

def check_and_auto_ban(user_id):
    """Check report_count and auto-ban if threshold reached"""
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

async def try_match_waiting_queue(uid):
    """Try to match user with someone in waiting queue"""
    if not waiting_queue:
        return None
    
    blocked = get_blocked_users(uid)
    
    # Try to find a match from waiting queue
    for waiting_id in list(waiting_queue):
        if waiting_id == uid:
            continue
        
        # Check if they can be matched
        cur.execute("""
            SELECT user_id FROM users
            WHERE user_id = %s
            AND (banned IS NULL OR banned = false)
            AND (is_online IS NULL OR is_online = true)
            AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        """, (waiting_id, uid))
        
        wait_row = cur.fetchone()
        if wait_row:
            # Check if current user is not blocked by waiting user
            wait_blocked = get_blocked_users(waiting_id)
            if uid not in wait_blocked:
                waiting_queue.discard(waiting_id)
                return waiting_id
    
    return None

def add_premium(user_id, delta):
    cur.execute("""
        UPDATE users
        SET premium_until = COALESCE(premium_until, NOW()) + %s
        WHERE user_id=%s
    """, (delta, user_id))
    
async def connect_users(user1, user2):
    active_chats[user1] = user2
    active_chats[user2] = user1
    
    # Remove from waiting queue if present
    waiting_queue.discard(user1)
    waiting_queue.discard(user2)

    # Save last_chat_user_id for reconnect
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
    
    # Notify both users
    try:
        await bot.send_message(user1, "✅ Match found! Start chatting...", reply_markup=chat_kb)
        await bot.send_message(user2, "✅ Match found! Start chatting...", reply_markup=chat_kb)
    except Exception:
        pass  # User may have blocked the bot

# ================= HANDELRS =================
    
@dp.message_handler(text="⚙ Settings")
async def settings_menu(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✏ Edit Profile")
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
async def slash_profile(message: types.Message):
    await profile(message)

@dp.message_handler(commands=["premium"])
async def slash_premium(message: types.Message):
    await premium(message)

@dp.message_handler(commands=["rules"])
async def slash_rules(message: types.Message):
    await rules(message)

@dp.message_handler(commands=["invite"])
async def slash_invite(message: types.Message):
    await invite(message)

@dp.message_handler(commands=["settings"])
async def slash_settings(message: types.Message):
    await settings_menu(message)

@dp.message_handler(commands=["find"])
async def slash_find(message: types.Message):
    await find_chat(message)
       
@dp.message_handler(text="✏ Edit Profile")
async def edit_profile_entry(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✏ Edit Age", "✏ Edit Gender")
    kb.add("✏ Edit City", "✏ Edit Country")
    kb.add("✏ Edit Interests")
    kb.add("⬅ Back")

    await message.answer(
        "✏ *Edit Profile*\nWhat do you want to change?",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message_handler(text="✏ Edit Age")
async def edit_age(message: types.Message):
    user_edit_state[message.from_user.id] = "age"
    await message.answer("Enter your age:")

@dp.message_handler(text="✏ Edit Gender")
async def edit_gender(message: types.Message):
    user_edit_state[message.from_user.id] = "gender"
    await message.answer("Enter your gender:")

@dp.message_handler(text="✏ Edit City")
async def edit_city(message: types.Message):
    user_edit_state[message.from_user.id] = "city"
    await message.answer("Enter your city:")

@dp.message_handler(text="✏ Edit Country")
async def edit_country(message: types.Message):
    user_edit_state[message.from_user.id] = "country"
    await message.answer("Enter your country:")


@dp.message_handler(text="✏ Edit Interests")
async def edit_interests(message: types.Message):
    user_edit_state[message.from_user.id] = "interests"
    await message.answer(
        "🏷 Enter your interests (comma separated)\n"
        "Example: music, movies, sports"
    )

@dp.message_handler(text="👨 Find a Man")
async def find_man(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium to use gender matching.")

    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
        SELECT user_id FROM users
        WHERE gender ILIKE 'male'
        AND user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="👩 Find a Woman")
async def find_woman(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium to use gender matching.")

    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
       SELECT user_id FROM users
       WHERE gender ILIKE 'female'
       AND user_id != %s
       AND (banned IS NULL OR banned = false)
       AND (is_online IS NULL OR is_online = true)
       AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
       AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
       ORDER BY RANDOM()
       LIMIT 1
    """, (uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="🎯 Find by Interests")
async def find_by_interests(message: types.Message):
    uid = message.from_user.id

    # Get user's interests
    cur.execute("SELECT interests FROM users WHERE user_id=%s", (uid,))
    user_row = cur.fetchone()
    
    if not user_row or not user_row[0]:
        return await message.answer(
            "❌ Please set your interests in profile settings first.",
            reply_markup=main_menu
        )

    user_interests = user_row[0]
    if not user_interests or (isinstance(user_interests, list) and len(user_interests) == 0):
        return await message.answer(
            "❌ Please set your interests in profile settings first.",
            reply_markup=main_menu
        )

    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    # Use PostgreSQL array overlap operator (&&)
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND interests IS NOT NULL
        AND interests && %s
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (uid, user_interests, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "😕 No users with similar interests found right now.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="🏙 Find in My City")
async def city_gender_choice(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer(
            "🔒 City-based matching is a Premium feature.\n\n⭐ Subscribe to Premium to unlock it."
        )

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🏙👨 Men in My City", "🏙👩 Women in My City")
    kb.add("⬅ Back")

    await message.answer(
        "🏙 *Find in My City*\nChoose who you want to chat with:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message_handler(text="🏙👨 Men in My City")
async def find_men_in_city(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium to use city matching.")

    # Get user's city
    cur.execute("SELECT city FROM users WHERE user_id=%s", (uid,))
    user_row = cur.fetchone()
    if not user_row or not user_row[0]:
        return await message.answer(
            "❌ Please set your city in profile settings first.",
            reply_markup=main_menu
        )

    user_city = user_row[0]
    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
        SELECT user_id FROM users
        WHERE gender ILIKE 'male'
        AND city ILIKE %s
        AND user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (user_city, uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="🏙👩 Women in My City")
async def find_women_in_city(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer("🔒 Subscribe to Premium to use city matching.")

    # Get user's city
    cur.execute("SELECT city FROM users WHERE user_id=%s", (uid,))
    user_row = cur.fetchone()
    if not user_row or not user_row[0]:
        return await message.answer(
            "❌ Please set your city in profile settings first.",
            reply_markup=main_menu
        )

    user_city = user_row[0]
    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
        SELECT user_id FROM users
        WHERE gender ILIKE 'female'
        AND city ILIKE %s
        AND user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (user_city, uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

@dp.message_handler(text="🔁 Reconnect")
async def reconnect_last_chat(message: types.Message):
    uid = message.from_user.id

    if not is_premium(uid):
        return await message.answer(
            "🔒 Reconnect is a Premium feature.\n\n⭐ Subscribe to Premium to unlock it.",
            reply_markup=main_menu
        )

    cur.execute(
        "SELECT last_chat_user_id FROM users WHERE user_id=%s",
        (uid,)
    )
    row = cur.fetchone()

    if not row or not row[0]:
        return await message.answer(
            "❌ No previous chat found to reconnect.",
            reply_markup=main_menu
        )

    partner_id = row[0]

    # Prevent reconnect if partner is self
    if partner_id == uid:
        return await message.answer("❌ Invalid last chat.", reply_markup=main_menu)

    # Check if partner is online and not banned
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id = %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
    """, (partner_id,))
    
    partner_row = cur.fetchone()
    if not partner_row:
        return await message.answer(
            "❌ Previous user is not available.",
            reply_markup=main_menu
        )

    # Check if user blocked the partner or vice versa
    blocked = get_blocked_users(uid)
    if partner_id in blocked:
        return await message.answer(
            "❌ Previous user is not available.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner_id)
    
@dp.message_handler(text="🚨 Report")
async def report_user(message: types.Message):
    uid = message.from_user.id

    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.")

    reported_id = active_chats[uid]
    report_state[uid] = reported_id

    await message.answer(
        "🚨 Please briefly describe the issue:\n"
        "(spam / abuse / harassment / fake profile)"
    )

@dp.message_handler(text="🚫 Block")
async def block_user(message: types.Message):
    uid = message.from_user.id

    if uid not in active_chats:
        return await message.answer("❌ You are not in a chat.")

    blocked_id = active_chats[uid]
    
    # Get current blocked_users array
    cur.execute("SELECT blocked_users FROM users WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    blocked_list = row[0] if row and row[0] else []
    
    # Add to blocked list if not already blocked
    if blocked_id not in blocked_list:
        blocked_list.append(blocked_id)
        cur.execute(
            "UPDATE users SET blocked_users=%s WHERE user_id=%s",
            (blocked_list, uid)
        )
    
    # End chat
    if uid in active_chats:
        partner = active_chats.pop(uid)
        active_chats.pop(partner, None)
        try:
            await bot.send_message(
                partner,
                "❌ Chat ended.",
                reply_markup=main_menu
            )
        except Exception:
            pass
    
    await message.answer(
        "🚫 User blocked. You won't be matched with them again.",
        reply_markup=main_menu
    )
    
# ================= MENUS =================

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add("🔍 Find Chat")
main_menu.add("👨 Find a Man", "👩 Find a Woman")
main_menu.add("🎯 Find by Interests")
main_menu.add("⭐ Premium", "👤 Profile")
main_menu.add("🎁 Invite & Earn", "📜 Rules")
main_menu.add("⚙ Settings","🏙 Find in My City")
main_menu.add("🔁 Reconnect")

chat_menu = ReplyKeyboardMarkup(resize_keyboard=True)
chat_menu.add("⏭ Next", "⛔ Stop")
chat_kb = ReplyKeyboardMarkup(resize_keyboard=True)
chat_kb.add("🚫 Block", "🚨 Report")
chat_kb.add("⛔ Stop", "➡ Next")

# ================= START =================

@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    username = message.from_user.username

    cur.execute("SELECT age FROM users WHERE user_id=%s", (uid,))
    user = cur.fetchone()

    if user and user[0] is not None:
        # Existing user
        await message.answer(
            "👋 Welcome back to *Chatogram*",
            reply_markup=main_menu,
            parse_mode="Markdown"
        )
        return

    # New user → insert if not exists
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

# ================= PROFILE =================

@dp.message_handler(text="👤 Profile")
async def profile(message: types.Message):
    uid = message.from_user.id

    with conn.cursor() as cur:
        cur.execute("""
            SELECT age, gender, city, country, interests, premium_until
            FROM users WHERE user_id = %s
        """, (uid,))
        row = cur.fetchone()

    if not row:
        await message.answer("❌ Profile not found.")
        return

    age, gender, city, country, interests, premium_until = row

    # Format interests as array
    if interests:
        if isinstance(interests, list):
            interests_text = ", ".join(interests) if interests else "Not set"
        else:
            interests_text = str(interests)
    else:
        interests_text = "Not set"
    
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

# ================= FIND CHAT =================

@dp.message_handler(text="🔍 Find Chat")
async def find_chat(message: types.Message):
    uid = message.from_user.id
    
    # Try to match with waiting queue first
    waiting_partner = await try_match_waiting_queue(uid)
    if waiting_partner:
        await connect_users(uid, waiting_partner)
        return
    
    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        # Add to waiting queue
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

# ================= CHAT CONTROLS =================

@dp.message_handler(text="⛔ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    
    # End chat if exists
    if uid in active_chats:
        partner = active_chats.pop(uid)
        active_chats.pop(partner, None)
        cur.execute("UPDATE users SET is_online=false WHERE user_id IN (%s, %s)", (uid, partner))
        try:
            await bot.send_message(
                partner,
                "❌ Chat ended.",
                reply_markup=main_menu
            )
        except Exception:
            pass
    
    await message.answer("❌ Chat ended", reply_markup=main_menu)

@dp.message_handler(text="⏭ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id

    # End current chat if exists
    if uid in active_chats:
        partner = active_chats.pop(uid)
        active_chats.pop(partner, None)
        cur.execute("UPDATE users SET is_online=false WHERE user_id IN (%s, %s)", (uid, partner))

    # Try to match with waiting queue first
    waiting_partner = await try_match_waiting_queue(uid)
    if waiting_partner:
        await connect_users(uid, waiting_partner)
        return

    blocked = get_blocked_users(uid)
    cur.execute("UPDATE users SET is_online=true WHERE user_id=%s", (uid,))

    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id != %s
        AND (banned IS NULL OR banned = false)
        AND (is_online IS NULL OR is_online = true)
        AND (blocked_users IS NULL OR NOT (%s = ANY(blocked_users)))
        AND (array_length(blocked_users, 1) IS NULL OR NOT (%s = ANY(blocked_users)))
        ORDER BY RANDOM()
        LIMIT 1
    """, (uid, uid, uid))

    partner = cur.fetchone()
    if not partner:
        waiting_queue.add(uid)
        return await message.answer(
            "No users found right now. Please try again later.",
            reply_markup=main_menu
        )

    await connect_users(uid, partner[0])

# ================= PREMIUM =================

@dp.message_handler(text="⭐ Premium")
async def premium(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⭐ 7 Days – 30 Stars", callback_data="buy_7"),
        InlineKeyboardButton("⭐ 30 Days – 120 Stars", callback_data="buy_30")
    )
    await message.answer("Upgrade to Premium", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy(callback: types.CallbackQuery):
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

    if payload == "premium_7":
        cur.execute("""
            UPDATE users
            SET premium_until = NOW() + INTERVAL '7 days'
            WHERE user_id = %s
        """, (message.from_user.id,))

        await message.answer("⭐ Premium activated for 7 days!")

    elif payload == "premium_30":
        cur.execute("""
            UPDATE users
            SET premium_until = NOW() + INTERVAL '30 days'
            WHERE user_id = %s
        """, (message.from_user.id,))

        await message.answer("⭐ Premium activated for 30 days!")

# ================= INVITE =================

@dp.message_handler(text="🎁 Invite & Earn")
async def invite(message: types.Message):
    link = f"https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}"
    await message.answer(
        f"Invite friends:\n{link}\n\n"
        "🎁 3 referrals = 3 hours premium"
    )

# ================= RULES =================

@dp.message_handler(text="📜 Rules")
async def rules(message: types.Message):
    await message.answer(
        "1️⃣ No abuse\n"
        "2️⃣ No spam\n"
        "3️⃣ No illegal content\n"
        "4️⃣ Respect privacy"
    )

#===============REPORTING==================

@dp.message_handler(lambda m: m.from_user.id in report_state)
async def save_report(message: types.Message):
    reporter = message.from_user.id
    if reporter not in report_state:
        return
    
    reported = report_state.pop(reporter)
    reason = message.text.strip()

    # Increment report_count for reported user
    cur.execute("""
        UPDATE users 
        SET report_count = COALESCE(report_count, 0) + 1
        WHERE user_id = %s
    """, (reported,))
    
    # Check and auto-ban if threshold reached
    if check_and_auto_ban(reported):
        try:
            await bot.send_message(
                reported,
                "❌ Your account has been banned due to multiple reports."
            )
        except Exception:
            pass

    # Save report to reports table (if it exists)
    try:
        cur.execute("""
            INSERT INTO reports (reporter_id, reported_id, reason)
            VALUES (%s, %s, %s)
        """, (reporter, reported, reason))
    except Exception:
        pass  # Table might not exist, continue anyway

    # End chat
    if reporter in active_chats:
        partner = active_chats.pop(reporter)
        active_chats.pop(partner, None)
        try:
            await bot.send_message(
                partner,
                "❌ Chat ended.",
                reply_markup=main_menu
            )
        except Exception:
            pass

    await message.answer(
        "🚨 Report submitted. Thank you for helping keep Chatogram safe.",
        reply_markup=main_menu
    )

# ================= ADMIN =================

@dp.message_handler(lambda m: m.from_user.id in user_edit_state)
async def save_profile_edit(message: types.Message):
    field = user_edit_state.pop(message.from_user.id)
    value = message.text.strip()

    # Handle interests as array
    if field == "interests":
        interests_list = [i.strip().lower() for i in value.split(",") if i.strip()]
        cur.execute(
            "UPDATE users SET interests=%s WHERE user_id=%s",
            (interests_list, message.from_user.id)
        )
    else:
        cur.execute(
            f"UPDATE users SET {field}=%s WHERE user_id=%s",
            (value, message.from_user.id)
        )

    await message.answer(
        f"✅ {field.capitalize()} updated successfully",
        reply_markup=main_menu
    )


@dp.message_handler(commands=["ban"])
async def ban(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.get_args())
    cur.execute("UPDATE users SET banned=true WHERE user_id=%s", (uid,))
    await message.answer("🚫 User banned")

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
        return await message.answer("👤 Enter your gender:")

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
        return await message.answer(
            "🏷 Enter your interests (comma separated)\n"
            "Example: music, movies, sports"
        )

    elif step == "interests":
        # Store as TEXT[] array
        interests_list = [i.strip().lower() for i in text.split(",") if i.strip()]

        cur.execute(
            "UPDATE users SET interests=%s WHERE user_id=%s",
            (interests_list, uid)
        )
        onboarding_state.pop(uid)
        await message.answer(
            "✅ Profile setup complete!\n\nYou can now start chatting 🎉",
            reply_markup=main_menu
        )

from datetime import timedelta

@dp.message_handler(commands=["addpremium"])
async def add_premium_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not authorized.")

    parts = message.text.split()

    # Usage: /addpremium <user_id> <days>
    if len(parts) != 3:
        return await message.answer(
            "Usage:\n/addpremium <user_id> <days>\n\nExample:\n/addpremium 123456789 7"
        )

    try:
        target_id = int(parts[1])
        days = int(parts[2])
    except ValueError:
        return await message.answer("❌ Invalid user ID or days.")

    cur.execute("""
        UPDATE users
        SET premium_until = COALESCE(premium_until, NOW()) + %s * INTERVAL '1 day'
        WHERE user_id = %s
    """, (days, target_id))

    await message.answer(
        f"⭐ Premium granted for {days} days to user {target_id}"
    )        

# ================= RUN =================

async def set_commands(dp):
    await bot.set_my_commands([
        types.BotCommand("start", "Start the bot"),
        types.BotCommand("find", "Find a random chat"),
        types.BotCommand("profile", "View your profile"),
        types.BotCommand("premium", "Buy premium"),
        types.BotCommand("invite", "Invite friends & earn"),
        types.BotCommand("rules", "Bot rules"),
        types.BotCommand("settings", "Profile & settings"),
    ])

if __name__ == "__main__":
    executor.start_polling(
        dp,
        skip_updates=True,
        on_startup=set_commands
    )  