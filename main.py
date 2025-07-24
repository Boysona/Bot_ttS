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
TOKEN = "7999849691:AAHmRwZ_Ef1I64SZqotZND6v7LrE-fFwRD0"
ADMIN_ID = 5978150981
WEBHOOK_URL = "excellent-davida-wwmahe-45f63d30.koyeb.app/"
REQUIRED_CHANNEL = "@transcriber_bot_news_channel"
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473"  # From Bot 2

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- MONGODB CONFIGURATION ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

# Collections
mongo_client: MongoClient = None
db = None
users_collection = None
tts_users_collection = None
processing_stats_collection = None
stt_users_collection = None  # For STT language preferences
bot_constructor_collection = None  # For bot constructor

# --- In-memory caches ---
local_user_data = {}            # { user_id: { "last_active": "...", "tts_conversion_count": N, ... } }
_tts_voice_cache = {}           # { user_id: voice_name }
_tts_pitch_cache = {}           # { user_id: pitch_value }
_tts_rate_cache = {}            # { user_id: rate_value }
_stt_lang_cache = {}            # { user_id: language_code }

# --- User state management ---
user_tts_mode = {}              # { user_id: voice_name }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }
user_stt_lang_mode = {}         # { user_id: "awaiting_lang_selection" or None }
user_bot_constructor_state = {} # { user_id: {"step": "awaiting_token"/"awaiting_service", "token": token} }

# --- Statistics counters ---
total_tts_conversions = 0
total_stt_transcriptions = 0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()
admin_state = {}

# Placeholder for keeping track of typing threads
processing_message_ids = {}

# --- STT Languages (from Bot 2) ---
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

# ======= Helper Functions =======
def connect_to_mongodb():
    global mongo_client, db
    global users_collection, tts_users_collection, processing_stats_collection
    global stt_users_collection, bot_constructor_collection
    global local_user_data, _tts_voice_cache, _tts_pitch_cache, _tts_rate_cache, _stt_lang_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db["users"]
        tts_users_collection = db["tts_users"]
        stt_users_collection = db["stt_users"]
        processing_stats_collection = db["tts_processing_stats"]
        bot_constructor_collection = db["bot_constructor"]

        # Create indexes
        users_collection.create_index([("last_active", ASCENDING)])
        tts_users_collection.create_index([("_id", ASCENDING)])
        stt_users_collection.create_index([("_id", ASCENDING)])
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])
        bot_constructor_collection.create_index([("user_id", ASCENDING)])

        logging.info("Connected to MongoDB. Loading data to memory...")

        # Load TTS user data
        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[tts_user["_id"]] = tts_user.get("voice", "en-US-AriaNeural")
            _tts_pitch_cache[tts_user["_id"]] = tts_user.get("pitch", 0)
            _tts_rate_cache[tts_user["_id"]] = tts_user.get("rate", 0)
        
        # Load STT user data
        for stt_user in stt_users_collection.find({}):
            _stt_lang_cache[stt_user["_id"]] = stt_user.get("language", "en")
        
        logging.info("User data loaded into in-memory caches.")

    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"Error during MongoDB connection: {e}")
        exit(1)

def update_user_activity_db(user_id: int):
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_transcription_count": 0
        }
    else:
        local_user_data[user_id_str]["last_active"] = now_iso

    try:
        users_collection.update_one(
            {"_id": user_id_str},
            {"$set": {"last_active": now_iso}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error updating user activity: {e}")

def get_stt_user_lang_db(user_id: str) -> str:
    return _stt_lang_cache.get(user_id, "en")

def set_stt_user_lang_db(user_id: str, lang: str):
    _stt_lang_cache[user_id] = lang
    try:
        stt_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"language": lang}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting STT language: {e}")

def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

def update_uptime_message(chat_id, message_id):
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

def check_subscription(user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def send_subscription_message(chat_id: int):
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
            "Looks like you're not a member of our channel yet! To use the bot, please join our channel.",
            reply_markup=markup,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ======= TTS Functions =======
TTS_VOICES_BY_LANGUAGE = {
    "Arabic": ["ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", ...],
    "English": ["en-AU-NatashaNeural", "en-AU-WilliamNeural", ...],
    # ... (other languages same as before)
}

ORDERED_TTS_LANGUAGES = [
    "English", "Arabic", "Spanish", "French", "German",
    "Chinese", "Japanese", "Portuguese", "Russian", "Turkish",
    # ... (other languages same as before)
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

# ... (Other TTS functions same as before)

# ======= STT Functions =======
def build_stt_language_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    row = []
    for i, name in enumerate(LANGUAGES.keys(), 1):
        row.append(types.KeyboardButton(name))
        if i % 3 == 0:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    return markup

def handle_stt_media(message):
    chat_id = message.chat.id
    user_id = str(message.from_user.id)
    
    # Get user's language preference
    lang_code = get_stt_user_lang_db(user_id)
    
    try:
        bot.send_chat_action(chat_id, "typing")
        processing_msg = bot.reply_to(message, "⏳ Processing...")
        
        # Get file ID based on media type
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
            bot.reply_to(message, "Unsupported file type.")
            return

        # Get file info and check size
        file_info = bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "⚠️ File is too large. Max allowed size is 20MB.")
            return

        # Download file
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        file_data = requests.get(file_url).content

        # Upload to AssemblyAI
        upload_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=file_data
        )
        audio_url = upload_res.json().get('upload_url')
        if not audio_url:
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.reply_to(message, "❌ Failed to upload file.")
            return

        # Start transcription
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
            return

        # Poll for transcription result
        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2)

        bot.delete_message(chat_id, processing_msg.message_id)

        # Handle result
        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                bot.reply_to(message, "ℹ️ No transcription text was returned.")
            elif len(text) <= 4000:
                bot.reply_to(message, text)
            else:
                # Create a file-like object in memory
                import io
                transcript_file = io.BytesIO(text.encode("utf-8"))
                transcript_file.name = "transcript.txt"
                bot.reply_to(message, "Transcript is too long, sending as file:", document=transcript_file)
        else:
            bot.reply_to(message, "❌ Sorry, transcription failed.")

    except Exception as e:
        logging.error(f"Error handling STT media: {e}")
        bot.reply_to(message, f"⚠️ An error occurred: {str(e)}")

# ======= Bot Constructor Functions =======
@bot.message_handler(commands=['createbot'])
def create_bot_start(message):
    user_id = str(message.from_user.id)
    user_bot_constructor_state[user_id] = {"step": "awaiting_token"}
    bot.send_message(
        message.chat.id,
        "🤖 Welcome to Bot Constructor!\n\n"
        "Please send me the bot token you received from @BotFather for your new bot.\n\n"
        "Type /cancel to abort the process."
    )

@bot.message_handler(func=lambda m: user_bot_constructor_state.get(str(m.from_user.id), {}).get("step") == "awaiting_token")
def handle_bot_token(message):
    user_id = str(message.from_user.id)
    token = message.text.strip()
    
    # Simple token validation
    if len(token) < 30 or ':' not in token:
        bot.send_message(message.chat.id, "❌ Invalid token format. Please send a valid bot token.")
        return
    
    user_bot_constructor_state[user_id] = {
        "step": "awaiting_service",
        "token": token
    }
    
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("TTS Create Bot", callback_data="constructor_tts"),
        InlineKeyboardButton("STT Create Bot", callback_data="constructor_stt")
    )
    
    bot.send_message(
        message.chat.id,
        "✅ Token received! Now choose the type of bot you want to create:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("constructor_"))
def handle_bot_type_selection(call):
    user_id = str(call.from_user.id)
    bot_type = call.data.split("_")[1]
    token = user_bot_constructor_state.get(user_id, {}).get("token")
    
    if not token:
        bot.answer_callback_query(call.id, "❌ Token missing. Please start over.")
        return
    
    # Save to database
    try:
        bot_constructor_collection.insert_one({
            "user_id": user_id,
            "bot_token": token,
            "bot_type": bot_type,
            "created_at": datetime.now().isoformat()
        })
        
        # Create simple bot instance
        if bot_type == "tts":
            bot_description = "Text-to-Speech Bot"
            welcome_message = "Welcome to your TTS bot! Send me text and I'll convert it to speech."
        else:
            bot_description = "Speech-to-Text Bot"
            welcome_message = "Welcome to your STT bot! Send me audio and I'll transcribe it for you."
        
        # Configure new bot (simplified)
        new_bot = telebot.TeleBot(token)
        new_bot.set_webhook(url=f"{WEBHOOK_URL}/webhook/{token}")
        
        @new_bot.message_handler(commands=['start'])
        def new_bot_start(m):
            new_bot.send_message(m.chat.id, welcome_message)
        
        bot.answer_callback_query(call.id, "✅ Bot created successfully!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Your {bot_description} is ready!\n\n"
                 f"Use this link to start your bot:\n"
                 f"https://t.me/{new_bot.get_me().username}\n\n"
                 f"Features:\n"
                 f"- {'Converts text to speech' if bot_type == 'tts' else 'Transcribes audio to text'}\n"
                 f"- Ready to use immediately"
        )
        
        # Clean up state
        if user_id in user_bot_constructor_state:
            del user_bot_constructor_state[user_id]
            
    except Exception as e:
        logging.error(f"Error creating bot: {e}")
        bot.answer_callback_query(call.id, "❌ Failed to create bot. Please try again later.")

# ======= Bot Handlers =======
@bot.message_handler(commands=['start'])
def start_handler(message):
    # ... (existing start handler code)

@bot.message_handler(commands=['stt', 'language'])
def stt_language_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)
    
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    user_stt_lang_mode[user_id] = "awaiting_lang_selection"
    bot.send_message(
        message.chat.id,
        "Choose your Media (Voice, Audio, Video) file language for transcription:",
        reply_markup=build_stt_language_keyboard()
    )

@bot.message_handler(func=lambda msg: msg.text in LANGUAGES and user_stt_lang_mode.get(str(msg.from_user.id)) == "awaiting_lang_selection")
def save_stt_language(message):
    user_id = str(message.from_user.id)
    lang_code = LANGUAGES[message.text]
    set_stt_user_lang_db(user_id, lang_code)
    user_stt_lang_mode[user_id] = None
    
    bot.send_message(
        message.chat.id,
        f"✅ Transcription Language Set: {message.text}\n\n"
        "🎙️ Send your voice message, audio file, or video note for transcription."
    )

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_wrapper(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)
    
    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Check if user has selected a language
    if not get_stt_user_lang_db(user_id):
        bot.send_message(message.chat.id, "❗ Please select a language first using /stt before sending a file.")
        return
    
    handle_stt_media(message)

# ... (Other handlers same as before)

# ======= Flask Routes =======
@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    # ... (existing webhook code)

@app.route("/webhook/<bot_token>", methods=["POST"])
def bot_constructor_webhook(bot_token):
    # Handle webhooks for constructed bots
    bot_data = bot_constructor_collection.find_one({"bot_token": bot_token})
    if not bot_data:
        return "Bot not found", 404
    
    content_type = request.headers.get("Content-Type", "")
    if content_type and content_type.startswith("application/json"):
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        
        # This would need proper bot instance management in a real implementation
        # For simplicity, we're just showing the concept
        temp_bot = telebot.TeleBot(bot_token)
        temp_bot.process_new_updates([update])
        
        return "", 200
    return abort(403)

# ... (Other Flask routes same as before)

if __name__ == "__main__":
    connect_to_mongodb()
    set_webhook_on_startup()
    set_bot_commands()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
