import os
import asyncio
import logging
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any, Union
import time

# --- Telethon imports for Telegram client and errors ---
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from telethon.sessions import StringSession

from utils import (
    start_announcement,
    stop_announcement,
    ensure_scheduler_running,
    get_folder_peers,
    process_single_announcement,
    get_cached_driver,
    get_cached_client,
    AnnouncementState,
    cleanup_client
)
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from keyboards import (
    admin_menu,
    driver_details_keyboard,
    login_menu,
    main_menu,
    pagination_keyboard,
)
from announcement_store import announcement_store, AnnouncementData
from driver_store import driver_store, Driver

# Load settings from config file
with open('config.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)

# Configure logging to write to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('announcement_debug.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# No filesystem sessions; StringSession only
SESSION_DIR = None

# Global state
_clients: Dict[int, TelegramClient] = {}
_tasks: Dict[int, asyncio.Task] = {}
_locks: Dict[int, asyncio.Lock] = {}
_client_locks: Dict[int, asyncio.Lock] = {}  # New lock for client operations

# Track last bot messages for each user
_last_bot_messages: Dict[int, int] = {}

# List of valid commands and menu buttons
VALID_COMMANDS = {
    '/start',
    '/delete',
    '/help',
    'Haydovchi qo\'shish',
    '🚗 Haydovchilar',
    'Qidirish',
    'Kirish',
    'Guruhlar papkasi',
    'Xabar Yaratish',
    'Stop',
    'Yangi xabar',
    'Delete session'
}

# Client management
_client_locks = {}
CLIENT_CACHE = {}

# --- Bot & Dispatcher ---
bot = Bot(token=settings['BOT_TOKEN'], parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# --- Helper functions ---
async def is_active_driver(user_id: int) -> bool:
    """Check if user is an active driver."""
    try:
        driver = await driver_store.get_driver(user_id)
        if not driver:
            return False
        return driver.is_active and not driver.is_expired
    except Exception:
        return False

async def is_admin(user_id: int) -> bool:
    """Check if user is an admin."""
    return user_id in settings['ADMINS']

# Create global state instance
state = AnnouncementState()

async def get_client(user_id: int) -> TelegramClient:
    """Get cached client via utils.get_cached_client (StringSession)."""
    return await get_cached_client(user_id)

async def disconnect_client(user_id: int):
    """Safely disconnect a client."""
    lock = _client_locks.get(user_id)
    if not lock:
        return
        
    async with lock:
        if user_id in CLIENT_CACHE:
            client = CLIENT_CACHE[user_id]
            await client.disconnect()
            del CLIENT_CACHE[user_id]


# --- FSM States ---
class AdminStates(StatesGroup):
    add_driver_name = State()
    add_driver_phone = State()
    add_driver_tg_id = State()
    add_driver_days = State()
    search_driver = State()


class DriverStates(StatesGroup):
    api_id = State()
    api_hash = State()
    phone = State()
    code = State()
    password = State()
    search = State()


class MessageStates(StatesGroup):
    text = State()
    interval = State()


class FolderStates(StatesGroup):
    waiting_name = State()


class AnnouncementStates(StatesGroup):
    text = State()
    interval = State()


# --- Helper functions ---
async def get_driver(user_id: int) -> Optional[Driver]:
    """Get driver by Telegram ID."""
    return await driver_store.get_driver(user_id)


async def get_all_drivers() -> List[Driver]:
    """Get all drivers."""
    return await driver_store.get_all_drivers()


async def get_active_drivers() -> List[Driver]:
    """Get all active drivers."""
    return await driver_store.get_active_drivers()


async def get_inactive_drivers() -> List[Driver]:
    """Get all inactive drivers."""
    return await driver_store.get_inactive_drivers()


async def clean_up_chat(message: types.Message, new_markup_message: Optional[types.Message] = None):
    """
    Clean up chat messages with improved logic:
    - Only deletes in non-admin chats
    - Keeps messages with reply_markup until new one arrives
    - Maintains cleaner chat history
    """
    user_id = message.from_user.id
    
    # Don't clean up in admin chats
    if user_id in settings['ADMINS']:
        return
        
    chat_id = message.chat.id
    try:
        # Delete user's message if it's not a valid command
        should_delete = True
        if message.text:
            # Check for exact matches
            if message.text in VALID_COMMANDS:
                should_delete = False
            # Check for slash commands with parameters (like /delete 123456)
            elif message.text.startswith('/'):
                command = message.text.split()[0]
                if command in VALID_COMMANDS:
                    should_delete = False
        
        if should_delete:
            try:
                await message.delete()
            except Exception as e:
                log.debug(f"Could not delete user message: {e}")
        
        # If we have a new message with markup, delete the previous one
        if new_markup_message:
            # Delete the previous bot message if it exists
            last_message_id = _last_bot_messages.get(user_id)
            if last_message_id:
                try:
                    await message.bot.delete_message(
                        chat_id=chat_id,
                        message_id=last_message_id
                    )
                except Exception as e:
                    log.debug(f"Could not delete previous message {last_message_id}: {e}")
            
            # Update the last message ID
            _last_bot_messages[user_id] = new_markup_message.message_id
            
            # Also try to delete any messages between the last and current message
            if last_message_id:
                try:
                    current_id = new_markup_message.message_id
                    for msg_id in range(last_message_id + 1, current_id):
                        try:
                            await message.bot.delete_message(
                                chat_id=chat_id,
                                message_id=msg_id
                            )
                        except Exception as e:
                            log.debug(f"Could not delete intermediate message {msg_id}: {e}")
                except Exception as e:
                    log.error(f"Error cleaning intermediate messages: {e}")
                    
    except Exception as e:
        log.error(f"Error cleaning up chat: {e}")


@router.message(CommandStart())
async def admin_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in settings['ADMINS']:
        sent_msg = await message.answer("Xush kelibsiz, admin! Variantni tanlang:", reply_markup=admin_menu)
        return

    if not await is_active_driver(user_id):
        sent_msg = await message.answer("❌ Ruxsat berilmagan. Administratordan hisobingizni faollashtirishni so'rang.\n@avtobot_admin")
        await clean_up_chat(message, sent_msg)
        return

    driver = await driver_store.get_driver(user_id)
    has_session = bool(driver and driver.session_string)

    # Check active announcement using JSON store only
    ann_data = await announcement_store.get_announcement(user_id)
    has_active_announcement = bool(ann_data and ann_data.is_running)

    if has_session:
        sent_msg = await message.answer(
            "Xush kelibsiz! Variantni tanlang:",
            reply_markup=main_menu(has_active_announcement)
        )
        await clean_up_chat(message, sent_msg)
    else:
        sent_msg = await message.answer(
            "Xush kelibsiz! Avval hisobingizga kirishingiz kerak. Kirish tugmasini bosing.",
            reply_markup=login_menu()
        )
        await clean_up_chat(message, sent_msg)


@router.message(lambda msg: msg.text == "Haydovchi qo'shish")
async def add_driver_start(message: types.Message, state: FSMContext):
    """Handler for adding a new driver."""
    sent_msg = await message.answer("Haydovchining ism-sharifini kiriting:")
    await state.set_state(AdminStates.add_driver_name)


@router.message(AdminStates.add_driver_name)
async def add_driver_name(message: types.Message, state: FSMContext):
    """Process driver name input."""
    # Ensure proper text encoding for Unicode characters (including Cyrillic)
    import unicodedata
    driver_name = unicodedata.normalize('NFC', message.text.strip())
    
    sent_msg = await message.answer("Haydovchining telefon raqamini kiriting:")
    await state.update_data(name=driver_name)
    await state.set_state(AdminStates.add_driver_phone)


@router.message(AdminStates.add_driver_phone)
async def add_driver_phone(message: types.Message, state: FSMContext):
    """Process driver phone input."""
    await state.update_data(phone=message.text.strip())
    sent_msg = await message.answer("Haydovchining Telegram ID raqamini kiriting")
    await state.set_state(AdminStates.add_driver_tg_id)


@router.message(AdminStates.add_driver_tg_id)
async def add_driver_tg_id(message: types.Message, state: FSMContext):
    """Process driver Telegram ID input."""
    tg_id = message.text.strip()

    if not tg_id.isdigit():
        sent_msg = await message.answer("Telegram ID raqami bo'lishi kerak. Qayta urining.")
        return

    await state.update_data(tg_id=int(tg_id))
    sent_msg = await message.answer("Haydovchi necha kun faol bo'lishi kerak? (Namuna : 30)")
    await state.set_state(AdminStates.add_driver_days)


@router.message(AdminStates.add_driver_days)
async def add_driver_days(message: types.Message, state: FSMContext):
    """Process driver active days input and create driver."""
    if not message.text.isdigit():
        sent_msg = await message.answer("Kunlar son sifatida bo'lishi kerak. Qayta urining.")
        return

    days = int(message.text.strip())
    data = await state.get_data()

    # Create driver
    active_until = datetime.now() + timedelta(days=days)

    try:
        driver = Driver(
            tg_id=data['tg_id'],
            full_name=data['name'],
            phone=data['phone'],
            active_until=active_until,
            is_active=True
        )
        await driver_store.create_driver(driver)

        # Sync the new driver to JSON cache
        await get_cached_driver(data['tg_id'])

        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        driver_name = unicodedata.normalize('NFC', data['name'])
        
        sent_msg = await message.answer(
            f"✅ Haydovchi {driver_name} kiritildi!\n"
            f"Quyidagi vaqtgacha faol: {active_until.strftime('%Y-%m-%d')}"
        )
    except Exception as e:
        sent_msg = await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")
    finally:
        await state.clear()


@router.message(lambda msg: msg.text == "🚗 Haydovchilar")
async def list_drivers(message: types.Message, state: FSMContext):
    """List all drivers."""
    user_id = message.from_user.id

    # Check if user is admin
    if not await is_admin(user_id):
        return

    # Get all drivers
    drivers = await get_all_drivers()
    active_drivers = await get_active_drivers()
    inactive_drivers = await get_inactive_drivers()

    # Create message
    text = f"🚗 Haydovchilar ro'yxati:\n\n"
    text += f"✅ Faol haydovchilar: {len(active_drivers)}\n"
    text += f"❌ Faol bo'lmagan haydovchilar: {len(inactive_drivers)}\n\n"

    if not drivers:
        text += "Haydovchilar mavjud emas."
    else:
        text += "Haydovchilar ro'yxati:\n\n"
        for i, driver in enumerate(drivers, 1):
            # Ensure proper text encoding for Unicode characters (including Cyrillic)
            import unicodedata
            driver_name = unicodedata.normalize('NFC', driver.full_name)
            status = "✅" if driver.is_active else "❌"
            text += f"{i}. {status} {driver_name} - {driver.phone}\n"

    sent_msg = await message.answer(text, reply_markup=pagination_keyboard(1, len(drivers)))


@router.message(lambda msg: msg.text == "Qidirish")
async def search_driver_start(message: types.Message, state: FSMContext):
    """Start driver search process."""
    user_id = message.from_user.id

    # Check if user is admin
    if not await is_admin(user_id):
        return

    sent_msg = await message.answer("Haydovchi telefon raqamini kiriting:")
    await state.set_state(DriverStates.search)


@router.message(DriverStates.search)
async def search_driver(message: types.Message, state: FSMContext):
    """Search for a driver by phone number."""
    phone = message.text.strip()

    try:
        # Get all drivers and find the one with matching phone
        drivers = await driver_store.get_all_drivers()
        driver = next((d for d in drivers if d.phone == phone), None)
        
        if not driver:
            sent_msg = await message.answer("❌ Haydovchi topilmadi.")
            return

        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        driver_name = unicodedata.normalize('NFC', driver.full_name)
        
        text = f"Haydovchi topildi:\n\n"
        text += f"👤 Ism: {driver_name}\n"
        text += f"📱 Telefon: {driver.phone}\n"
        text += f"🆔 Telegram ID: {driver.tg_id}\n"
        text += f"📅 Faol kunlar: {driver.active_until.strftime('%Y-%m-%d')} gacha\n"
        text += f"✅ Status: {'Faol' if driver.is_active else 'Faol emas'}"

        sent_msg = await message.answer(text, reply_markup=driver_details_keyboard(driver.tg_id, driver.is_active))
    except Exception as e:
        log.error(f"Error searching driver: {e}")
        sent_msg = await message.answer("❌ Xatolik yuz berdi.")
    finally:
        await state.clear()


@router.message(lambda msg: msg.text == "Kirish")
async def login_command(message: types.Message, state: FSMContext):
    """Handler for login command."""
    user_id = message.from_user.id

    # Check if user is an active driver
    if not await is_active_driver(user_id):
        return

    sent_msg = await message.answer_video(
        video="BAACAgIAAxkBAAMEaD9BXIbXrjuKjMdrKSFDklK5uDoAAk90AAJ4K_lJaNCgAbIgjqU2BA",
        caption="""Hurmatli haydovchi ushbu botdan ro'yxatdan o'tish uchun my.telegram.org saytiga kirib o'z akkauntingiz API_ID va API_HASH ma'lumotlarini tug'dim qilishingiz zarur ushbu video orqali u ma'lumotlarni qanday olish mumkinligini bilib olasiz"""
    )
    sent_msg = await message.answer("Telegram API ID raqamini kiriting (raqamli):")
    await state.set_state(DriverStates.api_id)


@router.message(DriverStates.api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    """Process API ID input."""
    if not message.text.isdigit():
        sent_msg = await message.answer("❌ API ID raqami bo'lishi kerak. Qayta urining.")
        return

    await state.update_data(api_id=int(message.text.strip()))
    sent_msg = await message.answer("Endi API Hash (maxfiy satrni) yuboring:")
    await state.set_state(DriverStates.api_hash)


@router.message(DriverStates.api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    """Process API hash input."""
    api_hash = message.text.strip()
    data = await state.get_data()

    # Update driver with API credentials
    user_id = message.from_user.id
    try:
        # Get existing driver
        driver = await driver_store.get_driver(user_id)
        if not driver:
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            await state.clear()
            return

        # Update API credentials
        driver.api_id = data['api_id']
        driver.api_hash = api_hash
        
        # Save updated driver
        await driver_store.update_driver(driver)

        sent_msg = await message.answer("Endi telefon raqamingizni yuboring (mamlakat kodi bilan +99812345678):")
        await state.set_state(DriverStates.phone)
    except Exception as e:
        log.error(f"Error updating driver API credentials: {e}")
        sent_msg = await message.answer("❌ Xatolik yuz berdi, iltimos qaytadan urinib ko'ring yoki admin bilan bo'laning.")
        await state.clear()


def digits_only(raw: str) -> str:
    """Strip every non-digit so '1-2 3-4 5' → '12345'."""
    return re.sub(r"\D", "", raw)


@router.message(DriverStates.phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Process phone number input and send verification code."""
    try:
        phone = message.text.strip()
        user_id = message.from_user.id
        
        # Get driver data
        driver = await driver_store.get_driver(user_id)
        if not driver:
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Save driver's Telegram ID in state
        await state.update_data(driver_id=driver.tg_id)

        # Create new client with StringSession
        session = StringSession(driver.session_string) if driver.session_string else StringSession()
        client = TelegramClient(
            session,
            driver.api_id,
            driver.api_hash,
            device_model="Taxi Bot",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="uz",
            # Add encoding settings for better Unicode support
            request_retries=3,
            timeout=30,
            # Ensure proper text encoding
            use_ipv6=False
        )

        try:
            # Connect and send code
            await client.connect()
            sent_code = await client.send_code_request(phone)

            # Save phone, code hash, and session string in state
            await state.update_data(
                phone=phone,
                phone_code_hash=sent_code.phone_code_hash,
                session_string=session.save()  # Save the session string for later use
            )

            await message.answer("📩 Kod yuborildi. Tasdiqlash kodini kiriting (1-2-3-4-5 formatda):")
            await state.set_state(DriverStates.code)

        except Exception as e:
            await message.answer(
                "❌ Xatolik yuz berdi, iltimos qaytadan urinib ko'ring."
            )
            await state.clear()
        finally:
            await client.disconnect()

    except Exception as e:
        await message.answer(
            "❌ Xatolik yuz berdi, iltimos qaytadan urinib ko'ring."
        )
        await state.clear()


@router.message(DriverStates.code)
async def process_code(message: types.Message, state: FSMContext):
    """Process the verification code."""
    try:
        # Convert formatted code to digits only (e.g., "1-2-3-4-5" -> "12345")
        code = ''.join(filter(str.isdigit, message.text.strip()))
        
        # Get state data
        data = await state.get_data()
        driver_id = data.get('driver_id')
        phone = data.get('phone')
        phone_code_hash = data.get('phone_code_hash')
        session_string = data.get('session_string')
        
        if not all([driver_id, phone, phone_code_hash, session_string]):
            await message.answer("❌ Xatolik yuz berdi: Missing required data. Please try logging in again.")
            return

        # Get driver from storage
        driver = await driver_store.get_driver(driver_id)
        if not driver:
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Create client with the same session used for code request
        session = StringSession(session_string)
        client = TelegramClient(
            session,
            driver.api_id,
            driver.api_hash,
            device_model="Taxi Bot",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="uz",
            # Add encoding settings for better Unicode support
            request_retries=3,
            timeout=30,
            # Ensure proper text encoding
            use_ipv6=False
        )

        try:
            await client.connect()
            
            try:
                await client.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=phone_code_hash
                )
                
                # Save the session string
                driver.session_string = session.save()
                await driver_store.update_driver(driver)
                
                await message.answer("✅ Muvaffaqiyatli kirildi!", reply_markup=main_menu(False))
                await state.clear()
                
            except SessionPasswordNeededError:
                await message.answer("🔐 Iltimos, 2FA parolini kiriting:")
                await state.set_state(DriverStates.password)
                
            except Exception as e:
                error_message = str(e)
                if "phone code invalid" in error_message.lower():
                    await message.answer(
                        "❌ Noto'g'ri kod kiritildi. Iltimos, qaytadan urinib ko'ring.",
                        reply_markup=login_menu()
                    )
                else:
                    await message.answer(
                        "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                        reply_markup=login_menu()
                    )
                await state.clear()
                
        except Exception as e:
            await message.answer(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=login_menu()
            )
            await state.clear()
            
        finally:
            await client.disconnect()
            
    except Exception as e:
        await message.answer(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=login_menu()
        )
        await state.clear()


@router.message(DriverStates.password)
async def process_password(message: types.Message, state: FSMContext):
    """Process the 2FA password."""
    try:
        password = message.text.strip()
        
        # Get state data
        data = await state.get_data()
        driver_id = data.get('driver_id')
        session_string = data.get('session_string')
        
        if not all([driver_id, session_string]):
            await message.answer("❌ Xatolik yuz berdi: Missing required data. Please try logging in again.")
            return

        # Get driver
        driver = await driver_store.get_driver(driver_id)
        if not driver:
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Create client with the same session used for initial authentication
        session = StringSession(session_string)
        client = TelegramClient(
            session,
            driver.api_id,
            driver.api_hash,
            device_model="Taxi Bot",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="uz",
            # Add encoding settings for better Unicode support
            request_retries=3,
            timeout=30,
            # Ensure proper text encoding
            use_ipv6=False
        )

        try:
            await client.connect()
            
            # Try to sign in with password
            await client.sign_in(password=password)
            
            # Save the session string
            driver.session_string = session.save()
            driver.password = password  # Save password for future use
            await driver_store.update_driver(driver)
            
            await message.answer("✅ Muvaffaqiyatli kirildi!", reply_markup=main_menu(False))
            await state.clear()
            
        except Exception as e:
            error_message = str(e)
            if "password invalid" in error_message.lower():
                await message.answer(
                    "❌ Noto'g'ri parol kiritildi. Iltimos, qaytadan urinib ko'ring.",
                    reply_markup=login_menu()
                )
            else:
                await message.answer(
                    "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                    reply_markup=login_menu()
                )
            await state.clear()
            
        finally:
            await client.disconnect()
            
    except Exception as e:
        await message.answer(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=login_menu()
        )
        await state.clear()


@router.message(lambda msg: msg.text == "Guruhlar papkasi")
async def folder_entry(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_active_driver(user_id):
        return

    # Get driver from storage
    driver = await driver_store.get_driver(user_id)
    if not driver:
        await message.answer("❌ Xatolik yuz berdi: Driver not found")
        return

    # Check if user has a valid session
    if not driver.session_string:
        await message.answer(
            "❌ Avval tizimga kirishingiz kerak.",
            reply_markup=login_menu()
        )
        return

    await message.answer(
        "📂 Foydalanmoqchi bo'lgan jild(folder) nomini yozing.\n\n"
        "Agar Telegram'da hali bu nomli jild bo'lmasa, uni yaratib, "
        "barcha kerakli guruhlarni shu jildga qo'shing, so'ng nomini kiriting."
    )
    await state.set_state(FolderStates.waiting_name)


@router.message(FolderStates.waiting_name)
async def folder_save(message: types.Message, state: FSMContext):
    """Process folder name input and save it."""
    # Ensure proper text encoding for Unicode characters (including Cyrillic)
    import unicodedata
    folder_name = unicodedata.normalize('NFC', message.text.strip())
    user_id = message.from_user.id

    # Clear state first to prevent duplicate processing
    await state.clear()

    try:
        # Get driver from storage
        driver = await driver_store.get_driver(user_id)
        if not driver:
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Check if user has a valid session
        if not driver.session_string:
            await message.answer(
                "❌ Avval tizimga kirishingiz kerak.",
                reply_markup=login_menu()
            )
            return

        # Use cached client (StringSession-based)
        client = await get_cached_client(user_id)

        try:
            # Get all dialog filters (folders)
            result = await client(GetDialogFiltersRequest())
            filters = getattr(result, 'filters', [])

            # Find the folder by name with proper title extraction
            folder = None
            for f in filters:
                # Extract title text from TextWithEntities
                title_text = getattr(getattr(f, 'title', None), 'text', '').lower()
                
                # Normalize both titles for proper Unicode comparison
                import unicodedata
                normalized_title = unicodedata.normalize('NFC', title_text)
                normalized_folder = unicodedata.normalize('NFC', folder_name.lower())
                
                if normalized_title == normalized_folder:
                    folder = f
                    break

            if not folder:
                # Get folder names with proper encoding
                folder_names = []
                for f in filters:
                    title_text = getattr(getattr(f, 'title', None), 'text', '')
                    if title_text:
                        import unicodedata
                        normalized_title = unicodedata.normalize('NFC', title_text)
                        folder_names.append(normalized_title)
                
                sent_msg = await message.answer(
                    f"📂 '{folder_name}' nomli jild topilmadi.\n"
                    "1. Telegram'da jild mavjudligini tekshiring\n"
                    "2. Jild nomini to'g'ri kiritganingizni tekshiring\n"
                    "3. Guruhlarni ushbu jildga qo'shganingizni tekshiring\n"
                    f"Topilgan jildlar: {folder_names}"
                )
                await clean_up_chat(message, sent_msg)
                return

            # Get the peers included in this folder
            peers = getattr(folder, 'include_peers', [])
            if not peers:
                sent_msg = await message.answer("❌ Jild bo'sh. Iltimos, kamida 1 ta guruh qo'shing.")
                await clean_up_chat(message, sent_msg)
                return

            # Update driver with folder info
            driver.folder_id = folder.id
            driver.folder_title = folder_name
            await driver_store.update_driver(driver)

            sent_msg = await message.answer(
                f"✅ Jild '{folder_name}' saqlandi! ({len(peers)} ta guruh)\n"
                "Endi 'Xabar Yaratish' tugmasini bosishingiz mumkin.",
                reply_markup=main_menu(False)
            )
            await clean_up_chat(message, sent_msg)

        except Exception as e:
            error_message = str(e)
            if "key is not registered" in error_message.lower():
                await message.answer(
                    "❌ Avval tizimga kirishingiz kerak.",
                    reply_markup=login_menu()
                )
            else:
                await message.answer(
                    f"❌ Xatolik yuz berdi: {str(e)}\n"
                    "Iltimos, qaytadan urinib ko'ring yoki administrator bilan bog'laning."
                )
        finally:
            pass

    except Exception as e:
        await message.answer(
            f"❌ Xatolik yuz berdi: {str(e)}\n"
            "Iltimos, qaytadan urinib ko'ring yoki administrator bilan bog'laning."
        )


@router.message(lambda msg: msg.text == "Xabar Yaratish")
async def create_message(message: types.Message, state: FSMContext):
    """Handle message creation."""
    try:
        user_id = message.from_user.id
        log.info(f"Creating message for user {user_id}")
        
        # Get driver from storage
        driver = await driver_store.get_driver(user_id)
        if not driver:
            log.error(f"Driver not found for user {user_id}")
            await message.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Check if user has a valid session
        if not driver.session_string:
            await message.answer(
                "❌ Avval tizimga kirishingiz kerak.",
                reply_markup=login_menu()
            )
            return

        # Check if folder is selected
        if not driver.folder_title:
            await message.answer(
                "❌ Avval 'Guruhlar papkasi' orqali papkani tanlang!",
                reply_markup=main_menu(False)
            )
            return

        # Ask for message text
        await message.answer("📝 Xabar matnini kiriting:")
        await state.set_state(MessageStates.text)
    except Exception as e:
        log.error(f"Error in create_message: {str(e)}")
        await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")


@router.message(MessageStates.text)
async def process_message_text(message: types.Message, state: FSMContext):
    """Process message text and ask for interval."""
    try:
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        message_text = unicodedata.normalize('NFC', message.text)
        
        # Log the received text for debugging
        log.info(f"Received message text: {message_text[:100]}...")
        
        # Save message text in state
        await state.update_data(message_text=message_text)
        
        # Create inline keyboard for interval selection
        keyboard = InlineKeyboardBuilder()
        keyboard.add(
            InlineKeyboardButton(text="2 daqiqa", callback_data="interval:2"),
            InlineKeyboardButton(text="5 daqiqa", callback_data="interval:5"),
            InlineKeyboardButton(text="10 daqiqa", callback_data="interval:10"),
            InlineKeyboardButton(text="15 daqiqa", callback_data="interval:15")
        )
        keyboard.adjust(2)  # 2 buttons per row

        await message.answer(
            "⏱ Qaysi intervalda xabar yuborilsin?",
            reply_markup=keyboard.as_markup()
        )
        await state.set_state(MessageStates.interval)
    except Exception as e:
        log.error(f"Error in process_message_text: {str(e)}")
        await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")


@router.callback_query(lambda c: c.data.startswith("interval:"))
async def process_interval(callback: types.CallbackQuery, state: FSMContext):
    """Process interval selection and start sending messages."""
    try:
        # Only handle if in correct state
        current_state = await state.get_state()
        if current_state != MessageStates.interval.state:
            await callback.answer("❌ Noto'g'ri holat. Iltimos, xabar matnini kiriting.", show_alert=True)
            return

        # Get interval from callback data
        interval = int(callback.data.split(":")[1])
        user_id = callback.from_user.id
        
        # Get message text from state
        data = await state.get_data()
        message_text = data.get('message_text')
        
        if not message_text:
            await callback.answer("❌ Xatolik yuz berdi: Message text not found")
            return
            
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        message_text = unicodedata.normalize('NFC', message_text)
        
        # Log the message text being used for announcement
        log.info(f"Creating announcement with text: {message_text[:100]}...")
            
        # Get driver from storage
        driver = await driver_store.get_driver(user_id)
        if not driver:
            await callback.answer("❌ Xatolik yuz berdi: Driver not found")
            return

        # Create announcement data with required id
        announcement = AnnouncementData(
            id=int(time.time()),
            driver_id=user_id,
            text=message_text,
            interval_min=interval,
            folder_title=driver.folder_title,
            is_running=True
        )

        log.info(f"Creating announcement for user {user_id} with interval {interval} min")

        # Overwrite/add the announcement for this driver in the JSON
        await announcement_store.create_announcement(announcement)
        
        # Start announcement
        # Persist as running and initialize state
        await start_announcement(user_id)
        
        # Clear state
        await state.clear()
        
        # Send confirmation
        await callback.message.edit_text(
            f"✅ Xabar yuborish boshladi!\n"
            f"📝 Xabar: {message_text}\n"
            f"⏱ Interval: {interval} daqiqa\n"
            f"📁 Papka: {driver.folder_title}"
        )
        
        # Show main menu
        await callback.message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu(True)
        )
        
    except Exception as e:
        log.error(f"Error in process_interval: {str(e)}")
        await callback.answer(f"❌ Xatolik yuz berdi: {str(e)}")


@router.message(lambda msg: msg.text == "Stop")
async def stop_command(message: types.Message):
    """Handler for stopping an active announcement."""
    user_id = message.from_user.id

    if not await is_active_driver(user_id):
        sent_msg = await message.answer("❌ Ruxsat berilmagan. Administratordan hisobingizni faollashtirishni so'rang.")
        await clean_up_chat(message, sent_msg)
        return

    # Get driver from storage
    driver = await driver_store.get_driver(user_id)
    if not driver:
        await message.answer("❌ Xatolik yuz berdi: Driver not found")
        return

    # Check if user has a valid session
    if not driver.session_string:
        await message.answer(
            "❌ Avval tizimga kirishingiz kerak.",
            reply_markup=login_menu()
        )
        return

    try:
        # First stop any running announcements in the cache for this specific driver
        await stop_announcement(user_id)
        
        # Delete announcement from JSON store
        await announcement_store.delete_announcement(user_id)
        
        sent_msg = await message.answer("✅ Sizning e'lonlaringiz to'xtatildi va o'chirildi.", reply_markup=main_menu(False))
        await clean_up_chat(message, sent_msg)
    except Exception as e:
        sent_msg = await message.answer("❌ E'lonlarni to'xtatishda xatolik yuz berdi.", reply_markup=main_menu(False))
        await clean_up_chat(message, sent_msg)
        log.error(f"Error stopping announcements: {e}")


@router.message(lambda msg: msg.text == "Yangi xabar")
async def new_message_command(message: types.Message, state: FSMContext):
    """Handler for creating a new message while one is running."""
    user_id = message.from_user.id

    # Check if user is an active driver
    if not await is_active_driver(user_id):
        await message.answer("❌ Ruxsat berilmagan. Administratordan hisobingizni faollashtirishni so'rang.")
        return

    # Get driver from storage
    driver = await driver_store.get_driver(user_id)
    if not driver:
        await message.answer("❌ Xatolik yuz berdi: Driver not found")
        return

    # Check if user has a valid session
    if not driver.session_string:
        await message.answer(
            "❌ Avval tizimga kirishingiz kerak.",
            reply_markup=login_menu()
        )
        return

    try:
        log.info(f"Creating new message for driver {user_id}")
        
        # First stop any running announcements in the cache for this specific driver
        await stop_announcement(user_id)
        
        # Delete announcement from JSON store
        await announcement_store.delete_announcement(user_id)
        
        # Start new message flow
        await message.answer("Yangi e'lon matnini kiriting:")
        await state.set_state(MessageStates.text)
    except Exception as e:
        await message.answer("❌ Xatolik yuz berdi. Administrator bilan bog'laning.")
        log.error(f"Error creating new message: {e}")


@router.message(lambda msg: msg.text == "Delete session")
async def delete_session_command(message: types.Message):
    """Handler for deleting a session."""
    user_id = message.from_user.id

    # Check if user is an active driver
    if not await is_active_driver(user_id):
        await message.answer("❌ Ruxsat berilmagan. Administratordan hisobingizni faollashtirishni so'rang.")
        return

    log.info(f"Deleting session for driver {user_id}")
    
    # Stop any running announcements
    await stop_announcement(user_id)

    # Clear cached client and StringSession
    try:
        await cleanup_client(user_id)
    except Exception:
        pass

    driver = await driver_store.get_driver(user_id)
    if driver and driver.session_string:
        driver.session_string = None
        await driver_store.update_driver(driver)
        await message.answer("✅ Seans o'chirildi.", reply_markup=main_menu(False))
    else:
        await message.answer("❌ O'chiriladigan seans mavjud emas.", reply_markup=main_menu(False))



@router.message(Command("delete"))
async def delete_driver_command(message: types.Message):
    """Handler for admin command to delete a driver by Telegram ID."""
    user_id = message.from_user.id

    # Check if user is an admin
    if not await is_admin(user_id):
        await message.answer("❌ Bu buyruq faqat adminlar uchun.")
        return

    # Extract command arguments
    command_parts = message.text.split()
    if len(command_parts) != 2:
        await message.answer(
            "❌ Noto'g'ri format. To'g'ri format:\n"
            "/delete {telegram_id}\n\n"
            "Misol: /delete 123456789"
        )
        return

    try:
        target_tg_id = int(command_parts[1])
    except ValueError:
        await message.answer("❌ Telegram ID raqam bo'lishi kerak.")
        return

    # Prevent admin from deleting themselves
    if target_tg_id == user_id:
        await message.answer("❌ O'zingizni o'chira olmaysiz.")
        return

    # Find and delete the driver
    try:
        log.info(f"Admin {user_id} attempting to delete driver {target_tg_id}")
        
        driver = await driver_store.get_driver(target_tg_id)
        if not driver:
            await message.answer(f"❌ Telegram ID {target_tg_id} bo'lgan haydovchi topilmadi.")
            return
            
        # Store driver info for confirmation message
        driver_name = driver.full_name
        driver_phone = driver.phone
        
        # Stop any running announcements for this driver
        await stop_announcement(target_tg_id)
        
        # Delete session file if it exists
        session_file = os.path.join(SESSION_DIR, f"{target_tg_id}.session")
        if os.path.exists(session_file):
            os.remove(session_file)
        
        # Delete driver from storage
        await driver_store.delete_driver(target_tg_id)
        
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        normalized_driver_name = unicodedata.normalize('NFC', driver_name)
        
        await message.answer(
            f"✅ Haydovchi o'chirildi:\n"
            f"👤 Ism: {normalized_driver_name}\n"
            f"📱 Telefon: {driver_phone}\n"
            f"🆔 Telegram ID: {target_tg_id}\n"
            f"📁 Seans va barcha ma'lumotlar o'chirildi."
        )
        
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")
        log.error(f"Error deleting driver {target_tg_id}: {e}")


# --- Callback Query Handlers ---
@router.callback_query(lambda c: c.data.startswith("extend:"))
async def extend_driver(callback: types.CallbackQuery):
    """Handler for extending a driver's active period."""
    user_id = callback.from_user.id

    # Check if user is an admin
    if not await is_admin(user_id):
        await callback.answer("❌ Ruxsat berilmagan.")
        return

    # Get driver ID from callback data
    driver_id = int(callback.data.split(":")[1])

    # Extend driver's active period
    try:
        driver = await driver_store.get_driver(driver_id)
        if not driver:
            await callback.answer("❌ Haydovchi topilmadi.", show_alert=True)
            return

        # Extend by 30 days
        if driver.active_until < datetime.now():
            # If expired, extend from now
            driver.active_until = datetime.now() + timedelta(days=30)
        else:
            # If not expired, extend from current expiry
            driver.active_until += timedelta(days=30)

        # Ensure driver is active
        driver.is_active = True

        # Update driver in storage
        await driver_store.update_driver(driver)

        await callback.answer(f"✅ {driver.active_until.strftime('%Y-%m-%d')} sanasigacha uzaytirildi")

        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        driver_name = unicodedata.normalize('NFC', driver.full_name)
        
        # Update message with new info
        status = "✅ Faol" if driver.is_active else "❌ Faolmas"
        msg_text = (
            f"Haydovchi: {driver_name}\n"
            f"Telefon: {driver.phone}\n"
            f"Status: {status}\n"
            f"Faol: {driver.active_until.strftime('%Y-%m-%d')} gacha"
        )

        keyboard = driver_details_keyboard(driver.tg_id, driver.is_active)

        await callback.message.edit_text(msg_text, reply_markup=keyboard)

    except ValueError as e:
        await callback.answer("❌ Noto'g'ri ID formati.", show_alert=True)
        log.error(f"Invalid driver ID format in extend_driver: {e}")
    except Exception as e:
        await callback.answer(f"❌ Xatolik yuz berdi: {str(e)}", show_alert=True)
        log.error(f"Error in extend_driver: {e}")


@router.callback_query(lambda c: c.data.startswith("activate:") or c.data.startswith("deactivate:"))
async def toggle_driver_status(callback: types.CallbackQuery):
    """Handler for activating/deactivating a driver."""
    user_id = callback.from_user.id

    # Check if user is an admin
    if not await is_admin(user_id):
        await callback.answer("❌ Ruxsat berilmagan.")
        return

    # Get action and driver ID from callback data
    action, driver_id = callback.data.split(":")
    driver_id = int(driver_id)

    # Update driver status
    try:
        driver = await driver_store.get_driver(driver_id)
        if not driver:
            await callback.answer("❌ Haydovchi topilmadi.", show_alert=True)
            return

        if action == "activate":
            driver.is_active = True
            status_text = "faollashtirildi"
        else:
            driver.is_active = False
            status_text = "deaktivlashtirildi"

        # Update driver in storage
        await driver_store.update_driver(driver)

        await callback.answer(f"✅ Haydovchi {status_text}.")

        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        driver_name = unicodedata.normalize('NFC', driver.full_name)
        
        # Update message with new info
        status = "✅ Faol" if driver.is_active else "❌ Faolmas"
        msg_text = (
            f"Haydovchi: {driver_name}\n"
            f"Telefon: {driver.phone}\n"
            f"Status: {status}\n"
            f"Faol: {driver.active_until.strftime('%Y-%m-%d')} gacha"
        )

        keyboard = driver_details_keyboard(driver.tg_id, driver.is_active)

        await callback.message.edit_text(msg_text, reply_markup=keyboard)

    except ValueError as e:
        await callback.answer("❌ Noto'g'ri ID formati.", show_alert=True)
        log.error(f"Invalid driver ID format in toggle_driver_status: {e}")
    except Exception as e:
        await callback.answer(f"❌ Xatolik yuz berdi: {str(e)}", show_alert=True)
        log.error(f"Error in toggle_driver_status: {e}")


class ChangeIDStates(StatesGroup):
    waiting_new_id = State()


@router.callback_query(F.data.startswith("change:"))
async def change_id_start(call: types.CallbackQuery, state: FSMContext):
    """Start changing driver's Telegram ID."""
    try:
        tg_id = int(call.data.split(":")[1])
        await state.update_data(tg_id=tg_id)

        # Remove keyboard and send request
        await call.message.edit_reply_markup()
        sent_msg = await call.message.answer("✅ Yangi Telegram ID ni kiriting (faqat raqam):")
        await state.update_data(last_bot_message=sent_msg.message_id)

        await state.set_state(ChangeIDStates.waiting_new_id)
        await call.answer()
    except ValueError as e:
        await call.answer("❌ Noto'g'ri ID formati.", show_alert=True)
        log.error(f"Invalid driver ID format in change_id_start: {e}")
    except Exception as e:
        await call.answer("❌ Xatolik yuz berdi.", show_alert=True)
        log.error(f"Error in change_id_start: {e}")


@router.message(ChangeIDStates.waiting_new_id)
async def change_id_finish(message: types.Message, state: FSMContext):
    """Finish changing driver's Telegram ID."""
    if not message.text.isdigit():
        sent_msg = await message.answer("❌ ID raqam bo'lishi kerak.")
        return

    new_tg_id = int(message.text.strip())
    data = await state.get_data()
    old_tg_id = data.get('tg_id')
    
    if not old_tg_id:
        sent_msg = await message.answer("❌ Eski ID topilmadi. Qaytadan urinib ko'ring.")
        await state.clear()
        return

    # Check if new ID already exists
    try:
        existing_driver = await driver_store.get_driver(new_tg_id)
        if existing_driver:
            sent_msg = await message.answer("❌ Bu ID allaqachon mavjud.")
            await state.clear()
            return

        # Update driver's Telegram ID
        driver = await driver_store.get_driver(old_tg_id)
        if not driver:
            sent_msg = await message.answer("❌ Haydovchi topilmadi.")
            await state.clear()
            return

        driver.tg_id = new_tg_id
        await driver_store.update_driver(driver)

        sent_msg = await message.answer(
            f"✅ Haydovchi ID si yangilandi!\n"
            f"Eski ID: {old_tg_id}\n"
            f"Yangi ID: {new_tg_id}"
        )
    except Exception as e:
        sent_msg = await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")
        log.error(f"Error in change_id_finish: {e}")
    finally:
        await state.clear()


@router.callback_query(lambda c: c.data.startswith("page_:"))
async def paginate_drivers(callback: types.CallbackQuery):
    """Handler for paginating through drivers list."""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ Access denied.")
        return

    page = int(callback.data.split(":")[1])

    # Get all drivers
    drivers = await driver_store.get_all_drivers()
    
    # Filter active and inactive drivers
    active_drivers = [d for d in drivers if d.is_active]
    inactive_drivers = [d for d in drivers if not d.is_active]
    
    total_count = len(drivers)
    active_count = len(active_drivers)
    inactive_count = len(inactive_drivers)
    
    # Calculate pagination
    offset = (page - 1) * 10
    page_drivers = drivers[offset:offset + 10]
    total_pages = (total_count + 9) // 10

    # Format message
    msg_text = f"Drivers: {active_count} active / {inactive_count} inactive / {total_count} total\n\n"
    for i, driver in enumerate(page_drivers, offset + 1):
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        driver_name = unicodedata.normalize('NFC', driver.full_name)
        status = "✅ Active" if driver.is_active else "❌ Inactive"
        msg_text += f"{i}. {driver_name} - {driver.phone}\n"
        msg_text += f"   {status} until {driver.active_until.strftime('%Y-%m-%d')}\n\n"

    # Update the message with new page
    await callback.message.edit_text(msg_text, reply_markup=pagination_keyboard(page, total_pages))
    await callback.answer()


# Add handler for unhandled messages
@router.message()
async def handle_unhandled(message: types.Message):
    """Handle any messages that weren't handled by other handlers."""
    user_id = message.from_user.id
    
    # Don't process admin messages
    if user_id in settings['ADMINS']:
        return
        
    # Delete the message if it's not a valid command
    if message.text and message.text not in VALID_COMMANDS:
        try:
            await message.delete()
        except Exception as e:
            log.debug(f"Could not delete unhandled message: {e}")


# Add cleanup on bot shutdown
async def on_shutdown():
    """Cleanup on bot shutdown."""
    for user_id in list(_clients.keys()):
        await disconnect_client(user_id)


# --- Main function ---
async def main():
    log.info("Starting bot...")
    await ensure_scheduler_running()
    log.info("Scheduler started successfully")
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
