
# ================= CHATOGRAM – FULL FINAL BUILD ================= #
from db import add_user, get_user, grant_premium, is_premium
import os
import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

# ================= ENV ================= #

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

UPI_ID = "kanchit.tiwari@ibl"   # CHANGE
UPI_NAME = "Kanchit Tiwari"
PREMIUM_PRICE = 49
PREMIUM_DAYS = 7
TRIAL_DURATION = 2 * 60 * 60

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= DB HELPERS ================= #

def get_user(uid):
    r = supabase.table("users").select("*").eq("user_id", uid).execute()
    return r.data[0] if r.data else None

def save_user(data):
    supabase.table("users").upsert(data).execute()

def is_premium(u):
    now = datetime.now(timezone.utc)
    if u.get("premium_until") and datetime.fromisoformat(u["premium_until"]) > now:
        return True
    if u.get("trial_start"):
        return (now - datetime.fromisoformat(u["trial_start"])).total_seconds() <= TRIAL_DURATION
    return False

def is_banned(u):
    if u.get("banned_until"):
        return datetime.fromisoformat(u["banned_until"]) > datetime.now(timezone.utc)
    return False

def is_admin(message: types.Message):
    return message.from_user.id == ADMIN_ID

# ================= MEMORY ================= #

waiting_any = []
waiting_male = []
waiting_female = []
active_chats = {}

# ================= STATES ================= #

class Onboarding(StatesGroup):
    age = State()
    country = State()
    gender = State()
    city = State()
    interest = State()

class SettingsEdit(StatesGroup):
    gender = State()
    city = State()
    interest = State()

class PaymentState(StatesGroup):
    utr = State()

# ================= KEYBOARDS ================= #

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔍 Find Chat")
    kb.add("👨 Find a Man ⭐", "👩 Find a Woman ⭐")
    kb.add("👤 Profile", "⚙️ Settings")
    kb.add("⭐ Premium", "🎁 Invite & Earn")
    kb.add("📜 Rules")
    return kb

def chat_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("⏭ Next", "⛔ Stop")
    kb.add("🚨 Report")
    return kb

# ================= START ================= #

@dp.message_handler(commands=["start"])
async def start(m: types.Message):
    if get_user(m.from_user.id):
        await m.answer("👋 Welcome back", reply_markup=main_menu())
        return
    await m.answer("Enter your age:", reply_markup=types.ReplyKeyboardRemove())
    await Onboarding.age.set()

@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        partner = active_chats.pop(uid)
        active_chats.pop(partner, None)
        await bot.send_message(partner, "❌ Stranger left the chat.", reply_markup=main_menu())
    await message.answer("⛔ Chat stopped.", reply_markup=main_menu())

@dp.message_handler(commands=["next"])
async def cmd_next(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        partner = active_chats.pop(uid)
        active_chats.pop(partner, None)
        await bot.send_message(partner, "⏭ Stranger skipped the chat.", reply_markup=main_menu())
    await find_chat(message)

@dp.message_handler(commands=["profile"])
async def cmd_profile(message: types.Message):
    await profile(message)

@dp.message_handler(commands=["settings"])
async def cmd_settings(message: types.Message):
    await settings(message)
    
@dp.message_handler(commands=["addpremium"])
async def cmd_addpremium(message: types.Message):
    if not is_admin(message):
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("Usage: /addpremium user_id days")
        return

    try:
        user_id = int(args[1])
        days = int(args[2])
    except ValueError:
        await message.answer("Invalid arguments.")
        return

    user = get_user(user_id)
    if not user:
        await message.answer("User not found.")
        return

    now = datetime.now(timezone.utc)

    if user.get("premium_until"):
        current = datetime.fromisoformat(user["premium_until"])
        start = current if current > now else now
    else:
        start = now

    new_expiry = start + timedelta(days=days)

    save_user({
        "user_id": user_id,
        "premium_until": new_expiry.isoformat(),
        "expiry_notified": False
    })

    await message.answer(
        f"⭐ Premium activated for user {user_id}\n"
        f"Valid till: {new_expiry.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    try:
        await bot.send_message(
            user_id,
            f"⭐ Premium activated!\nValid till {new_expiry.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    except:
        pass

# ================= ONBOARDING ================= #

@dp.message_handler(state=Onboarding.age)
async def age_h(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) < 18:
        await message.answer("18+ only")
        return
    await state.update_data(age=int(message.text))
    await message.answer("Country?")
    await Onboarding.country.set()


@dp.message_handler(state=Onboarding.country)
async def country_h(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    await message.answer("Gender (Male/Female/Other)?")
    await Onboarding.gender.set()


@dp.message_handler(state=Onboarding.gender)
async def gender_h(message: types.Message, state: FSMContext):
    if message.text.lower() not in ["male", "female", "other"]:
        await message.answer("Type Male / Female / Other")
        return
    await state.update_data(gender=message.text.capitalize())
    await message.answer("City?")
    await Onboarding.city.set()


@dp.message_handler(state=Onboarding.city)
async def city_h(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await message.answer("Interest?")
    await Onboarding.interest.set()


@dp.message_handler(state=Onboarding.interest)
async def interest_h(message: types.Message, state: FSMContext):
    data = await state.get_data()

    save_user({
        "user_id": message.from_user.id,
        "age": data["age"],
        "country": data["country"],
        "gender": data["gender"],
        "city": data["city"],
        "interest": message.text,
        "trial_start": datetime.now(timezone.utc).isoformat()
    })

    await message.answer("✅ Profile created", reply_markup=main_menu())
    await state.finish()

# ================= PROFILE ================= #

@dp.message_handler(lambda m: m.text == "👤 Profile")
async def profile(m):
    u = get_user(m.from_user.id)
    await m.answer(
        f"👤 Profile {'⭐' if is_premium(u) else ''}\n\n"
        f"City: {u['city']}\n"
        f"Gender: {u['gender']}\n"
        f"Interest: {u.get('interest','-')}\n"
        f"Premium: {'ACTIVE ⭐' if is_premium(u) else 'FREE'}"
    )

# ================= SETTINGS ================= #

@dp.message_handler(lambda m: m.text == "⚙️ Settings")
async def settings(m):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Change Gender", callback_data="sg"))
    kb.add(types.InlineKeyboardButton("Change City", callback_data="sc"))
    kb.add(types.InlineKeyboardButton("Change Interest", callback_data="si"))
    await m.answer("⚙️ Settings", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "sg")
async def sg(c): 
    await c.message.answer("New gender?")
    await SettingsEdit.gender.set()

@dp.callback_query_handler(lambda c: c.data == "sc")
async def sc(c):
    await c.message.answer("New city?")
    await SettingsEdit.city.set()

@dp.callback_query_handler(lambda c: c.data == "si")
async def si(c):
    await c.message.answer("New interest?")
    await SettingsEdit.interest.set()

@dp.message_handler(state=SettingsEdit.gender)
async def save_gender(message: types.Message, state: FSMContext):
    save_user({"user_id": m.from_user.id, "gender": m.text.capitalize()})
    await m.answer("Updated", reply_markup=main_menu())
    await s.finish()

@dp.message_handler(state=SettingsEdit.city)
async def save_city(message: types.Message, state: FSMContext):
    save_user({"user_id": m.from_user.id, "city": m.text})
    await m.answer("Updated", reply_markup=main_menu())
    await s.finish()

@dp.message_handler(state=SettingsEdit.interest)
async def save_interest(message: types.Message, state: FSMContext):
    save_user({"user_id": m.from_user.id, "interest": m.text})
    await m.answer("Updated", reply_markup=main_menu())
    await s.finish()

# ================= MATCHING ================= #

def remove_from_queues(uid):
    for q in (waiting_any, waiting_male, waiting_female):
        if uid in q:
            q.remove(uid)

async def connect(u1,u2):
    active_chats[u1]=u2
    active_chats[u2]=u1
    await bot.send_message(u1,"Connected",reply_markup=chat_menu())
    await bot.send_message(u2,"Connected",reply_markup=chat_menu())

@dp.message_handler(lambda m: m.text == "🔍 Find Chat")
async def find_chat(m):
    uid = m.from_user.id
    u = get_user(uid)
    if is_banned(u):
        await m.answer("🚫 You are banned.")
        return
    remove_from_queues(uid)

    if is_premium(u):
        for other in waiting_any:
            ou = get_user(other)
            if ou and (ou["city"]==u["city"] or ou.get("interest")==u.get("interest")):
                waiting_any.remove(other)
                await connect(uid,other)
                return

    if waiting_any:
        await connect(uid, waiting_any.pop(0))
    else:
        waiting_any.append(uid)
        await m.answer("Waiting for match...")

@dp.message_handler(lambda m: m.text == "👨 Find a Man ⭐")
async def find_man(m):
    if not is_premium(get_user(m.from_user.id)):
        await m.answer("⭐ Premium required")
        return
    waiting_female.append(m.from_user.id)
    await m.answer("Waiting for a man...")

@dp.message_handler(lambda m: m.text == "👩 Find a Woman ⭐")
async def find_woman(m):
    if not is_premium(get_user(m.from_user.id)):
        await m.answer("⭐ Premium required")
        return
    waiting_male.append(m.from_user.id)
    await m.answer("Waiting for a woman...")

# ================= CHAT ================= #

@dp.message_handler(lambda m: m.text == "⏭ Next")
async def next_chat(m):
    uid=m.from_user.id
    if uid in active_chats:
        other=active_chats.pop(uid)
        active_chats.pop(other,None)
        await bot.send_message(other,"Chat ended",reply_markup=main_menu())
        await find_chat(m)

@dp.message_handler(lambda m: m.text == "⛔ Stop")
async def stop_chat(m):
    uid=m.from_user.id
    if uid in active_chats:
        other=active_chats.pop(uid)
        active_chats.pop(other,None)
        await bot.send_message(other,"Chat ended",reply_markup=main_menu())
    await m.answer("Stopped",reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "🚨 Report")
async def report(m):
    uid=m.from_user.id
    if uid not in active_chats:
        return
    partner=active_chats[uid]
    pu=get_user(partner)
    rc=pu.get("report_count",0)+1
    ban=None
    if rc>=5:
        ban="2099-01-01T00:00:00Z"
    elif rc>=3:
        ban=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
    save_user({"user_id":partner,"report_count":rc,"banned_until":ban})
    await stop_chat(m)

# ================= PREMIUM ================= #

@dp.message_handler(lambda m: m.text == "⭐ Premium")
async def premium(m):
    u=get_user(m.from_user.id)
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("💳 Pay via UPI","❌ Cancel")
    await m.answer(
        f"Premium: {'ACTIVE ⭐' if is_premium(u) else 'INACTIVE'}\n₹{PREMIUM_PRICE}/{PREMIUM_DAYS} days",
        reply_markup=kb
    )

@dp.message_handler(lambda m: m.text == "💳 Pay via UPI")
async def pay(m):
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ I've Paid","❌ Cancel")
    await m.answer(f"Pay ₹{PREMIUM_PRICE}\nUPI: {UPI_ID}\nName: {UPI_NAME}",reply_markup=kb)

@dp.message_handler(lambda m: m.text == "✅ I've Paid")
async def paid(m):
    await m.answer("Send UTR:")
    await PaymentState.utr.set()

@dp.message_handler(state=PaymentState.utr)
async def utr(m,s):
    await bot.send_message(ADMIN_ID,f"Payment Request\nUser:{m.from_user.id}\nUTR:{m.text}")
    await m.answer("Verification pending",reply_markup=main_menu())
    await s.finish()

# ================= INVITE & RULES ================= #

@dp.message_handler(lambda m: m.text == "🎁 Invite & Earn")
async def invite(m):
    bot_info=await bot.get_me()
    await m.answer(f"Invite friends:\nhttps://t.me/{bot_info.username}?start=ref_{m.from_user.id}")

@dp.message_handler(lambda m: m.text == "📜 Rules")
async def rules(m):
    await m.answer("18+ only\nNo abuse\nNo sexual content\nNo personal info\nViolation = ban")

# ================= RELAY ================= #

@dp.message_handler()
async def relay(m):
    if m.from_user.id in active_chats:
        save_user({"user_id": m.from_user.id,
                   "total_messages": get_user(m.from_user.id).get("total_messages",0)+1})
        await bot.send_message(active_chats[m.from_user.id], m.text)

# ================= PREMIUM REMINDER ================= #

async def premium_reminder():
    while True:
        now=datetime.now(timezone.utc)
        users=supabase.table("users").select("*").execute().data
        for u in users:
            if u.get("premium_until") and not u.get("expiry_notified"):
                exp=datetime.fromisoformat(u["premium_until"])
                if timedelta(hours=0)<exp-now<=timedelta(hours=6):
                    await bot.send_message(u["user_id"],"⏰ Premium expires in 6 hours")
                    save_user({"user_id":u["user_id"],"expiry_notified":True})
        await asyncio.sleep(1800)

# ================= RUN ================= #

if __name__=="__main__":
    loop=asyncio.get_event_loop()
    loop.create_task(premium_reminder())
    executor.start_polling(dp,skip_updates=True)
