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

from msspeech import MSSpeech, MSSpeechError

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
# Replace with your NEW Bot's Token and Webhook URL
TOKEN = "7999849691:AAHmRwZ_Ef1I64SZqotZND6v7LrE-fFwRD0"  # <-- your NEW bot token
ADMIN_ID = 5978150981  # <-- admin Telegram ID
WEBHOOK_URL = "excellent-davida-wwmahe-45f63d30.koyeb.app/"  # <-- your Render URL (Make sure this is correct and unique for your NEW bot)

REQUIRED_CHANNEL = "@transcriber_bot_news_channel"  # <-- required subscription channel

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- MONGODB CONFIGURATION ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db" # You might want to change this DB name for the new bot

# Collections - Only keep what's relevant for TTS bot
mongo_client: MongoClient = None
db = None
users_collection = None
tts_users_collection = None
processing_stats_collection = None
stt_users_collection = None # New collection for STT user language settings
custom_bots_collection = None # New collection for custom bots created by the constructor

# --- In-memory caches ---
local_user_data = {}            # { user_id: { "last_active": "...", "tts_conversion_count": N, ... } }
_tts_voice_cache = {}           # { user_id: voice_name }
_tts_pitch_cache = {}           # { user_id: pitch_value }
_tts_rate_cache = {}            # { user_id: rate_value }
_stt_lang_cache = {}            # { user_id: lang_code } # Cache for STT language

# --- User state for Text-to-Speech input mode ---
user_tts_mode = {}              # { user_id: voice_name (e.g. "en-US-AriaNeural") or None }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }

# --- User state for Bot Constructor ---
user_bot_constructor_state = {} # { user_id: "awaiting_token" or "awaiting_service_selection" or None }
temp_bot_data = {}              # { user_id: { "token": "...", "service_type": "...", "bot_name": "..." } }

# --- Statistics counters (in-memory for quick access) ---
total_tts_conversions = 0 # This will be redundant, rely on DB for stats
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

admin_state = {}

# Placeholder for keeping track of typing threads
processing_message_ids = {}

# --- AssemblyAI Configuration (from Bot 2) ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # Replace with your actual key

# --- Supported STT Languages with Flags (from Bot 2) ---
STT_LANGUAGES = {
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaycan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇭": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Somali 🇸🇴": "so" # Added Somali as example
}

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    """
    Connect to MongoDB at startup, set up collections and indexes.
    Also, load all user data into in-memory caches.
    """
    global mongo_client, db
    global users_collection, tts_users_collection, processing_stats_collection, stt_users_collection, custom_bots_collection
    global local_user_data, _tts_voice_cache, _tts_pitch_cache, _tts_rate_cache, _stt_lang_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db["users"]
        tts_users_collection = db["tts_users"]
        processing_stats_collection = db["processing_stats"] # Centralized processing stats
        stt_users_collection = db["stt_users"] # New collection for STT languages
        custom_bots_collection = db["custom_bots"] # New collection for custom bots

        # Create indexes (if not already created)
        users_collection.create_index([("last_active", ASCENDING)])
        tts_users_collection.create_index([("_id", ASCENDING)])
        stt_users_collection.create_index([("_id", ASCENDING)])
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])
        custom_bots_collection.create_index([("token", ASCENDING)], unique=True) # Ensure bot tokens are unique

        logging.info("Connected to MongoDB and indexes created. Loading data to memory...")

        # --- Load all user data into in-memory caches on startup ---
        for user_doc in users_collection.find({}):
            local_user_data[user_doc["_id"]] = user_doc
        logging.info(f"Loaded {len(local_user_data)} user documents into local_user_data.")

        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[tts_user["_id"]] = tts_user.get("voice", "so-SO-MuuseNeural") # Default to Somali
            _tts_pitch_cache[tts_user["_id"]] = tts_user.get("pitch", 0)
            _tts_rate_cache[tts_user["_id"]] = tts_user.get("rate", 0)
        logging.info(f"Loaded {len(_tts_voice_cache)} TTS voice, pitch, and rate settings.")
        
        for stt_user in stt_users_collection.find({}):
            _stt_lang_cache[stt_user["_id"]] = stt_user.get("language", "en") # Default to English
        logging.info(f"Loaded {len(_stt_lang_cache)} STT language settings.")

        logging.info("All essential user data loaded into in-memory caches.")

    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"Error during MongoDB connection or initial data load: {e}")
        exit(1)


def update_user_activity_db(user_id: int):
    """
    Update user.last_active = now() in local_user_data cache and then in MongoDB.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0 # Initialize STT count for new users
        }
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

def increment_conversion_count_db(user_id: str, conversion_type: str):
    """
    Increment conversion_type_conversion_count in local_user_data cache and then in MongoDB,
    also update last_active.
    """
    now_iso = datetime.now().isoformat()
    count_field = f"{conversion_type}_conversion_count"

    # Update in-memory cache
    if user_id not in local_user_data:
        local_user_data[user_id] = {
            "_id": user_id,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    
    local_user_data[user_id][count_field] = local_user_data[user_id].get(count_field, 0) + 1
    local_user_data[user_id]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id},
            {
                "$inc": {count_field: 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing {conversion_type} count for {user_id} in DB: {e}")


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

def get_stt_user_language_db(user_id: str) -> str:
    """
    Return STT language from cache (default "en").
    """
    return _stt_lang_cache.get(user_id, "en")

def set_stt_user_language_db(user_id: str, lang_code: str):
    """
    Save STT language in DB and update cache.
    """
    _stt_lang_cache[user_id] = lang_code # Update in-memory cache
    try:
        stt_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"language": lang_code}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting STT language for {user_id} in DB: {e}")

def add_custom_bot_to_db(user_id: str, bot_token: str, service_type: str, bot_name: str):
    """
    Adds a new custom bot's details to the custom_bots_collection.
    """
    try:
        custom_bots_collection.insert_one({
            "creator_user_id": user_id,
            "token": bot_token,
            "service_type": service_type,
            "bot_name": bot_name,
            "created_at": datetime.now().isoformat()
        })
        logging.info(f"Custom bot '{bot_name}' ({service_type}) created by user {user_id} and saved to DB.")
        return True
    except Exception as e:
        logging.error(f"Error adding custom bot to DB: {e}")
        return False

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
#   B O T   H A N D L E R S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

    # Ensure user is in local_user_data and DB
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
        # Immediately save new user to DB
        try:
            users_collection.insert_one(local_user_data[user_id_str])
            logging.info(f"New user {user_id_str} inserted into MongoDB.")
        except Exception as e:
            logging.error(f"Error inserting new user {user_id_str} into DB: {e}")
    else:
        # Just update activity if already exists
        update_user_activity_db(message.from_user.id)

    # Check subscription immediately on /start for all users except admin in private chat
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure TTS/STT modes and constructor state are OFF on /start
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_bot_constructor_state[user_id_str] = None
    temp_bot_data.pop(user_id_str, None)

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(
            message.chat.id,
            "Admin Panel and Uptime (updating live)...",
            reply_markup=keyboard
        )
        with admin_uptime_lock:
            if (
                admin_uptime_message.get(ADMIN_ID)
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                pass

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
        welcome_message = (
            f"👋 Hey there, {user_first_name}! I'm your go-to bot for converting text into realistic AI voices and transcribing media! 🔊✍️\n\n"
            "✨ *Here's how to make the most of me:* ✨\n"
            "• Use /tts to **convert text to speech** and customize your voice.\n"
            "• Use /stt to **transcribe voice/audio/video** into text.\n"
            "• Use /create_bot to **build your own custom bot** without coding!\n\n"
            "Feel free to add me to your groups too! Just click the button below 👇"
        )
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("➕ Add Me to Your Groups", url="https://t.me/mediatotextbot?startgroup=")
        )
        
        bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=markup,
            parse_mode="Markdown"
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure modes are OFF on /help
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_constructor_state[user_id] = None

    help_text = (
        """
📚 *How to Use This Bot*

Ready to enhance your audio and text? Here's how it works:

1.  **Text-to-Speech (TTS) Conversion**
    * **Start TTS:** Use the /tts command.
    * **Choose a Voice:** Select from a wide range of languages and voices.
    * **Send Your Text:** I'll transform it into an audio clip.
    * **Fine-Tune:** Use /pitch to adjust tone and /rate to change speed.

2.  **Speech-to-Text (STT) Transcription**
    * **Start STT:** Use the /stt command.
    * **Choose a Language:** Select the language of your audio.
    * **Send Your Media:** Send voice messages, audio files, or video notes (up to 20MB). I'll transcribe them into text.

3.  **Bot Constructor (NEW!)**
    * **Create Your Own Bot:** Use the /create_bot command.
    * **Simple Setup:** Provide your bot token, choose a service (TTS or STT), and I'll create a new bot for you linked to our services! No coding needed.

4.  **Privacy & Data Handling**
    * **Your Text and Media are Private:** Any text for TTS or media for STT is processed instantly and **never stored** on our servers. Generated audio and transcript files are temporary and deleted after being sent to you.
    * **Your Settings are Saved:** We securely store your Telegram User ID and your chosen preferences (TTS voice, pitch, rate, STT language, and custom bot details) in our MongoDB database. This ensures your settings are remembered for future use. We also keep a record of basic activity (last active timestamp, TTS/STT conversion counts) for anonymous, aggregated statistics to help us improve the bot.

---

If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy! ✨
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure modes are OFF on /privacy
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_constructor_state[user_id] = None

    privacy_text = (
        """
🔐 *Privacy Notice: Your Data & This Bot*

Your privacy is incredibly important to us. This notice explains exactly how your data is handled in real-time when you use this bot.

1.  **Data We Process & Its Lifecycle:**
    * **Text for Speech Synthesis & Media for Transcription:** When you send text for TTS or media for STT, it's processed immediately. This data is **not stored** on our servers after processing. The generated audio/transcript files are also temporary and are deleted right after they're sent to you.
    * **User IDs, Preferences, and Activity Data:** We store your Telegram User ID and your personal preferences (your chosen TTS voice, pitch, rate, and STT language) in our MongoDB database. We also log basic activity like your "last active" timestamp and a count of your TTS and STT conversions. This helps us remember your settings for a consistent experience and allows us to gather anonymous, aggregated statistics to understand overall bot usage and improve our services. This data is also cached in memory for fast access during bot operation and is regularly updated in MongoDB.
    * **Custom Bot Information:** If you use the /create_bot feature, we store the new bot's token, the service type it provides (TTS or STT), its name, and your creator ID. This is necessary to manage and link the custom bot to our backend services. **Your custom bot token is stored securely.**

2.  **How Your Data is Used:**
    * **Core Service Delivery:** To perform the bot's primary functions: converting your text to speech, transcribing your media to text, and running your custom-created bots.
    * **Service Improvement:** To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total TTS/STT conversions).
    * **Personalization:** To maintain your preferred voice and transcription settings across your sessions.
    * **Custom Bot Management:** To ensure your custom bot functions correctly and utilizes our services.

3.  **Data Sharing Policy:**
    * We have a strict **no-sharing policy**. We **do not share** your personal data, text input, or media input with any third parties for marketing or any other purposes.
    * Text-to-speech functionality is powered by the Microsoft Cognitive Services Speech API. Speech-to-text functionality is powered by AssemblyAI. While your input is sent to these models for processing, we ensure that your data is **not stored by us** after it has been processed by these external services. Their own privacy policies govern how they handle the data during the conversion process.

4.  **Data Retention:**
    * **Text input, media input, and generated files:** These are **deleted immediately** after processing and delivery.
    * **User IDs and preferences:** This data is stored in MongoDB to support your settings and for anonymous usage statistics. This data is also cached in memory for performance. If you wish to have your stored preferences removed, you can simply stop using the bot. For explicit data deletion requests, please contact the bot administrator.
    * **Custom Bot Data:** This data is retained as long as your custom bot is active to ensure its functionality.

By using this bot, you confirm that you understand and agree to the data practices outlined in this Privacy Notice.

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure modes are OFF on /status
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_constructor_state[user_id] = None

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active today
    today_iso = datetime.now().date().isoformat()
    active_today_count = sum(1 for user_doc in local_user_data.values() if user_doc.get("last_active", "").startswith(today_iso))

    # Total registered users from local_user_data
    total_registered_users = len(local_user_data)

    # Processing stats
    try:
        total_tts_conversions_db = processing_stats_collection.count_documents({"type": "tts"})
        total_stt_conversions_db = processing_stats_collection.count_documents({"type": "stt"})
        
        pipeline_tts = [
            {"$match": {"type": "tts"}},
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result_tts = list(processing_stats_collection.aggregate(pipeline_tts))
        total_tts_proc_seconds = agg_result_tts[0]["total_time"] if agg_result_tts else 0

        pipeline_stt = [
            {"$match": {"type": "stt"}},
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result_stt = list(processing_stats_collection.aggregate(pipeline_stt))
        total_stt_proc_seconds = agg_result_stt[0]["total_time"] if agg_result_stt else 0

        total_custom_bots = custom_bots_collection.count_documents({})

    except Exception as e:
        logging.error(f"Error fetching processing stats from DB: {e}")
        total_tts_conversions_db = 0
        total_stt_conversions_db = 0
        total_tts_proc_seconds = 0
        total_stt_proc_seconds = 0
        total_custom_bots = 0


    tts_proc_hours = int(total_tts_proc_seconds) // 3600
    tts_proc_minutes = (int(total_tts_proc_seconds) % 3600) // 60
    tts_proc_seconds = int(total_tts_proc_seconds) % 60

    stt_proc_hours = int(total_stt_proc_seconds) // 3600
    stt_proc_minutes = (int(total_stt_proc_seconds) % 3600) // 60
    stt_proc_seconds = int(total_stt_proc_seconds) % 60


    text = (
        "📊 *Bot Statistics*\n\n"
        "🟢 *Bot Status: Online*\n"
        f"⏱️ The bot has been running for: *{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds*\n\n"
        "👥 *User Statistics*\n"
        f"▫️ Total Active Users Today: *{active_today_count}*\n"
        f"▫️ Total Registered Users: *{total_registered_users}*\n"
        f"▫️ Custom Bots Created: *{total_custom_bots}*\n\n"
        "⚙️ *Processing Statistics*\n"
        f"▫️ Total Text-to-Speech Conversions: *{total_tts_conversions_db}*\n"
        f"⏱️ Total TTS Processing Time: *{tts_proc_hours} hours {tts_proc_minutes} minutes {tts_proc_seconds} seconds*\n"
        f"▫️ Total Speech-to-Text Conversions: *{total_stt_conversions_db}*\n"
        f"⏱️ Total STT Processing Time: *stt_proc_hours} hours {stt_proc_minutes} minutes {stt_proc_seconds} seconds*\n\n"
        "---"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total_registered = users_collection.count_documents({}) # Get from DB directly
    bot.send_message(message.chat.id, f"Total registered users (from DB): {total_registered}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_prompt(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast_message'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast_message',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    all_users_chat_ids = users_collection.distinct("_id") # Use _id for users
    for uid in all_users_chat_ids:
        if uid == str(ADMIN_ID):
            continue
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
        time.sleep(0.05)

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   T T S   F U N C T I O N S
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
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str):
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⬆️ Higher", callback_data="pitch_set|+50"), # Combined options for cleaner UI
        InlineKeyboardButton("⬇️ Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset Pitch", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Faster", callback_data="rate_set|+50"), # Combined options for cleaner UI
        InlineKeyboardButton("🐢 Slower", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset Speed", callback_data="rate_set|0")
    )
    return markup

@bot.message_handler(commands=['tts'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None # Ensure we're in a clean TTS state
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_constructor_state[user_id] = None

    bot.send_message(message.chat.id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

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
    user_bot_constructor_state[uid] = None


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

        user_rate_input_mode[uid] = None # Reset the state

        bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /tts to change the voice.",
            parse_mode="Markdown",
            reply_markup=None # Remove keyboard after selection
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
    user_bot_constructor_state[uid] = None

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

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)

        set_tts_user_pitch_db(uid, pitch_value)

        user_pitch_input_mode[uid] = None # Reset the state

        bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /tts to pick a different voice.",
            parse_mode="Markdown",
            reply_markup=None # Remove keyboard after selection
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch from callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

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
    user_bot_constructor_state[uid] = None

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
    user_bot_constructor_state[uid] = None

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
        reply_markup=None # Remove keyboard after selection
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
    user_bot_constructor_state[uid] = None

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
    filename = f"tts_audio_cache/tts_{user_id}_{uuid.uuid4()}.mp3"

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
        increment_conversion_count_db(user_id, "tts") # Increment user's TTS count

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
#   S T T   F U N C T I O N S (from Bot 2)
# ────────────────────────────────────────────────────────────────────────────────

def build_stt_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    # Sort languages alphabetically by display name for consistent order
    sorted_languages = sorted(STT_LANGUAGES.keys())
    for name in sorted_languages:
        buttons.append(InlineKeyboardButton(name, callback_data=f"stt_lang|{STT_LANGUAGES[name]}"))
    
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

@bot.message_handler(commands=['stt'])
def cmd_speech_to_text(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Reset all other modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_constructor_state[user_id] = None

    bot.send_message(
        message.chat.id,
        "🎙️ Choose the *language* of your media file for transcription:",
        reply_markup=build_stt_language_keyboard(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Reset other modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_constructor_state[uid] = None


    _, lang_code = call.data.split("|", 1)
    set_stt_user_language_db(uid, lang_code)
    
    display_lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown Language")

    bot.answer_callback_query(call.id, f"✅ Language set to {display_lang_name.split(' ')[0]}!")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Transcription Language Set: *{display_lang_name}*\n\n"
             "🎙️ Please send your voice message, audio file, or video note, and I’ll transcribe it for you with precision.\n\n"
             "📁 Supported file size: Up to 20MB",
        parse_mode="Markdown",
        reply_markup=None # Remove keyboard after selection
    )

async def process_stt_media(message, lang_code: str):
    chat_id = message.chat.id
    user_id = str(message.from_user.id)

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = bot.send_message(chat_id, "⏳ Processing audio for transcription...")

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
            bot.send_message(chat_id, "Unsupported file type. Please send a voice, audio, video, or document file.")
            return

        file_info = bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            bot.send_message(chat_id, "⚠️ File is too large. Max allowed size is 20MB.")
            return

        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        file_data = requests.get(file_url).content

        processing_start_time = datetime.now() # Start timer for STT processing

        upload_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=file_data
        )
        audio_url = upload_res.json().get('upload_url')
        if not audio_url:
            raise Exception(f"Failed to upload file to AssemblyAI: {upload_res.json().get('error', 'Unknown error')}")

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
            raise Exception(f"AssemblyAI transcription request failed: {res_json.get('error', 'Unknown')}")

        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2)

        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                bot.send_message(chat_id, "ℹ️ No transcription text was returned.")
            elif len(text) <= 4000:
                bot.send_message(chat_id, text)
            else:
                import io
                transcript_file = io.BytesIO(text.encode("utf-8"))
                transcript_file.name = "transcript.txt"
                bot.send_document(chat_id, transcript_file, caption="Transcript is too long, sending as file.")

            processing_time = (datetime.now() - processing_start_time).total_seconds()
            increment_conversion_count_db(user_id, "stt")

            try:
                processing_stats_collection.insert_one({
                    "user_id": user_id,
                    "type": "stt",
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": "success",
                    "language": lang_code,
                    "media_type": message.content_type,
                    "file_size": file_info.file_size
                })
            except Exception as e:
                logging.error(f"Error inserting STT processing stat (success): {e}")

        else:
            bot.send_message(chat_id, f"❌ Sorry, transcription failed: {res.get('error', 'Unknown error')}")
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": user_id,
                    "type": "stt",
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": "fail_assemblyai_error",
                    "language": lang_code,
                    "media_type": message.content_type,
                    "file_size": file_info.file_size,
                    "error_message": res.get('error', 'Unknown error')
                })
            except Exception as e2:
                logging.error(f"Error inserting STT processing stat (assemblyai_error): {e2}")

    except Exception as e:
        logging.exception("STT error")
        bot.send_message(chat_id, f"⚠️ An unexpected error occurred during transcription: `{str(e)}`")
        if 'processing_start_time' in locals():
            processing_time = (datetime.now() - processing_start_time).total_seconds()
            try:
                processing_stats_collection.insert_one({
                    "user_id": user_id,
                    "type": "stt",
                    "processing_time": processing_time,
                    "timestamp": datetime.now().isoformat(),
                    "status": "fail_unknown",
                    "language": lang_code,
                    "media_type": message.content_type,
                    "error_message": str(e)
                })
            except Exception as e2:
                logging.error(f"Error inserting STT processing stat (unknown error): {e2}")
    finally:
        stop_typing.set()
        if processing_msg:
            try:
                bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Failed to delete processing message: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   C O N S T R U C T O R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['create_bot'])
def create_bot_command(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Reset all other modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    
    user_bot_constructor_state[user_id] = "awaiting_token"
    temp_bot_data[user_id] = {} # Initialize temp storage for this user

    bot.send_message(
        message.chat.id,
        "Alright, let's create your custom bot! First, please send me the **Bot Token** you got from @BotFather. \n\n"
        "If you don't have one, go to @BotFather and create a new bot, then copy its token and send it here. \n\n"
        "To cancel, type /cancel_bot_creation."
    )

@bot.message_handler(commands=['cancel_bot_creation'])
def cancel_bot_creation(message):
    user_id = str(message.from_user.id)
    if user_bot_constructor_state.get(user_id):
        user_bot_constructor_state.pop(user_id, None)
        temp_bot_data.pop(user_id, None)
        bot.send_message(message.chat.id, "Bot creation cancelled. You can start again with /create_bot.")
    else:
        bot.send_message(message.chat.id, "You are not currently in the bot creation process.")

@bot.message_handler(
    func=lambda message: user_bot_constructor_state.get(str(message.from_user.id)) == "awaiting_token",
    content_types=['text']
)
def handle_bot_token_input(message):
    user_id = str(message.from_user.id)
    bot_token = message.text.strip()

    # Basic validation for bot token
    if not (len(bot_token) > 20 and ':' in bot_token):
        bot.send_message(message.chat.id, "That doesn't look like a valid Bot Token. Please ensure you copy the *entire* token from @BotFather. It should contain a colon (:) and numbers/letters. Try again, or /cancel_bot_creation.")
        return

    # Try to get bot info using the token to validate it and get the bot's name
    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        bot_name = bot_info.first_name
    except Exception as e:
        logging.error(f"Error validating bot token {bot_token}: {e}")
        bot.send_message(message.chat.id, "❌ Hmm, I couldn't validate that token with Telegram. Please double-check it. It might be invalid or revoked. Try again, or /cancel_bot_creation.")
        return

    # Check if a bot with this token already exists in our database
    if custom_bots_collection.find_one({"token": bot_token}):
        bot.send_message(message.chat.id, "⚠️ This bot token is already linked to another custom bot in our system. Please use a unique token for each bot you create. Try again with a different token, or /cancel_bot_creation.")
        return

    temp_bot_data[user_id]["token"] = bot_token
    temp_bot_data[user_id]["bot_name"] = bot_name
    user_bot_constructor_state[user_id] = "awaiting_service_selection"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🗣️ TTS (Text-to-Speech) Bot", callback_data="create_bot_service|tts"),
        InlineKeyboardButton("✍️ STT (Speech-to-Text) Bot", callback_data="create_bot_service|stt")
    )
    bot.send_message(
        message.chat.id,
        f"Great! Your bot *{bot_name}* is recognized. Now, what service should your new bot provide?",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data.startswith("create_bot_service|"))
def handle_bot_service_selection(call):
    user_id = str(call.from_user.id)
    if user_bot_constructor_state.get(user_id) != "awaiting_service_selection":
        bot.answer_callback_query(call.id, "Please start the bot creation process again with /create_bot.")
        return

    _, service_type = call.data.split("|", 1)
    
    token = temp_bot_data[user_id].get("token")
    bot_name = temp_bot_data[user_id].get("bot_name")

    if not token or not bot_name:
        bot.answer_callback_query(call.id, "Error: Missing bot token or name. Please restart with /create_bot.")
        user_bot_constructor_state.pop(user_id, None)
        temp_bot_data.pop(user_id, None)
        return

    temp_bot_data[user_id]["service_type"] = service_type

    # Finalize bot creation by saving to DB
    if add_custom_bot_to_db(user_id, token, service_type, bot_name):
        bot.answer_callback_query(call.id, f"✅ {service_type.upper()} bot created!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Congratulations! Your new *{service_type.upper()} Bot* named *{bot_name}* is now active and ready to use.\n\n"
                 f"You can find it at: t.me/{bot_name.replace(' ', '')}\n\n" # Basic username guess
                 "It runs using the core functionalities of this bot. Share it with your friends!",
            parse_mode="Markdown",
            reply_markup=None
        )
    else:
        bot.answer_callback_query(call.id, "❌ Failed to create bot. Please try again.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="❌ An error occurred while trying to register your bot. Please try again or contact support if the issue persists.",
            reply_markup=None
        )

    user_bot_constructor_state.pop(user_id, None)
    temp_bot_data.pop(user_id, None)


# ────────────────────────────────────────────────────────────────────────────────
#   G E N E R A L   M E S S A G E   H A N D L E R S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['text', 'voice', 'audio', 'video', 'document', 'photo', 'sticker', 'video_note'])
def handle_all_content_types(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Handle messages for bot constructor first, if user is in that state
    if user_bot_constructor_state.get(uid) == "awaiting_token":
        handle_bot_token_input(message) # Reroute to token handler
        return
    # Note: awaiting_service_selection is handled by callback, not text

    # Handle custom input modes for TTS pitch/rate
    if user_rate_input_mode.get(uid) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate_db(uid, rate_val)
                bot.send_message(message.chat.id, f"🔊 Voice speed set to *{rate_val}*.", parse_mode="Markdown")
                user_rate_input_mode[uid] = None # Reset the state
            else:
                bot.send_message(message.chat.id, "❌ Invalid speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            bot.send_message(message.chat.id, "That's not a valid number for speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    if user_pitch_input_mode.get(uid) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_db(uid, pitch_val)
                bot.send_message(message.chat.id, f"🔊 Voice pitch set to *{pitch_val}*.", parse_mode="Markdown")
                user_pitch_input_mode[uid] = None # Reset the state
            else:
                bot.send_message(message.chat.id, "❌ Invalid pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            bot.send_message(message.chat.id, "That's not a valid number for pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    # If it's the admin, and they are NOT in broadcast state, redirect them to admin menu
    if str(message.from_user.id) == str(ADMIN_ID) and admin_state.get(str(ADMIN_ID)) != 'awaiting_broadcast_message':
        # If admin sends text and it's not a command or part of broadcast, show admin menu
        if message.content_type == 'text' and not message.text.startswith('/'):
            bot.send_message(
                message.chat.id,
                "Admin, please use the admin options or send a broadcast message.",
                reply_markup=telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add("Send Broadcast", "Total Users", "/status")
            )
            return
        # If admin sends any other media type and not in broadcast, just say unsupported for admin context
        elif message.content_type != 'text':
             bot.send_message(message.chat.id, "Admin, I only process text or specific admin commands. Please use the admin panel options.")
             return


    # Handle TTS text input (if not in any special state)
    if message.content_type == 'text':
        # If the text is a command, ignore it here; it will be handled by specific command handlers
        if message.text.startswith('/'):
            return

        # Get the user's selected voice from the persistent cache (loaded from DB)
        current_tts_voice = get_tts_user_voice_db(uid)

        # Proceed with TTS if a voice is selected (either default or user-chosen)
        if current_tts_voice: 
            # Limit text length to avoid excessive processing/API limits
            if len(message.text) > 1000: # Arbitrary limit, adjust as needed
                bot.send_message(message.chat.id, "Please keep your text under 1000 characters for text-to-speech conversion.")
                return

            threading.Thread(
                target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
            ).start()
        else:
            bot.send_message(
                message.chat.id,
                "Looks like you haven't chosen a voice yet for Text-to-Speech! Please use the /tts command first to select one, then send me your text. 🗣️"
            )
    
    # Handle STT media input
    elif message.content_type in ['voice', 'audio', 'video', 'document', 'video_note']:
        current_stt_lang = get_stt_user_language_db(uid)
        if current_stt_lang:
            threading.Thread(
                target=lambda: asyncio.run(process_stt_media(message, current_stt_lang))
            ).start()
        else:
            bot.send_message(message.chat.id, "❗ Please select a language first using /stt before sending a file for transcription.")
    else:
        # Fallback for unsupported media types (photo, sticker, etc.)
        bot.send_message(
            message.chat.id,
            "Sorry, I can only convert *text messages* into speech or *voice/audio/video files* into text. Please send one of those! 🗣️✍️"
        )


# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S   (Webhook setup)
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
        BotCommand("tts", "Convert Text to Speech"),
        BotCommand("stt", "Transcribe Speech to Text"),
        BotCommand("create_bot", "Create your own custom bot"),
        BotCommand("pitch", "Change TTS pitch"),
        BotCommand("rate", "Change TTS speed"),
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

