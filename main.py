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
TOKEN = "7999849691:AAFE7cMt2cyFMjZuQoXLObXfe58Ao1DMVvc"  # <-- Main Bot Token (Example: Replace with your actual main bot token)
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://dominant-fidela-wwmahe-2264ea75.koyeb.app/" # Main Bot Webhook

REQUIRED_CHANNEL = "@transcriber_bot_news_channel"

bot = telebot.TeleBot(TOKEN, threaded=True) # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # AssemblyAI for STT

# --- MONGODB CONFIGURATION ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

mongo_client: MongoClient = None
db = None
users_collection = None
tts_users_collection = None
processing_stats_collection = None
stt_users_collection = None # New: For STT user settings (language)
registered_bots_collection = None # New: To manage child bots

# --- In-memory caches ---
local_user_data = {}            # { user_id: { "last_active": "...", "tts_conversion_count": N, "stt_conversion_count": N, ... } }
_tts_voice_cache = {}           # { user_id: voice_name }
_tts_pitch_cache = {}           # { user_id: pitch_value }
_tts_rate_cache = {}            # { user_id: rate_value }
_stt_lang_cache = {}            # { user_id: lang_code } # New: For STT language

# --- User state for input modes ---
user_tts_mode = {}              # { user_id: voice_name (e.g. "en-US-AriaNeural") or None }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }
user_bot_creation_state = {}    # { user_id: {"step": "awaiting_token" | "awaiting_service", "token": "..."} } # New: For bot creation flow

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()
admin_state = {}

# Placeholder for keeping track of typing/recording threads
processing_message_ids = {}

# --- Supported STT Languages ---
STT_LANGUAGES = { # Renamed for clarity and to avoid conflict
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaycan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇭": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Somali 🇸🇴": "so" # Added Somali based on TTS voices
}

# --- Child Bot Management ---
child_bots: dict[str, telebot.TeleBot] = {} # {bot_token: telebot.TeleBot instance}
child_bot_polling_thread = None
child_bot_polling_stop_event = threading.Event()

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    """
    Connect to MongoDB at startup, set up collections and indexes.
    Also, load all user data into in-memory caches.
    """
    global mongo_client, db
    global users_collection, tts_users_collection, processing_stats_collection, stt_users_collection, registered_bots_collection
    global local_user_data, _tts_voice_cache, _tts_pitch_cache, _tts_rate_cache, _stt_lang_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db["users"]
        tts_users_collection = db["tts_users"]
        processing_stats_collection = db["processing_stats"] # Renamed collection for clarity, now stores both TTS & STT stats
        stt_users_collection = db["stt_users"] # New collection for STT preferences
        registered_bots_collection = db["registered_bots"] # New collection for child bots

        # Create indexes (if not already created)
        users_collection.create_index([("last_active", ASCENDING)])
        tts_users_collection.create_index([("_id", ASCENDING)])
        stt_users_collection.create_index([("_id", ASCENDING)]) # New index
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])
        registered_bots_collection.create_index([("token", ASCENDING)], unique=True) # New index

        logging.info("Connected to MongoDB and indexes created. Loading data to memory...")

        # --- Load all user data into in-memory caches on startup ---
        for user_doc in users_collection.find({}):
            local_user_data[user_doc["_id"]] = user_doc
        logging.info(f"Loaded {len(local_user_data)} user documents into local_user_data.")

        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[tts_user["_id"]] = tts_user.get("voice", "so-SO-MuuseNeural") # Default to Somali for TTS
            _tts_pitch_cache[tts_user["_id"]] = tts_user.get("pitch", 0)
            _tts_rate_cache[tts_user["_id"]] = tts_user.get("rate", 0)
        logging.info(f"Loaded {len(_tts_voice_cache)} TTS voice, pitch, and rate settings.")

        for stt_user in stt_users_collection.find({}): # Load STT user settings
            _stt_lang_cache[stt_user["_id"]] = stt_user.get("language_code", "en") # Default to English for STT
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
    Also ensures new users are created with default counts.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0 # Initialize for new users
        }
    else:
        local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id_str},
            {"$set": {"last_active": now_iso},
             "$setOnInsert": {"tts_conversion_count": 0, "stt_conversion_count": 0}}, # Ensure these fields exist on insert
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

def increment_processing_count_db(user_id: str, service_type: str):
    """
    Increment either tts_conversion_count or stt_conversion_count.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()
    field_to_inc = f"{service_type}_conversion_count"

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    local_user_data[user_id_str][field_to_inc] = local_user_data[user_id_str].get(field_to_inc, 0) + 1
    local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id_str},
            {
                "$inc": {field_to_inc: 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing {service_type} conversion count for {user_id_str} in DB: {e}")


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

def get_stt_user_lang_db(user_id: str) -> str:
    """
    Return STT language from cache (default "en").
    """
    return _stt_lang_cache.get(user_id, "en")

def set_stt_user_lang_db(user_id: str, lang_code: str):
    """
    Save STT language in DB and update cache.
    """
    _stt_lang_cache[user_id] = lang_code # Update in-memory cache
    try:
        stt_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"language_code": lang_code}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting STT language for {user_id} in DB: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   U T I L I T I E S   (keep typing, keep recording, update uptime)
# ────────────────────────────────────────────────────────────────────────────────

def keep_recording(target_bot, chat_id, stop_event):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action for bot {target_bot.token}: {e}")
            break

def keep_typing(target_bot, chat_id, stop_event): # New: For STT
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action for bot {target_bot.token}: {e}")
            break

def update_uptime_message(chat_id, message_id):
    """
    Live-update the admin uptime message every second.
    """
    bot_start_time = datetime.now() # Initialize here or pass from main
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

def check_subscription(target_bot, user_id: int) -> bool:
    """
    If REQUIRED_CHANNEL is set, verify user is a member.
    """
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = target_bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id} with bot {target_bot.token}: {e}")
        return False

def send_subscription_message(target_bot, chat_id: int):
    """
    Prompt user to join REQUIRED_CHANNEL.
    """
    # Only send subscription message if it's a private chat
    if target_bot.get_chat(chat_id).type == 'private':
        if not REQUIRED_CHANNEL:
            return
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                "Click here to join the channel",
                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
            )
        )
        target_bot.send_message(
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
#   T T S   F U N C T I O N S (Common to main and child bots)
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
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-FabriceNeural", "fr-CH-GerardNeural"
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
        InlineKeyboardButton("⬆️ Higher", callback_data="pitch_set|+50"),
        InlineKeyboardButton("⬇️ Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset Pitch", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Faster", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Slower", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset Speed", callback_data="rate_set|0")
    )
    return markup

async def synth_and_send_tts(target_bot, chat_id: int, user_id: str, text: str):
    """
    Use MSSpeech to synthesize text -> mp3, send and delete file.
    """
    # Replace periods with commas for faster speech output
    text = text.replace('.', ',')

    # Get user settings from DB/cache. Note: For child bots, these settings should ideally be per child-bot user,
    # but for simplicity, they currently share the main bot's user settings.
    voice = get_tts_user_voice_db(user_id)
    pitch = get_tts_user_pitch_db(user_id)
    rate = get_tts_user_rate_db(user_id)
    filename = f"tts_{user_id}_{uuid.uuid4()}.mp3"

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(target_bot, chat_id, stop_recording))
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
            target_bot.send_message(chat_id, "❌ Hmm, I couldn't generate the audio file. It might be empty or corrupted. Please try again with different text.")
            return

        with open(filename, "rb") as f:
            target_bot.send_audio(
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
        increment_processing_count_db(user_id, "tts") # Increment user's TTS count

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
        target_bot.send_message(chat_id, f"❌ I ran into a problem while synthesizing the voice: `{e}`. Please try again, or try a different voice.", parse_mode="Markdown")
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
        target_bot.send_message(chat_id, "❌ Oops! An unexpected error occurred during text-to-speech conversion. Please try again in a moment.")
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
#   S T T   F U N C T I O N S (Common to main and child bots)
# ────────────────────────────────────────────────────────────────────────────────

def build_stt_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    # Sort languages alphabetically for consistent display
    sorted_languages = sorted(STT_LANGUAGES.items(), key=lambda item: item[0])
    for lang_name, lang_code in sorted_languages:
        buttons.append(
            InlineKeyboardButton(lang_name, callback_data=f"stt_lang|{lang_code}")
        )
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup


async def process_stt_media(target_bot, chat_id: int, user_id: str, message_type: str, file_id: str):
    """
    Handles downloading media, uploading to AssemblyAI, and transcribing.
    """
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(target_bot, chat_id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, "⏳ Processing your media for transcription...")

        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ File is too large. Max size for transcription is 20MB.")
            return

        file_url = f"https://api.telegram.org/file/bot{target_bot.token}/{file_info.file_path}"
        file_data_response = requests.get(file_url, stream=True)
        file_data_response.raise_for_status() # Raise an exception for bad status codes

        # Directly send bytes to AssemblyAI
        processing_start_time = datetime.now()

        upload_res = requests.post("https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY, "Content-Type": "application/octet-stream"},
            data=file_data_response.content)
        upload_res.raise_for_status()
        audio_url = upload_res.json().get('upload_url')

        if not audio_url:
            raise Exception("AssemblyAI upload failed: No upload_url received.")

        lang_code = get_stt_user_lang_db(user_id)

        transcript_res = requests.post("https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"},
            json={"audio_url": audio_url, "language_code": lang_code, "speech_model": "best"})
        transcript_res.raise_for_status()
        transcript_id = transcript_res.json().get("id")

        if not transcript_id:
            raise Exception("AssemblyAI transcription request failed: No transcript ID received.")

        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2)

        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                target_bot.send_message(chat_id, "ℹ️ No transcription returned for this media.")
            elif len(text) <= 4000: # Telegram message limit
                target_bot.send_message(chat_id, text)
            else:
                import io
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="Your transcription is too long for a single message. Here's the text file:")
            increment_processing_count_db(user_id, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Transcription error: `{error_msg}`", parse_mode="Markdown")
            status = "fail_assemblyai_error"
            logging.error(f"AssemblyAI transcription failed for user {user_id}: {error_msg}")

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "stt",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "file_type": message_type,
                "file_size": file_info.file_size,
                "language_code": lang_code,
                "error_message": res.get("error") if status.startswith("fail") else None
            })
        except Exception as e:
            logging.error(f"Error inserting STT processing stat: {e}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Network or API error during STT processing for user {user_id}: {e}")
        target_bot.send_message(chat_id, "❌ A network error occurred while processing your file. Please try again.")
        status = "fail_network_error"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "stt",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "file_type": message_type,
                "file_size": file_info.file_size if 'file_info' in locals() else 0,
                "language_code": get_stt_user_lang_db(user_id),
                "error_message": str(e)
            })
        except Exception as e2:
            logging.error(f"Error inserting STT processing stat (network error): {e2}")

    except Exception as e:
        logging.exception(f"Unhandled error during STT processing for user {user_id}: {e}")
        target_bot.send_message(chat_id, "❌ Oops! An unexpected error occurred during transcription. Please try again in a moment.")
        status = "fail_unknown"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        try:
            processing_stats_collection.insert_one({
                "user_id": user_id,
                "type": "stt",
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "file_type": message_type,
                "file_size": file_info.file_size if 'file_info' in locals() else 0,
                "language_code": get_stt_user_lang_db(user_id),
                "error_message": str(e)
            })
        except Exception as e2:
            logging.error(f"Error inserting STT processing stat (unknown error): {e2}")
    finally:
        stop_typing.set()
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")


# ────────────────────────────────────────────────────────────────────────────────
#   C H I L D   B O T   C L A S S   &   M A N A G E M E N T
# ────────────────────────────────────────────────────────────────────────────────

class ChildBot:
    def __init__(self, token: str, service_type: str, owner_id: int):
        self.token = token
        self.service_type = service_type
        self.owner_id = owner_id
        self.bot_instance = telebot.TeleBot(self.token, threaded=True)
        self.setup_handlers()
        logging.info(f"Child bot {self.token[:5]}... initialized for {service_type} service.")

    def setup_handlers(self):
        # Universal /start and /help for child bots
        @self.bot_instance.message_handler(commands=['start', 'help'])
        def child_start_help(message):
            if not check_subscription(self.bot_instance, message.from_user.id):
                send_subscription_message(self.bot_instance, message.chat.id)
                return

            welcome_message = f"👋 Hello! I am your personal {self.service_type.upper()} bot."
            if self.service_type == "tts":
                welcome_message += "\nSend me text and I'll convert it to speech. Use /voice, /pitch, /rate to customize."
            elif self.service_type == "stt":
                welcome_message += "\nSend me voice/audio/video and I'll transcribe it. Use /language_stt to set the language."
            self.bot_instance.send_message(message.chat.id, welcome_message)

        if self.service_type == "tts":
            # TTS specific handlers for child bot
            @self.bot_instance.message_handler(commands=['voice'])
            def cmd_tts_voice_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                self.bot_instance.send_message(message.chat.id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

            @self.bot_instance.message_handler(commands=['pitch'])
            def cmd_tts_pitch_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                # Simulate user_pitch_input_mode for child bot logic
                user_pitch_input_mode[str(message.from_user.id)] = "awaiting_pitch_input"
                self.bot_instance.send_message(message.chat.id, "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:", reply_markup=make_pitch_keyboard())

            @self.bot_instance.message_handler(commands=['rate'])
            def cmd_tts_rate_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                # Simulate user_rate_input_mode for child bot logic
                user_rate_input_mode[str(message.from_user.id)] = "awaiting_rate_input"
                self.bot_instance.send_message(self.bot_instance, message.chat.id, "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:", reply_markup=make_rate_keyboard())


            @self.bot_instance.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
            def on_tts_language_select_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                _, lang_name = call.data.split("|", 1)
                self.bot_instance.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"Great! Now select a specific *voice* from the {lang_name} options below. 👇",
                    reply_markup=make_tts_voice_keyboard_for_language(lang_name),
                    parse_mode="Markdown"
                )
                self.bot_instance.answer_callback_query(call.id)

            @self.bot_instance.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
            def on_tts_voice_change_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                uid = str(call.from_user.id)
                _, voice = call.data.split("|", 1)
                set_tts_user_voice_db(uid, voice)
                current_pitch = get_tts_user_pitch_db(uid)
                current_rate = get_tts_user_rate_db(uid)

                self.bot_instance.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
                self.bot_instance.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"🔊 Perfect! You're now using: *{voice}*.\n\n"
                         f"Current settings:\n"
                         f"• Pitch: *{current_pitch}*\n"
                         f"• Speed: *{current_rate}*\n\n"
                         f"Ready to speak? Just send me your text!",
                    parse_mode="Markdown",
                    reply_markup=None
                )

            @self.bot_instance.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
            def on_tts_back_to_languages_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                self.bot_instance.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text="Choose the *language* for your voice. 👇",
                    reply_markup=make_tts_language_keyboard(),
                    parse_mode="Markdown"
                )
                self.bot_instance.answer_callback_query(call.id)

            @self.bot_instance.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
            def on_pitch_set_callback_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                uid = str(call.from_user.id)
                user_pitch_input_mode[uid] = None
                try:
                    _, pitch_value_str = call.data.split("|", 1)
                    pitch_value = int(pitch_value_str)
                    set_tts_user_pitch_db(uid, pitch_value)
                    self.bot_instance.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
                    self.bot_instance.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /voice to pick a different voice.",
                        parse_mode="Markdown",
                        reply_markup=None
                    )
                except ValueError:
                    self.bot_instance.answer_callback_query(call.id, "Invalid pitch value.")
                except Exception as e:
                    logging.error(f"Error setting pitch from callback for child bot {self.token}: {e}")
                    self.bot_instance.answer_callback_query(call.id, "An error occurred.")

            @self.bot_instance.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
            def on_rate_set_callback_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                uid = str(call.from_user.id)
                user_rate_input_mode[uid] = None
                try:
                    _, rate_value_str = call.data.split("|", 1)
                    rate_value = int(rate_value_str)
                    set_tts_user_rate_db(uid, rate_value)
                    self.bot_instance.answer_callback_query(call.id, f"Speed set to {rate_value}!")
                    self.bot_instance.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
                        parse_mode="Markdown",
                        reply_markup=None
                    )
                except ValueError:
                    self.bot_instance.answer_callback_query(call.id, "Invalid speed value.")
                except Exception as e:
                    logging.error(f"Error setting rate from callback for child bot {self.token}: {e}")
                    self.bot_instance.answer_callback_query(call.id, "An error occurred.")

            @self.bot_instance.message_handler(content_types=['text'])
            def handle_text_tts_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                uid = str(message.from_user.id)

                # Check if the user is in the "awaiting rate input" state for this bot
                if user_rate_input_mode.get(uid) == "awaiting_rate_input":
                    try:
                        rate_val = int(message.text)
                        if -100 <= rate_val <= 100:
                            set_tts_user_rate_db(uid, rate_val)
                            self.bot_instance.send_message(message.chat.id, f"🔊 Voice speed set to *{rate_val}*.", parse_mode="Markdown")
                            user_rate_input_mode[uid] = None # Reset the state
                        else:
                            self.bot_instance.send_message(message.chat.id, "❌ Invalid speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
                        return
                    except ValueError:
                        self.bot_instance.send_message(message.chat.id, "That's not a valid number for speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
                        return

                # Check if the user is in the "awaiting pitch input" state for this bot
                if user_pitch_input_mode.get(uid) == "awaiting_pitch_input":
                    try:
                        pitch_val = int(message.text)
                        if -100 <= pitch_val <= 100:
                            set_tts_user_pitch_db(uid, pitch_val)
                            self.bot_instance.send_message(message.chat.id, f"🔊 Voice pitch set to *{pitch_val}*.", parse_mode="Markdown")
                            user_pitch_input_mode[uid] = None # Reset the state
                        else:
                            self.bot_instance.send_message(message.chat.id, "❌ Invalid pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
                        return
                    except ValueError:
                        self.bot_instance.send_message(message.chat.id, "That's not a valid number for pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
                        return

                if len(message.text) > 1000:
                    self.bot_instance.send_message(message.chat.id, "Please keep your text under 1000 characters for text-to-speech conversion.")
                    return
                threading.Thread(target=lambda: asyncio.run(synth_and_send_tts(self.bot_instance, message.chat.id, uid, message.text))).start()

        elif self.service_type == "stt":
            # STT specific handlers for child bot
            @self.bot_instance.message_handler(commands=['language_stt'])
            def cmd_stt_lang_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                self.bot_instance.send_message(message.chat.id, "Choose the *language* for your Speech-to-Text transcription:", reply_markup=build_stt_language_keyboard(), parse_mode="Markdown")

            @self.bot_instance.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
            def on_stt_language_select_child(call):
                if not check_subscription(self.bot_instance, call.from_user.id):
                    send_subscription_message(self.bot_instance, call.message.chat.id)
                    self.bot_instance.answer_callback_query(call.id)
                    return
                uid = str(call.from_user.id)
                _, lang_code = call.data.split("|", 1)
                lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
                set_stt_user_lang_db(uid, lang_code)
                self.bot_instance.answer_callback_query(call.id, f"✅ Language set to {lang_name}!")
                self.bot_instance.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"✅ Transcription language set to: *{lang_name}*\n\n🎙️ Send a voice, audio, or video to transcribe (max 20MB).",
                    parse_mode="Markdown",
                    reply_markup=None
                )

            @self.bot_instance.message_handler(content_types=['voice', 'audio', 'video', 'document'])
            def handle_stt_media_types_child(message):
                if not check_subscription(self.bot_instance, message.from_user.id):
                    send_subscription_message(self.bot_instance, message.chat.id)
                    return
                uid = str(message.from_user.id)
                file_id = None
                message_type = None

                if message.voice:
                    file_id = message.voice.file_id
                    message_type = "voice"
                elif message.audio:
                    file_id = message.audio.file_id
                    message_type = "audio"
                elif message.video:
                    file_id = message.video.file_id
                    message_type = "video"
                elif message.document:
                    if message.document.mime_type and (message.document.mime_type.startswith('audio/') or message.document.mime_type.startswith('video/')):
                        file_id = message.document.file_id
                        message_type = "document_media"
                    else:
                        self.bot_instance.send_message(message.chat.id, "Sorry, I can only transcribe audio and video files. Please send a valid audio or video document.")
                        return

                if not file_id:
                    self.bot_instance.send_message(message.chat.id, "Unsupported file type for transcription. Please send a voice message, audio file, or video file.")
                    return

                if uid not in _stt_lang_cache:
                    self.bot_instance.send_message(message.chat.id, "❗ Please choose a language for transcription first using /language_stt.")
                    return

                threading.Thread(
                    target=lambda: asyncio.run(process_stt_media(self.bot_instance, message.chat.id, uid, message_type, file_id))
                ).start()

        # Fallback for unsupported messages in child bots
        @self.bot_instance.message_handler(func=lambda m: True, content_types=['text', 'sticker', 'photo'])
        def handle_unsupported_child(message):
            if not check_subscription(self.bot_instance, message.from_user.id):
                send_subscription_message(self.bot_instance, message.chat.id)
                return

            if message.text and message.text.startswith('/'): # Ignore commands not explicitly handled
                return

            if self.service_type == "tts":
                # If it's a TTS bot and text is sent, process it
                if message.text:
                    if len(message.text) > 1000:
                        self.bot_instance.send_message(message.chat.id, "Please keep your text under 1000 characters for text-to-speech conversion.")
                        return
                    threading.Thread(
                        target=lambda: asyncio.run(synth_and_send_tts(self.bot_instance, message.chat.id, str(message.from_user.id), message.text))
                    ).start()
                else:
                    self.bot_instance.send_message(message.chat.id, "I'm a TTS bot! Please send me text to convert to speech.")
            elif self.service_type == "stt":
                self.bot_instance.send_message(message.chat.id, "I'm an STT bot! Please send me voice messages, audio files, or video files to transcribe.")
            else:
                self.bot_instance.send_message(message.chat.id, "I'm not sure how to handle that. Please refer to my /help for supported features.")

    def run_polling(self):
        logging.info(f"Starting polling for child bot {self.token[:5]}...")
        self.bot_instance.infinity_polling(timeout=10, long_polling_timeout=5)


def start_child_bot_polling():
    """
    Background thread to check for and start new child bots.
    """
    while not child_bot_polling_stop_event.is_set():
        try:
            registered_bots = registered_bots_collection.find({"active": True})
            for bot_doc in registered_bots:
                token = bot_doc["token"]
                if token not in child_bots:
                    service_type = bot_doc["service_type"]
                    owner_id = bot_doc["owner_id"]
                    try:
                        child_bot = ChildBot(token, service_type, owner_id)
                        child_bots[token] = child_bot
                        logging.info(f"Successfully registered and started polling for new child bot: {token[:5]}... ({service_type})")
                        # Start polling for this new bot in a separate thread
                        threading.Thread(target=child_bot.run_polling, daemon=True).start()
                    except Exception as e:
                        logging.error(f"Failed to start child bot {token[:5]}...: {e}")
                        # Mark as inactive or log error in DB
                        registered_bots_collection.update_one({"token": token}, {"$set": {"active": False, "error": str(e)}})
                else:
                    # Check if an existing bot should be deactivated
                    if not bot_doc.get("active", True) and token in child_bots:
                        logging.info(f"Deactivating child bot {token[:5]}...")
                        # In a real scenario, you'd need a way to gracefully stop `infinity_polling`.
                        # telebot doesn't have a direct stop() method for infinity_polling.
                        # For now, we'll just remove it from `child_bots`, and it will eventually
                        # stop trying to process updates for it (or you'd restart the main app).
                        del child_bots[token]
                        logging.info(f"Child bot {token[:5]} removed from active list.")

            # Remove child bots from `child_bots` that are no longer in DB or marked inactive
            current_db_tokens = {doc["token"] for doc in registered_bots_collection.find({"active": True})}
            tokens_to_remove = [token for token in child_bots if token not in current_db_tokens]
            for token in tokens_to_remove:
                logging.info(f"Stopping and removing child bot {token[:5]}... (no longer active in DB)")
                del child_bots[token] # This assumes infinity_polling will eventually time out/stop
                # A more robust solution for stopping would involve passing a stop_event to infinity_polling.
                # For example: self.bot_instance.stop_polling() if TeleBot had such a method
                # or managing the polling loop more granularly within ChildBot.run_polling.

        except Exception as e:
            logging.error(f"Error in child bot polling thread: {e}")
        time.sleep(10) # Check for new bots every 10 seconds


# ────────────────────────────────────────────────────────────────────────────────
#   M A I N   B O T   H A N D L E R S
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

    # Ensure user is in local_user_data and DB, initialize STT count
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "stt_conversion_count": 0 # Initialize for new users
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
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.from_user.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Ensure all input modes are OFF on /start
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_bot_creation_state[user_id_str] = None # Clear bot creation state
    admin_state[message.from_user.id] = None # Clear admin state

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status", "/create_bot_panel")
        sent_message = bot.send_message(
            message.chat.id,
            "Admin Panel and Uptime (updating live)...",
            reply_markup=keyboard
        )
        global bot_start_time # Use global keyword to access the bot_start_time variable
        bot_start_time = datetime.now()
        with admin_uptime_lock:
            if (
                admin_uptime_message.get(ADMIN_ID)
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                pass # Thread already running, do nothing

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
            f"👋 Hey there, {user_first_name}! I'm your versatile AI voice assistant. I can convert your text to speech (TTS) and your speech/audio to text (STT), all for free! 🔊✍️\n\n"
            "✨ *Here's how to make the most of me:* ✨\n"
            "• Use /voice to **choose your preferred language and voice** for Text-to-Speech.\n"
            "• Experiment with /pitch to **adjust the voice's tone** (higher or lower).\n"
            "• Tweak /rate to **change the speaking speed** (faster or slower).\n"
            "• Use /language_stt to **set the language** for Speech-to-Text, then send me your voice, audio, or video files!\n\n"
            "**Want your own dedicated bot?** Use /create_bot to set one up in seconds!\n\n"
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

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.from_user.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Ensure all input modes are OFF on /help
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None

    help_text = (
        """
📚 *How to Use This Bot*

Ready to turn your text into speech or media into text? Here's how it works:

1.  **Text-to-Speech (TTS) Conversion**
    * **Choose a Voice:** Start by using the /voice command. You can select from a wide range of languages and voices.
    * **Send Your Text:** Once you've chosen a voice, simply send any text message. The bot will process it and reply with an audio clip.
    * **Fine-Tune Your Voice:**
        * Use /pitch to **adjust the tone** of the voice.
        * Use /rate to **change the speaking speed**.

2.  **Speech-to-Text (STT) Conversion**
    * **Set Language:** Use /language_stt to select the language of your audio/video file. This helps me transcribe more accurately!
    * **Send Media:** Send a voice message, audio file, or video file (max 20MB). I'll transcribe it and send you the text.

3.  **Create Your Own Bot**
    * Use /create_bot to launch your own dedicated bot for TTS or STT, managed entirely by me!

4.  **Privacy & Data Handling**
    * **Your Content is Private:** Any text you send for TTS or media you send for STT is processed instantly and **never stored** on our servers. Generated audio files and transcriptions are temporary and deleted after they're sent to you.
    * **Your Settings are Saved:** To make your experience seamless, we securely store your Telegram User ID and your chosen preferences (like selected TTS voice, pitch, rate, and STT language) in our MongoDB database. This ensures your settings are remembered for future use. We also keep a record of basic activity (such as your last active timestamp and usage counts) for anonymous, aggregated statistics to help us improve the bot.

---

If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy creating and transcribing! ✨
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Ensure all input modes are OFF on /privacy
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None

    privacy_text = (
        """
🔐 *Privacy Notice: Your Data & This Bot*

Your privacy is incredibly important to us. This notice explains exactly how your data is handled in real-time when you use this bot.

1.  **Data We Process & Its Lifecycle:**
    * **Text for Speech Synthesis (TTS):** When you send text to be converted into speech, it's processed immediately to generate the audio. Crucially, this text is **not stored** on our servers after processing. The generated audio file is also temporary and is deleted right after it's sent to you.
    * **Media for Speech Recognition (STT):** When you send voice, audio, or video files for transcription, they are processed immediately. These files are **not stored** on our servers after processing. The resulting text transcription is also temporary and deleted after being sent to you.
    * **User IDs, Preferences, and Activity Data:** We store your Telegram User ID and your personal preferences (your chosen TTS voice, pitch, rate, and STT language) in our MongoDB database. We also log basic activity like your "last active" timestamp and a count of your TTS and STT conversions. This helps us remember your settings for a consistent experience and allows us to gather anonymous, aggregated statistics to understand overall bot usage and improve our services. This data is also cached in memory for fast access during bot operation and is regularly updated in MongoDB.
    * **Created Bots Data:** If you use the `/create_bot` feature, we store the Telegram bot token you provide, your Telegram User ID (as the owner), the chosen service type (TTS or STT), and creation metadata in our `registered_bots_collection`. This data is used solely to manage and operate your dedicated bot instance.

2.  **How Your Data is Used:**
    * **Core Service Delivery:** To perform the bot's primary functions: converting your text into speech and transcribing your media into text.
    * **Service Improvement:** To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total TTS/STT conversions).
    * **Personalization:** To maintain your preferred voice settings and STT language across your sessions.
    * **Child Bot Management:** To initialize, run, and manage the dedicated bot instances you create through the `/create_bot` feature.

3.  **Data Sharing Policy:**
    * We have a strict **no-sharing policy**. We **do not share** your personal data or text/media input with any third parties for marketing or any other purposes.
    * Text-to-speech functionality is powered by the Microsoft Cognitive Services Speech API. Speech-to-text functionality is powered by the AssemblyAI API. While your input is sent to these models for processing, we ensure that your data is **not stored by us** after it has been processed by these external services. Their own privacy policies govern how they handle the data during the conversion process.
    * **Bot Tokens:** The bot token you provide for creating a new bot is stored and used by this system to operate your dedicated bot. It is not shared with any third party outside of the Telegram Bot API for its intended purpose.

4.  **Data Retention:**
    * **Text input, media files, and generated audio/transcription files:** These are **deleted immediately** after processing and delivery.
    * **User IDs and preferences:** This data is stored in MongoDB to support your settings and for anonymous usage statistics. This data is also cached in memory for performance. If you wish to have your stored preferences removed, please contact the bot administrator.
    * **Created Bots Data:** The data for created bots (token, owner_id, etc.) is retained as long as the bot is registered with this system. If you wish to deactivate or remove your created bot, please contact the bot administrator.

By using this bot, you confirm that you understand and agree to the data practices outlined in this Privacy Notice.

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Ensure all input modes are OFF on /status
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active today
    today_iso = datetime.now().date().isoformat()
    active_today_count = sum(1 for user_doc in local_user_data.values() if user_doc.get("last_active", "").startswith(today_iso))

    # Total registered users from local_user_data
    total_registered_users = len(local_user_data)

    # Count registered child bots
    total_child_bots = registered_bots_collection.count_documents({})
    active_child_bots = registered_bots_collection.count_documents({"active": True})

    # Processing stats
    try:
        total_tts_conversions_db = processing_stats_collection.count_documents({"type": "tts"})
        total_stt_conversions_db = processing_stats_collection.count_documents({"type": "stt"}) # New STT count

        # Aggregate TTS processing time
        pipeline_tts = [
            {"$match": {"type": "tts"}},
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result_tts = list(processing_stats_collection.aggregate(pipeline_tts))
        total_tts_proc_seconds = agg_result_tts[0]["total_time"] if agg_result_tts else 0

        # Aggregate STT processing time
        pipeline_stt = [ # New STT processing time
            {"$match": {"type": "stt"}},
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result_stt = list(processing_stats_collection.aggregate(pipeline_stt))
        total_stt_proc_seconds = agg_result_stt[0]["total_time"] if agg_result_stt else 0

    except Exception as e:
        logging.error(f"Error fetching processing stats from DB: {e}")
        total_tts_conversions_db = 0
        total_stt_conversions_db = 0
        total_tts_proc_seconds = 0
        total_stt_proc_seconds = 0

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
        f"▫️ Total Registered Users (Main Bot): *{total_registered_users}*\n"
        f"🤖 Total User-Created Bots: *{total_child_bots}*\n"
        f"🟢 Active User-Created Bots: *{active_child_bots}*\n\n"
        "⚙️ *Processing Statistics*\n"
        f"▫️ Total Text-to-Speech Conversions: *{total_tts_conversions_db}*\n"
        f"⏱️ Total TTS Processing Time: *{tts_proc_hours} hours {tts_proc_minutes} minutes {tts_proc_seconds} seconds*\n"
        f"▫️ Total Speech-to-Text Conversions: *{total_stt_conversions_db}*\n" # New
        f"⏱️ Total STT Processing Time: *{stt_proc_hours} hours {stt_proc_minutes} minutes {stt_proc_seconds} seconds*\n\n" # New
        "---"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total_registered = len(local_user_data)
    bot.send_message(message.chat.id, f"Total registered users (from memory): {total_registered}")

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
    # Broadcast to main bot users
    for uid in local_user_data.keys():
        if uid == str(ADMIN_ID):
            continue
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1
        time.sleep(0.05)

    # Also consider broadcasting to users of child bots if that's desired.
    # This would require iterating through `registered_bots_collection` and
    # using each child bot's instance to send messages to its users.
    # For now, it only broadcasts to main bot users.

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

@bot.message_handler(commands=['create_bot', 'create_bot_panel'])
def create_bot_command(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.from_user.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear other input modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    admin_state[message.from_user.id] = None

    user_bot_creation_state[user_id] = {"step": "awaiting_token"}
    bot.send_message(message.chat.id, "Alright! To create your own bot, I need its **token** from BotFather. Please send me the token now.\n\n"
                                      "*(You can get a new token from @BotFather by sending /newbot)*", parse_mode="Markdown")

@bot.message_handler(func=lambda m: user_bot_creation_state.get(str(m.from_user.id), {}).get("step") == "awaiting_token")
def process_bot_token(message):
    user_id = str(message.from_user.id)
    token = message.text.strip()

    # Basic token validation (more robust validation could be added)
    if not token or len(token) < 30 or ":" not in token:
        bot.send_message(message.chat.id, "That doesn't look like a valid bot token. Please try again or type /cancel to stop.")
        return

    # Check if the token is already registered
    if registered_bots_collection.find_one({"token": token}):
        bot.send_message(message.chat.id, "This bot token is already registered with our system. If you believe this is an error, please contact support.")
        user_bot_creation_state[user_id] = None # Reset state
        return

    user_bot_creation_state[user_id]["token"] = token
    user_bot_creation_state[user_id]["step"] = "awaiting_service"

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Text-to-Speech (TTS) Bot", callback_data="create_bot_service|tts"),
        InlineKeyboardButton("Speech-to-Text (STT) Bot", callback_data="create_bot_service|stt")
    )
    bot.send_message(message.chat.id, "Great! Now, what kind of bot do you want to create?", reply_markup=markup)

@bot.callback_query_handler(lambda c: c.data.startswith("create_bot_service|"))
def process_bot_service_selection(call):
    user_id = str(call.from_user.id)
    state = user_bot_creation_state.get(user_id)

    if not state or state.get("step") != "awaiting_service" or not state.get("token"):
        bot.answer_callback_query(call.id, "Something went wrong. Please try /create_bot again.")
        return

    service_type = call.data.split("|")[1]
    token = state["token"]

    try:
        # Register the new bot in MongoDB
        registered_bots_collection.insert_one({
            "token": token,
            "owner_id": user_id,
            "service_type": service_type,
            "created_at": datetime.now().isoformat(),
            "active": True
        })
        bot.answer_callback_query(call.id, "Bot registered! Please wait a moment for it to start.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Your new *{service_type.upper()} bot* has been successfully created and is starting up in the background! "
                 f"You can find it by searching for its username on Telegram.\n\n"
                 f"It might take a minute or two for it to become fully active. Enjoy your new bot!",
            parse_mode="Markdown",
            reply_markup=None
        )
        logging.info(f"User {user_id} created a new {service_type} bot with token {token[:5]}...")

    except Exception as e:
        bot.answer_callback_query(call.id, "Failed to register bot. Please try again.")
        bot.send_message(call.message.chat.id, f"❌ An error occurred while creating your bot: {e}. Please try again later or contact support.")
        logging.error(f"Error registering new bot for user {user_id}: {e}")
    finally:
        user_bot_creation_state[user_id] = None # Clear state

@bot.message_handler(commands=['cancel'])
def cancel_operation(message):
    user_id = str(message.from_user.id)
    # Clear all active input modes for the user
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None
    admin_state[message.from_user.id] = None

    bot.send_message(message.chat.id, "Operation cancelled. What else can I do for you?")


@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear other modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = "awaiting_rate_input" # Set this mode
    user_bot_creation_state[uid] = None


    bot.send_message(
        message.chat.id,
        "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:",
        reply_markup=make_rate_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)

        set_tts_user_rate_db(uid, rate_value)

        user_rate_input_mode[uid] = None
        user_bot_creation_state[uid] = None


        bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
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

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear other modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = "awaiting_pitch_input" # Set this mode
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


    bot.send_message(
        message.chat.id,
        "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:",
        reply_markup=make_pitch_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None # Clear mode
    user_bot_creation_state[uid] = None


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
            reply_markup=None # Remove keyboard after selection
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

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear other modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None


    bot.send_message(message.chat.id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


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

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


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

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=['language_stt']) # New command for STT language
def send_stt_language_prompt(message):
    chat_id = message.chat.id
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.from_user.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear other modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_bot_creation_state[user_id] = None


    bot.send_message(chat_id, "Choose the *language* for your Speech-to-Text transcription:", reply_markup=build_stt_language_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, call.message.chat.id):
        send_subscription_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang_code = call.data.split("|", 1)
    # Find the display name for the language code
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang_db(uid, lang_code)

    bot.answer_callback_query(call.id, f"✅ Language set to {lang_name}!")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Transcription language set to: *{lang_name}*\n\n🎙️ Send a voice, audio, or video to transcribe (max 20MB).",
        parse_mode="Markdown",
        reply_markup=None # Remove keyboard after selection
    )


@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear all modes when media is sent, as we assume user wants STT
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


    file_id = None
    message_type = None

    if message.voice:
        file_id = message.voice.file_id
        message_type = "voice"
    elif message.audio:
        file_id = message.audio.file_id
        message_type = "audio"
    elif message.video:
        file_id = message.video.file_id
        message_type = "video"
    elif message.document: # Assuming documents could be audio/video (e.g., .mp3, .mp4, .wav)
        # Add a check for common audio/video document types if needed, otherwise AssemblyAI will reject
        if message.document.mime_type and (message.document.mime_type.startswith('audio/') or message.document.mime_type.startswith('video/')):
            file_id = message.document.file_id
            message_type = "document_media"
        else:
            bot.send_message(message.chat.id, "Sorry, I can only transcribe audio and video files. Please send a valid audio or video document.")
            return

    if not file_id:
        bot.send_message(message.chat.id, "Unsupported file type for transcription. Please send a voice message, audio file, or video file.")
        return

    # Ensure a language is set for STT
    if uid not in _stt_lang_cache:
        bot.send_message(message.chat.id, "❗ Please choose a language for transcription first using /language_stt.")
        return

    threading.Thread(
        target=lambda: asyncio.run(process_stt_media(bot, message.chat.id, uid, message_type, file_id))
    ).start()

@bot.message_handler(content_types=['text'])
def handle_text_for_tts_or_mode_input(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # If the message is a command, ignore it here and let other handlers catch it.
    # This specifically fixes the /start message not appearing issue by not letting
    # this generic text handler interfere.
    if message.text.startswith('/'):
        return

    # Check if the user is in the "awaiting rate input" state
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

    # Check if the user is in the "awaiting pitch input" state
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

    # If not in a specific input mode, treat as TTS text
    current_voice = get_tts_user_voice_db(uid)

    if current_voice:
        if len(message.text) > 1000:
            bot.send_message(message.chat.id, "Please keep your text under 1000 characters for text-to-speech conversion.")
            return

        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(bot, message.chat.id, uid, message.text))
        ).start()
    else:
        # Fallback if no voice is selected (shouldn't happen with default)
        bot.send_message(
            message.chat.id,
            "Looks like you haven't chosen a voice yet! Please use the /voice command first to select one, then send me your text. 🗣️"
        )


@bot.message_handler(func=lambda m: True, content_types=['sticker', 'photo']) # Handle only remaining specific media types
def handle_unsupported_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(bot, message.chat.id):
        send_subscription_message(bot, message.chat.id)
        return

    # Clear all input modes, as this is likely a misfire
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_bot_creation_state[uid] = None


    bot.send_message(
        message.chat.id,
        "Sorry, I can only convert *text messages* into speech or transcribe *voice/audio/video files*. Please send one of those to interact with me!"
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
            update_data = request.get_data().decode("utf-8")
            update = telebot.types.Update.de_json(update_data)

            # Attempt to find the correct bot instance to process the update
            # This is a simplified routing. In a production env,
            # you might use distinct webhook paths per bot, or a reverse proxy.
            if update.message and update.message.text:
                # Get the bot ID from the token in the webhook URL (if using separate webhooks)
                # Or, more practically for a single webhook: inspect the update object's `bot` field
                # or rely on the `telebot` library's internal token validation.
                # For `telebot` with a single shared webhook, processing all known bots is a common (though not always efficient) approach.
                # The `TeleBot` instance itself will ignore updates not meant for its token.
                pass # The processing loop below handles this

            # Process with the main bot first
            try:
                bot.process_new_updates([update])
                logging.debug(f"Update processed by main bot: {update.update_id}")
            except Exception as e:
                logging.error(f"Error processing update {update.update_id} with main bot: {e}")

            # Try to process with any active child bots
            for child_bot_instance in child_bots.values():
                try:
                    child_bot_instance.process_new_updates([update])
                    logging.debug(f"Update processed by child bot {child_bot_instance.token[:5]}...: {update.update_id}")
                except Exception as e:
                    logging.error(f"Error processing update {update.update_id} with child bot {child_bot_instance.token[:5]}...: {e}")

            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        # For child bots, you'd typically set their webhooks too if they were to use webhooks.
        # But here, we're relying on one main webhook and child bots polling.
        # If child bots also needed webhooks, they'd need *different* URLs.
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        # Also delete webhooks for all child bots if they were set up
        for child_token, child_bot_instance in child_bots.items():
            try:
                child_bot_instance.delete_webhook()
                logging.info(f"Webhook deleted for child bot {child_token[:5]}...")
            except Exception as e:
                logging.warning(f"Failed to delete webhook for child bot {child_token[:5]}...: {e}")

        return "Webhooks deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete main bot webhook: {e}")
        return f"Failed to delete main bot webhook: {e}", 500

def set_bot_commands():
    """
    Sets the list of commands for the main bot using set_my_commands.
    """
    commands = [
        BotCommand("start", "Get Started"),
        BotCommand("voice", "Choose a different voice for TTS"),
        BotCommand("pitch", "Change TTS pitch"),
        BotCommand("rate", "Change TTS speed"),
        BotCommand("language_stt", "Set language for STT"), # New command
        BotCommand("create_bot", "Create your own dedicated bot"), # New command
        BotCommand("help", "❓ How to use the bot"),
        BotCommand("privacy", "🔒 Read privacy notice"),
        BotCommand("status", "Bot stats"),
        BotCommand("cancel", "Cancel current operation")
    ]
    try:
        bot.set_my_commands(commands)
        logging.info("Main bot commands set successfully.")
    except Exception as e:
        logging.error(f"Failed to set main bot commands: {e}")

def set_webhook_on_startup():
    try:
        # It's good practice to delete any existing webhook first, then set it.
        bot.delete_webhook()
        time.sleep(0.5) # Give a moment for Telegram to process
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

def set_bot_info_and_startup():
    connect_to_mongodb()
    set_webhook_on_startup()
    set_bot_commands()
    global bot_start_time
    bot_start_time = datetime.now() # Initialize bot_start_time here

    # Start the child bot polling thread
    global child_bot_polling_thread
    child_bot_polling_thread = threading.Thread(target=start_child_bot_polling, daemon=True)
    child_bot_polling_thread.start()
    logging.info("Child bot polling thread started.")

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"): # Create a simple directory for temporary TTS files
        os.makedirs("tts_audio_cache")
    set_bot_info_and_startup()
    # The Flask app will listen for all incoming requests for the main bot.
    # The child bots run in polling mode in separate threads.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

