import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# ------------------ LOAD ENV ------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN or DATABASE_URL missing")

# ------------------ LOGGING ------------------
logging.basicConfig(level=logging.INFO)

# ------------------ BOT ------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ------------------ DATABASE ------------------
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cur = conn.cursor()

def init_db():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        age INT,
        gender TEXT,
        city TEXT,
        country TEXT,
        is_premium BOOLEAN DEFAULT FALSE,
        banned BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

init_db()

# ------------------ KEYBOARDS ------------------
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(
    KeyboardButton("🔍 Find Chat"),
    KeyboardButton("👤 Profile")
)
main_menu.add(
    KeyboardButton("⭐ Premium"),
    KeyboardButton("⚙ Settings")
)
main_menu.add(
    KeyboardButton("📜 Rules"),
    KeyboardButton("🎁 Invite & Earn")
)

# ------------------ HELPERS ------------------
def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    return cur.fetchone()

def save_user(user_id, username):
    cur.execute("""
    INSERT INTO users (user_id, username)
    VALUES (%s, %s)
    ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username))
    conn.commit()

# ------------------ START ------------------
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    save_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "👋 Welcome to *Chatogram*\n\nWhere Strangers Become Voices.",
        reply_markup=main_menu,
        parse_mode="Markdown"
    )

# ------------------ PROFILE ------------------
@dp.message_handler(lambda m: m.text == "👤 Profile")
async def profile(message: types.Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Profile not found.")
        return

    star = "⭐" if user["is_premium"] else ""
    text = (
        f"👤 *Your Profile* {star}\n\n"
        f"ID: `{user['user_id']}`\n"
        f"Username: @{user['username']}\n"
        f"Age: {user['age']}\n"
        f"Gender: {user['gender']}\n"
        f"City: {user['city']}\n"
        f"Country: {user['country']}\n"
        f"Premium: {'Yes' if user['is_premium'] else 'No'}"
    )
    await message.answer(text, parse_mode="Markdown")

# ------------------ PREMIUM ------------------
@dp.message_handler(lambda m: m.text == "⭐ Premium")
async def premium(message: types.Message):
    await message.answer(
        "⭐ *Premium Plan*\n\n"
        "• Gender based matching\n"
        "• City filters\n"
        "• Reconnect users\n\n"
        "💰 ₹49 for 7 days",
        parse_mode="Markdown"
    )

# ------------------ RULES ------------------
@dp.message_handler(lambda m: m.text == "📜 Rules")
async def rules(message: types.Message):
    await message.answer(
        "📜 *Chat Rules*\n\n"
        "• Be respectful\n"
        "• No spam\n"
        "• No NSFW\n"
        "• Violators will be banned",
        parse_mode="Markdown"
    )

# ------------------ INVITE ------------------
@dp.message_handler(lambda m: m.text == "🎁 Invite & Earn")
async def invite(message: types.Message):
    link = f"https://t.me/{(await bot.get_me()).username}"
    await message.answer(
        f"🎁 Invite friends using this link:\n{link}"
    )

# ------------------ ADMIN: ADD PREMIUM ------------------
@dp.message_handler(commands=["addpremium"])
async def add_premium(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        uid = int(message.get_args())
        cur.execute("UPDATE users SET is_premium=TRUE WHERE user_id=%s", (uid,))
        conn.commit()
        await message.answer("✅ Premium activated")
    except:
        await message.answer("Usage: /addpremium USER_ID")

# ------------------ ADMIN: BAN ------------------
@dp.message_handler(commands=["ban"])
async def ban_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        uid = int(message.get_args())
        cur.execute("UPDATE users SET banned=TRUE WHERE user_id=%s", (uid,))
        conn.commit()
        await message.answer("🚫 User banned")
    except:
        await message.answer("Usage: /ban USER_ID")

# ------------------ DEFAULT ------------------
@dp.message_handler()
async def fallback(message: types.Message):
    await message.answer("Use menu buttons 👇", reply_markup=main_menu)

# ------------------ RUN ------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
