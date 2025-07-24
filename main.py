import uuid
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import threading
import time
import os

from msspeech import MSSpeech, MSSpeechError

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
TOKEN = "7999849691:AAHmRwZ_Ef1I64SZqotZND6v7LrE-fFwRD0"  # <-- your main bot token
ADMIN_ID = 5978150981  # <-- admin Telegram ID
WEBHOOK_URL = "https://excellent-davida-wwmahe-45f63d30.koyeb.app/"  # <-- your Render URL

REQUIRED_CHANNEL = "@transcriber_bot_news_channel"  # <-- required subscription channel

ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # AssemblyAI key for STT

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- MONGODB CONFIGURATION ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

mongo_client: MongoClient = None
db = None
users_collection = None # Unified users collection
tts_users_collection = None # Specific TTS settings
processing_stats_collection = None # TTS processing stats
bot_settings_collection = None # For global bot settings like STT toggle
builder_bots_collection = None # For the bot constructor feature

# --- In-memory caches ---
local_user_data = {}            # { user_id: { "last_active": "...", "tts_conversion_count": N, "stt_language": "en", ... } }
_tts_voice_cache = {}           # { user_id: voice_name }
_tts_pitch_cache = {}           # { user_id: pitch_value }
_tts_rate_cache = {}            # { user_id: rate_value }
_stt_language_cache = {}        # { user_id: lang_code }

# --- User state for Text-to-Speech input mode ---
user_tts_mode = {}              # { user_id: voice_name (e.g. "en-US-AriaNeural") or None }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }

# --- Admin State ---
admin_state = {}                # { admin_id: 'awaiting_broadcast_message' or 'awaiting_bot_token' or 'awaiting_bot_service' }
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

# --- Global Bot Feature State ---
stt_feature_enabled = False # Default to off, loaded from DB on startup

# --- Supported Languages for STT ---
STT_LANGUAGES = {
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaycan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇭": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇸": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi"
}

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    """
    Connect to MongoDB at startup, set up collections and indexes.
    Also, load all user data and global settings into in-memory caches.
    """
    global mongo_client, db, stt_feature_enabled
    global users_collection, tts_users_collection, processing_stats_collection, bot_settings_collection, builder_bots_collection
    global local_user_data, _tts_voice_cache, _tts_pitch_cache, _tts_rate_cache, _stt_language_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db["users"] # Unified user data
        tts_users_collection = db["tts_users"]
        processing_stats_collection = db["tts_processing_stats"]
        bot_settings_collection = db["bot_settings"] # New: global settings
        builder_bots_collection = db["builder_bots"] # New: for bot constructor

        # Create indexes (if not already created)
        users_collection.create_index([("last_active", ASCENDING)])
        users_collection.create_index([("chat_id", ASCENDING)], unique=True) # Ensure unique chat_id for main users
        tts_users_collection.create_index([("_id", ASCENDING)], unique=True)
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])
        bot_settings_collection.create_index([("key", ASCENDING)], unique=True)
        builder_bots_collection.create_index([("bot_token", ASCENDING)], unique=True, sparse=True) # Index for bot tokens

        logging.info("Connected to MongoDB and indexes created. Loading data to memory...")

        # --- Load global bot settings ---
        stt_setting = bot_settings_collection.find_one({"key": "stt_feature_enabled"})
        if stt_setting:
            stt_feature_enabled = stt_setting.get("value", False)
        else:
            bot_settings_collection.insert_one({"key": "stt_feature_enabled", "value": False})
            stt_feature_enabled = False
        logging.info(f"STT Feature Enabled (global): {stt_feature_enabled}")

        # --- Load all user data into in-memory caches on startup ---
        for user_doc in users_collection.find({}):
            local_user_data[str(user_doc["chat_id"])] = user_doc # Use chat_id as key for unified collection
        logging.info(f"Loaded {len(local_user_data)} user documents into local_user_data.")

        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[str(tts_user["_id"])] = tts_user.get("voice", "so-SO-MuuseNeural")
            _tts_pitch_cache[str(tts_user["_id"])] = tts_user.get("pitch", 0)
            _tts_rate_cache[str(tts_user["_id"])] = tts_user.get("rate", 0)
        logging.info(f"Loaded {len(_tts_voice_cache)} TTS voice, pitch, and rate settings.")

        # Load STT language settings
        for user_doc in users_collection.find({"language": {"$exists": True}}):
            _stt_language_cache[str(user_doc["chat_id"])] = user_doc["language"]
        logging.info(f"Loaded {len(_stt_language_cache)} STT language settings.")

        logging.info("All essential user data loaded into in-memory caches.")

    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"Error during MongoDB connection or initial data load: {e}")
        exit(1)

def update_stt_feature_status(status: bool):
    """Update global STT feature status in DB and memory."""
    global stt_feature_enabled
    stt_feature_enabled = status
    try:
        bot_settings_collection.update_one(
            {"key": "stt_feature_enabled"},
            {"$set": {"value": status}},
            upsert=True
        )
        logging.info(f"STT feature status updated to: {status}")
    except Exception as e:
        logging.error(f"Error updating STT feature status in DB: {e}")

def update_user_activity_db(user_id: int):
    """
    Update user.last_active = now() in local_user_data cache and then in MongoDB.
    Uses 'chat_id' as the primary key for the unified 'users' collection.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "chat_id": user_id, # Use chat_id here
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_transcription_count": 0 # Initialize STT count for new users
        }
    else:
        local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"chat_id": user_id}, # Query by chat_id
            {"$set": {"last_active": now_iso}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error updating user activity for {user_id_str} in DB: {e}")

def get_user_data_db(user_id: int) -> dict | None:
    """
    Return user document from local_user_data cache. If not found, try MongoDB
    and load into cache.
    """
    user_id_str = str(user_id)
    if user_id_str in local_user_data:
        return local_user_data[user_id_str]
    try:
        doc = users_collection.find_one({"chat_id": user_id}) # Query by chat_id
        if doc:
            local_user_data[user_id_str] = doc # Load into cache
        return doc
    except Exception as e:
        logging.error(f"Error fetching user data for {user_id_str} from DB: {e}")
        return None

def increment_tts_conversion_count_db(user_id: int):
    """
    Increment tts_conversion_count in local_user_data cache and then in MongoDB,
    also update last_active.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "chat_id": user_id,
            "last_active": now_iso,
            "tts_conversion_count": 1,
            "stt_transcription_count": 0
        }
    else:
        local_user_data[user_id_str]["tts_conversion_count"] = local_user_data[user_id_str].get("tts_conversion_count", 0) + 1
        local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"chat_id": user_id},
            {
                "$inc": {"tts_conversion_count": 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing TTS conversion count for {user_id_str} in DB: {e}")

def increment_stt_transcription_count_db(user_id: int):
    """
    Increment stt_transcription_count in local_user_data cache and then in MongoDB,
    also update last_active.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "chat_id": user_id,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_transcription_count": 1
        }
    else:
        local_user_data[user_id_str]["stt_transcription_count"] = local_user_data[user_id_str].get("stt_transcription_count", 0) + 1
        local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"chat_id": user_id},
            {
                "$inc": {"stt_transcription_count": 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing STT transcription count for {user_id_str} in DB: {e}")

def get_tts_user_voice_db(user_id: int) -> str:
    """
    Return TTS voice from cache (default "so-SO-MuuseNeural").
    """
    return _tts_voice_cache.get(str(user_id), "so-SO-MuuseNeural")

def set_tts_user_voice_db(user_id: int, voice: str):
    """
    Save TTS voice in DB and update cache.
    """
    uid_str = str(user_id)
    _tts_voice_cache[uid_str] = voice # Update in-memory cache
    try:
        # Use _id for tts_users_collection as it was designed
        tts_users_collection.update_one(
            {"_id": uid_str},
            {"$set": {"voice": voice}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS voice for {uid_str} in DB: {e}")

def get_tts_user_pitch_db(user_id: int) -> int:
    """
    Return TTS pitch from cache (default 0).
    """
    return _tts_pitch_cache.get(str(user_id), 0)

def set_tts_user_pitch_db(user_id: int, pitch: int):
    """
    Save TTS pitch in DB and update cache.
    """
    uid_str = str(user_id)
    _tts_pitch_cache[uid_str] = pitch # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": uid_str},
            {"$set": {"pitch": pitch}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS pitch for {uid_str} in DB: {e}")

def get_tts_user_rate_db(user_id: int) -> int:
    """
    Return TTS rate from cache (default 0).
    """
    return _tts_rate_cache.get(str(user_id), 0)

def set_tts_user_rate_db(user_id: int, rate: int):
    """
    Save TTS rate in DB and update cache.
    """
    uid_str = str(user_id)
    _tts_rate_cache[uid_str] = rate # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": uid_str},
            {"$set": {"rate": rate}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS rate for {uid_str} in DB: {e}")

def get_stt_user_language_db(user_id: int) -> str | None:
    """
    Return STT language from cache, or fetch from unified users_collection.
    """
    uid_str = str(user_id)
    if uid_str in _stt_language_cache:
        return _stt_language_cache[uid_str]
    user_doc = get_user_data_db(user_id) # Uses local_user_data first, then DB
    if user_doc and "language" in user_doc:
        _stt_language_cache[uid_str] = user_doc["language"] # Cache it
        return user_doc["language"]
    return None

def set_stt_user_language_db(user_id: int, lang_code: str):
    """
    Save STT language in DB (unified users_collection) and update cache.
    """
    uid_str = str(user_id)
    _stt_language_cache[uid_str] = lang_code # Update in-memory cache
    try:
        users_collection.update_one(
            {"chat_id": user_id},
            {"$set": {"language": lang_code}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting STT language for {uid_str} in DB: {e}")

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
#   K E Y B O A R D S
# ────────────────────────────────────────────────────────────────────────────────

# TTS Keyboards (unchanged)
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
        InlineKeyboardButton("⬆️ Higher (+50)", callback_data="pitch_set|+50"),
        InlineKeyboardButton("⬇️ Lower (-50)", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset Pitch (0)", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Faster (+50)", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Slower (-50)", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset Speed (0)", callback_data="rate_set|0")
    )
    return markup

# STT Keyboards (adapted from Bot 2)
def build_stt_language_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    row = []
    for i, name in enumerate(STT_LANGUAGES.keys(), 1):
        row.append(KeyboardButton(name))
        if i % 3 == 0:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    return markup

# Admin Keyboard (Unified)
def build_admin_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True) # Persistent keyboard
    markup.row("Total Users", "Send Broadcast")
    markup.row("View Bot Status", "Build New Bot") # Renamed /status to "View Bot Status", Added "Build New Bot"
    # Toggle STT feature
    if stt_feature_enabled:
        markup.add("Deactivate STT")
    else:
        markup.add("Activate STT")
    return markup

# User Main Menu Keyboard (Conditional on STT status)
def build_user_main_menu_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🗣️ Text-to-Speech", callback_data="show_tts_options")
    )
    if stt_feature_enabled:
        markup.add(
            InlineKeyboardButton("🎙️ Speech-to-Text", callback_data="show_stt_options")
        )
    markup.add(
        InlineKeyboardButton("❓ How to use", callback_data="show_help"),
        InlineKeyboardButton("🔒 Privacy Policy", callback_data="show_privacy")
    )
    return markup

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   H A N D L E R S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    user_id_str = str(user_id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

    # Ensure user is in local_user_data and DB
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "chat_id": user_id,
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "stt_transcription_count": 0
        }
        try:
            users_collection.insert_one(local_user_data[user_id_str])
            logging.info(f"New user {user_id} inserted into MongoDB.")
        except Exception as e:
            logging.error(f"Error inserting new user {user_id} into DB: {e}")
    else:
        update_user_activity_db(user_id)

    # Ensure all user-specific modes are OFF on /start
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    admin_state[user_id] = None # Clear any admin-specific state

    if user_id == ADMIN_ID:
        # Admin Panel
        sent_message = bot.send_message(
            message.chat.id,
            "👋 Welcome, Admin! Manage your bot's features from here.",
            reply_markup=build_admin_keyboard()
        )
        # Start uptime thread for admin
        with admin_uptime_lock:
            if (
                admin_uptime_message.get(ADMIN_ID)
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                pass # Uptime thread is already running
            else:
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
        # Regular User Welcome
        if message.chat.type == 'private' and not check_subscription(user_id):
            send_subscription_message(message.chat.id)
            return

        welcome_message = (
            f"👋 Hey there, {user_first_name}! I'm your go-to bot for converting text into realistic AI voices, and also "
            f"transcribing your voice, audio, and video files! 🔊🎙️\n\n"
            "Choose a service below to get started:"
        )
        bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=build_user_main_menu_keyboard(),
            parse_mode="Markdown"
        )

@bot.callback_query_handler(func=lambda c: c.data == "show_help")
def show_help_callback(call):
    user_id = call.from_user.id
    update_user_activity_db(user_id)

    if call.message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure all user-specific modes are OFF
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None
    admin_state[user_id] = None

    help_text = (
        """
📚 *How to Use This Bot*

Ready to turn your text into speech or your speech into text? Here's how it works:

---
### 🗣️ Text-to-Speech (TTS)
1.  **Choose a Voice:** Tap on *'Text-to-Speech'* from the main menu, then select a language and a specific voice.
2.  **Send Your Text:** Once you've chosen a voice, simply send any text message. The bot will transform it into an audio clip.
3.  **Fine-Tune Your Voice:**
    * Use /pitch to **adjust the tone** of the voice, making it higher or lower.
    * Use /rate to **change the speaking speed**, making it faster or slower.

---
### 🎙️ Speech-to-Text (STT) *(This feature is currently {stt_status})*
1.  **Select Language:** Tap on *'Speech-to-Text'* from the main menu, then choose the language of your audio.
2.  **Send Your Media:** Send your voice message, audio file (MP3, WAV, etc.), or video note.
3.  **Receive Transcription:** The bot will process your file and send you the transcribed text.
    * **Supported File Size:** Up to 20MB.

---
### 🔒 Privacy & Data Handling
* **Your Text/Audio is Private:** Any text you send for TTS or audio you send for STT is processed instantly and **never stored** on our servers. The generated audio/transcribed text files are also temporary and are automatically deleted after they're sent to you.
* **Your Settings are Saved:** To make your experience seamless, we securely store your Telegram User ID and your chosen preferences (like your selected TTS voice, pitch, rate, and STT language) in our database. This ensures your settings are remembered for future use. We also keep a record of basic activity (such as your last active timestamp and the number of conversions/transcriptions you've made) for anonymous, aggregated statistics to help us improve the bot.

---
If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy creating amazing voices and transcribing your audios! ✨
"""
    )
    stt_status = "ACTIVE" if stt_feature_enabled else "INACTIVE (Admin controlled)"
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=help_text.format(stt_status=stt_status),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "show_privacy")
def privacy_notice_callback(call):
    user_id = call.from_user.id
    update_user_activity_db(user_id)

    if call.message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure all user-specific modes are OFF
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None
    admin_state[user_id] = None

    privacy_text = (
        """
🔐 *Privacy Notice: Your Data & This Bot*

Your privacy is incredibly important to us. This notice explains exactly how your data is handled in real-time when you use this bot.

1.  **Data We Process & Its Lifecycle:**
    * **Text for Speech Synthesis:** When you send text to be converted into speech, it's processed immediately to generate the audio. Crucially, this text is **not stored** on our servers after processing. The generated audio file is also temporary and is deleted right after it's sent to you.
    * **Audio/Video for Transcription:** When you send voice, audio, or video files for transcription, they are temporarily uploaded to AssemblyAI for processing. These files are **not stored on our servers** after transcription. The transcribed text is also not stored by us after being sent to you.
    * **User IDs, Preferences, and Activity Data:** We store your Telegram User ID and your personal preferences (your chosen TTS voice, pitch, rate, and STT language) in our MongoDB database. We also log basic activity like your "last active" timestamp and a count of your TTS conversions and STT transcriptions. This helps us remember your settings for a consistent experience and allows us to gather anonymous, aggregated statistics to understand overall bot usage and improve our services. This data is also cached in memory for fast access during bot operation and is regularly updated in MongoDB.

2.  **How Your Data is Used:**
    * **Core Service Delivery:** To perform the bot's primary function: converting your text into speech and transcribing your media.
    * **Service Improvement:** To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total TTS conversions, total STT transcriptions).
    * **Personalization:** To maintain your preferred voice settings and STT language across your sessions.

3.  **Data Sharing Policy:**
    * We have a strict **no-sharing policy**. We **do not share** your personal data or text/audio input with any third parties for marketing or any other purposes.
    * Text-to-speech functionality is powered by the Microsoft Cognitive Services Speech API. Speech-to-text functionality is powered by AssemblyAI. While your input is sent to these models for processing, we ensure that your data is **not stored by us** after it has been processed by these external services. Their own privacy policies govern how they handle the data during the conversion process.

4.  **Data Retention:**
    * **Text input, audio/video input, and generated output files:** These are **deleted immediately** after processing and delivery.
    * **User IDs and preferences:** This data is stored in MongoDB to support your settings and for anonymous usage statistics. This data is also cached in memory for performance. If you wish to have your stored preferences removed, you can simply stop using the bot. For explicit data deletion requests, please contact the bot administrator.

By using this bot, you confirm that you understand and agree to the data practices outlined in this Privacy Notice.

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    )
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=privacy_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_to_main_menu")
def back_to_main_menu_callback(call):
    user_id = call.from_user.id
    user_first_name = call.from_user.first_name if call.from_user.first_name else "There"
    update_user_activity_db(user_id)

    if call.message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Clear user states
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None
    admin_state[user_id] = None

    welcome_message = (
        f"👋 Hey there, {user_first_name}! I'm your go-to bot for converting text into realistic AI voices, and also "
        f"transcribing your voice, audio, and video files! 🔊🎙️\n\n"
        "Choose a service below to get started:"
    )
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=welcome_message,
        reply_markup=build_user_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=['status'], func=lambda m: m.from_user.id != ADMIN_ID)
def status_handler_user(message):
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    # Ensure TTS modes are OFF on /status
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

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

        # Get total STT transcriptions from users_collection
        total_stt_conversions_db = users_collection.aggregate([
            {"$group": {"_id": None, "total_stt": {"$sum": "$stt_transcription_count"}}}
        ])
        total_stt_conversions_db = list(total_stt_conversions_db)[0]["total_stt"] if list(total_stt_conversions_db) else 0

        pipeline = [
            {"$match": {"type": "tts"}}, # Only sum processing time for TTS
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result = list(processing_stats_collection.aggregate(pipeline))
        total_tts_proc_seconds = agg_result[0]["total_time"] if agg_result else 0
    except Exception as e:
        logging.error(f"Error fetching processing stats from DB: {e}")
        total_tts_conversions_db = 0
        total_stt_conversions_db = 0
        total_tts_proc_seconds = 0

    tts_proc_hours = int(total_tts_proc_seconds) // 3600
    tts_proc_minutes = (int(total_tts_proc_seconds) % 3600) // 60
    tts_proc_seconds = int(total_tts_proc_seconds) % 60

    text = (
        "📊 *Bot Statistics*\n\n"
        "🟢 *Bot Status: Online*\n"
        f"⏱️ The bot has been running for: *{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds*\n\n"
        "👥 *User Statistics*\n"
        f"▫️ Total Active Users Today: *{active_today_count}*\n"
        f"▫️ Total Registered Users: *{total_registered_users}*\n\n"
        "⚙️ *Service Statistics*\n"
        f"▫️ Total Text-to-Speech Conversions: *{total_tts_conversions_db}*\n"
        f"▫️ Total Speech-to-Text Transcriptions: *{total_stt_conversions_db}*\n"
        f"⏱️ Total TTS Processing Time: *{tts_proc_hours} hours {tts_proc_minutes} minutes {tts_proc_seconds} seconds*\n\n"
        "---"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "View Bot Status" and m.from_user.id == ADMIN_ID)
def admin_status_handler(message):
    status_handler_user(message) # Reuse the same status message logic for admin
    bot.send_message(
        message.chat.id,
        "What else, Admin?",
        reply_markup=build_admin_keyboard()
    )


@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users_admin(message):
    total_registered = users_collection.count_documents({}) # Use DB for accurate count
    bot.send_message(message.chat.id, f"Total registered users (from DB): {total_registered}")
    bot.send_message(
        message.chat.id,
        "What else, Admin?",
        reply_markup=build_admin_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_prompt(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast_message'
    bot.send_message(message.chat.id, "Send the broadcast message now. To cancel, type /cancel_broadcast.")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast_message',
    content_types=['text', 'photo', 'video', 'audio', 'document', 'voice', 'sticker', 'video_note']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None # Reset state
    bot.send_message(message.chat.id, "Broadcasting your message now...")

    success = fail = 0
    # Fetch all user chat_ids from MongoDB, excluding the admin
    all_user_ids = [user_doc["chat_id"] for user_doc in users_collection.find({}, {"chat_id": 1})]
    all_user_ids = [uid for uid in all_user_ids if uid != ADMIN_ID]

    for uid in all_user_ids:
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
        time.sleep(0.05) # Small delay to respect Telegram API limits

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )
    bot.send_message(
        message.chat.id,
        "What else, Admin?",
        reply_markup=build_admin_keyboard()
    )

@bot.message_handler(commands=['cancel_broadcast'], func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast_message')
def cancel_broadcast(message):
    admin_state[message.from_user.id] = None
    bot.send_message(
        message.chat.id,
        "Broadcast cancelled. What else, Admin?",
        reply_markup=build_admin_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "Activate STT" and m.from_user.id == ADMIN_ID)
def activate_stt(message):
    update_stt_feature_status(True)
    bot.send_message(
        message.chat.id,
        "✅ Speech-to-Text feature is now *Active* for all users.",
        reply_markup=build_admin_keyboard(),
        parse_mode="Markdown"
    )
    # Also update user commands for all users
    set_bot_commands()

@bot.message_handler(func=lambda m: m.text == "Deactivate STT" and m.from_user.id == ADMIN_ID)
def deactivate_stt(message):
    update_stt_feature_status(False)
    bot.send_message(
        message.chat.id,
        "❌ Speech-to-Text feature is now *Deactivated* for all users.",
        reply_markup=build_admin_keyboard(),
        parse_mode="Markdown"
    )
    # Also update user commands to hide STT related commands
    set_bot_commands()

# ────────────────────────────────────────────────────────────────────────────────
#   T T S   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "show_tts_options")
def show_tts_options_callback(call):
    user_id = call.from_user.id
    update_user_activity_db(user_id)

    if call.message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure TTS modes are OFF before selecting voice
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="First, choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

    bot.send_message(message.chat.id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[str(uid)] = None
    user_rate_input_mode[str(uid)] = None

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
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[str(uid)] = None
    user_rate_input_mode[str(uid)] = None

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_db(uid, voice)

    user_tts_mode[str(uid)] = voice # Set user into TTS input mode

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[str(uid)] = None
    user_pitch_input_mode[str(uid)] = None
    user_rate_input_mode[str(uid)] = None

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[str(uid)] = None
    user_pitch_input_mode[str(uid)] = None
    user_rate_input_mode[str(uid)] = "awaiting_rate_input" # Set state for rate input

    bot.send_message(
        message.chat.id,
        "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:",
        reply_markup=make_rate_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_rate_input_mode[str(uid)] = None # Clear state

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)

        set_tts_user_rate_db(uid, rate_value)

        bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate from callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[str(uid)] = None
    user_pitch_input_mode[str(uid)] = "awaiting_pitch_input" # Set state for pitch input
    user_rate_input_mode[str(uid)] = None

    bot.send_message(
        message.chat.id,
        "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:",
        reply_markup=make_pitch_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and str(uid) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[str(uid)] = None # Clear state

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)

        set_tts_user_pitch_db(uid, pitch_value)

        bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /voice to pick a different voice.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch from callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

async def synth_and_send_tts(chat_id: int, user_id: int, text: str):
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

    processing_start_time = datetime.now()

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
                "user_id": str(user_id), # Store as string for consistency
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
                "user_id": str(user_id),
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
                "user_id": str(user_id),
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
#   S T T   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "show_stt_options")
def show_stt_options_callback(call):
    user_id = call.from_user.id
    update_user_activity_db(user_id)

    if call.message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Clear TTS related states
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

    if not stt_feature_enabled and user_id != ADMIN_ID:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⚠️ Speech-to-Text feature is currently *deactivated* by the admin. Please try again later.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
        )
        bot.answer_callback_query(call.id)
        return

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose your Media (Voice, Audio, Video) file language for transcription using the buttons below:",
        reply_markup=build_stt_language_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['language'])
def send_stt_language_prompt(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    if not stt_feature_enabled and user_id != ADMIN_ID:
        bot.send_message(
            chat_id,
            "⚠️ Speech-to-Text feature is currently *deactivated* by the admin. Please try again later.",
            parse_mode="Markdown"
        )
        return

    # Clear TTS related states
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

    bot.send_message(
        chat_id,
        "Choose your Media (Voice, Audio, Video) file language for transcription using the buttons below:",
        reply_markup=build_stt_language_keyboard()
    )

@bot.message_handler(func=lambda msg: msg.text in STT_LANGUAGES)
def save_user_stt_language(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    if not stt_feature_enabled and user_id != ADMIN_ID:
        bot.send_message(
            chat_id,
            "⚠️ Speech-to-Text feature is currently *deactivated* by the admin. Please try again later.",
            parse_mode="Markdown"
        )
        return

    # Clear TTS related states
    user_tts_mode[str(user_id)] = None
    user_pitch_input_mode[str(user_id)] = None
    user_rate_input_mode[str(user_id)] = None

    lang_code = STT_LANGUAGES[message.text]
    set_stt_user_language_db(user_id, lang_code)

    bot.send_message(
        chat_id,
        f"✅ Transcription Language Set: {message.text}\n\n"
        "🎙️ Please send your voice message, audio file, or video note, and I’ll transcribe it for you with precision.\n\n"
        "📁 Supported file size: Up to 20MB\n\n"
        "📞 Need text to audio Bot for free use: @txt_2_voice_Bot",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]])
    )


async def process_stt_media(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    # Re-check subscription for media handling too
    if message.chat.type == 'private' and str(user_id) != str(ADMIN_ID) and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    if not stt_feature_enabled and user_id != ADMIN_ID:
        bot.send_message(
            chat_id,
            "⚠️ Speech-to-Text feature is currently *deactivated* by the admin. Please try again later.",
            parse_mode="Markdown"
        )
        return

    lang_code = get_stt_user_language_db(user_id)
    if not lang_code:
        bot.send_message(chat_id, "❗ Please select a language first using /language or by tapping 'Speech-to-Text' from the main menu, before sending a file.")
        return

    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing_event))
    typing_thread.daemon = True
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = bot.reply_to(message, "⏳ Processing your media file. This might take a moment...")

        file_id = None
        if message.voice:
            file_id = message.voice.file_id
        elif message.audio:
            file_id = message.audio.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.document and (message.document.mime_type.startswith('audio/') or message.document.mime_type.startswith('video/')):
            file_id = message.document.file_id

        if not file_id:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "Unsupported file type. Please send a voice, audio, or video file for transcription.")
            return

        file_info = bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "⚠️ File is too large. Max allowed size for transcription is 20MB.")
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
            bot.reply_to(message, "❌ Failed to upload file to transcription service.")
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
            bot.reply_to(message, f"❌ Transcription initiation error: {res_json.get('error', 'Unknown')}")
            return

        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2)

        bot.delete_message(chat_id, processing_msg.message_id)

        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                bot.reply_to(message, "ℹ️ No transcription text was returned.")
            elif len(text) <= 4000:
                bot.reply_to(message, text)
            else:
                import io
                transcript_file = io.BytesIO(text.encode("utf-8"))
                transcript_file.name = "transcript.txt"
                bot.reply_to(message, "The transcription result is longer than 4000 characters. Here it is as a file:", document=transcript_file)
            increment_stt_transcription_count_db(user_id) # Increment STT count
        else:
            bot.reply_to(message, f"❌ Sorry, transcription failed: {res.get('error', 'Unknown error during transcription.')}")

    except Exception as e:
        logging.error(f"Error handling STT media: {e}")
        if processing_msg:
            bot.delete_message(chat_id, processing_msg.message_id)
        bot.reply_to(message, f"⚠️ An unexpected error occurred during transcription: {str(e)}")
    finally:
        stop_typing_event.set()


# ────────────────────────────────────────────────────────────────────────────────
#   B O T   C O N S T R U C T O R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "Build New Bot" and m.from_user.id == ADMIN_ID)
def handle_build_new_bot(message):
    admin_state[message.from_user.id] = 'awaiting_bot_token'
    bot.send_message(
        message.chat.id,
        "Okay, Admin. To create a new bot configuration, please send me the **Telegram Bot Token** for your new bot.\n\n"
        "To get a new token, talk to @BotFather and use `/newbot`."
    )

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_bot_token', content_types=['text'])
def handle_new_bot_token(message):
    new_token = message.text.strip()
    if not new_token or len(new_token) < 30: # Basic token length check
        bot.send_message(message.chat.id, "That doesn't look like a valid Telegram Bot Token. Please send a correct token.")
        return

    admin_state[message.from_user.id] = {'awaiting_bot_service': new_token}

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Text-to-Speech (TTS)", callback_data="build_service|tts"),
        InlineKeyboardButton("Speech-to-Text (STT)", callback_data="build_service|stt"),
        InlineKeyboardButton("Both (TTS & STT)", callback_data="build_service|both")
    )
    bot.send_message(
        message.chat.id,
        "Great! Now, what service should this new bot provide?",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("build_service|") and c.from_user.id == ADMIN_ID)
def handle_new_bot_service_selection(call):
    admin_data = admin_state.get(call.from_user.id)
    if not isinstance(admin_data, dict) or 'awaiting_bot_service' not in admin_data:
        bot.answer_callback_query(call.id, "Error: Please restart the bot creation process via 'Build New Bot'.")
        return

    new_token = admin_data['awaiting_bot_service']
    _, service_type = call.data.split("|", 1)

    try:
        # Check if a bot with this token already exists in builder_bots_collection
        if builder_bots_collection.find_one({"bot_token": new_token}):
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="A bot with this token already exists in your configurations. Please use a unique token or delete the old one first.",
                parse_mode="Markdown"
            )
            admin_state[call.from_user.id] = None
            bot.answer_callback_query(call.id)
            return

        bot_config = {
            "bot_token": new_token,
            "service_type": service_type,
            "admin_id": call.from_user.id,
            "created_at": datetime.now().isoformat()
        }
        builder_bots_collection.insert_one(bot_config)

        confirmation_message = (
            f"🎉 **New Bot Configuration Created!** 🎉\n\n"
            f"**Token:** `{new_token}`\n"
            f"**Service:** *{service_type.upper()}*\n\n"
            f"To deploy this bot, you would typically need a separate server or platform (like Koyeb or Render) where you can run a new instance of this bot's code using *this specific token*.\n\n"
            f"**Next Steps:**\n"
            f"1.  **Deploy New Instance:** Set up a new deployment environment for a new bot instance.\n"
            f"2.  **Use This Codebase:** You can use a copy of *this bot's codebase* for your new bot instance.\n"
            f"3.  **Set Environment Variables:** For the new instance, set `TOKEN` to `{new_token}` and configure its `WEBHOOK_URL` to its *own unique URL* (e.g., `https://your-new-bot-name.koyeb.app/`).\n"
            f"4.  **Admin ID:** Set the `ADMIN_ID` for the new bot to *your Telegram ID* (`{call.from_user.id}`).\n\n"
            f"This current bot will not manage the new bot directly. Each bot operates independently with its own token."
        )

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=confirmation_message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Error creating new bot config: {e}")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"❌ An error occurred while creating the bot configuration: `{e}`. Please try again.",
            parse_mode="Markdown"
        )
    finally:
        admin_state[call.from_user.id] = None # Clear state
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "What else, Admin?",
            reply_markup=build_admin_keyboard()
        )

# ────────────────────────────────────────────────────────────────────────────────
#   G E N E R A L   M E S S A G E   H A N D L E R   (Prioritized)
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['text', 'voice', 'audio', 'video', 'document', 'photo', 'sticker', 'video_note'])
def handle_all_messages(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_id_str = str(user_id)
    
    update_user_activity_db(user_id)

    # Admin special states
    if user_id == ADMIN_ID:
        # If admin is in broadcast mode, let the broadcast handler take over
        if admin_state.get(user_id) == 'awaiting_broadcast_message':
            broadcast_message(message)
            return
        # If admin is in bot constructor token mode
        if admin_state.get(user_id) == 'awaiting_bot_token':
            handle_new_bot_token(message)
            return
        # Admin sending regular text or media when not in a special state
        if message.text not in ["Total Users", "Send Broadcast", "View Bot Status", "Build New Bot", "Activate STT", "Deactivate STT"]:
            bot.send_message(chat_id, "Admin, please use the admin options or specific commands to interact.", reply_markup=build_admin_keyboard())
        return

    # Non-admin users: Check subscription first
    if message.chat.type == 'private' and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    # Check for user input modes first (pitch/rate for TTS)
    if user_rate_input_mode.get(user_id_str) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate_db(user_id, rate_val)
                bot.send_message(message.chat.id, f"🔊 Voice speed set to *{rate_val}*.", parse_mode="Markdown",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]]))
                user_rate_input_mode[user_id_str] = None
            else:
                bot.send_message(message.chat.id, "❌ Invalid speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            bot.send_message(message.chat.id, "That's not a valid number for speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    if user_pitch_input_mode.get(user_id_str) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_db(user_id, pitch_val)
                bot.send_message(message.chat.id, f"🔊 Voice pitch set to *{pitch_val}*.", parse_mode="Markdown",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main_menu")]]))
                user_pitch_input_mode[user_id_str] = None
            else:
                bot.send_message(message.chat.id, "❌ Invalid pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            bot.send_message(message.chat.id, "That's not a valid number for pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    # If STT feature is enabled AND a media file is sent
    if stt_feature_enabled and (message.voice or message.audio or message.video or (message.document and (message.document.mime_type and (message.document.mime_type.startswith('audio/') or message.document.mime_type.startswith('video/'))))):
        threading.Thread(
            target=lambda: asyncio.run(process_stt_media(message))
        ).start()
        return # Handled by STT

    # If a text message is sent (and not handled by pitch/rate input)
    if message.content_type == 'text':
        # If user is in TTS mode (meaning they selected a voice) or default, process as TTS
        current_voice = get_tts_user_voice_db(user_id)
        if current_voice:
            if len(message.text) > 1000:
                bot.send_message(message.chat.id, "Please keep your text under 1000 characters for text-to-speech conversion.")
                return
            threading.Thread(
                target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id, message.text))
            ).start()
            return
        else:
            # If no specific mode is active, prompt user to choose
            bot.send_message(
                message.chat.id,
                "Looks like you haven't chosen a service yet! Please select an option from the menu below to get started.",
                reply_markup=build_user_main_menu_keyboard()
            )
            return

    # Fallback for unsupported content types (e.g., photos, stickers, unrecognized documents)
    if not (message.voice or message.audio or message.video or message.document):
        bot.send_message(
            message.chat.id,
            "Sorry, I can only convert *text messages* into AI voices, or transcribe *voice, audio, or video files*. "
            "Please send me something I can work with, or choose an option from the menu!",
            reply_markup=build_user_main_menu_keyboard()
        )
        return

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
    Sets the list of commands for the bot using set_my_commands, conditionally
    including STT commands based on global setting.
    """
    commands = [
        BotCommand("start", "Get Started"),
        BotCommand("voice", "Choose a different voice (TTS)"),
        BotCommand("pitch", "Change pitch (TTS)"),
        BotCommand("rate", "Change speed (TTS)"),
        BotCommand("status", "Bot stats"),
        BotCommand("help", "❓ How to use the bot"),
        BotCommand("privacy", "🔒 Read privacy notice"),
    ]
    if stt_feature_enabled:
        commands.append(BotCommand("language", "Set transcription language (STT)"))

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
    connect_to_mongodb() # Connect to DB and load initial settings
    set_webhook_on_startup()
    set_bot_commands() # Set commands based on initial STT feature state

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"): # Create a simple directory for temporary TTS files
        os.makedirs("tts_audio_cache")
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
