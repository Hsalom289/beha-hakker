import asyncio
import sqlite3
import os
import time
import random
from datetime import datetime, timedelta
import pytz
import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

# Logging sozlash
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)
logger = logging.getLogger(__name__)

# Bot sozlamalari
API_ID = '23278326'  # YANGI API_ID bilan almashtiring
API_HASH = '1914eb5e6c74aef5d768ca3a9ff673ed'  # YANGI API_HASH bilan almashtiring
BOT_TOKEN = '7673048625:AAGDE-7X2C5Uay-tPU-hlmvCnZGLLulGJck'
ALLOWED_USER_IDS = [6374979572, 7807493773]  # Ruxsat berilgan ID lar ro‘yxati
AUTO_AD_INTERVAL = 3600  # Default 1 soat (sekundda)
timezone = pytz.timezone('Asia/Tashkent')

# Bot va Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# SQLite sozlash
conn = sqlite3.connect('sessions.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (user_id INTEGER, phone TEXT, session_file TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS ads
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ad_text TEXT, last_sent INTEGER)''')
try:
    cursor.execute("ALTER TABLE ads ADD COLUMN last_sent INTEGER")
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("ALTER TABLE ads ADD COLUMN user_id INTEGER")
except sqlite3.OperationalError:
    pass
conn.commit()

# Global o'zgaruvchilar (user_id bo‘yicha izolyatsiya)
user_states = {}  # {user_id: state}
auto_ad_tasks = {}  # {user_id: {phone: task}}
is_auto_ad_running = {}  # {user_id: bool}
global_stats = {}  # {user_id: {phone: stats}}
active_numbers = {}  # {user_id: set(phone)}

# Klaviaturalar
def get_main_keyboard(user_id, show_stop_button=False):
    keyboard = [
        [KeyboardButton(text="📱 Nomerlar"), KeyboardButton(text="📢 Reklama")],
        [KeyboardButton(text="🚀 Tarqatish"), KeyboardButton(text="🤖 Avto tarqatish")],
        [KeyboardButton(text="⚙️ Sozlamalar")]
    ]
    if show_stop_button and is_auto_ad_running.get(user_id, False):
        keyboard.append([KeyboardButton(text="🛑 To‘xtatish")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_manage_numbers_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Ulash"), KeyboardButton(text="🗑️ O‘chirish")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )

def get_settings_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏰ Vaqtni o‘zgartirish")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )

def get_auto_ad_mode_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤖 SpamBot bilan"), KeyboardButton(text="🚫 Spambotsiz")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )

def get_auto_ad_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Hamma nomer")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )

def get_distribute_mode_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤖 SpamBot bilan"), KeyboardButton(text="🚫 Spambotsiz")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )

def get_back_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True)

# Ruxsat tekshiruvi
def restricted(func):
    async def wrapper(message: Message):
        if message.from_user.id not in ALLOWED_USER_IDS:
            await message.answer("Bu bot faqat maxsus foydalanuvchilar uchun! 😒")
            return
        return await func(message)
    return wrapper

# Sessiya faylini tekshirish
def check_session_file(session_file):
    return os.path.exists(session_file)

# Spam holatini ko‘rsatish (faqat xabarni ko‘rsatadi, to‘xtatmaydi)
async def clear_spam(client: TelegramClient, message: Message, phone: str):
    try:
        await client.send_message('@SpamBot', '/start')
        await asyncio.sleep(2)
        await client.send_message('@SpamBot', '/start')
        await asyncio.sleep(2)
        messages = await client.get_messages('@SpamBot', limit=1)
        if messages and messages[0].text:
            spam_message = messages[0].text
            await message.answer(f"📡 {phone} uchun @SpamBot javobi: {spam_message}")
            logger.info(f"@SpamBot javobi ({phone}): {spam_message}")
        else:
            await message.answer(f"❌ {phone} uchun @SpamBot javob bermadi")
            logger.warning(f"@SpamBot javob bermadi ({phone})")
        return True  # Har qanday holatda tarqatish davom etadi
    except FloodWaitError as e:
        await message.answer(f"⏳ {phone}: Cheklov: {e.seconds} sek. kuting.")
        logger.warning(f"@SpamBot cheklovi ({phone}): {e.seconds} sek.")
        return True  # Cheklov bo‘lsa ham tarqatish davom etadi
    except Exception as e:
        await message.answer(f"❌ {phone}: @SpamBot tekshiruvida xato: {str(e)}")
        logger.error(f"@SpamBot tekshiruvida xato ({phone}): {str(e)}")
        return True  # Xato bo‘lsa ham tarqatish davom etadi

# Start komandasi
@dp.message(Command("start"))
@restricted
async def send_welcome(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {}
    await message.answer("Salom! 😎 Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
    logger.info(f"Bot /start komandasi qabul qildi: User {user_id}")

# Nomerlarni boshqarish
@dp.message(lambda message: message.text == "📱 Nomerlar")
@restricted
async def manage_numbers(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "manage_numbers"}
    await message.answer("📱 Nomerlar menyusi:", reply_markup=get_manage_numbers_keyboard())
    logger.info(f"Nomerlar menyusi ochildi: User {user_id}")

# Sozlamalar
@dp.message(lambda message: message.text == "⚙️ Sozlamalar")
@restricted
async def settings_menu(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "settings"}
    await message.answer("⚙️ Sozlamalar:", reply_markup=get_settings_keyboard())
    logger.info(f"Sozlamalar menyusi ochildi: User {user_id}")

# Nomer ulash
@dp.message(lambda message: message.text == "📱 Ulash")
@restricted
async def start_phone_auth(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "phone"}
    await message.answer("📞 Telefon raqamingizni kiriting (+998901234567 formatida):")
    logger.info(f"Nomer ulash boshlandi: User {user_id}")

# Nomer o‘chirish
@dp.message(lambda message: message.text == "🗑️ O‘chirish")
@restricted
async def start_delete_number(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT phone, session_file FROM sessions WHERE user_id = ?", (user_id,))
    sessions = cursor.fetchall()
    phones_info = []
    for phone, session_file in sessions:
        if check_session_file(session_file):
            client = TelegramClient(session_file, API_ID, API_HASH)
            try:
                await client.connect()
                user = await client.get_me()
                name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                phones_info.append((phone, name, session_file))
            except Exception as e:
                logger.error(f"Sessiya o‘chirishda xato ({phone}): {str(e)}")
                cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
                if os.path.exists(session_file):
                    try:
                        os.remove(session_file)
                    except Exception as e:
                        logger.error(f"Fayl o‘chirishda xato ({session_file}): {str(e)}")
                conn.commit()
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({phone})")
        else:
            cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
            conn.commit()

    if not phones_info:
        await message.answer("❌ Nomer ulanmagan!")
        user_states[user_id] = {"step": "manage_numbers"}
        await message.answer("📱 Nomerlar menyusi:", reply_markup=get_manage_numbers_keyboard())
        return

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=f"{i+1}. {phone} ({name})")] for i, (phone, name, _) in enumerate(phones_info)] + [[KeyboardButton(text="⬅️ Orqaga")]],
        resize_keyboard=True
    )
    user_states[user_id] = {"step": "delete_number", "phones_info": phones_info, "prev_step": "manage_numbers"}
    await message.answer("🗑️ O‘chirish uchun nomerni tanlang:", reply_markup=keyboard)
    logger.info(f"Nomer o‘chirish menyusi ochildi: User {user_id}")

# Reklama saqlash
@dp.message(lambda message: message.text == "📢 Reklama")
@restricted
async def start_ad_save(message: Message):
    user_id = message.from_user.id
    cursor.execute("DELETE FROM ads WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='ads'")
    conn.commit()
    logger.info(f"Eski reklamalar va AUTOINCREMENT o‘chirildi: User {user_id}")
    cursor.execute("SELECT id, ad_text FROM ads WHERE user_id = ?", (user_id,))
    ads = cursor.fetchall()
    if ads:
        logger.error(f"O‘chirish muvaffaqiyatsiz! Bazada hali reklamalar bor: {ads}")
        await message.answer("❌ Eski reklamalarni o‘chirishda xato yuz berdi. Iltimos, qayta urinib ko‘ring.")
        return
    user_states[user_id] = {"step": "ad_count"}
    await message.answer("📝 Necha ta reklama kiritmoqchisiz? (Masalan, 5):", reply_markup=get_back_keyboard())
    logger.info(f"Reklama soni so‘raldi: User {user_id}")

# Reklama tarqatish
@dp.message(lambda message: message.text == "🚀 Tarqatish")
@restricted
async def start_ad_distribute(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "select_distribute_mode"}
    await message.answer("📩 Tarqatish usulini tanlang:", reply_markup=get_distribute_mode_keyboard())
    logger.info(f"Tarqatish usuli menyusi ochildi: User {user_id}")

# Avto reklama tarqatish
@dp.message(lambda message: message.text == "🤖 Avto tarqatish")
@restricted
async def start_auto_ad_distribute(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "select_auto_ad_mode"}
    await message.answer("📩 Avto tarqatish usulini tanlang:", reply_markup=get_auto_ad_mode_keyboard())
    logger.info(f"Avto tarqatish usuli menyusi ochildi: User {user_id}")

# Reklama yuborish
async def send_ads(client: TelegramClient, message: Message, phone: str, name: str, use_spambot=True):
    user_id = message.from_user.id
    active_numbers.setdefault(user_id, set()).add(phone)  # Nomer faol deb belgilash
    logger.info(f"Nomer faol qilindi: {phone} (User {user_id})")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await message.answer(f"❌ Sessiya faol emas ({phone})!")
            cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
            if os.path.exists(client.session.filename):
                try:
                    os.remove(client.session.filename)
                except Exception as e:
                    logger.error(f"Fayl o‘chirishda xato ({client.session.filename}): {str(e)}")
            conn.commit()
            return

        if use_spambot:
            await clear_spam(client, message, phone)  # Xabarni ko‘rsatadi, lekin to‘xtatmaydi
        else:
            await message.answer(f"🚫 {phone}: SpamBot tekshiruvi o‘tkazilmasdan tarqatish boshlandi.")

        dialogs = await client.get_dialogs()
        groups = [dialog for dialog in dialogs if dialog.is_group]
        if not groups:
            await message.answer(f"❌ {phone} da guruh yo‘q!")
            return

        cursor.execute("SELECT id, ad_text FROM ads WHERE user_id = ?", (user_id,))
        ads = cursor.fetchall()
        if not ads:
            await message.answer("❌ Reklama matni yo‘q!")
            return
        selected_ad = random.choice(ads)
        ad_id, ad_text = selected_ad
        logger.info(f"Tanlangan reklama: ID={ad_id}, Matn={ad_text[:50]}... (User {user_id})")

        await message.answer(f"{ad_id}-reklama tanlandi\n📱 {phone} ({name}): {len(groups)} guruhga reklama yuborilmoqda... 🚀")
        success_count = 0
        failed_count = 0

        for group in groups:
            if phone in auto_ad_tasks.get(user_id, {}) and auto_ad_tasks[user_id][phone].cancelled():
                break
            try:
                await client.send_message(group, ad_text)
                success_count += 1
                await asyncio.sleep(0.1)
            except FloodWaitError as e:
                failed_count += 1
                await message.answer(f"⏳ {phone}: Cheklov: {e.seconds} sek. kuting.")
                continue
            except Exception:
                failed_count += 1
                continue

        cursor.execute("UPDATE ads SET last_sent = ? WHERE id = ? AND user_id = ?", (int(time.time()), ad_id, user_id))
        conn.commit()

        global_stats.setdefault(user_id, {})[phone] = {"success": success_count, "failed": failed_count, "name": name}
        await message.answer(
            f"📊 Nomer: {phone}\n"
            f"Ism Familiya: {name}\n"
            f"✅ Tarqatildi: {success_count}\n"
            f"❌ Tarqatilmadi: {failed_count}"
        )
    except Exception as e:
        await message.answer(f"Xato ({phone}): {str(e)}")
        logger.error(f"Reklama yuborishda xato ({phone}): {str(e)} (User {user_id})")
    finally:
        try:
            await client.disconnect()
        except Exception:
            logger.warning(f"Sessiya yopishda xato ({phone})")
        active_numbers.get(user_id, set()).discard(phone)  # Nomer faol emas deb belgilash
        logger.info(f"Nomer faolligi o‘chirildi: {phone} (User {user_id})")

# Avto reklama tsikli
async def auto_ad_cycle(message: Message, phones_info, exclude_indices, use_spambot=True):
    user_id = message.from_user.id
    is_auto_ad_running[user_id] = True
    while is_auto_ad_running.get(user_id, False):
        cycle_start_time = time.time()
        tasks = []
        for i, (phone, name, session_file) in enumerate(phones_info):
            if i not in exclude_indices:
                client = TelegramClient(session_file, API_ID, API_HASH)
                task = asyncio.create_task(send_ads(client, message, phone, name, use_spambot))
                auto_ad_tasks.setdefault(user_id, {})[phone] = task
                tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            stats_message = ""
            for phone, stats in global_stats.get(user_id, {}).items():
                stats_message += (
                    f"📊 Nomer: {phone}\n"
                    f"Ism Familiya: {stats['name']}\n"
                    f"✅ Tarqatildi: {stats['success']}\n"
                    f"❌ Tarqatilmadi: {stats['failed']}\n\n"
                )
            elapsed_time = time.time() - cycle_start_time
            remaining_time = max(0, AUTO_AD_INTERVAL - elapsed_time)
            next_time = (datetime.now(timezone) + timedelta(seconds=remaining_time)).strftime("%H:%M (%z)")
            stats_message += f"⏳ Keyingi tarqatish: {next_time}"
            await message.answer(stats_message, reply_markup=get_main_keyboard(user_id, True))
            # Faol nomerlarni tozalash
            for phone in list(auto_ad_tasks.get(user_id, {}).keys()):
                if auto_ad_tasks[user_id][phone].done():
                    auto_ad_tasks[user_id].pop(phone, None)
                    active_numbers.get(user_id, set()).discard(phone)
                    logger.info(f"Avto tarqatish tugadi, nomer holati yangilandi: {phone} (User {user_id})")
            await asyncio.sleep(remaining_time)
        else:
            await message.answer("❌ Nomer tanlanmadi!")
            is_auto_ad_running[user_id] = False
            break

# Xabarlarni qayta ishlash
@dp.message()
@restricted
async def handle_message(message: Message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {})
    global_stats.setdefault(user_id, {})
    active_numbers.setdefault(user_id, set())
    auto_ad_tasks.setdefault(user_id, {})
    is_auto_ad_running.setdefault(user_id, False)

    try:
        logger.info(f"Xabar qabul qilindi: {message.text} (User {user_id})")
        if message.text == "⬅️ Orqaga":
            if state.get("step") == "manage_numbers":
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
            elif state.get("step") in ["phone", "code", "password", "delete_number"]:
                user_states[user_id] = {"step": "manage_numbers"}
                await message.answer("📱 Nomerlar menyusi:", reply_markup=get_manage_numbers_keyboard())
            elif state.get("step") == "settings":
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
            elif state.get("step") == "set_interval":
                user_states[user_id] = {"step": "settings"}
                await message.answer("⚙️ Sozlamalar:", reply_markup=get_settings_keyboard())
            elif state.get("step") in ["ad_count", "ad_text"]:
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Reklama kiritish to‘xtatildi: User {user_id}")
            elif state.get("step") in ["select_distribute_mode", "select_phone", "select_auto_ad_mode", "select_auto_exclude"]:
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
            return

        if message.text == "🛑 To‘xtatish" and is_auto_ad_running.get(user_id, False):
            is_auto_ad_running[user_id] = False
            for phone, task in auto_ad_tasks.get(user_id, {}).items():
                task.cancel()
            auto_ad_tasks[user_id].clear()
            active_numbers[user_id].clear()  # Barcha nomerlar holatini tozalash
            logger.info(f"Barcha nomerlar holati yangilandi: {active_numbers[user_id]} (User {user_id})")
            total_accounts = len(global_stats.get(user_id, {}))
            total_success = sum(s["success"] for s in global_stats.get(user_id, {}).values())
            total_failed = sum(s["failed"] for s in global_stats.get(user_id, {}).values())
            report = (
                f"🛑 Reklama to‘xtatildi:\n"
                f"🔢 Tarqatgan akkauntlar: {total_accounts} ta\n"
                f"📈 Jami guruhlar: ✅ {total_success} ❌ {total_failed}"
            )
            await message.answer(report, reply_markup=get_main_keyboard(user_id, False))
            global_stats[user_id].clear()
            user_states[user_id] = {}
            logger.info(f"Avto tarqatish to‘xtatildi: User {user_id}")
            return

        if message.text == "⏰ Vaqtni o‘zgartirish":
            user_states[user_id] = {"step": "set_interval"}
            await message.answer("⏰ Intervalni sekundda kiriting (masalan, 300):", reply_markup=get_back_keyboard())
            logger.info(f"Vaqt o‘zgartirish boshlandi: User {user_id}")
            return

        if state.get("step") == "set_interval":
            try:
                seconds = float(message.text.strip())
                if seconds <= 0:
                    await message.answer("❌ Musbat son kiriting!")
                    return
                global AUTO_AD_INTERVAL
                AUTO_AD_INTERVAL = int(seconds)
                await message.answer(f"✅ Interval {seconds} sekundga o‘zgartirildi.")
                user_states[user_id] = {"step": "settings"}
                await message.answer("⚙️ Sozlamalar:", reply_markup=get_settings_keyboard())
                logger.info(f"Interval o‘zgartirildi: {seconds} sekund (User {user_id})")
            except ValueError:
                await message.answer("❌ Faqat raqam kiriting (masalan, 300)!")
            return

        if state.get("step") == "phone":
            phone = message.text.strip()
            if not phone.startswith('+') or not phone[1:].isdigit():
                await message.answer("Iltimos, to'g'ri formatda raqam kiriting (+998901234567)!")
                logger.info(f"Noto‘g‘ri telefon raqami kiritildi: {phone} (User {user_id})")
                return

            session_file = os.path.join('sessions', f"{user_id}_{phone}.session")
            client = TelegramClient(session_file, API_ID, API_HASH, connection_retries=10, retry_delay=5)

            try:
                await client.connect()
                await client.send_code_request(phone)
                user_states[user_id] = {"step": "code", "phone": phone, "session_file": session_file, "client": client}
                await message.answer("📩 Sizga kod yuborildi. Iltimos, kodni kiriting:")
                logger.info(f"Kod yuborildi: {phone} (User {user_id})")
            except Exception as e:
                await message.answer(f"Xato yuz berdi: {str(e)}")
                logger.error(f"Kod yuborish muvaffaqiyatsiz: {phone}, Xato: {str(e)} (User {user_id})")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({phone})")

        elif state.get("step") == "code":
            code = message.text.strip()
            client = state.get("client")
            phone = state.get("phone")
            session_file = state.get("session_file")

            try:
                await client.connect()
                await client.sign_in(phone, code)
                await save_session(user_id, phone, session_file)
                await message.answer("✅ Nomer muvaffaqiyatli ulandi! Sessiya saqlandi.")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Nomer ulandi: {phone} (User {user_id})")
            except SessionPasswordNeededError:
                user_states[user_id]["step"] = "password"
                await message.answer("🔐 2FA parol talab qilinadi. Iltimos, parolni kiriting:")
                logger.info(f"Parol talab qilindi: {phone} (User {user_id})")
            except Exception as e:
                await message.answer(f"Xato: {str(e)}")
                logger.error(f"Kod kirishda xato ({phone}): {str(e)} (User {user_id})")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({phone})")

        elif state.get("step") == "password":
            password = message.text.strip()
            client = state.get("client")
            phone = state.get("phone")
            session_file = state.get("session_file")

            try:
                await client.connect()
                await client.sign_in(password=password)
                await save_session(user_id, phone, session_file)
                await message.answer("✅ Nomer muvaffaqiyatli ulandi! Sessiya saqlandi.")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Nomer ulandi (parol bilan): {phone} (User {user_id})")
            except Exception as e:
                await message.answer(f"Xato: {str(e)}")
                logger.error(f"Parol kirishda xato ({phone}): {str(e)} (User {user_id})")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({phone})")

        elif state.get("step") == "ad_count":
            try:
                ad_count = int(message.text.strip())
                if ad_count <= 0:
                    await message.answer("❌ Musbat son kiriting!")
                    return
                user_states[user_id] = {
                    "step": "ad_text",
                    "ad_count": ad_count,
                    "current_ad": 1,
                    "ad_texts": []
                }
                await message.answer(f"📝 Reklama matnini kiriting (1/{ad_count}):", reply_markup=get_back_keyboard())
                logger.info(f"Reklama soni kiritildi: {ad_count} (User {user_id})")
            except ValueError:
                await message.answer("❌ Faqat raqam kiriting (masalan, 5)!")
            return

        elif state.get("step") == "ad_text":
            ad_text = message.text.strip()
            if not ad_text:
                await message.answer("❌ Matn kiriting!")
                return
            state["ad_texts"].append(ad_text)
            state["current_ad"] += 1
            if state["current_ad"] <= state["ad_count"]:
                await message.answer(f"📝 Reklama matnini kiriting ({state['current_ad']}/{state['ad_count']}):", reply_markup=get_back_keyboard())
                logger.info(f"Reklama matni kiritildi: {state['current_ad']-1}/{state['ad_count']} (User {user_id})")
            else:
                for ad in state["ad_texts"]:
                    cursor.execute("INSERT INTO ads (user_id, ad_text, last_sent) VALUES (?, ?, ?)", (user_id, ad, 0))
                conn.commit()
                await message.answer(f"✅ {state['ad_count']} ta reklama saqlandi!")
                cursor.execute("SELECT id, ad_text FROM ads WHERE user_id = ?", (user_id,))
                saved_ads = cursor.fetchall()
                logger.info(f"Saqlangan reklamalar: {saved_ads} (User {user_id})")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Reklamalar saqlandi: {state['ad_count']} ta (User {user_id})")

        elif state.get("step") == "delete_number":
            selected_text = message.text.strip()
            phones_info = state.get("phones_info", [])
            selected_phone = None
            selected_session_file = None
            for phone, name, session_file in phones_info:
                if selected_text.startswith(f"{phones_info.index((phone, name, session_file))+1}. {phone}"):
                    selected_phone = phone
                    selected_session_file = session_file
                    break
            if not selected_phone or not selected_session_file:
                await message.answer("❌ Noto‘g‘ri nomer!")
                user_states[user_id] = {"step": "manage_numbers"}
                await message.answer("📱 Nomerlar menyusi:", reply_markup=get_manage_numbers_keyboard())
                return
            client = TelegramClient(selected_session_file, API_ID, API_HASH)
            try:
                await client.connect()
                if await client.is_user_authorized():
                    await client.log_out()
                if os.path.exists(selected_session_file):
                    try:
                        os.remove(selected_session_file)
                    except Exception as e:
                        logger.error(f"Fayl o‘chirishda xato ({selected_session_file}): {str(e)}")
                cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, selected_phone))
                conn.commit()
                await message.answer(f"✅ {selected_phone} o‘chirildi!")
            except Exception as e:
                await message.answer(f"Xato: {str(e)}")
                logger.error(f"Nomer o‘chirishda xato ({selected_phone}): {str(e)}")
                if os.path.exists(selected_session_file):
                    try:
                        os.remove(selected_session_file)
                    except Exception as e:
                        logger.error(f"Fayl o‘chirishda xato ({selected_session_file}): {str(e)}")
                cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, selected_phone))
                conn.commit()
                await message.answer(f"✅ {selected_phone} o‘chirildi!")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({selected_phone})")
            user_states[user_id] = {"step": "manage_numbers"}
            await message.answer("📱 Nomerlar menyusi:", reply_markup=get_manage_numbers_keyboard())

        elif state.get("step") == "select_distribute_mode":
            if message.text in ["🤖 SpamBot bilan", "🚫 Spambotsiz"]:
                use_spambot = message.text == "🤖 SpamBot bilan"
                cursor.execute("SELECT phone, session_file FROM sessions WHERE user_id = ?", (user_id,))
                sessions = cursor.fetchall()
                phones_info = []
                for phone, session_file in sessions:
                    if check_session_file(session_file):
                        client = TelegramClient(session_file, API_ID, API_HASH)
                        try:
                            await client.connect()
                            user = await client.get_me()
                            name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                            phones_info.append((phone, name, session_file))
                        except Exception as e:
                            logger.error(f"Sessiya xatosi ({phone}): {str(e)}")
                            cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
                            if os.path.exists(session_file):
                                try:
                                    os.remove(session_file)
                                except Exception as e:
                                    logger.error(f"Fayl o‘chirishda xato ({session_file}): {str(e)}")
                            conn.commit()
                        finally:
                            try:
                                await client.disconnect()
                            except Exception:
                                logger.warning(f"Sessiya yopishda xato ({phone})")
                    else:
                        cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
                        conn.commit()

                if not phones_info:
                    await message.answer("❌ Nomer ulanmagan!")
                    user_states[user_id] = {}
                    await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                    return

                keyboard = ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text=f"{i+1}. {phone} ({name})")] for i, (phone, name, _) in enumerate(phones_info)] + [[KeyboardButton(text="⬅️ Orqaga")]],
                    resize_keyboard=True
                )
                user_states[user_id] = {"step": "select_phone", "phones_info": phones_info, "spambot": use_spambot}
                await message.answer("📞 Tarqatish uchun nomerni tanlang:", reply_markup=keyboard)
                logger.info(f"{'SpamBot bilan' if use_spambot else 'Spambotsiz'} tarqatish nomer tanlash: User {user_id}")
            else:
                await message.answer("❌ Noto‘g‘ri usul! Iltimos, quyidagi knopkalardan birini tanlang.")
                return

        elif state.get("step") == "select_phone":
            selected_text = message.text.strip()
            phones_info = state.get("phones_info", [])
            use_spambot = state.get("spambot", True)
            selected_phone = None
            selected_session_file = None
            for phone, name, session_file in phones_info:
                if selected_text.startswith(f"{phones_info.index((phone, name, session_file))+1}. {phone}"):
                    selected_phone = phone
                    selected_session_file = session_file
                    break
            if not selected_phone or not selected_session_file:
                await message.answer("❌ Noto‘g‘ri nomer!")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                return
            client = TelegramClient(selected_session_file, API_ID, API_HASH)
            try:
                name = next((p[1] for p in phones_info if p[0] == selected_phone), "")
                await send_ads(client, message, selected_phone, name, use_spambot)
                await message.answer("✅ Tarqatish yakunlandi!")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Tarqatish yakunlandi: {selected_phone}, SpamBot={use_spambot} (User {user_id})")
            except Exception as e:
                await message.answer(f"Xato: {str(e)}")
                logger.error(f"Tarqatishda xato ({selected_phone}): {str(e)} (User {user_id})")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(f"Sessiya yopishda xato ({selected_phone})")
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))

        elif state.get("step") == "select_auto_ad_mode":
            if message.text in ["🤖 SpamBot bilan", "🚫 Spambotsiz"]:
                use_spambot = message.text == "🤖 SpamBot bilan"
                cursor.execute("SELECT phone, session_file FROM sessions WHERE user_id = ?", (user_id,))
                sessions = cursor.fetchall()
                phones_info = []
                for phone, session_file in sessions:
                    if check_session_file(session_file):
                        client = TelegramClient(session_file, API_ID, API_HASH)
                        try:
                            await client.connect()
                            user = await client.get_me()
                            name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                            phones_info.append((phone, name, session_file))
                        except Exception as e:
                            logger.error(f"Sessiya xatosi ({phone}): {str(e)}")
                            cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
                            if os.path.exists(session_file):
                                try:
                                    os.remove(session_file)
                                except Exception as e:
                                    logger.error(f"Fayl o‘chirishda xato ({session_file}): {str(e)}")
                            conn.commit()
                        finally:
                            try:
                                await client.disconnect()
                            except Exception:
                                logger.warning(f"Sessiya yopishda xato ({phone})")
                    else:
                        cursor.execute("DELETE FROM sessions WHERE user_id = ? AND phone = ?", (user_id, phone))
                        conn.commit()

                if not phones_info:
                    await message.answer("❌ Nomer ulanmagan!")
                    user_states[user_id] = {}
                    await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                    return

                numbers_message = "".join(f"{i+1}. {phone} ({name})\n" for i, (phone, name, _) in enumerate(phones_info))
                numbers_message += "Qaysi nomerni istisno qilasiz? (Raqamlarni kiriting, masalan: 1 2)"
                user_states[user_id] = {"step": "select_auto_exclude", "phones_info": phones_info, "spambot": use_spambot}
                await message.answer(numbers_message, reply_markup=get_auto_ad_keyboard())
                logger.info(f"Avto tarqatish nomer tanlash, SpamBot={use_spambot}: User {user_id}")
            else:
                await message.answer("❌ Noto‘g‘ri usul! Iltimos, quyidagi knopkalardan birini tanlang.")
                return

        elif state.get("step") == "select_auto_exclude":
            if message.text == "📋 Hamma nomer":
                phones_info = state.get("phones_info", [])
                use_spambot = state.get("spambot", True)
                await auto_ad_cycle(message, phones_info, [], use_spambot)
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Avto tarqatish boshlandi (hamma nomer, SpamBot={use_spambot}): User {user_id}")
            else:
                exclude_input = message.text.strip()
                phones_info = state.get("phones_info", [])
                use_spambot = state.get("spambot", True)
                exclude_indices = []
                if exclude_input:
                    try:
                        exclude_indices = [int(i) - 1 for i in exclude_input.split()]
                        exclude_indices = [i for i in exclude_indices if 0 <= i < len(phones_info)]
                    except ValueError:
                        await message.answer("❌ Raqamlarni kiriting (masalan, 1 2)!")
                        return
                await auto_ad_cycle(message, phones_info, exclude_indices, use_spambot)
                user_states[user_id] = {}
                await message.answer("Funksiyani tanlang:", reply_markup=get_main_keyboard(user_id))
                logger.info(f"Avto tarqatish boshlandi (istisno: {exclude_indices}, SpamBot={use_spambot}): User {user_id}")

    except Exception as e:
        await message.answer("❌ Botda umumiy xato yuz berdi. Iltimos, qaytadan boshlang (/start):")
        logger.error(f"Xabar qayta ishlashda umumiy xato: {str(e)} (User {user_id})")
        user_states[user_id] = {}

# Sessiyani SQLite'ga saqlash
async def save_session(user_id: int, phone: str, session_file: str):
    cursor.execute("INSERT OR REPLACE INTO sessions (user_id, phone, session_file) VALUES (?, ?, ?)",
                   (user_id, phone, session_file))
    conn.commit()
    logger.info(f"Sessiya saqlandi: {phone} (User {user_id})")

# Botni ishga tushirish
async def main():
    logger.info("Bot ishga tushmoqda...")
    while True:
        try:
            await dp.start_polling(bot)
            logger.info("Bot polling boshlandi")
        except Exception as e:
            logger.error(f"Bot polling xatosi: {str(e)}")
            await asyncio.sleep(5)

if __name__ == '__main__':
    if not os.path.exists('sessions'):
        os.makedirs('sessions')
    asyncio.run(main())