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
from pymongo.errors import ConnectionFailure, PyMongoError

from msspeech import MSSpeech, MSSpeechError

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
TOKEN = "7790991731:AAFgEjc6fO-iTSSkpt3lEJBH86gQY5nIgAw"  # <-- Main Bot Token
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://dominant-fidela-wwmahe-2264ea75.koyeb.app/" # Main Bot Webhook

REQUIRED_CHANNEL = "@news_channals"

bot = telebot.TeleBot(TOKEN, threaded=True) # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # AssemblyAI for STT

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"
mongo_client = None
db = None

# --- In-memory data storage ---
in_memory_data = {
    "users": {},            
    "tts_settings": {},     
    "stt_settings": {},     
    "processing_stats": []  
}

# --- User state for input modes ---
user_tts_mode = {}              
user_pitch_input_mode = {}      
user_rate_input_mode = {}       
user_register_bot_mode = {}     

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()
admin_state = {}

# Placeholder for keeping track of typing/recording threads
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

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O D B   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    global mongo_client, db
    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ping')
        db = mongo_client[DB_NAME]
        logging.info("Successfully connected to MongoDB.")
    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        mongo_client = None
        db = None
    except PyMongoError as e:
        logging.error(f"MongoDB error: {e}")
        mongo_client = None
        db = None

def get_user_data_db(user_id: int) -> dict | None:
    if db:
        return db.users.find_one({"_id": str(user_id)})
    return in_memory_data["users"].get(str(user_id))

def update_user_activity_db(user_id: int):
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()
    if db:
        db.users.update_one(
            {"_id": user_id_str},
            {"$set": {"last_active": now_iso},
             "$setOnInsert": {"tts_conversion_count": 0, "stt_conversion_count": 0}},
            upsert=True
        )
    if user_id_str not in in_memory_data["users"]:
        in_memory_data["users"][user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    else:
        in_memory_data["users"][user_id_str]["last_active"] = now_iso

def increment_processing_count_db(user_id: int, service_type: str):
    user_id_str = str(user_id)
    field_to_inc = f"{service_type}_conversion_count"
    if db:
        db.users.update_one(
            {"_id": user_id_str},
            {"$inc": {field_to_inc: 1},
             "$set": {"last_active": datetime.now().isoformat()}},
            upsert=True
        )
    if user_id_str not in in_memory_data["users"]:
        in_memory_data["users"][user_id_str] = {
            "_id": user_id_str,
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    in_memory_data["users"][user_id_str][field_to_inc] = in_memory_data["users"][user_id_str].get(field_to_inc, 0) + 1
    in_memory_data["users"][user_id_str]["last_active"] = datetime.now().isoformat()

def get_tts_user_voice_db(user_id: int) -> str:
    user_data = get_user_data_db(user_id)
    return user_data.get("tts_settings", {}).get("voice", "so-SO-MuuseNeural") if user_data else "so-SO-MuuseNeural"

def set_tts_user_voice_db(user_id: int, voice: str):
    if db:
        db.users.update_one(
            {"_id": str(user_id)},
            {"$set": {"tts_settings.voice": voice}},
            upsert=True
        )
    if str(user_id) not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][str(user_id)] = {}
    in_memory_data["tts_settings"][str(user_id)]["voice"] = voice

def get_tts_user_pitch_db(user_id: int) -> int:
    user_data = get_user_data_db(user_id)
    return user_data.get("tts_settings", {}).get("pitch", 0) if user_data else 0

def set_tts_user_pitch_db(user_id: int, pitch: int):
    if db:
        db.users.update_one(
            {"_id": str(user_id)},
            {"$set": {"tts_settings.pitch": pitch}},
            upsert=True
        )
    if str(user_id) not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][str(user_id)] = {}
    in_memory_data["tts_settings"][str(user_id)]["pitch"] = pitch

def get_tts_user_rate_db(user_id: int) -> int:
    user_data = get_user_data_db(user_id)
    return user_data.get("tts_settings", {}).get("rate", 0) if user_data else 0

def set_tts_user_rate_db(user_id: int, rate: int):
    if db:
        db.users.update_one(
            {"_id": str(user_id)},
            {"$set": {"tts_settings.rate": rate}},
            upsert=True
        )
    if str(user_id) not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][str(user_id)] = {}
    in_memory_data["tts_settings"][str(user_id)]["rate"] = rate

def get_stt_user_lang_db(user_id: int) -> str:
    user_data = get_user_data_db(user_id)
    return user_data.get("stt_settings", {}).get("language_code", "en") if user_data else "en"

def set_stt_user_lang_db(user_id: int, lang_code: str):
    if db:
        db.users.update_one(
            {"_id": str(user_id)},
            {"$set": {"stt_settings.language_code": lang_code}},
            upsert=True
        )
    if str(user_id) not in in_memory_data["stt_settings"]:
        in_memory_data["stt_settings"][str(user_id)] = {}
    in_memory_data["stt_settings"][str(user_id)]["language_code"] = lang_code

def register_child_bot_db(token: str, owner_id: str, service_type: str):
    if db:
        result = db.registered_bots.update_one(
            {"_id": token},
            {"$set": {
                "owner_id": owner_id,
                "service_type": service_type,
                "registration_date": datetime.now().isoformat()
            }},
            upsert=True
        )
        if result.upserted_id or result.modified_count > 0:
            logging.info(f"Child bot {token[:5]}... registered for owner {owner_id} with service {service_type}")
            return True
        else:
            logging.warning(f"Child bot {token[:5]}... already registered")
            return True
    return False

def get_child_bot_info_db(token: str) -> dict | None:
    if db:
        return db.registered_bots.find_one({"_id": token})
    return None

def get_all_registered_child_bots_db() -> list:
    if db:
        return list(db.registered_bots.find({}))
    return []

# ───────────────────────
#   U T I L I T I E S
# ───────────────────────

def keep_recording(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

def keep_typing(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
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

            uptime_text = (
                f"⏱️ <b>Bot Uptime</b>\n\n"
                f"<code>{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds</code>"
            )

            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=uptime_text,
                parse_mode="HTML"
            )
            time.sleep(1)
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

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
    if bot.get_chat(chat_id).type == 'private' and REQUIRED_CHANNEL:
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                "👉 Join Our Channel 👈",
                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
            )
        )
        bot.send_message(
            chat_id,
            "🔒 <b>Access Restricted</b>\n\n"
            "To use this bot, please join our channel:\n\n"
            "After joining, send /start again to continue.",
            reply_markup=markup,
            parse_mode="HTML"
        )

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   H A N D L E R S (Main Bot - Redesigned UI)
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    user_first_name = message.from_user.first_name or "There"

    update_user_activity_db(user_id)

    if message.chat.type == 'private' and user_id != ADMIN_ID and not check_subscription(user_id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_id_str = str(user_id)
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None
    
    if user_id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("📊 Total Users", "📣 Send Broadcast")
        global bot_start_time
        if 'bot_start_time' not in globals():
            bot_start_time = datetime.now()

        sent_message = bot.send_message(
            message.chat.id,
            "🛠️ <b>Admin Panel</b>\n\n⏱️ Uptime status updating live...",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        with admin_uptime_lock:
            if (admin_uptime_message.get(ADMIN_ID) and 
                admin_uptime_message[ADMIN_ID].get('thread') and 
                admin_uptime_message[ADMIN_ID]['thread'].is_alive()):
                pass
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
        welcome_message = (
            f"✨ <b>Hello {user_first_name}!</b> 👋\n\n"
            "I'm your <b>AI Voice Assistant</b> that can convert:\n"
            "🔊 Text to Speech (TTS) and\n"
            "📝 Speech to Text (STT)\n\n"
            "Here's how to use me:\n"
            "• /voice - Choose voice for text-to-speech\n"
            "• /pitch - Adjust voice tone\n"
            "• /rate - Change speaking speed\n"
            "• /language_stt - Set language for speech recognition\n"
            "• /register_bot - Create your own dedicated bot!\n\n"
            "<i>Feel free to add me to your groups too!</i> 👇"
        )

        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("➕ Add to Group", url="https://t.me/mediatotextbot?startgroup="),
            InlineKeyboardButton("❓ Help Guide", callback_data="help_guide")
        )

        bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=markup,
            parse_mode="HTML"
        )

@bot.callback_query_handler(func=lambda call: call.data == "help_guide")
def help_guide_callback(call):
    help_handler(call.message)
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and user_id != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_id_str = str(user_id)
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    help_text = (
        "📚 <b>How to Use This Bot</b>\n\n"
        "🔹 <u>Text-to-Speech (TTS)</u>\n"
        "1. Use /voice to select a language and voice\n"
        "2. Send me any text\n"
        "3. I'll reply with audio\n"
        "4. Use /pitch & /rate to fine-tune\n\n"
        "🔹 <u>Speech-to-Text (STT)</u>\n"
        "1. Use /language_stt to set language\n"
        "2. Send voice, audio, or video\n"
        "3. I'll transcribe it to text\n\n"
        "🔹 <u>Create Your Own Bot</u>\n"
        "• Use /register_bot to get a dedicated TTS or STT bot\n\n"
        "🔒 <i>Your content is processed instantly and never stored</i>\n\n"
        "Need more help? Contact @user33230"
    )
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"))
    
    bot.send_message(
        message.chat.id, 
        help_text,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    start_handler(call.message)
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and user_id != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_id_str = str(user_id)
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    privacy_text = (
        "🔐 <b>Privacy Notice</b>\n\n"
        "Your privacy matters:\n\n"
        "• Text/media processed instantly\n"
        "• Nothing stored on our servers\n"
        "• Generated files are temporary\n"
        "• Settings saved for your convenience\n\n"
        "<i>Basic usage statistics collected anonymously to improve service</i>\n\n"
        "Questions? Contact @user33230"
    )
    
    bot.send_message(
        message.chat.id, 
        privacy_text,
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "📊 Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    if db:
        total_registered = db.users.count_documents({})
        bot.send_message(message.chat.id, f"👥 Total registered users: <b>{total_registered}</b>", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "⚠️ Database not connected. Cannot retrieve total users.")

@bot.message_handler(func=lambda m: m.text == "📣 Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast_prompt(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast_message'
    bot.send_message(message.chat.id, "📩 Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast_message',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    if db:
        for user_doc in db.users.find({}, {"_id": 1}):
            uid = user_doc["_id"]
            if uid == str(ADMIN_ID):
                continue
            try:
                bot.copy_message(uid, message.chat.id, message.message_id)
                success += 1
            except telebot.apihelper.ApiTelegramException as e:
                logging.error(f"Failed to send broadcast to {uid}: {e}")
                fail += 1
            time.sleep(0.05)
    else:
        for uid in in_memory_data["users"].keys():
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
        f"✅ Broadcast complete\n\n"
        f"• Successful: <b>{success}</b>\n"
        f"• Failed: <b>{fail}</b>",
        parse_mode="HTML"
    )

# ───────────────────────
#   B O T   R E G I S T R A T I O N
# ───────────────────────

@bot.message_handler(commands=['register_bot'])
def register_bot_command(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    if message.chat.type == 'private' and message.from_user.id != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    user_register_bot_mode[uid] = "awaiting_token"
    bot.send_message(message.chat.id,
                     "🛠️ <b>Create Your Own Bot!</b>\n\n"
                     "To create your bot, please send me your <b>Bot API Token</b> from @BotFather.\n\n"
                     "It looks like: <code>123456:ABC-DEF1234ghIkl-zyx57W2E1</code>",
                     parse_mode="HTML")

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) and user_register_bot_mode[str(m.from_user.id)].get("state") == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    if not (30 < len(bot_token) < 50 and ':' in bot_token:
        bot.send_message(message.chat.id, "⚠️ That doesn't look like a valid Bot API Token. Please try again.")
        return

    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        logging.info(f"Token validated: {bot_info.username} ({bot_info.id})")
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔊 TTS Bot", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("📝 STT Bot", callback_data="register_bot_service|stt")
        )
        bot.send_message(message.chat.id,
                         f"✅ Verified token for @{bot_info.username}\n\n"
                         "What kind of service should your new bot provide?",
                         reply_markup=markup)

    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error validating token: {e}")
        bot.send_message(message.chat.id,
                         f"❌ Couldn't validate token. It might be invalid or revoked.\n\nError: <code>{e}</code>", 
                         parse_mode="HTML")
        user_register_bot_mode[uid] = None
    except Exception as e:
        logging.error(f"Unexpected error validating token: {e}")
        bot.send_message(message.chat.id, "⚠️ An unexpected error occurred. Please try again later.")
        user_register_bot_mode[uid] = None

@bot.callback_query_handler(lambda c: c.data.startswith("register_bot_service|") and user_register_bot_mode.get(str(c.from_user.id)) and user_register_bot_mode[str(c.from_user.id)].get("state") == "awaiting_service_type")
def on_register_bot_service_select(call):
    uid = str(call.from_user.id)
    data_state = user_register_bot_mode.get(uid)
    if not data_state or data_state.get("state") != "awaiting_service_type":
        bot.answer_callback_query(call.id, "⚠️ Invalid state. Please start over with /register_bot.")
        return

    bot_token = data_state.get("token")
    _, service_type = call.data.split("|", 1)

    if not bot_token:
        bot.answer_callback_query(call.id, "⚠️ Bot token not found. Please start over.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="⚠️ Something went wrong. Please use /register_bot again.")
        user_register_bot_mode[uid] = None
        return

    if register_child_bot_db(bot_token, uid, service_type):
        try:
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
            temp_child_bot = telebot.TeleBot(bot_token)
            temp_child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)

            set_child_bot_commands(temp_child_bot, service_type)

            bot.answer_callback_query(call.id, f"✅ Your {service_type.upper()} bot is ready!")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"🎉 <b>Your New {service_type.upper()} Bot is Active!</b>\n\n"
                     f"🔗 Find it here: https://t.me/{temp_child_bot.get_me().username}\n\n"
                     f"It uses your settings from this main bot. Enjoy!",
                parse_mode="HTML"
            )
            logging.info(f"Webhook set for child bot {temp_child_bot.get_me().username}")
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to set webhook for child bot: {e}")
            bot.answer_callback_query(call.id, "⚠️ Failed to set webhook. Please try again.")
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=f"❌ Setup failed. Error: <code>{e}</code>", parse_mode="HTML")
        except Exception as e:
            logging.error(f"Unexpected error during child bot setup: {e}")
            bot.send_message(call.message.chat.id, "⚠️ An unexpected error occurred during setup. Please try again later.")
            bot.answer_callback_query(call.id, "⚠️ Setup error occurred.")
    else:
        bot.answer_callback_query(call.id, "⚠️ Failed to register your bot. Please try again.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text="⚠️ Failed to register your bot. Please try again later.")

    user_register_bot_mode[uid] = None

# ────────────────────────────────────────────────────────────────────────────────
#   T T S   F U N C T I O N S (Redesigned UI)
# ────────────────────────────────────────────────────────────────────────────────

TTS_VOICES_BY_LANGUAGE = {
    "Arabic": ["ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural"],
    "English": ["en-US-AriaNeural", "en-US-GuyNeural", "en-GB-LibbyNeural"],
    "Spanish": ["es-ES-AlvaroNeural", "es-ES-ElviraNeural"],
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
    "Somali": ["so-SO-UbaxNeural", "so-SO-MuuseNeural"]
}

LANGUAGE_EMOJIS = {
    "Arabic": "🇸🇦",
    "English": "🇬🇧",
    "Spanish": "🇪🇸",
    "Hindi": "🇮🇳",
    "French": "🇫🇷",
    "German": "🇩🇪",
    "Chinese": "🇨🇳",
    "Japanese": "🇯🇵",
    "Portuguese": "🇵🇹",
    "Russian": "🇷🇺",
    "Turkish": "🇹🇷",
    "Korean": "🇰🇷",
    "Italian": "🇮🇹",
    "Indonesian": "🇮🇩",
    "Vietnamese": "🇻🇳",
    "Thai": "🇹🇭",
    "Dutch": "🇳🇱",
    "Polish": "🇵🇱",
    "Swedish": "🇸🇪",
    "Somali": "🇸🇴"
}

ORDERED_TTS_LANGUAGES = [
    "English", "Arabic", "Spanish", "French", "German",
    "Chinese", "Japanese", "Portuguese", "Russian", "Turkish",
    "Hindi", "Somali", "Italian", "Indonesian", "Vietnamese",
    "Thai", "Korean", "Dutch", "Polish", "Swedish"
]

def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    for lang_name in ORDERED_TTS_LANGUAGES:
        if lang_name in TTS_VOICES_BY_LANGUAGE:
            emoji = LANGUAGE_EMOJIS.get(lang_name, "🔊")
            markup.add(InlineKeyboardButton(f"{emoji} {lang_name}", callback_data=f"tts_lang|{lang_name}"))
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str):
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        gender = "♀️" if "Female" in voice or any(name in voice for name in ["Aria", "Jenny", "Sonia"]) else "♂️"
        markup.add(InlineKeyboardButton(f"{gender} {voice.split('-')[-1]}", callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("🔙 Back to Languages", callback_data="tts_back_to_languages"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🔺 Higher", callback_data="pitch_set|+50"),
        InlineKeyboardButton("🔻 Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Default", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("⚡️ Faster", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Slower", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Default", callback_data="rate_set|0")
    )
    return markup

def handle_rate_command(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = "awaiting_rate_input"
    user_register_bot_mode[user_id_str] = None

    target_bot.send_message(
        chat_id,
        "🎚️ <b>Adjust Speaking Speed</b>\n\n"
        "Choose a preset or enter a custom value:\n"
        "• -100 = Slowest\n"
        "• 0 = Normal\n"
        "• +100 = Fastest",
        reply_markup=make_rate_keyboard(),
        parse_mode="HTML"
    )

def handle_rate_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_rate_input_mode[user_id_str] = None

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)

        set_tts_user_rate_db(user_id_for_settings, rate_value)

        target_bot.answer_callback_query(call.id, f"✅ Speed set to {rate_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🎚️ <b>Speaking Speed Updated</b>\n\n"
                 f"🔹 Speed: <b>{rate_value}</b>\n\n"
                 f"Ready to convert text? Send me something!",
            parse_mode="HTML",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "⚠️ Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate: {e}")
        target_bot.answer_callback_query(call.id, "⚠️ An error occurred.")

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_rate_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_rate_callback(call, bot, uid)

def handle_pitch_command(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = "awaiting_pitch_input"
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    target_bot.send_message(
        chat_id,
        "🎚️ <b>Adjust Voice Pitch</b>\n\n"
        "Choose a preset or enter a custom value:\n"
        "• -100 = Lowest\n"
        "• 0 = Normal\n"
        "• +100 = Highest",
        reply_markup=make_pitch_keyboard(),
        parse_mode="HTML"
    )

def handle_pitch_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_pitch_input_mode[user_id_str] = None

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)

        set_tts_user_pitch_db(user_id_for_settings, pitch_value)

        target_bot.answer_callback_query(call.id, f"✅ Pitch set to {pitch_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🎚️ <b>Voice Pitch Updated</b>\n\n"
                 f"🔹 Pitch: <b>{pitch_value}</b>\n\n"
                 f"Ready to convert text? Send me something!",
            parse_mode="HTML",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "⚠️ Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch: {e}")
        target_bot.answer_callback_query(call.id, "⚠️ An error occurred.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_pitch_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_pitch_callback(call, bot, uid)

def handle_voice_command(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    target_bot.send_message(chat_id, "🌍 <b>Select a Language</b>", reply_markup=make_tts_language_keyboard(), parse_mode="HTML")

def handle_tts_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    _, lang_name = call.data.split("|", 1)
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🗣️ <b>Select a Voice for {lang_name}</b>",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name),
        parse_mode="HTML"
    )
    target_bot.answer_callback_query(call.id)

def handle_tts_voice_change_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_db(user_id_for_settings, voice)

    user_tts_mode[user_id_str] = voice

    current_pitch = get_tts_user_pitch_db(user_id_for_settings)
    current_rate = get_tts_user_rate_db(user_id_for_settings)

    target_bot.answer_callback_query(call.id, f"✅ Voice changed to {voice}")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🔊 <b>Voice Selected</b>\n\n"
             f"🔹 Voice: <b>{voice}</b>\n"
             f"🔹 Pitch: <b>{current_pitch}</b>\n"
             f"🔹 Speed: <b>{current_rate}</b>\n\n"
             f"Send me text to convert to speech!",
        parse_mode="HTML",
        reply_markup=None
    )

def handle_tts_back_to_languages_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="🌍 <b>Select a Language</b>",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="HTML"
    )
    target_bot.answer_callback_query(call.id)

@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and user_id != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_voice_command(message, bot, user_id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_language_select_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_voice_change_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_back_to_languages_callback(call, bot, uid)

async def synth_and_send_tts(chat_id: int, user_id_for_settings: int, text: str, target_bot: telebot.TeleBot):
    text = text.replace('.', ',')

    voice = get_tts_user_voice_db(user_id_for_settings)
    pitch = get_tts_user_pitch_db(user_id_for_settings)
    rate = get_tts_user_rate_db(user_id_for_settings)
    filename = f"tts_{user_id_for_settings}_{uuid.uuid4()}.mp3"

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording, target_bot))
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
            target_bot.send_message(chat_id, "❌ Couldn't generate audio. Please try different text.")
            return

        with open(filename, "rb") as f:
            target_bot.send_audio(
                chat_id,
                f,
                caption=f"🔊 <b>Your Audio is Ready!</b>\n\n"
                        f"🔹 Voice: <b>{voice}</b>\n"
                        f"🔹 Pitch: <b>{pitch}</b>\n"
                        f"🔹 Speed: <b>{rate}</b>\n\n"
                        f"Enjoy listening! 🎧",
                parse_mode="HTML"
            )

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        increment_processing_count_db(user_id_for_settings, "tts")

        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
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
        target_bot.send_message(chat_id, f"❌ Problem synthesizing voice: <code>{e}</code>", parse_mode="HTML")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
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
        logging.exception("TTS error")
        target_bot.send_message(chat_id, "❌ This voice is not available, please choose another one.")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
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
                logging.error(f"Error deleting TTS file: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   S T T   F U N C T I O N S (Redesigned UI)
# ────────────────────────────────────────────────────────────────────────────────

def build_stt_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    sorted_languages = sorted(STT_LANGUAGES.items(), key=lambda item: item[0])
    for lang_name, lang_code in sorted_languages:
        buttons.append(InlineKeyboardButton(lang_name, callback_data=f"stt_lang|{lang_code}"))
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def handle_language_stt_command(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = message.chat.id
    user_id_str = str(user_id_for_settings)
    
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

    target_bot.send_message(chat_id, "🌍 <b>Select Transcription Language</b>", reply_markup=build_stt_language_keyboard(), parse_mode="HTML")

def handle_stt_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: int):
    chat_id = call.message.chat.id
    
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang_db(user_id_for_settings, lang_code)

    target_bot.answer_callback_query(call.id, f"✅ Language set to {lang_name}")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🌍 <b>Transcription Language Set</b>\n\n"
             f"🔹 Language: <b>{lang_name}</b>\n\n"
             f"🎙️ Send a voice, audio, or video to transcribe (max 20MB)",
        parse_mode="HTML",
        reply_markup=None
    )

@bot.message_handler(commands=['language_stt'])
def send_stt_language_prompt(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    update_user_activity_db(user_id)

    if message.chat.type == 'private' and user_id != ADMIN_ID and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_language_stt_command(message, bot, user_id)

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = call.from_user.id
    update_user_activity_db(uid)

    if call.message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_stt_language_select_callback(call, bot, uid)

async def process_stt_media(chat_id: int, user_id_for_settings: int, message_type: str, file_id: str, target_bot: telebot.TeleBot, original_message_id: int):
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing, target_bot))
    typing_thread.daemon = True
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, "🔍 Processing your media...", reply_to_message_id=original_message_id)

        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ File too large. Max size: 20MB.")
            return

        file_url = f"https://api.telegram.org/file/bot{target_bot.token}/{file_info.file_path}"
        file_data_response = requests.get(file_url, stream=True)
        file_data_response.raise_for_status()

        processing_start_time = datetime.now()

        upload_res = requests.post("https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY, "Content-Type": "application/octet-stream"},
            data=file_data_response.content)
        upload_res.raise_for_status()
        audio_url = upload_res.json().get('upload_url')

        if not audio_url:
            raise Exception("AssemblyAI upload failed: No upload_url received.")

        lang_code = get_stt_user_lang_db(user_id_for_settings)

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
                target_bot.send_message(chat_id, "ℹ️ No transcription returned.", reply_to_message_id=original_message_id)
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, f"📝 <b>Transcription Result</b>\n\n{text}", reply_to_message_id=original_message_id, parse_mode="HTML")
            else:
                import io
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="📝 <b>Transcription Result</b> (too long for message)", reply_to_message_id=original_message_id, parse_mode="HTML")
            increment_processing_count_db(user_id_for_settings, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Transcription error: <code>{error_msg}</code>", parse_mode="HTML", reply_to_message_id=original_message_id)
            status = "fail_assemblyai_error"
            logging.error(f"AssemblyAI transcription failed: {error_msg}")

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
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
        logging.error(f"Network or API error during STT: {e}")
        target_bot.send_message(chat_id, "❌ Network error. Please try again.", reply_to_message_id=original_message_id)
        status = "fail_network_error"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang_db(user_id_for_settings),
            "error_message": str(e)
        })

    except Exception as e:
        logging.exception(f"Unhandled error during STT: {e}")
        target_bot.send_message(chat_id, "❌ File too large. Max size: 20MB.", reply_to_message_id=original_message_id)
        status = "fail_unknown"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        in_memory_data["processing_stats"].append({
            "user_id": str(user_id_for_settings),
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang_db(user_id_for_settings),
            "error_message": str(e)
        })
    finally:
        stop_typing.set()
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")

def handle_stt_media_types_common(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    update_user_activity_db(user_id_for_settings)

    user_id_str = str(user_id_for_settings)
    user_tts_mode[user_id_str] = None
    user_pitch_input_mode[user_id_str] = None
    user_rate_input_mode[user_id_str] = None
    user_register_bot_mode[user_id_str] = None

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
            target_bot.send_message(message.chat.id, "❌ Unsupported file type. I can only transcribe audio and video files.")
            return

    if not file_id:
        target_bot.send_message(message.chat.id, "❌ Unsupported file type. Send a voice, audio, or video for transcription.")
        return

    if not get_stt_user_lang_db(user_id_for_settings):
        target_bot.send_message(message.chat.id, "❗ Please choose a language first using /language_stt")
        return

    threading.Thread(
        target=lambda: asyncio.run(process_stt_media(message.chat.id, user_id_for_settings, message_type, file_id, target_bot, message.message_id))
    ).start()

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_types(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_stt_media_types_common(message, bot, uid)

def handle_text_for_tts_or_mode_input_common(message, target_bot: telebot.TeleBot, user_id_for_settings: int):
    update_user_activity_db(user_id_for_settings)
    user_id_str = str(user_id_for_settings)

    if message.text.startswith('/'):
        return

    if user_rate_input_mode.get(user_id_str) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate_db(user_id_for_settings, rate_val)
                target_bot.send_message(message.chat.id, f"🎚️ <b>Speed Updated</b>\n\n🔹 Speed: <b>{rate_val}</b>", parse_mode="HTML")
                user_rate_input_mode[user_id_str] = None
            else:
                target_bot.send_message(message.chat.id, "⚠️ Invalid speed. Enter -100 to +100 (0 for normal). Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "⚠️ Not a valid number. Enter -100 to +100 (0 for normal). Try again:")
            return

    if user_pitch_input_mode.get(user_id_str) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_db(user_id_for_settings, pitch_val)
                target_bot.send_message(message.chat.id, f"🎚️ <b>Pitch Updated</b>\n\n🔹 Pitch: <b>{pitch_val}</b>", parse_mode="HTML")
                user_pitch_input_mode[user_id_str] = None
            else:
                target_bot.send_message(message.chat.id, "⚠️ Invalid pitch. Enter -100 to +100 (0 for normal). Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "⚠️ Not a valid number. Enter -100 to +100 (0 for normal). Try again:")
            return

    current_voice = get_tts_user_voice_db(user_id_for_settings)

    if current_voice:
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id_for_settings, message.text, target_bot))
        ).start()
    else:
        target_bot.send_message(
            message.chat.id,
            "❌ <b>No Voice Selected!</b>\n\n"
            "Please choose a voice first with /voice",
            parse_mode="HTML"
        )

@bot.message_handler(content_types=['text'])
def handle_text_for_tts_or_mode_input(message):
    uid = message.from_user.id
    update_user_activity_db(uid)

    if message.chat.type == 'private' and uid != ADMIN_ID and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_text_for_tts_or_mode_input_common(message, bot, uid)

@bot.message_handler(func=lambda m: True, content_types=['sticker', 'photo'])
def handle_unsupported_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity_db(int(uid))

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None

    bot.send_message(
        message.chat.id,
        "❌ <b>Unsupported Content</b>\n\n"
        "I can only convert text to speech or transcribe voice/audio/video files.",
        parse_mode="HTML"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S   (Webhook setup)
# ────────────────────────────────────────────────────────────────────────────────

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

            bot_info = get_child_bot_info_db(child_bot_token)
            if not bot_info:
                logging.warning(f"Received update for unregistered child bot: {child_bot_token[:5]}...")
                return abort(404)

            owner_id = int(bot_info["owner_id"])
            service_type = bot_info["service_type"]

            child_bot_instance = telebot.TeleBot(child_bot_token)

            message = update.message
            callback_query = update.callback_query

            user_id_for_settings = None
            user_first_name = "There"
            if message:
                user_id_for_settings = message.from_user.id
                user_first_name = message.from_user.first_name or "There"
            elif callback_query:
                user_id_for_settings = callback_query.from_user.id
                user_first_name = callback_query.from_user.first_name or "There"
            
            if not user_id_for_settings:
                logging.warning(f"Could not determine user_id for child bot update")
                return "", 200

            if message:
                chat_id = message.chat.id
                if message.text and message.text.startswith('/start'):
                    if service_type == "stt":
                        welcome_message = (
                            f"👋Salam {user_first_name}\n"
                            "• Send a voice, video, or audio file\n"
                            "• I'll transcribe it for you\n"
                            "• Choose language with /language_stt\n"
                            "Powered by @MediaToTextBot"
                        )
                    elif service_type == "tts":
                        welcome_message = (
                            f"👋Salam {user_first_name}\n"
                            "• Send text to convert to audio\n"
                            "• Choose voice with /voice\n"
                            "Powered by @MediaToTextBot"
                        )
                    else:
                        welcome_message = f"👋 Welcome! I'm your dedicated {service_type.upper()} bot."
                    child_bot_instance.send_message(chat_id, welcome_message)
                    return "", 200

                if message.text:
                    if service_type == "tts":
                        if message.text.startswith('/voice'):
                            handle_voice_command(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                        elif message.text.startswith('/pitch'):
                            handle_pitch_command(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                        elif message.text.startswith('/rate'):
                            handle_rate_command(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                        else:
                            handle_text_for_tts_or_mode_input_common(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                    elif service_type == "stt":
                        if message.text.startswith('/language_stt'):
                            handle_language_stt_command(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                        else:
                            child_bot_instance.send_message(chat_id, "❌ This is an STT bot. Send a voice/audio/video file.")
                            return "", 200
                elif message.voice or message.audio or message.video or message.document:
                    if service_type == "stt":
                        handle_stt_media_types_common(message, child_bot_instance, user_id_for_settings)
                    else:
                        child_bot_instance.send_message(chat_id, "❌ This is a TTS bot. Send me text to convert to speech.")
                    return "", 200
                else:
                    child_bot_instance.send_message(chat_id, "⚠️ Unsupported content type for this bot.")
                    return "", 200

            elif callback_query:
                call = callback_query
                chat_id = call.message.chat.id
                
                if service_type == "tts":
                    if call.data.startswith("tts_lang|"):
                        handle_tts_language_select_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                    elif call.data.startswith("tts_voice|"):
                        handle_tts_voice_change_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                    elif call.data == "tts_back_to_languages":
                        handle_tts_back_to_languages_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                    elif call.data.startswith("pitch_set|"):
                        handle_pitch_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                    elif call.data.startswith("rate_set|"):
                        handle_rate_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                elif service_type == "stt":
                    if call.data.startswith("stt_lang|"):
                        handle_stt_language_select_callback(call, child_bot_instance, user_id_for_settings)
                        return "", 200
                
                child_bot_instance.answer_callback_query(call.id, "⚠️ This action is not available for this bot.")
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
        BotCommand("pitch", "Adjust voice pitch"),
        BotCommand("rate", "Adjust speaking speed"),
        BotCommand("language_stt", "Set STT language"),
        BotCommand("register_bot", "Create your own bot"),
        BotCommand("help", "How to use the bot"),
        BotCommand("privacy", "Privacy notice"),
    ]
    try:
        bot.set_my_commands(commands)
        logging.info("Main bot commands set successfully.")
    except Exception as e:
        logging.error(f"Failed to set main bot commands: {e}")

def set_child_bot_commands(child_bot_instance: telebot.TeleBot, service_type: str):
    commands = []
    if service_type == "tts":
        commands = [
            BotCommand("start", "Start your TTS bot"),
            BotCommand("voice", "Change TTS voice"),
            BotCommand("pitch", "Change TTS pitch"),
            BotCommand("rate", "Change TTS speed")
        ]
    elif service_type == "stt":
        commands = [
            BotCommand("start", "Start your STT bot"),
            BotCommand("language_stt", "Set transcription language")
        ]
    
    try:
        child_bot_instance.set_my_commands(commands)
        logging.info(f"Commands set for child bot {child_bot_instance.get_me().username}")
    except Exception as e:
        logging.error(f"Error setting child bot commands: {e}")

def set_webhook_on_startup():
    try:
        bot.delete_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Main bot webhook set to {WEBHOOK_URL}")

        for bot_doc in get_all_registered_child_bots_db():
            token = bot_doc["_id"]
            info = bot_doc
            child_bot_instance = telebot.TeleBot(token)
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{token}"
            try:
                child_bot_instance.set_webhook(url=child_bot_webhook_url, drop_pending_updates=False)
                set_child_bot_commands(child_bot_instance, info["service_type"])
                logging.info(f"Webhook set for child bot {token[:5]}...")
            except Exception as e:
                logging.error(f"Error setting webhook for child bot: {e}")

    except Exception as e:
        logging.error(f"Failed to set main bot webhook: {e}")

def initialize_bot_components():
    global bot_start_time
    bot_start_time = datetime.now()
    connect_to_mongodb()
    set_webhook_on_startup()
    set_bot_commands()

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"):
        os.makedirs("tts_audio_cache")
    initialize_bot_components()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
