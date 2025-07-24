import uuid
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
import asyncio
import threading
import time
import os # Import os for file operations
import io # For in-memory file handling

from msspeech import MSSpeech, MSSpeechError

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- GLOBAL BOT CONFIGURATION ---
# IMPORTANT: Replace with your NEW Bot's Token and Webhook URL
TOKEN = "7999849691:AAHmRwZ_Ef1I64SZqotZND6v7LrE-fFwRD0"  # <-- Your NEW bot token (from Bot 1 as it's the primary)
ADMIN_ID = 5978150981  # <-- Admin Telegram ID
# The webhook URL should be the URL of your deployed merged bot
WEBHOOK_URL = "excellent-davida-wwmahe-45f63d30.koyeb.app/" # Using Bot 1's webhook as example

REQUIRED_CHANNEL = "@transcriber_bot_news_channel"  # <-- required subscription channel

ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # From Bot 2

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- MONGODB CONFIGURATION ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db" # Keeping the same DB name as it seems shared

mongo_client: MongoClient = None
db = None
users_collection = None # Unified users collection
tts_users_collection = None # For TTS specific settings (voice, pitch, rate)
processing_stats_collection = None # For TTS processing stats

# --- In-memory caches for TTS bot (from Bot 1) ---
local_user_data = {}            # { user_id: { "_id": "...", "last_active": "...", "tts_conversion_count": N, "transcription_language": "en" } }
_tts_voice_cache = {}           # { user_id: voice_name }
_tts_pitch_cache = {}           # { user_id: pitch_value }
_tts_rate_cache = {}            # { user_id: rate_value }

# --- User state for Text-to-Speech input mode (from Bot 1) ---
user_tts_mode = {}              # { user_id: voice_name (e.g. "en-US-AriaNeural") or None }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }

# --- User state for Transcription bot (from Bot 2) ---
user_transcription_language_selection_mode = {} # { chat_id: True/False } (to indicate user is choosing lang)
admin_broadcast_state = {}      # { chat_id: True/False } (for admin broadcast)

# --- Statistics counters (in-memory for quick access) (from Bot 1) ---
total_tts_conversions = 0 # This will be tracked in DB too
bot_start_time = datetime.now()

# Admin uptime message storage (from Bot 1)
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

# Admin state for broadcast (from Bot 1, combined with Bot 2's for clarity)
admin_state = {} # Renaming from admin_broadcast_state in Bot 2 to avoid confusion with Bot 1's admin_state

# Placeholder for keeping track of typing threads
processing_message_ids = {}

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O   H E L P E R   F U N C T I O N S (Unified)
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    """
    Connect to MongoDB at startup, set up collections and indexes.
    Also, load all user data into in-memory caches.
    """
    global mongo_client, db
    global users_collection, tts_users_collection, processing_stats_collection
    global local_user_data, _tts_voice_cache, _tts_pitch_cache, _tts_rate_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster') # Check connection
        db = mongo_client[DB_NAME]

        # Unified users collection for general user data and transcription language
        users_collection = db["users"]
        # Specific collection for TTS voice settings (pitch, rate can be here too)
        tts_users_collection = db["tts_users"]
        # Collection for TTS processing stats
        processing_stats_collection = db["tts_processing_stats"]

        # Create indexes (if not already created)
        users_collection.create_index([("last_active", ASCENDING)])
        users_collection.create_index([("chat_id", ASCENDING)], unique=True) # Ensure unique chat_id for transcription
        tts_users_collection.create_index([("_id", ASCENDING)])
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])

        logging.info("Connected to MongoDB and indexes created. Loading data to memory...")

        # --- Load all user data into in-memory caches on startup ---
        for user_doc in users_collection.find({}):
            local_user_data[str(user_doc["_id"])] = user_doc
        logging.info(f"Loaded {len(local_user_data)} user documents into local_user_data.")

        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[str(tts_user["_id"])] = tts_user.get("voice", "so-SO-MuuseNeural") # Default Somali voice from Bot 1
            _tts_pitch_cache[str(tts_user["_id"])] = tts_user.get("pitch", 0)
            _tts_rate_cache[str(tts_user["_id"])] = tts_user.get("rate", 0)
        logging.info(f"Loaded {len(_tts_voice_cache)} TTS voice, pitch, and rate settings.")

        logging.info("All essential user data loaded into in-memory caches.")

    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1) # Exit if cannot connect to DB
    except Exception as e:
        logging.error(f"Error during MongoDB connection or initial data load: {e}")
        exit(1) # Exit on other critical errors

def update_user_activity_db(user_id: int):
    """
    Update user.last_active = now() in local_user_data cache and then in MongoDB.
    This also handles initial creation for new users.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "chat_id": user_id, # For transcription bot's usage
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "transcription_language": "en" # Default transcription language
        }
        # Immediately insert new user to DB
        try:
            users_collection.insert_one(local_user_data[user_id_str])
            logging.info(f"New user {user_id_str} inserted into MongoDB.")
        except Exception as e:
            logging.error(f"Error inserting new user {user_id_str} into DB: {e}")
    else:
        local_user_data[user_id_str]["last_active"] = now_iso
        # Persist to MongoDB
        try:
            users_collection.update_one(
                {"_id": user_id_str},
                {"$set": {"last_active": now_iso}},
                upsert=True
            )
        except Exception as e:
            logging.error(f"Error updating user activity for {user_id_str} in DB: {e}")


def get_user_data_db(user_id: str) -> dict | None:
    """
    Return user document from local_user_data cache. If not found, try MongoDB
    and load into cache.
    """
    if user_id in local_user_data:
        return local_user_data[user_id]
    try:
        doc = users_collection.find_one({"_id": user_id})
        if doc:
            local_user_data[user_id] = doc # Load into cache
        return doc
    except Exception as e:
        logging.error(f"Error fetching user data for {user_id} from DB: {e}")
        return None

def increment_tts_conversion_count_db(user_id: str):
    """
    Increment tts_conversion_count in local_user_data cache and then in MongoDB,
    also update last_active.
    """
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id not in local_user_data:
        # This should ideally not happen if update_user_activity_db is called first
        local_user_data[user_id] = {
            "_id": user_id,
            "chat_id": int(user_id),
            "last_active": now_iso,
            "tts_conversion_count": 1,
            "transcription_language": "en"
        }
        try:
            users_collection.insert_one(local_user_data[user_id])
        except Exception as e:
            logging.error(f"Error inserting new user {user_id} during TTS count increment: {e}")
    else:
        local_user_data[user_id]["tts_conversion_count"] = local_user_data[user_id].get("tts_conversion_count", 0) + 1
        local_user_data[user_id]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id},
            {
                "$inc": {"tts_conversion_count": 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing TTS conversion count for {user_id} in DB: {e}")


def get_tts_user_voice_db(user_id: str) -> str:
    """
    Return TTS voice from cache (default "so-SO-MuuseNeural").
    """
    return _tts_voice_cache.get(user_id, "so-SO-MuuseNeural")

def set_tts_user_voice_db(user_id: str, voice: str):
    """
    Save TTS voice in DB and update cache.
    """
    _tts_voice_cache[user_id] = voice # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"voice": voice}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS voice for {user_id} in DB: {e}")

def get_tts_user_pitch_db(user_id: str) -> int:
    """
    Return TTS pitch from cache (default 0).
    """
    return _tts_pitch_cache.get(user_id, 0)

def set_tts_user_pitch_db(user_id: str, pitch: int):
    """
    Save TTS pitch in DB and update cache.
    """
    _tts_pitch_cache[user_id] = pitch # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"pitch": pitch}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS pitch for {user_id} in DB: {e}")

def get_tts_user_rate_db(user_id: str) -> int:
    """
    Return TTS rate from cache (default 0).
    """
    return _tts_rate_cache.get(user_id, 0)

def set_tts_user_rate_db(user_id: str, rate: int):
    """
    Save TTS rate in DB and update cache.
    """
    _tts_rate_cache[user_id] = rate # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"rate": rate}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS rate for {user_id} in DB: {e}")

def get_user_transcription_language_db(user_id: str) -> str:
    """
    Return transcription language from local_user_data cache (default "en").
    """
    user_doc = local_user_data.get(user_id)
    return user_doc.get("transcription_language", "en") if user_doc else "en"

def set_user_transcription_language_db(user_id: str, lang_code: str):
    """
    Save transcription language in DB and update cache.
    """
    # Update in-memory cache
    if user_id not in local_user_data:
        local_user_data[user_id] = {
            "_id": user_id,
            "chat_id": int(user_id),
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "transcription_language": lang_code
        }
        try:
            users_collection.insert_one(local_user_data[user_id])
        except Exception as e:
            logging.error(f"Error inserting new user {user_id} during lang set: {e}")
    else:
        local_user_data[user_id]["transcription_language"] = lang_code

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id},
            {"$set": {"transcription_language": lang_code}},
            upsert=True # Upsert ensures it's created if not exists
        )
    except Exception as e:
        logging.error(f"Error setting transcription language for {user_id} in DB: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   U T I L I T I E S   (keep typing, keep recording, update uptime)
# ────────────────────────────────────────────────────────────────────────────────

def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

def update_uptime_message(chat_id, message_id):
    """
    Live-update the admin uptime message every second.
    """
    while True:
        try:
            elapsed = datetime.now() - bot_start_time
            total_seconds = int(elapsed.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)

            uptime_text = (
                f"**Bot Uptime:**\n"
                f"{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds"
            )

            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=uptime_text,
                parse_mode="Markdown"
            )
            time.sleep(1)
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

# ────────────────────────────────────────────────────────────────────────────────
#   S U B S C R I P T I O N   C H E C K
# ────────────────────────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    """
    If REQUIRED_CHANNEL is set, verify user is a member.
    """
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id: int):
    """
    Prompt user to join REQUIRED_CHANNEL.
    """
    # Only send subscription message if it's a private chat
    if bot.get_chat(chat_id).type == 'private':
        if not REQUIRED_CHANNEL:
            return
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                "Click here to join the channel",
                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
            )
        )
        bot.send_message(
            chat_id,
            """
Looks like you're not a member of our channel yet! To use the bot, please join:
➡️ [Transcriber Bot News Channel](https://t.me/transcriber_bot_news_channel)

Once you've joined, send /start again to unlock the bot's features.
""",
            reply_markup=markup,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   H A N D L E R S (Unified)
# ────────────────────────────────────────────────────────────────────────────────

# --- Transcription Bot Languages (from Bot 2) ---
LANGUAGES = {
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaycan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇭": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi"
}

def build_transcription_language_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    row = []
    for i, name in enumerate(LANGUAGES.keys(), 1):
        row.append(types.KeyboardButton(name))
        if i % 3 == 0:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    markup.add(types.KeyboardButton("Back to Main Menu")) # Added to easily go back
    return markup

def build_admin_keyboard_merged():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False) # Not one_time_keyboard
    markup.add(types.KeyboardButton("Send Broadcast"), types.KeyboardButton("Total Users"), types.KeyboardButton("/status"))
    markup.add(types.KeyboardButton("Show All Users (Debug)"))
    return markup

def build_main_menu_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🗣️ Text-to-Speech", callback_data="main_tts"),
        InlineKeyboardButton("🎙️ Transcribe Media", callback_data="main_transcribe")
    )
    markup.add(
        InlineKeyboardButton("❓ Help", callback_data="main_help"),
        InlineKeyboardButton("🔒 Privacy", callback_data="main_privacy")
    )
    return markup


@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

    # Ensure user is in local_user_data and DB
    update_user_activity_db(message.from_user.id) # This handles upserting new users

    # Check subscription immediately on /start for all users except admin in private chat
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure TTS and Transcription modes are OFF on /start
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off

    if message.from_user.id == ADMIN_ID:
        # Admin menu with uptime
        sent_message = bot.send_message(
            message.chat.id,
            "👋 Welcome, Admin! Admin Panel and Uptime (updating live)...",
            reply_markup=build_admin_keyboard_merged()
        )
        with admin_uptime_lock:
            # Check if an uptime thread for this admin is already running and active
            if (
                admin_uptime_message.get(ADMIN_ID)
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                # If existing thread is alive, update its message_id if it's different
                if admin_uptime_message[ADMIN_ID]['message_id'] != sent_message.message_id:
                    admin_uptime_message[ADMIN_ID]['message_id'] = sent_message.message_id
            else:
                # Start new uptime thread if none exists or is dead
                admin_uptime_message[ADMIN_ID] = {
                    'message_id': sent_message.message_id,
                    'chat_id': message.chat.id
                }
                uptime_thread = threading.Thread(
                    target=update_uptime_message,
                    args=(message.chat.id, sent_message.message_id)
                )
                uptime_thread.daemon = True
                uptime_thread.start()
                admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread
    else:
        # User welcome message with main menu options
        welcome_message = (
            f"👋 Hey there, {user_first_name}! I'm your versatile bot for all things audio! 🔊\n\n"
            "I can convert your *text into realistic AI voices* (Text-to-Speech) and also *transcribe your audio and video files into text* (Media Transcriber).\n\n"
            "✨ *What would you like to do?* ✨"
        )
        bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=build_main_menu_keyboard(),
            parse_mode="Markdown"
        )


@bot.callback_query_handler(func=lambda c: c.data == "main_tts")
def main_menu_tts(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure other modes are off
    user_transcription_language_selection_mode[call.message.chat.id] = False
    admin_broadcast_state[call.message.chat.id] = False # Just in case

    text_to_send = (
        "🗣️ *Text-to-Speech Mode Activated!* \n\n"
        "To get started, simply send me any text, and I'll transform it into an audio clip.\n\n"
        "You can also customize the voice before sending your text:\n"
        "• Use /voice to **choose your preferred language and voice**.\n"
        "• Experiment with /pitch to **adjust the voice's tone**.\n"
        "• Tweak /rate to **change the speaking speed**.\n\n"
        "Send your text now! Or use the commands above to customize."
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text_to_send,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, "Text-to-Speech mode active!")


@bot.callback_query_handler(func=lambda c: c.data == "main_transcribe")
def main_menu_transcribe(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure other modes are off
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    admin_broadcast_state[call.message.chat.id] = False # Just in case

    current_lang_code = get_user_transcription_language_db(uid)
    current_lang_name = next((name for name, code in LANGUAGES.items() if code == current_lang_code), "English 🇬🇧")

    text_to_send = (
        "🎙️ *Media Transcriber Mode Activated!* \n\n"
        f"Your current transcription language is: *{current_lang_name}*.\n\n"
        "Please send your voice message, audio file, or video note, and I’ll transcribe it for you with precision.\n\n"
        "📁 Supported file size: Up to 20MB\n"
        "Want to change the transcription language? Use the button below 👇"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Change Transcription Language", callback_data="transcribe_change_lang"),
        InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")
    )
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text_to_send,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, "Media Transcriber mode active!")


@bot.callback_query_handler(func=lambda c: c.data == "transcribe_change_lang")
def on_transcribe_change_lang(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_transcription_language_selection_mode[call.message.chat.id] = True # Set state for language selection
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose your Media (Voice, Audio, Video) file language for transcription using the buttons below:",
        reply_markup=build_transcription_language_keyboard()
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "back_to_main_menu")
def back_to_main_menu_callback(call):
    user_id_str = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.from_user.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure all specific modes are off
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_transcription_language_selection_mode[call.message.chat.id] = False
    admin_state[call.from_user.id] = None # Reset admin states if any

    welcome_message = (
        f"👋 Welcome back! What would you like to do? 🔊\n\n"
        "I can convert your *text into realistic AI voices* (Text-to-Speech) and also *transcribe your audio and video files into text* (Media Transcriber).\n\n"
        "✨ *Choose an option:* ✨"
    )
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=welcome_message,
        reply_markup=build_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=['help'])
@bot.callback_query_handler(func=lambda c: c.data == "main_help")
def help_handler(message_or_call):
    is_callback = isinstance(message_or_call, types.CallbackQuery)
    message = message_or_call.message if is_callback else message_or_call
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        if is_callback:
            bot.answer_callback_query(message_or_call.id)
        return

    # Ensure TTS and Transcription modes are OFF on /help
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off
    admin_state[message.from_user.id] = None # Reset admin states if any

    help_text = (
        """
📚 *How to Use This Bot*

This bot offers two main functionalities:

---
**1. 🗣️ Text-to-Speech (TTS)**
Ready to turn your text into speech? Here's how:
* **Choose a Voice:** Use the /voice command to select from many languages and voices.
* **Send Your Text:** After choosing a voice, send any text message. The bot will reply with an audio clip.
* **Fine-Tune Voice:**
    * Use /pitch to **adjust the tone** (higher or lower).
    * Use /rate to **change the speaking speed** (faster or slower).

---
**2. 🎙️ Media Transcriber**
Want to convert audio/video to text?
* **Set Language:** Use the /language command or "Change Transcription Language" button (from main menu then Transcribe Media) to choose the language of your media file.
* **Send Media:** Send a voice message, audio file, or video file (up to 20MB). The bot will transcribe it for you.

---
If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy creating and transcribing! ✨
"""
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))

    if is_callback:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=help_text,
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(message_or_call.id)
    else:
        bot.send_message(message.chat.id, help_text, parse_mode="Markdown", reply_markup=markup)


@bot.message_handler(commands=['privacy'])
@bot.callback_query_handler(func=lambda c: c.data == "main_privacy")
def privacy_notice_handler(message_or_call):
    is_callback = isinstance(message_or_call, types.CallbackQuery)
    message = message_or_call.message if is_callback else message_or_call
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        if is_callback:
            bot.answer_callback_query(message_or_call.id)
        return

    # Ensure TTS and Transcription modes are OFF on /privacy
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off
    admin_state[message.from_user.id] = None # Reset admin states if any

    privacy_text = (
        """
🔐 *Privacy Notice: Your Data & This Bot*

Your privacy is incredibly important to us. This notice explains exactly how your data is handled in real-time when you use this bot.

1.  **Data We Process & Its Lifecycle:**
    * **Text for Speech Synthesis:** When you send text to be converted into speech, it's processed immediately to generate the audio. Crucially, this text is **not stored** on our servers after processing. The generated audio file is also temporary and is deleted right after it's sent to you.
    * **Media for Transcription:** When you send audio or video for transcription, the file is temporarily uploaded to AssemblyAI for processing and then the transcript is returned. We **do not store** your media files or the full transcripts on our servers after the transcription is delivered.
    * **User IDs, TTS Voices, and Activity Data:** We store your Telegram User ID and your personal preferences (your chosen TTS voice, as well as your pitch and rate settings, and your preferred transcription language) in our MongoDB database. We also log basic activity like your "last active" timestamp and a count of your TTS conversions. This helps us remember your settings for a consistent experience and allows us to gather anonymous, aggregated statistics to understand overall bot usage and improve our services. This data is also cached in memory for fast access during bot operation and is regularly updated in MongoDB.

2.  **How Your Data is Used:**
    * **Core Service Delivery:** To perform the bot's primary functions: converting your text into speech and transcribing your media into text.
    * **Service Improvement:** To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total TTS conversions).
    * **Personalization:** To maintain your preferred voice settings and characteristics across your sessions for TTS, and your chosen transcription language.

3.  **Data Sharing Policy:**
    * We have a strict **no-sharing policy**. We **do not share** your personal data or text input with any third parties for marketing or any other purposes.
    * Text-to-speech functionality is powered by the Microsoft Cognitive Services Speech API. Media transcription is powered by AssemblyAI. While your input (text or media) is sent to these models for processing, we ensure that your data is **not stored by us** after it has been processed by these external services. Their own privacy policies govern how they handle the data during the conversion process.

4.  **Data Retention:**
    * **Text input, generated audio files, and media files for transcription:** These are **deleted immediately** after processing and delivery.
    * **User IDs and preferences:** This data is stored in MongoDB to support your settings and for anonymous usage statistics. This data is also cached in memory for performance. If you wish to have your stored preferences removed, please contact the bot administrator.

By using this bot, you confirm that you understand and agree to the data practices outlined in this Privacy Notice.

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))

    if is_callback:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=privacy_text,
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(message_or_call.id)
    else:
        bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown", reply_markup=markup)


@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure TTS and Transcription modes are OFF on /status
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_transcription_language_selection_mode[message.chat.id] = False
    admin_state[message.from_user.id] = None # Reset admin states if any

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active today
    today_iso = datetime.now().date().isoformat()
    active_today_count = sum(1 for user_doc in local_user_data.values() if user_doc.get("last_active", "").startswith(today_iso))

    # Total registered users from local_user_data
    total_registered_users = len(local_user_data)

    # Processing stats for TTS
    try:
        total_tts_conversions_db = processing_stats_collection.count_documents({"type": "tts"})

        pipeline = [
            {"$match": {"type": "tts"}}, # Only sum processing time for TTS
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result = list(processing_stats_collection.aggregate(pipeline))
        total_proc_seconds = agg_result[0]["total_time"] if agg_result else 0
    except Exception as e:
        logging.error(f"Error fetching TTS processing stats from DB: {e}")
        total_tts_conversions_db = 0
        total_proc_seconds = 0

    proc_hours = int(total_proc_seconds) // 3600
    proc_minutes = (int(total_proc_seconds) % 3600) // 60
    proc_seconds = int(total_proc_seconds) % 60

    # No specific transcription stats are logged in Bot 2's DB
    # If you want to add them, you'd need to modify the transcription handler to log to processing_stats_collection as well.

    text = (
        "📊 *Bot Statistics*\n\n"
        "🟢 *Bot Status: Online*\n"
        f"⏱️ The bot has been running for: *{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds*\n\n"
        "👥 *User Statistics*\n"
        f"▫️ Total Active Users Today: *{active_today_count}*\n"
        f"▫️ Total Registered Users: *{total_registered_users}*\n\n"
        "⚙️ *Processing Statistics (Text-to-Speech)*\n"
        f"▫️ Total Text-to-Speech Conversions: *{total_tts_conversions_db}*\n"
        f"⏱️ Total TTS Processing Time: *{proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds*\n\n"
        "---"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=build_main_menu_keyboard())


@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users_admin(message): # Renamed to avoid conflict
    total_registered = users_collection.count_documents({}) # Get from DB for accuracy
    bot.send_message(message.chat.id, f"Total registered users (from database): {total_registered}", reply_markup=build_admin_keyboard_merged())


@bot.message_handler(func=lambda m: m.text == "Show All Users (Debug)" and m.from_user.id == ADMIN_ID)
def show_all_users_debug(message):
    if str(message.from_user.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "You are not authorized to use this command.")
        return

    users_info = ""
    count = 0
    try:
        for user_doc in users_collection.find({}):
            user_id = user_doc.get("_id", "N/A")
            last_active = user_doc.get("last_active", "N/A")
            tts_count = user_doc.get("tts_conversion_count", 0)
            trans_lang = user_doc.get("transcription_language", "N/A")
            users_info += f"ID: `{user_id}`, Active: {last_active}, TTS Count: {tts_count}, Trans Lang: {trans_lang}\n"
            count += 1
            if len(users_info) > 3500: # Telegram message limit is 4096 characters
                bot.send_message(message.chat.id, f"```{users_info}```", parse_mode="Markdown")
                users_info = "" # Reset for next batch
                time.sleep(0.5) # Avoid flood limits
        if users_info: # Send any remaining info
            bot.send_message(message.chat.id, f"```{users_info}```", parse_mode="Markdown")
        bot.send_message(message.chat.id, f"Total users retrieved: {count}", reply_markup=build_admin_keyboard_merged())
    except Exception as e:
        logging.error(f"Error fetching all users for admin: {e}")
        bot.send_message(message.chat.id, f"Error retrieving user list: {e}", reply_markup=build_admin_keyboard_merged())


@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_prompt(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast_message'
    bot.send_message(message.chat.id, "Send the broadcast message now (text, photo, video, audio, document). To cancel, type /cancel_broadcast", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(commands=['cancel_broadcast'], func=lambda message: message.chat.id == ADMIN_ID and admin_state.get(message.chat.id) == 'awaiting_broadcast_message')
def cancel_broadcast(message):
    admin_state[message.chat.id] = None # Exit broadcast state
    bot.send_message(
        message.chat.id,
        "Broadcast cancelled. What else, Admin?",
        reply_markup=build_admin_keyboard_merged()
    )


@bot.message_handler(content_types=['text', 'photo', 'video', 'audio', 'document', 'voice'],
                     func=lambda message: message.chat.id == ADMIN_ID and admin_state.get(message.chat.id) == 'awaiting_broadcast_message')
def broadcast_message(message):
    admin_state[message.chat.id] = None # Exit broadcast state after receiving the message

    bot.send_message(message.chat.id, "Broadcasting your message now...")

    all_users_chat_ids_from_db = users_collection.distinct("chat_id")

    success = 0
    fail = 0

    for user_chat_id in all_users_chat_ids_from_db:
        if user_chat_id == ADMIN_ID: # Don't send broadcast to admin themselves
            continue
        try:
            bot.copy_message(user_chat_id, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {user_chat_id}: {e}")
            fail += 1
        time.sleep(0.05) # Small delay to avoid hitting Telegram's flood limits

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}",
        reply_markup=build_admin_keyboard_merged()
    )

# --- Transcription Language Selection (from Bot 2) ---
@bot.message_handler(commands=['language'])
def send_language_prompt_transcription(message):
    chat_id = message.chat.id
    user_id_str = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure other modes are off
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    admin_state[message.from_user.id] = None # Reset admin states if any

    user_transcription_language_selection_mode[chat_id] = True
    bot.send_message(
        chat_id,
        "Choose your Media (Voice, Audio, Video) file language for transcription using the buttons below:",
        reply_markup=build_transcription_language_keyboard()
    )


@bot.message_handler(func=lambda msg: msg.text in LANGUAGES or msg.text == "Back to Main Menu")
def save_user_language_transcription(message):
    chat_id = message.chat.id
    user_id_str = str(chat_id)

    if message.text == "Back to Main Menu":
        user_transcription_language_selection_mode[chat_id] = False
        start_handler(message) # Re-call start to show main menu
        return

    # Only save language if not admin or if admin chose a language (though admin will typically use admin buttons)
    if chat_id != ADMIN_ID:
        if user_transcription_language_selection_mode.get(chat_id, False):
            lang_code = LANGUAGES[message.text]
            set_user_transcription_language_db(user_id_str, lang_code)
            user_transcription_language_selection_mode[chat_id] = False # Exit language selection mode

            bot.send_message(
                chat_id,
                f"✅ Transcription Language Set: *{message.text}*\n\n"
                "🎙️ Please send your voice message, audio file, or video note, and I’ll transcribe it for you with precision.\n\n"
                "📁 Supported file size: Up to 20MB\n\n"
                "📞 Need text to audio Bot for free use: @txt_2_voice_Bot",
                parse_mode="Markdown",
                reply_markup=build_main_menu_keyboard() # Show main menu options after selection
            )
        else:
            bot.send_message(chat_id, "Please use the /language command or 'Change Transcription Language' button to select a language.", reply_markup=build_main_menu_keyboard())
    else:
        # If admin accidentally taps a language, redirect to admin menu
        bot.send_message(
            chat_id,
            "Admin, please use the admin options.",
            reply_markup=build_admin_keyboard_merged()
        )

# ────────────────────────────────────────────────────────────────────────────────
#   T T S   F U N C T I O N S (from Bot 1)
# ────────────────────────────────────────────────────────────────────────────────

TTS_VOICES_BY_LANGUAGE = {
    "Arabic": [
    "ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural",
    "ar-BH-AliNeural", "ar-BH-LailaNeural",
    "ar-EG-SalmaNeural", "ar-EG-ShakirNeural",
    "ar-IQ-BasselNeural", "ar-IQ-RanaNeural",
    "ar-JO-SanaNeural", "ar-JO-TaimNeural",
    "ar-KW-FahedNeural", "ar-KW-NouraNeural",
    "ar-LB-LaylaNeural", "ar-LB-RamiNeural",
    "ar-LY-ImanNeural", "ar-LY-OmarNeural",
    "ar-MA-JamalNeural", "ar-MA-MounaNeural",
    "ar-OM-AbdullahNeural", "ar-OM-AyshaNeural",
    "ar-QA-AmalNeural", "ar-QA-MoazNeural",
    "ar-SA-HamedNeural", "ar-SA-ZariyahNeural",
    "ar-SY-AmanyNeural", "ar-SY-LaithNeural",
    "ar-TN-HediNeural", "ar-TN-ReemNeural",
    "ar-AE-FatimaNeural", "ar-AE-HamdanNeural",
    "ar-YE-MaryamNeural", "ar-YE-SalehNeural"
],

"English": [
    "en-AU-NatashaNeural", "en-AU-WilliamNeural",
    "en-CA-ClaraNeural", "en-CA-LiamNeural",
    "en-HK-SamNeural", "en-HK-YanNeural",
    "en-IN-NeerjaNeural", "en-IN-PrabhatNeural",
    "en-IE-ConnorNeural", "en-IE-EmilyNeural",
    "en-KE-AsiliaNeural", "en-KE-ChilembaNeural",
    "en-NZ-MitchellNeural", "en-NZ-MollyNeural",
    "en-NG-AbeoNeural", "en-NG-EzinneNeural",
    "en-PH-James", "en-PH-RosaNeural",
    "en-SG-LunaNeural", "en-SG-WayneNeural",
    "en-ZA-LeahNeural", "en-ZA-LukeNeural",
    "en-TZ-ElimuNeural", "en-TZ-ImaniNeural",
    "en-GB-LibbyNeural", "en-GB-MaisieNeural",
    "en-GB-RyanNeural", "en-GB-SoniaNeural",
    "en-GB-ThomasNeural",
    "en-US-AriaNeural", "en-US-AnaNeural",
    "en-US-ChristopherNeural", "en-US-EricNeural",
    "en-US-GuyNeural", "en-US-JennyNeural",
    "en-US-MichelleNeural", "en-US-RogerNeural",
    "en-US-SteffanNeural"
],

"Spanish": [
    "es-AR-ElenaNeural", "es-AR-TomasNeural",
    "es-BO-MarceloNeural", "es-BO-SofiaNeural",
    "es-CL-CatalinaNeural", "es-CL-LorenzoNeural",
    "es-CO-GonzaloNeural", "es-CO-SalomeNeural",
    "es-CR-JuanNeural", "es-CR-MariaNeural",
    "es-CU-BelkysNeural", "es-CU-ManuelNeural",
    "es-DO-EmilioNeural", "es-DO-RamonaNeural",
    "es-EC-AndreaNeural", "es-EC-LorenaNeural",
    "es-SV-RodrigoNeural", "es-SV-LorenaNeural",
    "es-GQ-JavierNeural", "es-GQ-TeresaNeural",
    "es-GT-AndresNeural", "es-GT-MartaNeural",
    "es-HN-CarlosNeural", "es-HN-KarlaNeural",
    "es-MX-DaliaNeural", "es-MX-JorgeNeural",
    "es-NI-FedericoNeural", "es-NI-YolandaNeural",
    "es-PA-MargaritaNeural", "es-PA-RobertoNeural",
    "es-PY-MarioNeural", "es-PY-TaniaNeural",
    "es-PE-AlexNeural", "es-PE-CamilaNeural",
    "es-PR-KarinaNeural", "es-PR-VictorNeural",
    "es-ES-AlvaroNeural", "es-ES-ElviraNeural",
    "es-US-AlonsoNeural", "es-US-PalomaNeural",
    "es-UY-MateoNeural", "es-UY-ValentinaNeural",
    "es-VE-PaolaNeural", "es-VE-SebastianNeural"
],
    "Hindi": [
        "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"
    ],
    "French": [
        "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural",
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-GerardNeural"
    ],
    "German": [
        "de-DE-KatjaNeural", "de-DE-ConradNeural", "de-CH-LeniNeural", "de-CH-JanNeural",
        "de-AT-IngridNeural", "de-AT-JonasNeural"
    ],
    "Chinese": [
        "zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-YunjianNeural",
        "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural"
    ],
    "Japanese": [
        "ja-JP-NanamiNeural", "ja-JP-KeitaNeural"
    ],
    "Portuguese": [
        "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"
    ],
    "Russian": [
        "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"
    ],
    "Turkish": [
        "tr-TR-EmelNeural", "tr-TR-AhmetNeural"
    ],
    "Korean": [
        "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"
    ],
    "Italian": [
        "it-IT-ElsaNeural", "it-IT-DiegoNeural"
    ],
    "Indonesian": [
        "id-ID-GadisNeural", "id-ID-ArdiNeural"
    ],
    "Vietnamese": [
        "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"
    ],
    "Thai": [
        "th-TH-PremwadeeNeural", "th-TH-NiwatNeural"
    ],
    "Dutch": [
        "nl-NL-ColetteNeural", "nl-NL-MaartenNeural"
    ],
    "Polish": [
        "pl-PL-ZofiaNeural", "pl-PL-MarekNeural"
    ],
    "Swedish": [
        "sv-SE-SofieNeural", "sv-SE-MattiasNeural"
    ],
    "Filipino": [
        "fil-PH-BlessicaNeural", "fil-PH-AngeloNeural"
    ],
    "Greek": [
        "el-GR-AthinaNeural", "el-GR-NestorasNeural"
    ],
    "Hebrew": [
        "he-IL-AvriNeural", "he-IL-HilaNeural"
    ],
    "Hungarian": [
        "hu-HU-NoemiNeural", "hu-HU-AndrasNeural"
    ],
    "Czech": [
        "cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"
    ],
    "Danish": [
        "da-DK-ChristelNeural", "da-DK-JeppeNeural"
    ],
    "Finnish": [
        "fi-FI-SelmaNeural", "fi-FI-HarriNeural"
    ],
    "Norwegian": [
        "nb-NO-PernilleNeural", "nb-NO-FinnNeural"
    ],
    "Romanian": [
        "ro-RO-AlinaNeural", "ro-RO-EmilNeural"
    ],
    "Slovak": [
        "sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"
    ],
    "Ukrainian": [
        "uk-UA-PolinaNeural", "uk-UA-OstapNeural"
    ],
    "Malay": [
        "ms-MY-YasminNeural", "ms-MY-OsmanNeural"
    ],
    "Bengali": [
        "bn-BD-NabanitaNeural", "bn-BD-BasharNeural"
    ],
    "Urdu": [
        "ur-PK-AsmaNeural", "ur-PK-FaizanNeural"
    ],
    "Nepali": [
        "ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"
    ],
    "Sinhala": [
        "si-LK-SameeraNeural", "si-LK-ThiliniNeural"
    ],
    "Lao": [
        "lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"
    ],
    "Myanmar": [
        "my-MM-NilarNeural", "my-MM-ThihaNeural"
    ],
    "Georgian": [
        "ka-GE-EkaNeural", "ka-GE-GiorgiNeural"
    ],
    "Armenian": [
        "hy-AM-AnahitNeural", "hy-AM-AraratNeural"
    ],
    "Azerbaijani": [
        "az-AZ-BabekNeural", "az-AZ-BanuNeural"
    ],
    "Uzbek": [
        "uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"
    ],
    "Serbian": [
        "sr-RS-NikolaNeural", "sr-RS-SophieNeural"
    ],
    "Croatian": [
        "hr-HR-GabrijelaNeural", "hr-HR-SreckoNeural"
    ],
    "Slovenian": [
        "sl-SI-PetraNeural", "sl-SI-RokNeural"
    ],
    "Latvian": [
        "lv-LV-EveritaNeural", "lv-LV-AnsisNeural"
    ],
    "Lithuanian": [
        "lt-LT-OnaNeural", "lt-LT-LeonasNeural"
    ],
    "Amharic": [
        "am-ET-MekdesNeural", "am-ET-AbebeNeural"
    ],
    "Swahili": [
        "sw-KE-ZuriNeural", "sw-KE-RafikiNeural"
    ],
    "Zulu": [
        "zu-ZA-ThandoNeural", "zu-ZA-ThembaNeural"
    ],
    "Afrikaans": [
        "af-ZA-AdriNeural", "af-ZA-WillemNeural"
    ],
    "Somali": [
        "so-SO-UbaxNeural", "so-SO-MuuseNeural"
    ],
    "Persian": [
        "fa-IR-DilaraNeural", "fa-IR-ImanNeural"
    ],
}

ORDERED_TTS_LANGUAGES = [
    "English", "Arabic", "Spanish", "French", "German",
    "Chinese", "Japanese", "Portuguese", "Russian", "Turkish",
    "Hindi", "Somali", "Italian", "Indonesian", "Vietnamese",
    "Thai", "Korean", "Dutch", "Polish", "Swedish",
    "Filipino", "Greek", "Hebrew", "Hungarian", "Czech",
    "Danish", "Finnish", "Norwegian", "Romanian", "Slovak",
    "Ukrainian", "Malay", "Bengali", "Urdu", "Nepali",
    "Sinhala", "Lao", "Myanmar", "Georgian", "Armenian",
    "Azerbaijani", "Uzbek", "Serbian", "Croatian", "Slovenian",
    "Latvian", "Lithuanian", "Amharic", "Swahili", "Zulu",
    "Afrikaans", "Persian"
]

def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_name in ORDERED_TTS_LANGUAGES:
        if lang_name in TTS_VOICES_BY_LANGUAGE:
            buttons.append(
                InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}")
            )
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str):
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⬆️ Higher", callback_data="pitch_set|+50"), # Combined options for cleaner UI
        InlineKeyboardButton("⬇️ Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset Pitch", callback_data="pitch_set|0")
    )
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Faster", callback_data="rate_set|+50"), # Combined options for cleaner UI
        InlineKeyboardButton("🐢 Slower", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset Speed", callback_data="rate_set|0")
    )
    markup.add(InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu"))
    return markup

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = "awaiting_rate_input"
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off
    admin_state[message.from_user.id] = None # Reset admin states if any

    bot.send_message(
        message.chat.id,
        "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:",
        reply_markup=make_rate_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)

        set_tts_user_rate_db(uid, rate_value)

        user_rate_input_mode[uid] = None

        bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
            parse_mode="Markdown",
            reply_markup=build_main_menu_keyboard() # Offer main menu after selection
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate from callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = "awaiting_pitch_input"
    user_rate_input_mode[uid] = None
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off
    admin_state[message.from_user.id] = None # Reset admin states if any

    bot.send_message(
        message.chat.id,
        "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:",
        reply_markup=make_pitch_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)

        set_tts_user_pitch_db(uid, pitch_value)

        user_pitch_input_mode[uid] = None

        bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /voice to pick a different voice.",
            parse_mode="Markdown",
            reply_markup=build_main_menu_keyboard() # Offer main menu after selection
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch from callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_transcription_language_selection_mode[message.chat.id] = False # Ensure transcription language selection is off
    admin_state[message.from_user.id] = None # Reset admin states if any

    bot.send_message(message.chat.id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    _, lang_name = call.data.split("|", 1)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Great! Now select a specific *voice* from the {lang_name} options below. 👇",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_db(uid, voice)

    user_tts_mode[uid] = voice

    current_pitch = get_tts_user_pitch_db(uid)
    current_rate = get_tts_user_rate_db(uid)

    bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Perfect! You're now using: *{voice}*.\n\n"
             f"Current settings:\n"
             f"• Pitch: *{current_pitch}*\n"
             f"• Speed: *{current_rate}*\n\n"
             f"Ready to speak? Just send me your text!",
        parse_mode="Markdown",
        reply_markup=build_main_menu_keyboard() # Offer main menu after selection
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id: int, user_id: str, text: str):
    """
    Use MSSpeech to synthesize text -> mp3, send and delete file.
    """
    # Replace periods with commas for faster speech output
    text = text.replace('.', ',')

    voice = get_tts_user_voice_db(user_id)
    pitch = get_tts_user_pitch_db(user_id)
    rate = get_tts_user_rate_db(user_id)
    filename = f"tts_{user_id}_{uuid.uuid4()}.mp3"

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording))
    recording_thread.daemon = True
    recording_thread.start()

    processing_start_time = datetime.now() # Start timer for TTS processing

    try:
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.set_rate(rate)
        await mss.set_pitch(pitch)
        await mss.set_volume(1.0)

        await mss.synthesize(text, filename)

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ Hmm, I couldn't generate the audio file. It might be empty or corrupted. Please try again with different text.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(
                chat_id,
                f,
                caption=f"🎧 *Here's your audio!* \n\n"
                        f"Voice: *{voice}*\n"
                        f"Pitch: *{pitch}*\n"
                        f"Speed: *{rate}*\n\n"
                        f"Enjoy listening! ✨",
                parse_mode="Markdown"
            )

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        increment_tts_conversion_count_db(user_id) # Increment user's TTS count

        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "tts",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": "success",
                "voice": voice,
                "pitch": pitch,
                "rate": rate,
                "text_length": len(text)
            })
        except Exception as e:
            logging.error(f"Error inserting TTS processing stat (success): {e}")

    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        bot.send_message(chat_id, f"❌ I ran into a problem while synthesizing the voice: `{e}`. Please try again, or try a different voice.", parse_mode="Markdown")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "tts",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": "fail_msspeech_error",
                "voice": voice,
                "pitch": pitch,
                "rate": rate,
                "error_message": str(e)
            })
        except Exception as e2:
            logging.error(f"Error inserting TTS processing stat (msspeech_error): {e2}")

    except Exception as e:
        logging.exception("TTS error")
        bot.send_message(chat_id, "❌ Oops! An unexpected error occurred during text-to-speech conversion. Please try again in a moment.")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "tts",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": "fail_unknown",
                "voice": voice,
                "pitch": pitch,
                "rate": rate,
                "error_message": str(e)
            })
        except Exception as e2:
            logging.error(f"Error inserting TTS processing stat (unknown error): {e2}")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting TTS file {filename}: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   M E D I A   T R A N S C R I P T I O N   F U N C T I O N S (from Bot 2)
# ────────────────────────────────────────────────────────────────────────────────

def handle_transcription_request(message):
    chat_id = message.chat.id
    user_id_str = str(message.from_user.id)

    # Retrieve user language from local_user_data (loaded from MongoDB)
    lang_code = get_user_transcription_language_db(user_id_str)

    try:
        stop_typing = threading.Event()
        typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing))
        typing_thread.daemon = True
        typing_thread.start()

        processing_msg = bot.reply_to(message, "⏳ Processing...")

        file_id = None
        if message.voice:
            file_id = message.voice.file_id
        elif message.audio:
            file_id = message.audio.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id

        if not file_id:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "Unsupported file type for transcription. Please send a voice, audio, video, or document file.")
            stop_typing.set()
            return

        file_info = bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024: # 20MB limit
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "⚠️ File is too large. Max allowed size for transcription is 20MB.")
            stop_typing.set()
            return

        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        file_data = requests.get(file_url).content

        upload_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=file_data
        )
        audio_url = upload_res.json().get('upload_url')
        if not audio_url:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "❌ Failed to upload file for transcription.")
            stop_typing.set()
            return

        transcript_res = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={
                "authorization": ASSEMBLYAI_API_KEY,
                "content-type": "application/json"
            },
            json={
                "audio_url": audio_url,
                "language_code": lang_code,
                "speech_model": "best"
            }
        )

        res_json = transcript_res.json()
        transcript_id = res_json.get("id")
        if not transcript_id:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, f"❌ Transcription error: {res_json.get('error', 'Unknown')}")
            stop_typing.set()
            return

        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2) # Poll every 2 seconds

        bot.delete_message(chat_id, processing_msg.message_id)
        stop_typing.set() # Stop typing action

        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                bot.reply_to(message, "ℹ️ No transcription text was returned for your media.")
            elif len(text) <= 4000:
                bot.reply_to(message, text)
            else:
                transcript_file = io.BytesIO(text.encode("utf-8"))
                transcript_file.name = "transcript.txt"
                bot.reply_to(message, "Natiijada qoraalka ayaa ka dheer 4000 oo xaraf, halkan ka degso fayl ahaan:", document=transcript_file)
        else:
            bot.reply_to(message, "❌ Sorry, transcription failed for your media.")

    except Exception as e:
        logging.error(f"Error handling media transcription: {e}")
        bot.reply_to(message, f"⚠️ An error occurred during transcription: {str(e)}")
    finally:
        stop_typing.set() # Ensure typing action is stopped even on error


# --- Unified Message Handler for Text and Media ---
@bot.message_handler(content_types=['text', 'voice', 'audio', 'video', 'document', 'photo', 'sticker', 'video_note'])
def handle_all_messages(message):
    uid = str(message.from_user.id)
    chat_id = message.chat.id
    update_user_activity_db(message.from_user.id)

    # Check subscription for all users except admin in private chat
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Handle admin messages first
    if chat_id == ADMIN_ID:
        if admin_state.get(chat_id) == 'awaiting_broadcast_message':
            # This is handled by specific broadcast_message handler above
            return
        elif message.text in ["Send Broadcast", "Total Users", "/status", "Show All Users (Debug)"]:
            # Let specific admin handlers process these
            return
        else:
            # If admin sends anything else, redirect to admin menu
            bot.send_message(chat_id, "Admin, please use the admin options.", reply_markup=build_admin_keyboard_merged())
            return

    # Handle transcription language selection messages from regular users
    if user_transcription_language_selection_mode.get(chat_id, False):
        if message.text in LANGUAGES or message.text == "Back to Main Menu":
            save_user_language_transcription(message)
            return
        else:
            bot.send_message(chat_id, "Please select a language from the provided buttons or tap 'Back to Main Menu'.")
            return

    # Handle TTS pitch/rate input from regular users
    if user_rate_input_mode.get(uid) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate_db(uid, rate_val)
                bot.send_message(chat_id, f"🔊 Voice speed set to *{rate_val}*.", parse_mode="Markdown", reply_markup=build_main_menu_keyboard())
                user_rate_input_mode[uid] = None # Reset the state
            else:
                bot.send_message(chat_id, "❌ Invalid speed. Please enter a number from -100 to +100 or 0 for normal. Try again:", reply_markup=make_rate_keyboard())
            return
        except ValueError:
            bot.send_message(chat_id, "That's not a valid number for speed. Please enter a number from -100 to +100 or 0 for normal. Try again:", reply_markup=make_rate_keyboard())
            return

    if user_pitch_input_mode.get(uid) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_db(uid, pitch_val)
                bot.send_message(chat_id, f"🔊 Voice pitch set to *{pitch_val}*.", parse_mode="Markdown", reply_markup=build_main_menu_keyboard())
                user_pitch_input_mode[uid] = None # Reset the state
            else:
                bot.send_message(chat_id, "❌ Invalid pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:", reply_markup=make_pitch_keyboard())
            return
        except ValueError:
            bot.send_message(chat_id, "That's not a valid number for pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:", reply_markup=make_pitch_keyboard())
            return

    # If it's a media file, try to transcribe
    if message.voice or message.audio or message.video or message.document or message.video_note:
        if message.video_note and message.video_note.file_size > 20 * 1024 * 1024:
            bot.send_message(chat_id, "⚠️ Video note is too large. Max allowed size is 20MB for transcription.")
            return
        elif message.video and message.video.file_size > 20 * 1024 * 1024:
            bot.send_message(chat_id, "⚠️ Video is too large. Max allowed size is 20MB for transcription.")
            return

        # Check if user has set a transcription language, if not, prompt them
        user_data = get_user_data_db(uid)
        if not user_data or "transcription_language" not in user_data:
            bot.send_message(chat_id, "❗ Please select a language first using the 'Transcribe Media' option from the main menu, then 'Change Transcription Language' button before sending a file.", reply_markup=build_main_menu_keyboard())
            return
        else:
            handle_transcription_request(message)
            return

    # If it's a text message not handled by state-specific handlers (pitch/rate/lang selection)
    if message.content_type == 'text':
        # Limit text length to avoid excessive processing/API limits
        if len(message.text) > 1000: # Arbitrary limit, adjust as needed
            bot.send_message(chat_id, "Please keep your text under 1000 characters for text-to-speech conversion.")
            return

        # Default to TTS if it's a text message and not a command or state-specific input
        current_voice = get_tts_user_voice_db(uid) # This will always return a default if not set by user
        if current_voice:
            threading.Thread(
                target=lambda: asyncio.run(synth_and_send_tts(chat_id, uid, message.text))
            ).start()
        else: # This path should technically not be hit due to default voice
            bot.send_message(
                chat_id,
                "Looks like you haven't chosen a voice yet! Please use the /voice command first to select one, then send me your text. 🗣️",
                reply_markup=build_main_menu_keyboard()
            )
        return

    # For any other unsupported media types (like photos, stickers)
    bot.send_message(
        chat_id,
        "Sorry, I can only convert *text messages* into speech or *transcribe audio/video files*. Please send me valid input!",
        reply_markup=build_main_menu_keyboard()
    )


# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S   (Webhook setup) (Unified)
# ────────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    if request.method in ("GET", "HEAD"):
        return "OK", 200
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return "Webhook deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook: {e}")
        return f"Failed to delete webhook: {e}", 500

def set_bot_commands():
    """
    Sets the list of commands for the bot using set_my_commands.
    """
    commands = [
        BotCommand("start", "Get Started / Main Menu"),
        BotCommand("voice", "Choose a Text-to-Speech voice"),
        BotCommand("pitch", "Change TTS pitch"),
        BotCommand("rate", "Change TTS speed"),
        BotCommand("language", "Set transcription language"),
        BotCommand("help", "❓ How to use the bot"),
        BotCommand("privacy", "🔒 Read privacy notice"),
        BotCommand("status", "Bot stats")
    ]
    try:
        bot.set_my_commands(commands)
        logging.info("Bot commands set successfully.")
    except Exception as e:
        logging.error(f"Failed to set bot commands: {e}")


def set_webhook_on_startup():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

def set_bot_info_and_startup():
    connect_to_mongodb()
    set_webhook_on_startup()
    set_bot_commands()

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"): # Create a simple directory for temporary TTS files
        os.makedirs("tts_audio_cache")
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

