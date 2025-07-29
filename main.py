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

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
TOKEN = "8345774316:AAGX_M74WnBjb5Ore7exUtuiEPKXicFgHs0"  # Main Bot Token
ADMIN_ID = 5978150981
WEBHOOK_URL = "available-elga-wwmahe-605c287a.koyeb.app/"  # Main Bot Webhook
REQUIRED_CHANNEL = "@news_channals"

bot = telebot.TeleBot(TOKEN, threaded=True)  # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "894ad2705ab54e33bb011a87b658ede2"  # AssemblyAI for STT

# --- MongoDB Configuration ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# Define collections
users_collection = db["users"]
tts_settings_collection = db["tts_settings"]
stt_settings_collection = db["stt_settings"]
registered_bots_collection = db["registered_bots"]
processing_stats_collection = db["processing_stats"]
tokens_collection = db["tokens"]  # As requested, though we'll primarily use the specific collections

# --- User state for input modes (kept in-memory as temporary) ---
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
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaijan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇱": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "Uzbek 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Swahili 🇰🇪": "sw"
}

# --- MongoDB Helper Functions ---

def update_user_activity(user_id: int):
    """Update user activity in the users collection."""
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()
    users_collection.update_one(
        {"_id": user_id_str},
        {"$set": {"last_active": now_iso},
         "$setOnInsert": {"tts_conversion_count": 0, "stt_conversion_count": 0}},
        upsert=True
    )

def get_user_data(user_id: str) -> dict | None:
    """Retrieve user data from the users collection."""
    return users_collection.find_one({"_id": user_id})

def increment_processing_count(user_id: str, service_type: str):
    """Increment conversion count for a user."""
    field_to_inc = f"{service_type}_conversion_count"
    users_collection.update_one(
        {"_id": user_id},
        {"$inc": {field_to_inc: 1},
         "$set": {"last_active": datetime.now().isoformat()}}
    )

def get_tts_user_voice(user_id: str) -> str:
    """Retrieve TTS voice from the tts_settings collection."""
    settings = tts_settings_collection.find_one({"_id": user_id})
    return settings.get("voice", "so-SO-MuuseNeural") if settings else "so-SO-MuuseNeural"

def set_tts_user_voice(user_id: str, voice: str):
    """Save TTS voice to the tts_settings collection."""
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"voice": voice}},
        upsert=True
    )

def get_tts_user_pitch(user_id: str) -> int:
    """Retrieve TTS pitch from the tts_settings collection."""
    settings = tts_settings_collection.find_one({"_id": user_id})
    return settings.get("pitch", 0) if settings else 0

def set_tts_user_pitch(user_id: str, pitch: int):
    """Save TTS pitch to the tts_settings collection."""
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"pitch": pitch}},
        upsert=True
    )

def get_tts_user_rate(user_id: str) -> int:
    """Retrieve TTS rate from the tts_settings collection."""
    settings = tts_settings_collection.find_one({"_id": user_id})
    return settings.get("rate", 0) if settings else 0

def set_tts_user_rate(user_id: str, rate: int):
    """Save TTS rate to the tts_settings collection."""
    tts_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"rate": rate}},
        upsert=True
    )

def get_stt_user_lang(user_id: str) -> str:
    """Retrieve STT language from the stt_settings collection."""
    settings = stt_settings_collection.find_one({"_id": user_id})
    return settings.get("language_code", "en") if settings else "en"

def set_stt_user_lang(user_id: str, lang_code: str):
    """Save STT language to the stt_settings collection."""
    stt_settings_collection.update_one(
        {"_id": user_id},
        {"$set": {"language_code": lang_code}},
        upsert=True
    )

def register_child_bot(bot_token: str, owner_id: str, service_type: str):
    """Register a child bot in the registered_bots collection."""
    registered_bots_collection.update_one(
        {"_id": bot_token},
        {"$set": {
            "owner_id": owner_id,
            "service_type": service_type,
            "registration_date": datetime.now().isoformat()
        }},
        upsert=True
    )
    logging.info(f"Child bot {bot_token[:5]}... registered for owner {owner_id} with service {service_type} in database.")
    return True

def get_child_bot_info(bot_token: str) -> dict | None:
    """Retrieve child bot info from the registered_bots collection."""
    return registered_bots_collection.find_one({"_id": bot_token})

def add_processing_stat(stat: dict):
    """Add a processing stat to the processing_stats collection."""
    processing_stats_collection.insert_one(stat)

# --- Utilities ---

def keep_recording(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

def update_uptime_message(chat_id, message_id):
    """Live-update the admin uptime message every second."""
    bot_start_time = datetime.now()
    while True:
        try:
            elapsed = datetime# [Remaining utility functions remain unchanged: check_subscription, send_subscription_message, etc.]

# --- Bot Handlers ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

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
            f"👋 Hey there, {user_first_name}! I'm your versatile AI voice assistant. I can convert your text to speech (TTS) and your speech/audio to text (STT), all for free! 🔊✍️\n\n"
            "✨ *Here's how to make the most of me:* ✨\n"
            "• Use /voice to **choose your preferred language and voice** for Text-to-Speech.\n"
            "• Experiment with /pitch to **adjust the voice's tone** (higher or lower).\n"
            "• Tweak /rate to **change the speaking speed** (faster or slower).\n"
            "• Use /language_stt to **set the language** for Speech-to-Text, then send me your voice, audio, or video files!\n"
            "• Want your *own dedicated bot* for TTS or STT? Use /register_bot to create one!\n\n"
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
    update_user_activity(message.from_user.id)
    # [Rest of the help_handler remains unchanged]

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total_registered = users_collection.count_documents({})
    bot.send_message(message.chat.id, f"Total registered users: {total_registered}")

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
    user_ids = [doc["_id"] for doc in users_collection.find({}, {"_id": 1})]
    for uid in user_ids:
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
шеб

        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# --- TTS and STT Functions ---

async def synth_and_send_tts(chat_id: int, user_id_for_settings: str, text: str, target_bot: telebot.TeleBot):
    text = text.replace('.', ',')
    voice = get_tts_user_voice(user_id_for_settings)
    pitch = get_tts_user_pitch(user_id_for_settings)
    rate = get_tts_user_rate(user_id_for_settings)
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
        # [Error handling remains unchanged, just update function calls]
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "tts",
            "processing_time": (datetime.now() - processing_start_time).total_seconds(),
            "timestamp": datetime.now().isoformat(),
            "status": "fail_msspeech_error",
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

async def process_stt_media(chat_id: int, user_id_for_settings: str, message_type: str, file_id: str, target_bot: telebot.TeleBot, original_message_id: int):
    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, " Processing...", reply_to_message_id=original_message_id)
        file_info = target_bot.get_file(file_id)
        # [Rest of the function remains unchanged until the processing stats]
        increment_processing_count(user_id_for_settings, "stt")
        add_processing_stat({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": (datetime.now() - processing_start_time).total_seconds(),
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size,
            "language_code": lang_code,
            "error_message": res.get("error") if status.startswith("fail") else None
        })
    except Exception as e:
        # [Error handling remains unchanged, just update function calls]
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
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")

# --- Flask Routes ---

@app.route("/child_webhook/<child_bot_token>", methods=["POST"])
def child_webhook(child_bot_token):
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))

            bot_info = get_child_bot_info(child_bot_token)
            if not bot_info:
                logging.warning(f"Received update for unregistered child bot token: {child_bot_token[:5]}...")
                return abort(404)

            owner_id = bot_info["owner_id"]
            service_type = bot_info["service_type"]

            child_bot_instance = telebot.TeleBot(child_bot_token, threaded=True)

            message = update.message
            callback_query = update.callback_query

            user_id_for_settings = None
            user_first_name = "There"
            if message:
                user_id_for_settings = str(message.from_user.id)
                user_first_name = message.from_user.first_name if message.from_user.first_name else "There"
            elif callback_query:
                user_id_for_settings = str(callback_query.from_user.id)
                user_first_name = callback_query.from_user.first_name if callback_query.from_user.first_name else "There"
            
            if not user_id_for_settings:
                return "", 200

            if message:
                chat_id = message.chat.id
                if message.text and message.text.startswith('/start'):
                    # [Welcome message logic remains unchanged]
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
                            child_bot_instance.send_message(chat_id, "This is an STT bot. Please send me a voice, audio, or video file to transcribe, or use `/language_stt` to set the transcription language.")
                            return "", 200
                elif message.voice or message.audio or message.video or message.document:
                    if service_type == "stt":
                        handle_stt_media_types_common(message, child_bot_instance, user_id_for_settings)
                    else:
                        child_bot_instance.send_message(chat_id, "This is a TTS bot. Please send me text to convert to speech.")
                    return "", 200
                else:
                    child_bot_instance.send_message(chat_id, "I'm sorry, I can only process specific types of messages based on my service type. Please check my `/start` message for details.")
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
                
                child_bot_instance.answer_callback_query(call.id, "This action is not available for this bot's service type.")
                return "", 200
            
            return "", 200
    return abort(403)

def set_webhook_on_startup():
    try:
        bot.delete_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Main bot webhook set successfully to {WEBHOOK_URL}")

        for bot_doc in registered_bots_collection.find():
            token = bot_doc["_id"]
            info = bot_doc
            child_bot_instance = telebot.TeleBot(token)
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{token}"
            try:
                child_bot_instance.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)
                set_child_bot_commands(child_bot_instance, info["service_type"])
                logging.info(f"Webhook re-set for child bot {token[:5]}... to {child_bot_webhook_url}")
            except telebot.apihelper.ApiTelegramException as e:
                logging.error(f"Failed to re-set webhook for child bot: {e}")
            except Exception as e:
                logging.error(f"Unexpected error re-setting webhook for child bot: {e}")

    except Exception as e:
        logging.error(f"Failed to set main bot webhook on startup: {e}")

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
