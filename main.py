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

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
user_edit_state = {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
onboarding_state = {}

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

def add_premium(user_id, delta):
    cur.execute("""
        UPDATE users
        SET premium_until = COALESCE(premium_until, NOW()) + %s
        WHERE user_id=%s
    """, (delta, user_id))

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

# ================= MENUS =================

main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add("🔍 Find Chat")
main_menu.add("👨 Find a Man", "👩 Find a Woman")
main_menu.add("⭐ Premium", "👤 Profile")
main_menu.add("🎁 Invite & Earn", "📜 Rules")
main_menu.add("⚙ Settings")

chat_menu = ReplyKeyboardMarkup(resize_keyboard=True)
chat_menu.add("⏭ Next", "⛔ Stop")

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
    cur.execute("""
        SELECT age, gender, city, country, premium_until
        FROM users WHERE user_id=%s
    """, (uid,))
    row = cur.fetchone()

    star = "⭐" if is_premium(uid) else ""
    await message.answer(
        f"{star} *Your Profile*\n"
        f"Age: {row[0]}\n"
        f"Gender: {row[1]}\n"
        f"City: {row[2]}\n"
        f"Country: {row[3]}",
        parse_mode="Markdown"
    )

# ================= FIND CHAT =================

@dp.message_handler(text="🔍 Find Chat")
async def find_chat(message: types.Message):
    await message.answer("🔄 Searching for a match...", reply_markup=chat_menu)

@dp.message_handler(text="👨 Find a Man")
async def find_man(message: types.Message):
    if not is_premium(message.from_user.id):
        return await message.answer("⭐ Subscribe to Premium")
    await message.answer("🔄 Finding a man...", reply_markup=chat_menu)

@dp.message_handler(text="👩 Find a Woman")
async def find_woman(message: types.Message):
    if not is_premium(message.from_user.id):
        return await message.answer("⭐ Subscribe to Premium")
    await message.answer("🔄 Finding a woman...", reply_markup=chat_menu)

# ================= CHAT CONTROLS =================

@dp.message_handler(text="⛔ Stop")
async def stop_chat(message: types.Message):
    await message.answer("❌ Chat ended", reply_markup=main_menu)

@dp.message_handler(text="⏭ Next")
async def next_chat(message: types.Message):
    await message.answer("🔄 Finding next chat...", reply_markup=chat_menu)

# ================= PREMIUM =================

@dp.message_handler(text="⭐ Premium")
async def premium(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⭐ 7 Days – 30 Stars", callback_data="buy_7"),
        InlineKeyboardButton("⭐ 30 Days – 150 Stars", callback_data="buy_30")
    )
    await message.answer("Upgrade to Premium", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("buy_"))
async def buy(callback: types.CallbackQuery):
    days = 7 if callback.data == "buy_7" else 30
    stars = 50 if days == 7 else 150

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
async def success_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    days = int(payload.split("_")[1])
    add_premium(message.from_user.id, timedelta(days=days))
    await message.answer("⭐ Premium Activated!", reply_markup=main_menu)

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

# ================= ADMIN =================

@dp.message_handler(lambda m: m.from_user.id in user_edit_state)
async def save_profile_edit(message: types.Message):
    field = user_edit_state.pop(message.from_user.id)
    value = message.text.strip()

    cur.execute(
        f"UPDATE users SET {field}=%s WHERE user_id=%s",
        (value, message.from_user.id)
    )

    await message.answer(
        f"✅ {field.capitalize()} updated successfully",
        reply_markup=main_menu
    )

@dp.message_handler(commands=["addpremium"])
async def addpremium(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.get_args())
    add_premium(uid, timedelta(days=30))
    await message.answer("✅ Premium added")

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

    if step == "gender":
        cur.execute("UPDATE users SET gender=%s WHERE user_id=%s", (text, uid))
        onboarding_state[uid] = "city"
        return await message.answer("🏙 Enter your city:")

    if step == "city":
        cur.execute("UPDATE users SET city=%s WHERE user_id=%s", (text, uid))
        onboarding_state[uid] = "country"
        return await message.answer("🌍 Enter your country:")

    if step == "country":
        cur.execute("UPDATE users SET country=%s WHERE user_id=%s", (text, uid))
        onboarding_state.pop(uid)

        await message.answer(
            "✅ Profile setup complete!\n\nYou can now start chatting 🎉",
            reply_markup=main_menu
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