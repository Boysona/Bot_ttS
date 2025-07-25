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

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION ---
TOKEN = "7999849691:AAFE7cMt2cyFMjZuQoXLObXfe58Ao1DMnvc"  # <-- Main Bot Token
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://dominant-fidela-wwmahe-2264ea75.koyeb.app/" # Main Bot Webhook

REQUIRED_CHANNEL = "@transcriber_bot_news_channel"

bot = telebot.TeleBot(TOKEN, threaded=True) # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # AssemblyAI for STT

# --- In-memory data stores ---
user_data = {}            # {user_id: {"last_active": "...", "tts_conversion_count": N, "stt_conversion_count": N}}
tts_settings = {}         # {user_id: {"voice": "...", "pitch": N, "rate": N}}
stt_settings = {}         # {user_id: {"language_code": "..."}}
registered_bots = {}      # {bot_token: {"owner_id": "...", "service_type": "..."}}

# --- User state for input modes ---
user_tts_mode = {}              # {user_id: voice_name or None}
user_pitch_input_mode = {}      # {user_id: "awaiting_pitch_input" or None}
user_rate_input_mode = {}       # {user_id: "awaiting_rate_input" or None}
user_register_bot_mode = {}     # {user_id: "awaiting_token" or "awaiting_service_type"}

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
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇭": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "O'zbekcha 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Somali 🇸🇴": "so"
}

# ────────────────────────────────────────────────────────────────────────────────
#   I N - M E M O R Y   D A T A   H E L P E R S
# ────────────────────────────────────────────────────────────────────────────────

def update_user_activity(user_id: int):
    """Update user activity timestamp and initialize counts if needed"""
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    else:
        user_data[user_id_str]["last_active"] = now_iso

def get_tts_user_voice(user_id: str) -> str:
    """Get TTS voice setting (default: Somali)"""
    return tts_settings.get(user_id, {}).get("voice", "so-SO-MuuseNeural")

def set_tts_user_voice(user_id: str, voice: str):
    """Set TTS voice setting"""
    if user_id not in tts_settings:
        tts_settings[user_id] = {}
    tts_settings[user_id]["voice"] = voice

def get_tts_user_pitch(user_id: str) -> int:
    """Get TTS pitch setting (default: 0)"""
    return tts_settings.get(user_id, {}).get("pitch", 0)

def set_tts_user_pitch(user_id: str, pitch: int):
    """Set TTS pitch setting"""
    if user_id not in tts_settings:
        tts_settings[user_id] = {}
    tts_settings[user_id]["pitch"] = pitch

def get_tts_user_rate(user_id: str) -> int:
    """Get TTS rate setting (default: 0)"""
    return tts_settings.get(user_id, {}).get("rate", 0)

def set_tts_user_rate(user_id: str, rate: int):
    """Set TTS rate setting"""
    if user_id not in tts_settings:
        tts_settings[user_id] = {}
    tts_settings[user_id]["rate"] = rate

def get_stt_user_lang(user_id: str) -> str:
    """Get STT language setting (default: English)"""
    return stt_settings.get(user_id, {}).get("language_code", "en")

def set_stt_user_lang(user_id: str, lang_code: str):
    """Set STT language setting"""
    if user_id not in stt_settings:
        stt_settings[user_id] = {}
    stt_settings[user_id]["language_code"] = lang_code

def increment_processing_count(user_id: str, service_type: str):
    """Increment conversion count for TTS or STT"""
    user_id_str = str(user_id)
    update_user_activity(int(user_id_str))
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "last_active": datetime.now().isoformat(),
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    
    if service_type == "tts":
        user_data[user_id_str]["tts_conversion_count"] = user_data[user_id_str].get("tts_conversion_count", 0) + 1
    elif service_type == "stt":
        user_data[user_id_str]["stt_conversion_count"] = user_data[user_id_str].get("stt_conversion_count", 0) + 1

def register_child_bot(token: str, owner_id: str, service_type: str):
    """Register a new child bot"""
    registered_bots[token] = {
        "owner_id": owner_id,
        "service_type": service_type,
        "registration_date": datetime.now().isoformat()
    }
    logging.info(f"Child bot {token[:5]}... registered for owner {owner_id}")
    return True

def get_child_bot_info(token: str) -> dict | None:
    """Get child bot info by token"""
    return registered_bots.get(token)

# ────────────────────────────────────────────────────────────────────────────────
#   U T I L I T I E S
# ────────────────────────────────────────────────────────────────────────────────

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
    """Live-update the admin uptime message"""
    bot_start_time = datetime.now()
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
    """Verify user is a member of REQUIRED_CHANNEL"""
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def send_subscription_message(chat_id: int):
    """Prompt user to join REQUIRED_CHANNEL"""
    if bot.get_chat(chat_id).type == 'private' and REQUIRED_CHANNEL:
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
#   B O T   H A N D L E R S (Main Bot)
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    user_first_name = message.from_user.first_name or "There"
    
    # Initialize user data if needed
    update_user_activity(message.from_user.id)
    
    # Check subscription for non-admin users
    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    # Admin state reset
    if message.from_user.id == ADMIN_ID:
        admin_state[message.from_user.id] = None

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        global bot_start_time
        if 'bot_start_time' not in globals():
            bot_start_time = datetime.now()

        sent_message = bot.send_message(
            message.chat.id,
            "Admin Panel and Uptime (updating live)...",
            reply_markup=keyboard
        )
        with admin_uptime_lock:
            if admin_uptime_message.get(ADMIN_ID) and admin_uptime_message[ADMIN_ID].get('thread') and admin_uptime_message[ADMIN_ID]['thread'].is_alive():
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

    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    help_text = """
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

3.  **Create Your Own Bot!**
    * **Dedicated Bots:** Use /register_bot if you want to create your own lightweight bot that acts as a dedicated TTS or STT service, powered by this framework! You just provide your bot's token.

4.  **Privacy & Data Handling**
    * **Your Content is Private:** Any text you send for TTS or media you send for STT is processed instantly and **never stored** on our servers. Generated audio files and transcriptions are temporary and deleted after they're sent to you.
    * **Your Settings are Saved:** To make your experience seamless, we securely store your Telegram User ID and your chosen preferences (like selected TTS voice, pitch, rate, and STT language) in memory. This ensures your settings are remembered during the session.

---

If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy creating and transcribing! ✨
"""
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    privacy_text = """
🔐 *Privacy Notice*

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    global bot_start_time
    if 'bot_start_time' not in globals():
        bot_start_time = datetime.now()

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active users today
    today = datetime.now().date()
    active_today = sum(
        1 for user_id, data in user_data.items() 
        if datetime.fromisoformat(data["last_active"]).date() == today
    )

    # Count registered users and bots
    total_users = len(user_data)
    total_bots = len(registered_bots)

    # Count conversions
    total_tts = sum(data.get("tts_conversion_count", 0) for data in user_data.values())
    total_stt = sum(data.get("stt_conversion_count", 0) for data in user_data.values())

    text = (
        "📊 *Bot Statistics*\n\n"
        "🟢 *Bot Status: Online*\n"
        f"⏱️ Uptime: *{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds*\n\n"
        "👥 *User Statistics*\n"
        f"▫️ Active Today: *{active_today}*\n"
        f"▫️ Total Users: *{total_users}*\n"
        f"▫️ Registered Child Bots: *{total_bots}*\n\n"
        "⚙️ *Processing Statistics*\n"
        f"▫️ TTS Conversions: *{total_tts}*\n"
        f"▫️ STT Conversions: *{total_stt}*\n"
        "---"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total registered users: {len(user_data)}")

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
    for user_id in user_data:
        if user_id == str(ADMIN_ID):
            continue
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except Exception as e:
            logging.error(f"Failed to send broadcast to {user_id}: {e}")
            fail += 1
        time.sleep(0.05)

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   R E G I S T R A T I O N
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['register_bot'])
def register_bot_command(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset other modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    user_register_bot_mode[uid] = "awaiting_token"
    bot.send_message(
        message.chat.id,
        "Alright! To create your own lightweight bot, please send me your **Bot API Token**. "
        "You can get this from @BotFather on Telegram. It looks like `123456:ABC-DEF1234ghIkl-zyx57W2E1`."
    )

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    # Basic validation
    if not (30 < len(bot_token) < 50 and ':' in bot_token:
        bot.send_message(message.chat.id, "That doesn't look like a valid Bot API Token. Please try again.")
        return

    # Validate token
    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        logging.info(f"Token validated: {bot_info.username}")
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Text-to-Speech (TTS) Bot", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("Speech-to-Text (STT) Bot", callback_data="register_bot_service|stt")
        )
        bot.send_message(
            message.chat.id,
            f"Great! I've verified the token for @{bot_info.username}. "
            "Now, what kind of service should your new bot provide?",
            reply_markup=markup
        )
    except Exception as e:
        logging.error(f"Error validating token: {e}")
        bot.send_message(
            message.chat.id,
            f"❌ I couldn't validate that token. Please check your token and try again. Error: `{e}`",
            parse_mode="Markdown"
        )
        user_register_bot_mode[uid] = None

@bot.callback_query_handler(lambda c: c.data.startswith("register_bot_service|") and user_register_bot_mode.get(str(c.from_user.id)) and user_register_bot_mode[str(c.from_user.id)].get("state") == "awaiting_service_type")
def on_register_bot_service_select(call):
    uid = str(call.from_user.id)
    data_state = user_register_bot_mode.get(uid)
    if not data_state or data_state.get("state") != "awaiting_service_type":
        bot.answer_callback_query(call.id, "Invalid state. Please start over with /register_bot.")
        return

    bot_token = data_state.get("token")
    _, service_type = call.data.split("|", 1)

    # Check if token already registered
    if bot_token in registered_bots:
        bot.answer_callback_query(call.id, "This bot token is already registered!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="This bot token is already registered."
        )
        user_register_bot_mode[uid] = None
        return

    # Register the bot
    if register_child_bot(bot_token, uid, service_type):
        try:
            # Set webhook
            child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
            temp_child_bot = telebot.TeleBot(bot_token)
            temp_child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)

            bot.answer_callback_query(call.id, f"✅ Your {service_type.upper()} bot is registered!")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"🎉 Your new *{service_type.upper()} Bot* is now active!\n\n"
                     f"You can find it here: https://t.me/{temp_child_bot.get_me().username}\n\n"
                     f"It will use your settings from this main bot.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Failed to set webhook: {e}")
            bot.answer_callback_query(call.id, "Failed to set up your bot. Please try again.")
    else:
        bot.answer_callback_query(call.id, "Failed to register your bot.")

    user_register_bot_mode[uid] = None

# ────────────────────────────────────────────────────────────────────────────────
#   T T S   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

TTS_VOICES_BY_LANGUAGE = {
    "Arabic": [
        "ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural",
        # ... (other voices)
    ],
    "English": [
        "en-AU-NatashaNeural", "en-AU-WilliamNeural",
        # ... (other voices)
    ],
    # ... (other languages)
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
            buttons.append(InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}"))
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

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset other modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_register_bot_mode[uid] = None

    user_rate_input_mode[uid] = "awaiting_rate_input"
    bot.send_message(
        message.chat.id,
        "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:",
        reply_markup=make_rate_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    user_rate_input_mode[uid] = None
    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)
        set_tts_user_rate(uid, rate_value)
        bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset other modes
    user_tts_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None

    user_pitch_input_mode[uid] = "awaiting_pitch_input"
    bot.send_message(
        message.chat.id,
        "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:",
        reply_markup=make_pitch_keyboard()
    )

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    user_pitch_input_mode[uid] = None
    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)
        set_tts_user_pitch(uid, pitch_value)
        bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /voice to pick a different voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch: {e}")
        bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset other modes
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    bot.send_message(
        message.chat.id, 
        "First, choose the *language* for your voice. 👇", 
        reply_markup=make_tts_language_keyboard(), 
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
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
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    _, voice = call.data.split("|", 1)
    set_tts_user_voice(uid, voice)
    user_tts_mode[uid] = voice

    current_pitch = get_tts_user_pitch(uid)
    current_rate = get_tts_user_rate(uid)

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
        reply_markup=None
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id: int, user_id: str, text: str, target_bot: telebot.TeleBot):
    """Synthesize text to speech and send audio"""
    text = text.replace('.', ',')  # Faster speech output
    voice = get_tts_user_voice(user_id)
    pitch = get_tts_user_pitch(user_id)
    rate = get_tts_user_rate(user_id)
    filename = f"tts_{user_id}_{uuid.uuid4()}.mp3"

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording, target_bot))
    recording_thread.daemon = True
    recording_thread.start()

    try:
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.set_rate(rate)
        await mss.set_pitch(pitch)
        await mss.set_volume(1.0)

        await mss.synthesize(text, filename)

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            target_bot.send_message(chat_id, "❌ Failed to generate audio. Please try again.")
            return

        with open(filename, "rb") as f:
            target_bot.send_audio(
                chat_id,
                f,
                caption=f"🎧 *Here's your audio!* \n\nVoice: *{voice}*\nPitch: *{pitch}*\nSpeed: *{rate}*",
                parse_mode="Markdown"
            )

        increment_processing_count(user_id, "tts")
    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        target_bot.send_message(chat_id, f"❌ TTS error: `{e}`", parse_mode="Markdown")
    except Exception as e:
        logging.exception("TTS error")
        target_bot.send_message(chat_id, "❌ Unexpected TTS error")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting TTS file: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   S T T   F U N C T I O N S
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

@bot.message_handler(commands=['language_stt'])
def send_stt_language_prompt(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and user_id != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset other modes
    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    bot.send_message(
        message.chat.id, 
        "Choose the *language* for your Speech-to-Text transcription:", 
        reply_markup=build_stt_language_keyboard(), 
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity(call.from_user.id)

    if call.message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang(uid, lang_code)

    bot.answer_callback_query(call.id, f"✅ Language set to {lang_name}!")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Transcription language set to: *{lang_name}*\n\n🎙️ Send a voice, audio, or video to transcribe (max 20MB).",
        parse_mode="Markdown",
        reply_markup=None
    )

async def process_stt_media(chat_id: int, user_id: str, message_type: str, file_id: str, target_bot: telebot.TeleBot):
    """Transcribe media to text using AssemblyAI"""
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(chat_id, stop_typing, target_bot))
    typing_thread.daemon = True
    typing_thread.start()

    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, "⏳ Processing your media for transcription...")

        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ File is too large. Max size is 20MB.")
            return

        # Download file
        file_url = f"https://api.telegram.org/file/bot{target_bot.token}/{file_info.file_path}"
        file_data = requests.get(file_url).content

        # Upload to AssemblyAI
        upload_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLYAI_API_KEY},
            data=file_data
        )
        upload_res.raise_for_status()
        audio_url = upload_res.json().get('upload_url')

        if not audio_url:
            raise Exception("Upload failed")

        # Start transcription
        lang_code = get_stt_user_lang(user_id)
        transcript_res = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"},
            json={"audio_url": audio_url, "language_code": lang_code}
        )
        transcript_res.raise_for_status()
        transcript_id = transcript_res.json().get("id")

        # Poll for results
        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            res = requests.get(polling_url, headers={"authorization": ASSEMBLYAI_API_KEY}).json()
            if res['status'] in ['completed', 'error']:
                break
            time.sleep(2)

        if res['status'] == 'completed':
            text = res.get("text", "")
            if not text:
                target_bot.send_message(chat_id, "ℹ️ No speech detected")
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, text)
            else:
                with open("transcript.txt", "w") as f:
                    f.write(text)
                with open("transcript.txt", "rb") as f:
                    target_bot.send_document(chat_id, f, caption="Your transcription")
                os.remove("transcript.txt")
            increment_processing_count(user_id, "stt")
        else:
            error_msg = res.get("error", "Unknown error")
            target_bot.send_message(chat_id, f"❌ Transcription error: `{error_msg}`", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"STT error: {e}")
        target_bot.send_message(chat_id, f"❌ STT processing error: `{e}`", parse_mode="Markdown")
    finally:
        stop_typing.set()
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception:
                pass

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Reset input modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None

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
        bot.send_message(message.chat.id, "Unsupported file type")
        return

    # Check language setting
    if uid not in stt_settings:
        bot.send_message(message.chat.id, "❗ Please set language first with /language_stt")
        return

    threading.Thread(
        target=lambda: asyncio.run(process_stt_media(message.chat.id, uid, "media", file_id, bot))
    ).start()

@bot.message_handler(content_types=['text'])
def handle_text_for_tts_or_mode_input(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    # Handle rate input mode
    if user_rate_input_mode.get(uid) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate(uid, rate_val)
                bot.send_message(message.chat.id, f"🔊 Voice speed set to *{rate_val}*", parse_mode="Markdown")
                user_rate_input_mode[uid] = None
            else:
                bot.send_message(message.chat.id, "❌ Invalid speed. Enter a number between -100 and 100")
            return
        except ValueError:
            bot.send_message(message.chat.id, "❌ Invalid number")
            return

    # Handle pitch input mode
    if user_pitch_input_mode.get(uid) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch(uid, pitch_val)
                bot.send_message(message.chat.id, f"🔊 Voice pitch set to *{pitch_val}*", parse_mode="Markdown")
                user_pitch_input_mode[uid] = None
            else:
                bot.send_message(message.chat.id, "❌ Invalid pitch. Enter a number between -100 and 100")
            return
        except ValueError:
            bot.send_message(message.chat.id, "❌ Invalid number")
            return

    # Handle TTS conversion
    current_voice = get_tts_user_voice(uid)
    if current_voice:
        if len(message.text) > 1000:
            bot.send_message(message.chat.id, "❌ Text too long. Max 1000 characters")
            return

        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text, bot))
        ).start()
    else:
        bot.send_message(
            message.chat.id,
            "Please select a voice first with /voice command"
        )

@bot.message_handler(func=lambda m: True, content_types=['sticker', 'photo'])
def handle_unsupported_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity(message.from_user.id)

    if message.chat.type == 'private' and uid != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Reset input modes
    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None

    bot.send_message(
        message.chat.id,
        "I can only convert text to speech or transcribe audio/video. Send /help for instructions."
    )

# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S
# ────────────────────────────────────────────────────────────────────────────────

# Global variable for bot start time
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
                logging.warning(f"Unregistered child bot: {child_bot_token[:5]}...")
                return abort(404)

            # Create temporary bot instance
            child_bot = telebot.TeleBot(child_bot_token)
            service_type = bot_info["service_type"]
            owner_id = bot_info["owner_id"]
            
            # Determine user ID from update
            user_id = None
            if update.message:
                user_id = str(update.message.from_user.id)
            elif update.callback_query:
                user_id = str(update.callback_query.from_user.id)
            
            if not user_id:
                return "", 200

            # Process update based on service type
            if update.message:
                if update.message.text and update.message.text.startswith('/start'):
                    welcome_msg = f"👋 Welcome! I'm your dedicated {service_type.upper()} bot."
                    child_bot.send_message(update.message.chat.id, welcome_msg)
                elif service_type == "tts" and update.message.text:
                    handle_text_for_tts_or_mode_input_common(update.message, child_bot, user_id)
                elif service_type == "stt" and (update.message.voice or update.message.audio or update.message.video):
                    handle_stt_media_types_common(update.message, child_bot, user_id)
                else:
                    child_bot.send_message(update.message.chat.id, f"This is a {service_type.upper()} bot. Send /help for instructions.")
            elif update.callback_query:
                # Handle callbacks similarly to main bot
                pass

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
    """Set bot commands for main bot"""
    commands = [
        BotCommand("start", "Get Started"),
        BotCommand("voice", "Choose TTS voice"),
        BotCommand("pitch", "Change TTS pitch"),
        BotCommand("rate", "Change TTS speed"),
        BotCommand("language_stt", "Set STT language"),
        BotCommand("register_bot", "Create your own bot"),
        BotCommand("help", "How to use"),
        BotCommand("status", "Bot stats")
    ]
    try:
        bot.set_my_commands(commands)
        logging.info("Main bot commands set")
    except Exception as e:
        logging.error(f"Failed to set commands: {e}")

def set_webhook_on_startup():
    """Configure webhook on startup"""
    try:
        bot.delete_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Webhook setup error: {e}")

def initialize_bot():
    """Initialize bot on startup"""
    global bot_start_time
    bot_start_time = datetime.now()
    set_webhook_on_startup()
    set_bot_commands()

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"):
        os.makedirs("tts_audio_cache")
    initialize_bot()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
