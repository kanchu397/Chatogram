import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# ---------------- ENV ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("Missing BOT_TOKEN or DATABASE_URL")

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)

# ---------------- BOT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ---------------- DB ----------------
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cur = conn.cursor()

# ---------------- STATE ----------------
waiting_users = []
active_chats = {}
editing = {}

# ---------------- KEYBOARDS ----------------
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add("🔍 Find Chat", "👨 Find a Man", "👩 Find a Woman")
main_menu.add("👤 Profile", "✏ Edit Profile")
main_menu.add("⭐ Premium", "🎁 Invite & Earn", "📜 Rules")

chat_menu = ReplyKeyboardMarkup(resize_keyboard=True)
chat_menu.add("⏹ Stop", "⏭ Next")

# ---------------- HELPERS ----------------
def get_user(uid):
    cur.execute("SELECT * FROM users WHERE user_id=%s", (uid,))
    return cur.fetchone()

def has_premium(user):
    if user["premium_until"] and user["premium_until"] > datetime.utcnow():
        return True
    return False

# ---------------- START ----------------
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = message.from_user.id
    username = message.from_user.username

    cur.execute("""
    INSERT INTO users (user_id, username, premium_until)
    VALUES (%s, %s, NOW() + INTERVAL '2 HOURS')
    ON CONFLICT (user_id) DO NOTHING
    """, (uid, username))
    conn.commit()

    # referral
    args = message.get_args()
    if args.startswith("ref_"):
        try:
            ref = int(args.split("_")[1])
            if ref != uid:
                cur.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id=%s", (ref,))
                conn.commit()
        except:
            pass

    await message.answer(
        "👋 Welcome to *Chatogram*\n🆓 2 hours FREE premium",
        reply_markup=main_menu,
        parse_mode="Markdown"
    )

# ---------------- PROFILE ----------------
@dp.message_handler(lambda m: m.text == "👤 Profile")
async def profile(message: types.Message):
    u = get_user(message.from_user.id)
    star = "⭐" if has_premium(u) else ""

    await message.answer(
        f"👤 Profile {star}\n\n"
        f"Age: {u['age']}\n"
        f"Gender: {u['gender']}\n"
        f"City: {u['city']}\n"
        f"Country: {u['country']}\n"
        f"Premium: {'Yes' if has_premium(u) else 'No'}"
    )

# ---------------- EDIT PROFILE (SIMPLE) ----------------
@dp.message_handler(lambda m: m.text == "✏ Edit Profile")
async def edit_start(message: types.Message):
    editing[message.from_user.id] = "age"
    await message.answer("Enter age:")

@dp.message_handler()
async def edit_flow(message: types.Message):
    uid = message.from_user.id

    # editing flow
    if uid in editing:
        step = editing[uid]

        if step == "age":
            cur.execute("UPDATE users SET age=%s WHERE user_id=%s", (message.text, uid))
            editing[uid] = "gender"
            await message.answer("Enter gender (male/female):")

        elif step == "gender":
            cur.execute("UPDATE users SET gender=%s WHERE user_id=%s", (message.text.lower(), uid))
            editing[uid] = "city"
            await message.answer("Enter city:")

        elif step == "city":
            cur.execute("UPDATE users SET city=%s WHERE user_id=%s", (message.text, uid))
            editing[uid] = "country"
            await message.answer("Enter country:")

        elif step == "country":
            cur.execute("UPDATE users SET country=%s WHERE user_id=%s", (message.text, uid))
            editing.pop(uid)
            await message.answer("✅ Profile updated", reply_markup=main_menu)

        conn.commit()
        return

    # relay chat
    if uid in active_chats:
        partner = active_chats.get(uid)
        if partner:
            await bot.send_message(partner, message.text)
        return

    await message.answer("Use menu 👇", reply_markup=main_menu)

# ---------------- CHAT ----------------
@dp.message_handler(lambda m: m.text == "🔍 Find Chat")
async def find_chat(message: types.Message):
    uid = message.from_user.id
    if uid in waiting_users or uid in active_chats:
        return

    if waiting_users:
        p = waiting_users.pop(0)
        active_chats[uid] = p
        active_chats[p] = uid
        await bot.send_message(uid, "✅ Connected", reply_markup=chat_menu)
        await bot.send_message(p, "✅ Connected", reply_markup=chat_menu)
    else:
        waiting_users.append(uid)
        await message.answer("⏳ Searching...")

@dp.message_handler(lambda m: m.text in ["👨 Find a Man", "👩 Find a Woman"])
async def gender_match(message: types.Message):
    uid = message.from_user.id
    u = get_user(uid)

    if not has_premium(u):
        await message.answer("⭐ Premium required")
        return

    target = "male" if "Man" in message.text else "female"

    for w in waiting_users:
        cur.execute("SELECT gender FROM users WHERE user_id=%s", (w,))
        g = cur.fetchone()
        if g and g["gender"] == target:
            waiting_users.remove(w)
            active_chats[uid] = w
            active_chats[w] = uid
            await bot.send_message(uid, "✅ Connected", reply_markup=chat_menu)
            await bot.send_message(w, "✅ Connected", reply_markup=chat_menu)
            return

    waiting_users.append(uid)
    await message.answer("⏳ Searching premium match...")

@dp.message_handler(lambda m: m.text == "⏹ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    p = active_chats.pop(uid, None)
    if p:
        active_chats.pop(p, None)
        await bot.send_message(p, "❌ Partner left", reply_markup=main_menu)
    await message.answer("❌ Chat stopped", reply_markup=main_menu)

@dp.message_handler(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    await stop_chat(message)
    await find_chat(message)

# ---------------- PREMIUM ----------------
@dp.message_handler(lambda m: m.text == "⭐ Premium")
async def premium(message: types.Message):
    await message.answer(
        "⭐ Premium ₹49 / 7 days\n\n"
        "UPI:\nupi://pay?pa=yourupi@upi&pn=Chatogram&am=49\n\n"
        "After payment, send screenshot to admin."
    )

# ---------------- INVITE ----------------
@dp.message_handler(lambda m: m.text == "🎁 Invite & Earn")
async def invite(message: types.Message):
    me = await bot.get_me()
    await message.answer(
        f"Invite link:\nhttps://t.me/{me.username}?start=ref_{message.from_user.id}"
    )

# ---------------- RULES ----------------
@dp.message_handler(lambda m: m.text == "📜 Rules")
async def rules(message: types.Message):
    await message.answer("Be respectful. No spam. No NSFW.")

# ---------------- ADMIN ----------------
@dp.message_handler(commands=["addpremium"])
async def add_premium(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.get_args())
    cur.execute(
        "UPDATE users SET premium_until = NOW() + INTERVAL '7 DAYS' WHERE user_id=%s",
        (uid,)
    )
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

# ---------------- RUN ----------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)