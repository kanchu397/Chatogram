import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from dotenv import load_dotenv

# ---------------- LOAD ENV ----------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("Missing BOT_TOKEN or DATABASE_URL")

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)

# ---------------- BOT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ---------------- DATABASE ----------------
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
        premium_until TIMESTAMP,
        referrals INT DEFAULT 0,
        banned BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

init_db()

# ---------------- RUNTIME STATES ----------------
waiting_users = []
active_chats = {}

# ---------------- FSM ----------------
class EditProfile(StatesGroup):
    age = State()
    gender = State()
    city = State()
    country = State()

# ---------------- KEYBOARDS ----------------
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add("🔍 Find Chat", "👨 Find a Man", "👩 Find a Woman")
main_menu.add("👤 Profile", "⚙ Settings")
main_menu.add("⭐ Premium", "🎁 Invite & Earn", "📜 Rules")

chat_menu = ReplyKeyboardMarkup(resize_keyboard=True)
chat_menu.add("⏹ Stop", "⏭ Next")

settings_menu = ReplyKeyboardMarkup(resize_keyboard=True)
settings_menu.add("✏ Edit Profile", "⬅ Back")

# ---------------- HELPERS ----------------
def get_user(uid):
    cur.execute("SELECT * FROM users WHERE user_id=%s", (uid,))
    return cur.fetchone()

def user_has_premium(user):
    if user["is_premium"]:
        return True
    if user["premium_until"] and user["premium_until"] > datetime.utcnow():
        return True
    return False
# ---------------- START + REFERRAL ----------------
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    uid = message.from_user.id
    username = message.from_user.username

    cur.execute("""
    INSERT INTO users (user_id, username, premium_until)
    VALUES (%s, %s, NOW() + INTERVAL '2 HOURS')
    ON CONFLICT (user_id) DO NOTHING
    """, (uid, username))
    conn.commit()

    # Referral handling
    args = message.get_args()
    if args.startswith("ref_"):
        ref_id = int(args.split("_")[1])
        if ref_id != uid:
            cur.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id=%s", (ref_id,))
            conn.commit()

    await message.answer(
        "👋 *Welcome to Chatogram*\n\n"
        "🆓 You got *2 hours FREE premium* 🎉\n"
        "_Where Strangers Become Voices_",
        reply_markup=main_menu,
        parse_mode="Markdown"
    )

# ---------------- PROFILE ----------------
@dp.message_handler(lambda m: m.text == "👤 Profile")
async def profile(message: types.Message):
    user = get_user(message.from_user.id)
    star = "⭐" if user_has_premium(user) else ""

    text = (
        f"👤 *Your Profile* {star}\n\n"
        f"Age: {user['age']}\n"
        f"Gender: {user['gender']}\n"
        f"City: {user['city']}\n"
        f"Country: {user['country']}\n"
        f"Premium: {'Yes' if user_has_premium(user) else 'No'}"
    )
    await message.answer(text, parse_mode="Markdown")

# ---------------- SETTINGS ----------------
@dp.message_handler(lambda m: m.text == "⚙ Settings")
async def settings(message: types.Message):
    await message.answer("⚙ Settings", reply_markup=settings_menu)

@dp.message_handler(lambda m: m.text == "✏ Edit Profile")
async def edit_profile(message: types.Message):
    await message.answer("Enter your age:")
    await EditProfile.age.set()

@dp.message_handler(state=EditProfile.age)
async def edit_age(message: types.Message, state: FSMContext):
    await state.update_data(age=message.text)
    await message.answer("Enter gender (male/female):")
    await EditProfile.gender.set()

@dp.message_handler(state=EditProfile.gender)
async def edit_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text.lower())
    await message.answer("Enter city:")
    await EditProfile.city.set()

@dp.message_handler(state=EditProfile.city)
async def edit_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await message.answer("Enter country:")
    await EditProfile.country.set()

@dp.message_handler(state=EditProfile.country)
async def edit_country(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cur.execute("""
    UPDATE users SET age=%s, gender=%s, city=%s, country=%s
    WHERE user_id=%s
    """, (data["age"], data["gender"], data["city"], message.text, message.from_user.id))
    conn.commit()

    await state.finish()
    await message.answer("✅ Profile updated", reply_markup=main_menu)

# ---------------- CHAT ENGINE ----------------
@dp.message_handler(lambda m: m.text == "🔍 Find Chat")
async def find_chat(message: types.Message):
    uid = message.from_user.id

    if uid in active_chats or uid in waiting_users:
        return

    if waiting_users:
        partner = waiting_users.pop(0)
        active_chats[uid] = partner
        active_chats[partner] = uid

        await bot.send_message(uid, "✅ Connected!", reply_markup=chat_menu)
        await bot.send_message(partner, "✅ Connected!", reply_markup=chat_menu)
    else:
        waiting_users.append(uid)
        await message.answer("⏳ Searching...")

@dp.message_handler(lambda m: m.text in ["👨 Find a Man", "👩 Find a Woman"])
async def premium_match(message: types.Message):
    uid = message.from_user.id
    user = get_user(uid)

    if not user_has_premium(user):
        await message.answer("⭐ Subscribe to premium to use this.")
        return

    target_gender = "male" if "Man" in message.text else "female"

    for w in waiting_users:
        cur.execute("SELECT gender FROM users WHERE user_id=%s", (w,))
        partner = cur.fetchone()
        if partner and partner["gender"] == target_gender:
            waiting_users.remove(w)
            active_chats[uid] = w
            active_chats[w] = uid
            await bot.send_message(uid, "✅ Connected!", reply_markup=chat_menu)
            await bot.send_message(w, "✅ Connected!", reply_markup=chat_menu)
            return

    waiting_users.append(uid)
    await message.answer("⏳ Searching premium match...")

@dp.message_handler(lambda m: m.from_user.id in active_chats and m.text not in ["⏹ Stop", "⏭ Next"])
async def relay(message: types.Message):
    receiver = active_chats.get(message.from_user.id)
    if receiver:
        await bot.send_message(receiver, message.text)

@dp.message_handler(lambda m: m.text == "⏹ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    partner = active_chats.pop(uid, None)
    if partner:
        active_chats.pop(partner, None)
        await bot.send_message(partner, "❌ Partner left", reply_markup=main_menu)
    await message.answer("❌ Chat stopped", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    await stop_chat(message)
    await find_chat(message)

# ---------------- PREMIUM ----------------
@dp.message_handler(lambda m: m.text == "⭐ Premium")
async def premium(message: types.Message):
    await message.answer(
        "⭐ *Premium – ₹49 / 7 days*\n\n"
        "• Gender match\n"
        "• City filter (next)\n"
        "• Reconnect (next)\n\n"
        "💳 Pay via UPI:\n"
        "`upi://pay?pa=yourupi@upi&pn=Chatogram&am=49`\n\n"
        "After payment, send screenshot to admin.",
        parse_mode="Markdown"
    )

# ---------------- INVITE ----------------
@dp.message_handler(lambda m: m.text == "🎁 Invite & Earn")
async def invite(message: types.Message):
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{message.from_user.id}"
    await message.answer(f"🎁 Invite link:\n{link}")

# ---------------- RULES ----------------
@dp.message_handler(lambda m: m.text == "📜 Rules")
async def rules(message: types.Message):
    await message.answer("📜 Be respectful. No spam. No NSFW.")

# ---------------- ADMIN ----------------
@dp.message_handler(commands=["addpremium"])
async def add_premium(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.get_args())
    cur.execute("""
    UPDATE users SET is_premium=TRUE,
    premium_until = NOW() + INTERVAL '7 DAYS'
    WHERE user_id=%s
    """, (uid,))
    conn.commit()
    await message.answer("✅ Premium added")

@dp.message_handler(commands=["ban"])
async def ban(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.get_args())
    cur.execute("UPDATE users SET banned=TRUE WHERE user_id=%s", (uid,))
    conn.commit()
    await message.answer("🚫 User banned")

# ---------------- FALLBACK ----------------
@dp.message_handler()
async def fallback(message: types.Message):
    await message.answer("Use menu 👇", reply_markup=main_menu)

# ---------------- RUN ----------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
