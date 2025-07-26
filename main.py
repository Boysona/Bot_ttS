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
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from msspeech import MSSpeech, MSSpeechError

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
TOKEN = "7790991731:AAFgEjc6fO-iTSSkpt3lEJBH86gQY5nIgAw"
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://bot-tts-2d0g.onrender.com/"
REQUIRED_CHANNEL = "@news_channals"
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473"

# --- MongoDB Connection ---
def connect_to_mongodb():
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client[DB_NAME]
        logging.info("Connected to MongoDB successfully.")
        return db
    except ConnectionFailure as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        raise

db = connect_to_mongodb()
users_collection = db["users"]
tts_settings_collection = db["tts_settings"]
stt_settings_collection = db["stt_settings"]
registered_bots_collection = db["registered_bots"]
processing_stats_collection = db["processing_stats"]

# --- User state for input modes ---
user_tts_mode = {}
user_pitch_input_mode = {}
user_rate_input_mode = {}
user_register_bot_mode = {}

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()
admin_state = {}
processing_message_ids = {}

# --- Supported STT Languages ---
STT_LANGUAGES = {
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaycan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇱": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Somali 🇸🇴": "so"
}

# --- MongoDB Helper Functions ---
def update_user_activity(user_id: int):
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()
    users_collection.update_one(
        {"_id": user_id_str},
        {
            "$set": {"last_active": now_iso},
            "$setOnInsert": {"tts_conversion_count": 0, "stt_conversion_count": 0}
        },
        upsert=True
    )

def get_user_data(user_id: str) -> dict | None:
    return users_collection.find_one({"_id": user_id})

def increment_processing_count(user_id: str, service_type: str):
    field_to_inc = f"{service_type}_conversion_count"
    users_collection.update_one(
        {"_id": user_id},
        {
            "$inc": {field_to_inc: 1},
            "$set": {"last_active": datetime.now().isoformat()}
        },
        upsert=True
    )

def get_tts_user_voice(user_id: str) -> str:
    doc = tts_settings_collection.find_one({"_id": user_id})
    return doc.get("voice", "so-SO-MuuseNeural") if doc else "so-SO-MuuseNeural"

def set_tts_user_voice(user_id: str, voice: str):
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"voice": voice}},
        upsert=True
    )

def get_tts_user_pitch(user_id: str) -> int:
    doc = tts_settings_collection.find_one({"_id": user_id})
    return doc.get("pitch", 0) if doc else 0

def set_tts_user_pitch(user_id: str, pitch: int):
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"pitch": pitch}},
        upsert=True
    )

def get_tts_user_rate(user_id: str) -> int:
    doc = tts_settings_collection.find_one({"_id": user_id})
    return doc.get("rate", 0) if doc else 0

def set_tts_user_rate(user_id: str, rate: int):
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"rate": rate}},
        upsert=True
    )

def get_stt_user_lang(user_id: str) -> str:
    doc = stt_settings_collection.find_one({"_id": user_id})
    return doc.get("language_code", "en") if doc else "en"

def set_stt_user_lang(user_id: str, lang_code: str):
    stt_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"language_code": lang_code}},
        upsert=True
    )

def register_child_bot(token: str, owner_id: str, service_type: str):
    existing = registered_bots_collection.find_one({"_id": token})
    if existing:
        # Attempt to restart even if token exists
        try:
            child_bot = telebot.TeleBot(token)
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{token}"
            child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)
            set_child_bot_commands(child_bot, service_type)
            logging.info(f"Webhook re-set for existing child bot {token[:5]}...")
            return True
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to re-set webhook for existing child bot {token[:5]}...: {e}")
            return False
    
    registered_bots_collection.insert_one({
        "_id": token,
        "owner_id": owner_id,
        "service_type": service_type,
        "registration_date": datetime.now().isoformat()
    })
    return True

def get_child_bot_info(token: str) -> dict | None:
    return registered_bots_collection.find_one({"_id": token})

def add_processing_stat(stat: dict):
    processing_stats_collection.insert_one(stat)

# --- Utilities ---
def keep_recording(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action for bot {target_bot.token[:5]}...: {e}")
            break

def keep_typing(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action for bot {target_bot.token[:5]}...: {e}")
            break

def update_uptime_message(chat_id, message_id):
    bot_start_time = datetime.now()
    while True:
        try:
            elapsed = datetime.now() - bot_start_time
            total_seconds = int(elapsed.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)
            uptime_text = f"**Bot Uptime:**\n{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"
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

# --- Subscription Check ---
def check_subscription(user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id: int):
    if bot.get_chat(chat_id).type == 'private':
        if not REQUIRED_CHANNEL:
            return
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("Click to join", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
        )
        bot.send_message(
            chat_id,
            "🔒 Access Restricted\n\nPlease join our channel to use this bot.\n\nJoin and send /start again.",
            reply_markup=markup
        )

# --- Bot Handlers ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name or "There"
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users")
        global bot_start_time
        if 'bot_start_time' not in globals():
            bot_start_time = datetime.now()
        sent_message = bot.send_message(
            message.chat.id,
            "Admin Panel\nUptime updating...",
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
                'chat_id': message.chat.id,
                'thread': threading.Thread(
                    target=update_uptime_message,
                    args=(message.chat.id, sent_message.message_id)
                )
            }
            admin_uptime_message[ADMIN_ID]['thread'].daemon = True
            admin_uptime_message[ADMIN_ID]['thread'].start()
    else:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("➕ Add to Groups", url="https://t.me/mediatotextbot?startgroup=")
        )
        bot.send_message(
            message.chat.id,
            f"👋 Hi {user_first_name}! I'm your AI voice assistant.\n"
            "• /voice: Choose TTS language/voice\n"
            "• /pitch: Adjust voice tone\n"
            "• /rate: Change speaking speed\n"
            "• /language_stt: Set STT language\n"
            "• /register_bot: Create your own bot\n"
            "Send text for speech or media for transcription!",
            reply_markup=markup
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    bot.send_message(
        message.chat.id,
        "📚 *Help*\n\n"
        "• **TTS**: Use /voice to select voice, /pitch or /rate to adjust. Send text to convert.\n"
        "• **STT**: Use /language_stt to set language, then send voice/audio/video (max 20MB).\n"
        "• **Custom Bot**: Create your own bot with /register_bot.\n"
        "• **Privacy**: Text/media processed instantly, not stored. Settings saved temporarily.\n"
        "Contact @user33230 for support.",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    bot.send_message(
        message.chat.id,
        "🔐 *Privacy*\n\nYour data is processed instantly and not stored. Contact @user33230 with concerns.",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total = users_collection.count_documents({})
    bot.send_message(message.chat.id, f"Total registered users: {total}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_prompt(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast_message'
    bot.send_message(message.chat.id, "Enter broadcast message:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast_message',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for user in users_collection.find():
        uid = user["_id"]
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
        f"Broadcast complete.\nSuccess: {success}\nFailed: {fail}"
    )

@bot.message_handler(commands=['register_bot'])
def register_bot_command(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = "awaiting_token"

    bot.send_message(
        message.chat.id,
        "Send your Bot API Token from @BotFather (e.g., 123456:ABC-DEF1234ghIkl-zyx57W2E1)."
    )

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    if not (30 < len(bot_token) < 50 and ':' in bot_token):
        bot.send_message(message.chat.id, "Invalid Bot API Token. Please try again.")
        return

    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Text-to-Speech", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("Speech-to-Text", callback_data="register_bot_service|stt")
        )
        bot.send_message(
            message.chat.id,
            f"Token verified for @{bot_info.username}. Choose service type:",
            reply_markup=markup
        )
    except telebot.apihelper.ApiTelegramException as e:
        bot.send_message(
            message.chat.id,
            f"❌ Invalid or revoked token: `{e}`. Check with @BotFather and try again.",
            parse_mode="Markdown"
        )
        user_register_bot_mode[uid] = None
    except Exception as e:
        logging.error(f"Unexpected error validating token for user {uid}: {e}")
        bot.send_message(message.chat.id, "Error validating token. Try again later.")
        user_register_bot_mode[uid] = None

@bot.callback_query_handler(lambda c: c.data.startswith("register_bot_service|") and user_register_bot_mode.get(str(c.from_user.id)))
def on_register_bot_service_select(call):
    uid = str(call.from_user.id)
    data_state = user_register_bot_mode.get(uid)
    if not data_state or data_state.get("state") != "awaiting_service_type":
        bot.answer_callback_query(call.id, "Please start over with /register_bot.")
        return

    bot_token = data_state.get("token")
    _, service_type = call.data.split("|", 1)

    if not bot_token:
        bot.answer_callback_query(call.id, "Bot token not found. Start over.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Error: Please use /register_bot again."
        )
        user_register_bot_mode[uid] = None
        return

    try:
        child_bot = telebot.TeleBot(bot_token)
        child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
        child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)
        set_child_bot_commands(child_bot, service_type)

        if register_child_bot(bot_token, uid, service_type):
            bot.answer_callback_query(call.id, f"{service_type.upper()} bot registered!")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"🎉 Your *{service_type.upper()} Bot* is active!\n"
                     f"Find it: https://t.me/{child_bot.get_me().username}\n"
                     f"Uses your settings from this bot.",
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "Failed to register bot.")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Failed to register bot. Try again later."
            )
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set webhook for child bot {bot_token[:5]}...: {e}")
        bot.answer_callback_query(call.id, "Failed to set up bot.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"❌ Error setting up bot: `{e}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Unexpected error during child bot setup: {e}")
        bot.answer_callback_query(call.id, "Error during setup.")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Unexpected error during setup. Try again later."
        )
    user_register_bot_mode[uid] = None

# --- TTS Functions ---
TTS_VOICES_BY_LANGUAGE = {
    "Arabic": ["ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", "ar-BH-AliNeural", "ar-BH-LailaNeural"],
    "English": ["en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural", "en-GB-ThomasNeural"],
    "Spanish": ["es-ES-ElviraNeural", "es-ES-AlvaroNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural"],
    "Hindi": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "French": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"],
    "German": ["de-DE-KatjaNeural", "de-DE-ConradNeural"],
    "Chinese": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural"],
    "Japanese": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"],
    "Portuguese": ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"],
    "Russian": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
    "Turkish": ["tr-TR-EmelNeural", "tr-TR-AhmetNeural"],
    "Korean": ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural"],
    "Italian": ["it-IT-ElsaNeural", "it-IT-DiegoNeural"],
    "Indonesian": ["id-ID-GadisNeural", "id-ID-ArdiNeural"],
    "Vietnamese": ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"],
    "Thai": ["th-TH-PremwadeeNeural", "th-TH-NiwatNeural"],
    "Dutch": ["nl-NL-ColetteNeural", "nl-NL-MaartenNeural"],
    "Polish": ["pl-PL-ZofiaNeural", "pl-PL-MarekNeural"],
    "Swedish": ["sv-SE-SofieNeural", "sv-SE-MattiasNeural"],
    "Filipino": ["fil-PH-BlessicaNeural", "fil-PH-AngeloNeural"],
    "Somali": ["so-SO-UbaxNeural", "so-SO-MuuseNeural"],
    # Add other languages as needed
}

ORDERED_TTS_LANGUAGES = sorted(TTS_VOICES_BY_LANGUAGE.keys())

def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton(lang, callback_data=f"tts_lang|{lang}") for lang in ORDERED_TTS_LANGUAGES]
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str):
    markup = InlineKeyboardMarkup(row_width=2)
    for voice in TTS_VOICES_BY_LANGUAGE.get(lang_name, []):
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="tts_back_to_languages"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⬆️ Higher", callback_data="pitch_set|+50"),
        InlineKeyboardButton("⬇️ Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Faster", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Slower", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset", callback_data="rate_set|0")
    )
    return markup

def handle_rate_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = "awaiting_rate_input"
    user_register_bot_mode[user_id_for_settings] = None
    target_bot.send_message(
        message.chat.id,
        "Choose speaking speed (-100 to +100, 0 is normal):",
        reply_markup=make_rate_keyboard()
    )

def handle_rate_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_rate_input_mode[user_id_for_settings] = None
    try:
        _, rate_value = call.data.split("|", 1)
        rate_value = int(rate_value)
        set_tts_user_rate(user_id_for_settings, rate_value)
        target_bot.answer_callback_query(call.id, f"Speed set to {rate_value}")
        target_bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Speed set to *{rate_value}*.\nSend text or use /voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate for user {user_id_for_settings}: {e}")
        target_bot.answer_callback_query(call.id, "Error setting speed.")

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_rate_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_rate_callback(call, bot, uid)

def handle_pitch_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = "awaiting_pitch_input"
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    target_bot.send_message(
        message.chat.id,
        "Choose voice pitch (-100 to +100, 0 is normal):",
        reply_markup=make_pitch_keyboard()
    )

def handle_pitch_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_pitch_input_mode[user_id_for_settings] = None
    try:
        _, pitch_value = call.data.split("|", 1)
        pitch_value = int(pitch_value)
        set_tts_user_pitch(user_id_for_settings, pitch_value)
        target_bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}")
        target_bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Pitch set to *{pitch_value}*.\nSend text or use /voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch for user {user_id_for_settings}: {e}")
        target_bot.answer_callback_query(call.id, "Error setting pitch.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_pitch_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_pitch_callback(call, bot, uid)

def handle_voice_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    target_bot.send_message(
        message.chat.id,
        "Choose language for your voice:",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )

def handle_tts_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    _, lang_name = call.data.split("|", 1)
    target_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Select a *{lang_name}* voice:",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name),
        parse_mode="Markdown"
    )
    target_bot.answer_callback_query(call.id)

def handle_tts_voice_change_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    _, voice = call.data.split("|", 1)
    set_tts_user_voice(user_id_for_settings, voice)
    user_tts_mode[user_id_for_settings] = voice
    current_pitch = get_tts_user_pitch(user_id_for_settings)
    current_rate = get_tts_user_rate(user_id_for_settings)
    target_bot.answer_callback_query(call.id, f"Voice set to {voice}")
    target_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Voice: *{voice}*\nPitch: *{current_pitch}*\nSpeed: *{current_rate}*\nSend text to convert!",
        parse_mode="Markdown",
        reply_markup=None
    )

def handle_tts_back_to_languages_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    target_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose language for your voice:",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    target_bot.answer_callback_query(call.id)

@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_voice_command(message, bot, user_id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_tts_language_select_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_tts_voice_change_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_tts_back_to_languages_callback(call, bot, uid)

async def synth_and_send_tts(chat_id: int, user_id_for_settings: str, text: str, target_bot: telebot.TeleBot):
    text = text.replace('.', ',')
    voice = get_tts_user_voice(user_id_for_settings)
    pitch = get_tts_user_pitch(user_id_for_settings)
    rate = get_tts_user_rate(user_id_for_settings)
    filename = f"tts_audio_cache/tts_{user_id_for_settings}_{uuid.uuid4()}.mp3"

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording, target_bot))
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
            target_bot.send_message(chat_id, "❌ Failed to generate audio. Try different text.")
            return

        with open(filename, "rb") as f:
            target_bot.send_audio(
                chat_id,
                f,
                caption=f"🎧 Audio generated!\nVoice: *{voice}*\nPitch: *{pitch}*\nSpeed: *{rate}*",
                parse_mode="Markdown"
            )

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        increment_processing_count(user_id_for_settings, "tts")
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "tts",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "voice": voice,
            "pitch": pitch,
            "rate": rate,
            "text_length": len(text)
        })
    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        target_bot.send_message(chat_id, f"❌ Voice synthesis error: `{e}`. Try another voice.", parse_mode="Markdown")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "tts",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": "fail_msspeech_error",
            "voice": voice,
            "pitch": pitch,
            "rate": rate,
            "error_message": str(e)
        })
    except Exception as e:
        logging.error(f"TTS error: {e}")
        target_bot.send_message(chat_id, "Voice unavailable. Please choose another.")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "tts",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": "fail_unknown",
            "voice": voice,
            "pitch": pitch,
            "rate": rate,
            "error_message": str(e)
        })
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting TTS file {filename}: {e}")

# --- STT Functions ---
def build_stt_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton(name, callback_data=f"stt_lang|{code}") for name, code in sorted(STT_LANGUAGES.items())]
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def handle_language_stt_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None
    target_bot.send_message(
        message.chat.id,
        "Choose transcription language:",
        reply_markup=build_stt_language_keyboard(),
        parse_mode="Markdown"
    )

def handle_stt_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang(user_id_for_settings, lang_code)
    target_bot.answer_callback_query(call.id, f"Language set to {lang_name}")
    target_bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Transcription language: *{lang_name}*\nSend voice/audio/video (max 20MB).",
        parse_mode="Markdown",
        reply_markup=None
    )

@bot.message_handler(commands=['language_stt'])
def send_stt_language_prompt(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_language_stt_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)
    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    handle_stt_language_select_callback(call, bot, uid)

async def process_stt_media(chat_id: int, user_id_for_settings: str, message_type: str, file_id: str, target_bot: telebot.TeleBot, original_message_id: int):
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing, target_bot))
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, "Processing...", reply_to_message_id=original_message_id)
        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ File too large (max 20MB).", reply_to_message_id=original_message_id)
            return

        file_url = f"https://api.telegram.org/file/bot{target_bot.token}/{file_info.file_path}"
        file_data_response = requests.get(file_url, stream=True)
        file_data_response.raise_for_status()

        processing_start_time = datetime.now()
        upload_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY, "Content-Type": "application/octet-stream"},
            data=file_data_response.content
        )
        upload_res.raise_for_status()
        audio_url = upload_res.json().get('upload_url')

        if not audio_url:
            raise Exception("AssemblyAI upload failed: No upload_url received.")

        lang_code = get_stt_user_lang(user_id_for_settings)
        transcript_res = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"},
            json={"audio_url": audio_url, "language_code": lang_code, "speech_model": "best"}
        )
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
                target_bot.send_message(chat_id, "ℹ️ No transcription returned.", reply_to_message_id=original_message_id)
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, text, reply_to_message_id=original_message_id)
            else:
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="Transcription too long for message.", reply_to_message_id=original_message_id)
            increment_processing_count(user_id_for_settings, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Transcription error: `{error_msg}`", parse_mode="Markdown", reply_to_message_id=original_message_id)
            status = "fail_assemblyai_error"
            logging.error(f"AssemblyAI transcription failed for user {user_id_for_settings}: {error_msg}")

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size,
            "language_code": lang_code,
            "error_message": res.get("error") if status.startswith("fail") else None
        })
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error during STT for user {user_id_for_settings}: {e}")
        target_bot.send_message(chat_id, "❌ Network error. Try again.", reply_to_message_id=original_message_id)
        status = "fail_network_error"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang(user_id_for_settings),
            "error_message": str(e)
        })
    except Exception as e:
        logging.error(f"Unhandled error during STT for user {user_id_for_settings}: {e}")
        target_bot.send_message(chat_id, "❌ File too large (max 20MB).", reply_to_message_id=original_message_id)
        status = "fail_unknown"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang(user_id_for_settings),
            "error_message": str(e)
        })
    finally:
        stop_typing.set()
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")

def handle_stt_media_types_common(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    update_user_activity(int(user_id_for_settings))
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

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
            target_bot.send_message(message.chat.id, "Please send a valid audio or video file.")
            return

    if not file_id:
        target_bot.send_message(message.chat.id, "Unsupported file type. Send voice/audio/video.")
        return

    if not stt_settings_collection.find_one({"_id": user_id_for_settings}):
        target_bot.send_message(message.chat.id, "❗ Set transcription language with /language_stt.")
        return

    threading.Thread(
        target=lambda: asyncio.run(process_stt_media(message.chat.id, user_id_for_settings, message_type, file_id, target_bot, message.message_id))
    ).start()

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_stt_media_types_common(message, bot, uid)

def handle_text_for_tts_or_mode_input_common(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    update_user_activity(int(user_id_for_settings))
    if message.text.startswith('/'):
        return

    if user_rate_input_mode.get(user_id_for_settings) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate(user_id_for_settings, rate_val)
                target_bot.send_message(message.chat.id, f"🔊 Speed set to *{rate_val}*.", parse_mode="Markdown")
                user_rate_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Speed must be -100 to +100. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "❌ Invalid number. Use -100 to +100. Try again:")
            return

    if user_pitch_input_mode.get(user_id_for_settings) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch(user_id_for_settings, pitch_val)
                target_bot.send_message(message.chat.id, f"🔊 Pitch set to *{pitch_val}*.", parse_mode="Markdown")
                user_pitch_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Pitch must be -100 to +100. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "❌ Invalid number. Use -100 to +100. Try again:")
            return

    current_voice = get_tts_user_voice(user_id_for_settings)
    if current_voice:
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id_for_settings, message.text, target_bot))
        ).start()
    else:
        target_bot.send_message(
            message.chat.id,
            "No voice selected. Use /voice to choose one."
        )

@bot.message_handler(content_types=['text'])
def handle_text_for_tts_or_mode_input(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    handle_text_for_tts_or_mode_input_common(message, bot, uid)

@bot.message_handler(content_types=['sticker', 'photo'])
def handle_unsupported_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None
    bot.send_message(
        message.chat.id,
        "I can only process text for TTS or voice/audio/video for STT."
    )

# --- Flask Routes ---
bot_start_time = datetime.now()

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

@app.route("/child_webhook/<child_bot_token>", methods=["POST"])
def child_webhook(child_bot_token):
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot_info = get_child_bot_info(child_bot_token)
            if not bot_info:
                logging.warning(f"Unregistered child bot token: {child_bot_token[:5]}...")
                return abort(404)

            owner_id = bot_info["owner_id"]
            service_type = bot_info["service_type"]
            child_bot_instance = telebot.TeleBot(child_bot_token, threaded=True)

            message = update.message
            callback_query = update.callback_query
            user_id_for_settings = str((message or callback_query).from_user.id) if (message or callback_query) else None
            user_first_name = (message or callback_query).from_user.first_name or "There" if (message or callback_query) else "There"

            if not user_id_for_settings:
                logging.warning(f"No user_id for child bot update: {child_bot_token[:5]}...")
                return "", 200

            if message:
                chat_id = message.chat.id
                if message.text and message.text.startswith('/start'):
                    welcome_message = (
                        f"👋 Hi {user_first_name}!\n"
                        f"• {'Send text for speech' if service_type == 'tts' else 'Send voice/audio/video to transcribe'}.\n"
                        f"• Use {'/voice' if service_type == 'tts' else '/language_stt'} to set options.\n"
                        "Powered by @MediaToTextBot"
                    )
                    child_bot_instance.send_message(chat_id, welcome_message)
                    return "", 200

                if message.text:
                    if service_type == "tts":
                        if message.text.startswith('/voice'):
                            handle_voice_command(message, child_bot_instance, user_id_for_settings)
                        elif message.text.startswith('/pitch'):
                            handle_pitch_command(message, child_bot_instance, user_id_for_settings)
                        elif message.text.startswith('/rate'):
                            handle_rate_command(message, child_bot_instance, user_id_for_settings)
                        else:
                            handle_text_for_tts_or_mode_input_common(message, child_bot_instance, user_id_for_settings)
                    elif service_type == "stt":
                        if message.text.startswith('/language_stt'):
                            handle_language_stt_command(message, child_bot_instance, user_id_for_settings)
                        else:
                            child_bot_instance.send_message(chat_id, "Send voice/audio/video or use /language_stt.")
                    return "", 200
                elif message.voice or message.audio or message.video or message.document:
                    if service_type == "stt":
                        handle_stt_media_types_common(message, child_bot_instance, user_id_for_settings)
                    else:
                        child_bot_instance.send_message(chat_id, "Send text for TTS conversion.")
                    return "", 200
                else:
                    child_bot_instance.send_message(chat_id, f"Use {'text' if service_type == 'tts' else 'voice/audio/video'} for this bot.")
                    return "", 200
            elif callback_query:
                call = callback_query
                if service_type == "tts":
                    if call.data.startswith("tts_lang|"):
                        handle_tts_language_select_callback(call, child_bot_instance, user_id_for_settings)
                    elif call.data.startswith("tts_voice|"):
                        handle_tts_voice_change_callback(call, child_bot_instance, user_id_for_settings)
                    elif call.data == "tts_back_to_languages":
                        handle_tts_back_to_languages_callback(call, child_bot_instance, user_id_for_settings)
                    elif call.data.startswith("pitch_set|"):
                        handle_pitch_callback(call, child_bot_instance, user_id_for_settings)
                    elif call.data.startswith("rate_set|"):
                        handle_rate_callback(call, child_bot_instance, user_id_for_settings)
                    return "", 200
                elif service_type == "stt":
                    if call.data.startswith("stt_lang|"):
                        handle_stt_language_select_callback(call, child_bot_instance, user_id_for_settings)
                    return "", 200
                child_bot_instance.answer_callback_query(call.id, "Action not available for this bot.")
                return "", 200
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
    commands = [
        BotCommand("start", "Get Started"),
        BotCommand("voice", "Choose TTS voice"),
        BotCommand("pitch", "Adjust TTS pitch"),
        BotCommand("rate", "Adjust TTS speed"),
        BotCommand("language_stt", "Set STT language"),
        BotCommand("register_bot", "Create custom bot"),
        BotCommand("help", "How to use"),
        BotCommand("privacy", "Privacy notice")
    ]
    try:
        bot.set_my_commands(commands)
        logging.info("Main bot commands set.")
    except Exception as e:
        logging.error(f"Failed to set main bot commands: {e}")

def set_child_bot_commands(child_bot_instance: telebot.TeleBot, service_type: str):
    commands = (
        [
            BotCommand("start", "Start TTS bot"),
            BotCommand("voice", "Change voice"),
            BotCommand("pitch", "Change pitch"),
            BotCommand("rate", "Change speed")
        ] if service_type == "tts" else [
            BotCommand("start", "Start STT bot"),
            BotCommand("language_stt", "Set transcription language")
        ]
    )
    try:
        child_bot_instance.set_my_commands(commands)
        logging.info(f"Commands set for child bot {child_bot_instance.get_me().username} ({service_type}).")
    except Exception as e:
        logging.error(f"Failed to set commands for child bot: {e}")

def set_webhook_on_startup():
    try:
        bot.delete_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Main bot webhook set to {WEBHOOK_URL}")
        for bot_doc in registered_bots_collection.find():
            token = bot_doc["_id"]
            service_type = bot_doc["service_type"]
            child_bot_instance = telebot.TeleBot(token)
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{token}"
            try:
                child_bot_instance.set_webhook(url=child_bot_webhook_url, drop_pending_updates=False)
                set_child_bot_commands(child_bot_instance, service_type)
                logging.info(f"Webhook re-set for child bot {token[:5]}...")
            except Exception as e:
                logging.error(f"Failed to re-set webhook for child bot {token[:5]}...: {e}")
    except Exception as e:
        logging.error(f"Failed to set main bot webhook: {e}")

def set_bot_info_and_startup():
    global bot_start_time
    bot_start_time = datetime.now()
    set_webhook_on_startup()
    set_bot_commands()

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"):
        os.makedirs("tts_audio_cache")
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
