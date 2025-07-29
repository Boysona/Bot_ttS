




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
TOKEN = "7790991731:AAEX7SyxJv0mjH2fZBbZFZ95sAR_6DCYO10"  # <-- Main Bot Token
ADMIN_ID = 5978150981
WEBHOOK_URL = "available-elga-wwmahe-605c287a.koyeb.app/" # Main Bot Webhook

REQUIRED_CHANNEL = "@news_channals"

bot = telebot.TeleBot(TOKEN, threaded=True) # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "894ad2705ab54e33bb011a87b658ede2" # AssemblyAI for STT

# --- In-memory data storage ---
in_memory_data = {
    "users": {},            # { user_id: { "last_active": "...", "tts_conversion_count": N, "stt_conversion_count": N, ... } }
    "tts_settings": {},     # { user_id: { "voice": "...", "pitch": N, "rate": N } }
    "stt_settings": {},     # { user_id: { "language_code": "..." } }
    "registered_bots": {},  # { child_bot_token: { "owner_id": ..., "service_type": ... } }
    "processing_stats": []  # List of dictionaries for processing logs
}

# --- User state for input modes ---
user_tts_mode = {}              # { user_id: voice_name or None }
user_pitch_input_mode = {}      # { user_id: "awaiting_pitch_input" or None }
user_rate_input_mode = {}       # { user_id: "awaiting_rate_input" or None }
user_register_bot_mode = {}     # { user_id: "awaiting_token" or "awaiting_service_type" }

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

# ────────────────────────────────────────────────────────────────────────────────
#   I N - M E M O R Y   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def init_in_memory_data():
    """Initializes in-memory data structures."""
    logging.info("Initializing in-memory data structures.")

def update_user_activity_in_memory(user_id: int):
    """Update user activity in in_memory_data."""
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    if user_id_str not in in_memory_data["users"]:
        in_memory_data["users"][user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    else:
        in_memory_data["users"][user_id_str]["last_active"] = now_iso

def get_user_data_in_memory(user_id: str) -> dict | None:
    """Return user document from in_memory_data["users"]."""
    return in_memory_data["users"].get(user_id)

def increment_processing_count_in_memory(user_id: str, service_type: str):
    """Increment conversion count for user."""
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    if user_id_str not in in_memory_data["users"]:
        in_memory_data["users"][user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "tts_conversion_count": 0,
            "stt_conversion_count": 0
        }
    
    field_to_inc = f"{service_type}_conversion_count"
    in_memory_data["users"][user_id_str][field_to_inc] = in_memory_data["users"][user_id_str].get(field_to_inc, 0) + 1
    in_memory_data["users"][user_id_str]["last_active"] = now_iso

def get_tts_user_voice_in_memory(user_id: str) -> str:
    """Return TTS voice from in-memory cache."""
    return in_memory_data["tts_settings"].get(user_id, {}).get("voice", "so-SO-MuuseNeural")

def set_tts_user_voice_in_memory(user_id: str, voice: str):
    """Save TTS voice in in-memory cache."""
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["voice"] = voice

def get_tts_user_pitch_in_memory(user_id: str) -> int:
    """Return TTS pitch from in-memory cache."""
    return in_memory_data["tts_settings"].get(user_id, {}).get("pitch", 0)

def set_tts_user_pitch_in_memory(user_id: str, pitch: int):
    """Save TTS pitch in in-memory cache."""
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["pitch"] = pitch

def get_tts_user_rate_in_memory(user_id: str) -> int:
    """Return TTS rate from in-memory cache."""
    return in_memory_data["tts_settings"].get(user_id, {}).get("rate", 0)

def set_tts_user_rate_in_memory(user_id: str, rate: int):
    """Save TTS rate in in-memory cache."""
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["rate"] = rate

def get_stt_user_lang_in_memory(user_id: str) -> str:
    """Return STT language from in-memory cache."""
    return in_memory_data["stt_settings"].get(user_id, {}).get("language_code", "en")

def set_stt_user_lang_in_memory(user_id: str, lang_code: str):
    """Save STT language in in-memory cache."""
    if user_id not in in_memory_data["stt_settings"]:
        in_memory_data["stt_settings"][user_id] = {}
    in_memory_data["stt_settings"][user_id]["language_code"] = lang_code

def register_child_bot_in_memory(token: str, owner_id: str, service_type: str):
    """Registers a child bot in the in-memory data."""
    in_memory_data["registered_bots"][token] = {
        "owner_id": owner_id,
        "service_type": service_type,
        "registration_date": datetime.now().isoformat()
    }
    logging.info(f"Child bot {token[:5]}... registered for owner {owner_id} with service {service_type} in memory.")
    return True

def get_child_bot_info_in_memory(token: str) -> dict | None:
    """Retrieves child bot information from in-memory data."""
    return in_memory_data["registered_bots"].get(token)

def add_processing_stat_in_memory(stat: dict):
    """Adds a processing statistic entry to the in-memory list."""
    in_memory_data["processing_stats"].append(stat)

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

def update_uptime_message(chat_id, message_id):
    """Live-update the admin uptime message every second."""
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
    """Verify user is a member of the required channel."""
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def send_subscription_message(chat_id: int):
    """Prompt user to join REQUIRED_CHANNEL."""
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
            "🔒 Access Restricted\n\nPlease join our channel to use this bot.\n\nJoin and send /start again.",
            reply_markup=markup
        )

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   H A N D L E R S (Main Bot)
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "There"

    update_user_activity_in_memory(message.from_user.id)

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
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    help_text = (
        """
📚 *How to Use This Bot*

Ready to turn your text into speech or media into text? Here's how it works:

1.  **Text-to-Speech (TTS) Conversion**
    * **Choose a Voice:** Start by using the /voice command. You can select from a wide range of languages and voices.
    * **Send Your Text:** Once you've chosen a voice, simply send any text message. The bot will process it and reply with an audio clip.
    * **Fine-Tune Your Voice:**
        * Use /pitch to **adjust the tone** of the voice.
        * Use /rate to **change the speaking speed** (faster or slower).

2.  **Speech-to-Text (STT) Conversion**
    * **Set Language:** Use /language_stt to select the language of your audio/video file. This helps me transcribe more accurately!
    * **Send Media:** Send a voice message, audio file, or video file (max 20MB). I'll transcribe it and send you the text.

3.  **Create Your Own Bot!**
    * **Dedicated Bots:** Use /register_bot if you want to create your own lightweight bot that acts as a dedicated TTS or STT service, powered by this framework! You just provide your bot's token.

4.  **Privacy & Data Handling**
    * **Your Content is Private:** Any text you send for TTS or media you send for STT is processed instantly and **never stored** on our servers. Generated audio files and transcriptions are temporary and deleted after they're sent to you.
    * **Your Settings are Saved (in-memory):** To make your experience seamless, your chosen preferences (like selected TTS voice, pitch, rate, and STT language) are stored in my *temporary in-memory storage*. This means your settings are remembered as long as the bot is running, but they will be *reset if the bot restarts*. We also keep a record of basic activity (such as your last active timestamp and usage counts) for anonymous, aggregated statistics.

---

If you have any questions or run into any issues, don't hesitate to reach out to @user33230.

Enjoy creating and transcribing! ✨
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[user_id] = None
    user_pitch_input_mode[user_id] = None
    user_rate_input_mode[user_id] = None
    user_register_bot_mode[user_id] = None

    privacy_text = (
        """
🔐 *Privacy Notice*

If you have any questions or concerns about your privacy, please feel free to contact the bot administrator at @user33230.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total_registered = len(in_memory_data["users"])
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
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   R E G I S T E R   B O T   F E A T U R E
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['registerbot'])
def register_bot_command(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None

    user_register_bot_mode[uid] = "awaiting_token"
    bot.send_message(message.chat.id,
                     "Alright! To create your own lightweight bot, please send me your **Bot API Token**. "
                     "You can get this from @BotFather on Telegram. It looks like `123456:ABC-DEF1234ghIkl-zyx57W2E1`.")

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    if not (30 < len(bot_token) < 50 and ':' in bot_token):
        bot.send_message(message.chat.id, "That doesn't look like a valid Bot API Token. Please make sure it's correct and try again.")
        return

    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Text-to-Speech (TTS) Bot", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("Speech-to-Text (STT) Bot", callback_data="register_bot_service|stt")
        )
        bot.send_message(message.chat.id,
                         f"Great! I've verified the token for @{bot_info.username}. "
                         "Now, what kind of service should your new bot provide?",
                         reply_markup=markup)

    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error validating token: {e}")
        bot.send_message(message.chat.id,
                         f"❌ I couldn't validate that token with Telegram. It might be invalid or revoked. "
                         "Please check your token from @BotFather and try again. Error: `{e}`", parse_mode="Markdown")
        user_register_bot_mode[uid] = None
    except Exception as e:
        logging.error(f"Unexpected error validating token: {e}")
        bot.send_message(message.chat.id, "An unexpected error occurred while validating your token. Please try again later.")
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

    if not bot_token:
        bot.answer_callback_query(call.id, "Bot token not found. Please start over.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="Something went wrong. Please use /register_bot again.")
        user_register_bot_mode[uid] = None
        return

    # Always register the bot (even if already exists)
    register_child_bot_in_memory(bot_token, uid, service_type)

    try:
        # Set webhook for the child bot
        child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
        temp_child_bot = telebot.TeleBot(bot_token)
        temp_child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)

        # Set commands for the newly registered child bot
        set_child_bot_commands(temp_child_bot, service_type)

        bot.answer_callback_query(call.id, f"✅ Your {service_type.upper()} bot is registered!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Your new *{service_type.upper()} Bot* is now active!\n\n"
                 f"You can find it here: https://t.me/{temp_child_bot.get_me().username}\n\n"
                 f"It will use your settings (voice/pitch/rate for TTS, language for STT) from this main bot. "
                 f"No new server or code needed! Just start interacting with it.",
            parse_mode="Markdown"
        )
        logging.info(f"Webhook set for child bot {temp_child_bot.get_me().username} to {child_bot_webhook_url}")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set webhook for child bot: {e}")
        bot.answer_callback_query(call.id, "Failed to set webhook for your bot. Please try again.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text=f"❌ An error occurred while setting up your bot. Please try again. Error: `{e}`", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Unexpected error during child bot setup: {e}")
        bot.send_message(call.message.chat.id, "An unexpected error occurred during setup. Please try again later.")
        bot.answer_callback_query(call.id, "An unexpected error occurred during setup.")
    finally:
        user_register_bot_mode[uid] = None

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
        "ne-NP-SagarNeural", "ne-NP-HemkalaNeural"
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
    "Mongolian": [
        "mn-MN-BataaNeural", "mn-MN-YesuiNeural"
    ],
    "Maltese": [
        "mt-MT-GraceNeural", "mt-MT-JosephNeural"
    ],
    "Irish": [
        "ga-IE-ColmNeural", "ga-IE-OrlaNeural"
    ],
    "Albanian": [
        "sq-AL-AnilaNeural", "sq-AL-IlirNeural"
    ]
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
    "Afrikaans", "Persian", "Mongolian", "Maltese", "Irish", "Albanian"
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

def handle_rate_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = message.chat.id
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = "awaiting_rate_input"
    user_register_bot_mode[user_id_for_settings] = None

    target_bot.send_message(
        chat_id,
        "How fast should I speak? Choose a preset or enter a custom value from -100 (slowest) to +100 (fastest), with 0 being normal:",
        reply_markup=make_rate_keyboard()
    )

def handle_rate_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_rate_input_mode[user_id_for_settings] = None

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)

        set_tts_user_rate_in_memory(user_id_for_settings, rate_value)

        target_bot.answer_callback_query(call.id, f"Speed set to {rate_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 Your speaking speed is now set to *{rate_value}*.\n\nReady for some text? Or use /voice to change the voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid speed value.")
    except Exception as e:
        logging.error(f"Error setting rate: {e}")
        target_bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['rate'])
def cmd_voice_rate(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_rate_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("rate_set|"))
def on_rate_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_rate_callback(call, bot, uid)

def handle_pitch_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = message.chat.id
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = "awaiting_pitch_input"
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    target_bot.send_message(
        chat_id,
        "Let's adjust the voice pitch! Choose a preset or enter a custom value from -100 (lowest) to +100 (highest), with 0 being normal:",
        reply_markup=make_pitch_keyboard()
    )

def handle_pitch_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)

        set_tts_user_pitch_in_memory(user_id_for_settings, pitch_value)

        target_bot.answer_callback_query(call.id, f"Pitch set to {pitch_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 Your voice pitch is now set to *{pitch_value}*.\n\nReady for some text? Or use /voice to pick a different voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid pitch value.")
    except Exception as e:
        logging.error(f"Error setting pitch: {e}")
        target_bot.answer_callback_query(call.id, "An error occurred.")

@bot.message_handler(commands=['pitch'])
def cmd_voice_pitch(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_pitch_command(message, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("pitch_set|"))
def on_pitch_set_callback(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_pitch_callback(call, bot, uid)

def handle_voice_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = message.chat.id
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    target_bot.send_message(chat_id, "First, choose the *language* for your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

def handle_tts_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    _, lang_name = call.data.split("|", 1)
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"Great! Now select a specific *voice* from the {lang_name} options below. 👇",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name),
        parse_mode="Markdown"
    )
    target_bot.answer_callback_query(call.id)

def handle_tts_voice_change_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_in_memory(user_id_for_settings, voice)
    user_tts_mode[user_id_for_settings] = voice

    current_pitch = get_tts_user_pitch_in_memory(user_id_for_settings)
    current_rate = get_tts_user_rate_in_memory(user_id_for_settings)

    target_bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🔊 Perfect! You're now using: *{voice}*.\n\n"
             f"Current settings:\n"
             f"• Pitch: *{current_pitch}*\n"
             f"• Speed: *{current_rate}*\n\n"
             f"Ready to speak? Just send me your text!",
        parse_mode="Markdown",
        reply_markup=None
    )

def handle_tts_back_to_languages_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="Choose the *language* for your voice. 👇",
        reply_markup=make_tts_language_keyboard(),
        parse_mode="Markdown"
    )
    target_bot.answer_callback_query(call.id)

@bot.message_handler(commands=['voice'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_voice_command(message, bot, user_id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_language_select_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_voice_change_callback(call, bot, uid)

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_tts_back_to_languages_callback(call, bot, uid)

async def synth_and_send_tts(chat_id: int, user_id_for_settings: str, text: str, target_bot: telebot.TeleBot):
    text = text.replace('.', ',')
    voice = get_tts_user_voice_in_memory(user_id_for_settings)
    pitch = get_tts_user_pitch_in_memory(user_id_for_settings)
    rate = get_tts_user_rate_in_memory(user_id_for_settings)
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
        increment_processing_count_in_memory(user_id_for_settings, "tts")

        add_processing_stat_in_memory({
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
        target_bot.send_message(chat_id, f"❌ I ran into a problem while synthesizing the voice: `{e}`. Please try again, or try a different voice.", parse_mode="Markdown")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat_in_memory({
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
        logging.exception("TTS error")
        target_bot.send_message(chat_id, "This voice is not available, please choose another one.")
        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat_in_memory({
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
                logging.error(f"Error deleting TTS file: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   S T T   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def build_stt_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    sorted_languages = sorted(STT_LANGUAGES.items(), key=lambda item: item[0])
    for lang_name, lang_code in sorted_languages:
        buttons.append(
            InlineKeyboardButton(lang_name, callback_data=f"stt_lang|{lang_code}")
        )
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def handle_language_stt_command(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = message.chat.id
    user_tts_mode[user_id_for_settings] = None
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    target_bot.send_message(chat_id, "Choose the *language* for your Speech-to-Text transcription:", reply_markup=build_stt_language_keyboard(), parse_mode="Markdown")

def handle_stt_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang_in_memory(user_id_for_settings, lang_code)

    target_bot.answer_callback_query(call.id, f"✅ Language set to {lang_name}!")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ Transcription language set to: *{lang_name}*\n\n🎙️ Send a voice, audio, or video to transcribe (max 20MB).",
        parse_mode="Markdown",
        reply_markup=None
    )

@bot.message_handler(commands=['language_stt'])
def send_stt_language_prompt(message):
    chat_id = message.chat.id
    user_id = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_language_stt_command(message, bot, user_id)

@bot.callback_query_handler(lambda c: c.data.startswith("stt_lang|"))
def on_stt_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_in_memory(call.from_user.id)

    if call.message.chat.type == 'private' and str(call.from_user.id) != str(ADMIN_ID) and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
    
    handle_stt_language_select_callback(call, bot, uid)

async def process_stt_media(chat_id: int, user_id_for_settings: str, message_type: str, file_id: str, target_bot: telebot.TeleBot, original_message_id: int):
    processing_msg = None
    try:
        processing_msg = target_bot.send_message(chat_id, " Processing...", reply_to_message_id=original_message_id)

        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ File is too large. Max size for transcription is 20MB.")
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

        lang_code = get_stt_user_lang_in_memory(user_id_for_settings)

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
                target_bot.send_message(chat_id, "ℹ️ No transcription returned for this media.", reply_to_message_id=original_message_id)
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, text, reply_to_message_id=original_message_id)
            else:
                import io
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="Your transcription is too long for a single message. Here's the text file:", reply_to_message_id=original_message_id)
            increment_processing_count_in_memory(user_id_for_settings, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Transcription error: `{error_msg}`", parse_mode="Markdown", reply_to_message_id=original_message_id)
            status = "fail_assemblyai_error"
            logging.error(f"AssemblyAI transcription failed: {error_msg}")

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        add_processing_stat_in_memory({
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
        logging.error(f"Network or API error during STT processing: {e}")
        target_bot.send_message(chat_id, "❌ A network error occurred while processing your file. Please try again.", reply_to_message_id=original_message_id)
        status = "fail_network_error"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        add_processing_stat_in_memory({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang_in_memory(user_id_for_settings),
            "error_message": str(e)
        })

    except Exception as e:
        logging.exception(f"Unhandled error during STT processing: {e}")
        target_bot.send_message(chat_id, "The file is too large, please send one that is 20MB or smaller.", reply_to_message_id=original_message_id)
        status = "fail_unknown"
        processing_time = (datetime.now() - processing_start_time).total_seconds() if 'processing_start_time' in locals() else 0
        add_processing_stat_in_memory({
            "user_id": user_id_for_settings,
            "type": "stt",
            "processing_time": processing_time,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "file_type": message_type,
            "file_size": file_info.file_size if 'file_info' in locals() else 0,
            "language_code": get_stt_user_lang_in_memory(user_id_for_settings),
            "error_message": str(e)
        })
    finally:
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")

def handle_stt_media_types_common(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    update_user_activity_in_memory(int(user_id_for_settings))
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
            target_bot.send_message(message.chat.id, "Sorry, I can only transcribe audio and video files. Please send a valid audio or video document.")
            return

    if not file_id:
        target_bot.send_message(message.chat.id, "Unsupported file type for transcription. Please send a voice message, audio file, or video file.")
        return

    if user_id_for_settings not in in_memory_data["stt_settings"]:
        target_bot.send_message(message.chat.id, "❗ Please choose a language for transcription first using /language_stt.")
        return

    threading.Thread(
        target=lambda: asyncio.run(process_stt_media(message.chat.id, user_id_for_settings, message_type, file_id, target_bot, message.message_id))
    ).start()

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_stt_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_stt_media_types_common(message, bot, uid)

def handle_text_for_tts_or_mode_input_common(message, target_bot: telebot.TeleBot, user_id_for_settings: str):
    update_user_activity_in_memory(int(user_id_for_settings))

    if message.text.startswith('/'):
        return

    if user_rate_input_mode.get(user_id_for_settings) == "awaiting_rate_input":
        try:
            rate_val = int(message.text)
            if -100 <= rate_val <= 100:
                set_tts_user_rate_in_memory(user_id_for_settings, rate_val)
                target_bot.send_message(message.chat.id, f"🔊 Voice speed set to *{rate_val}*.", parse_mode="Markdown")
                user_rate_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Invalid speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "That's not a valid number for speed. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    if user_pitch_input_mode.get(user_id_for_settings) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_in_memory(user_id_for_settings, pitch_val)
                target_bot.send_message(message.chat.id, f"🔊 Voice pitch set to *{pitch_val}*.", parse_mode="Markdown")
                user_pitch_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Invalid pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "That's not a valid number for pitch. Please enter a number from -100 to +100 or 0 for normal. Try again:")
            return

    current_voice = get_tts_user_voice_in_memory(user_id_for_settings)

    if current_voice:
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id_for_settings, message.text, target_bot))
        ).start()
    else:
        target_bot.send_message(
            message.chat.id,
            "Looks like you haven't chosen a voice yet! Please use the /voice command first to select one, then send me your text. 🗣️"
        )

@bot.message_handler(content_types=['text'])
def handle_text_for_tts_or_mode_input(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return
    
    handle_text_for_tts_or_mode_input_common(message, bot, uid)

@bot.message_handler(func=lambda m: True, content_types=['sticker', 'photo'])
def handle_unsupported_media_types(message):
    uid = str(message.from_user.id)
    update_user_activity_in_memory(message.from_user.id)

    if message.chat.type == 'private' and str(message.from_user.id) != str(ADMIN_ID) and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    user_tts_mode[uid] = None
    user_pitch_input_mode[uid] = None
    user_rate_input_mode[uid] = None
    user_register_bot_mode[uid] = None

    bot.send_message(
        message.chat.id,
        "Sorry, I can only convert *text messages* into speech or transcribe *voice/audio/video files*. Please send one of those to interact with me!"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S
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

            bot_info = get_child_bot_info_in_memory(child_bot_token)
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
                    if service_type == "stt":
                        welcome_message = (
                            f"👋Salam {user_first_name}\n"
                            "• Send a voice, video, or audio file,\n"
                            "• I’ll transcribe it and send it back to you!\n"
                            "• Choose your media file language,\n"
                            "• Or click /language_stt Powered by https://t.me/MediaToTextBot"
                        )
                    elif service_type == "tts":
                        welcome_message = (
                            f"👋Salam {user_first_name}\n"
                            "• Send me any text and I’ll convert it to audio,\n"
                            "• Then send it back to you!\n"
                            "• Choose your text language and avatar speaking type,\n"
                            "• Or click /voice\n"
                            "• For more commands, go to the Menu Powered by https://t.me/MediaToTextBot"
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
        BotCommand("voice", "Choose a different voice for tts"),
        BotCommand("pitch", "Change TTS pitch"),
        BotCommand("rate", "Change TTS speed"),
        BotCommand("language_stt", "Change default language for stt"),
        BotCommand("registerbot", "Create your own bot"),
        BotCommand("help", " How to use the bot"),
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
            BotCommand("start", "Get Started"),
            BotCommand("voice", "Change TTS voice"),
            BotCommand("pitch", "Change TTS pitch"),
            BotCommand("rate", "Change TTS speed")
        ]
    elif service_type == "stt":
        commands = [
            BotCommand("start", "Get Started"),
            BotCommand("language_stt", "Change language")
        ]
    
    try:
        child_bot_instance.set_my_commands(commands)
        logging.info(f"Commands set successfully for child bot {child_bot_instance.get_me().username} ({service_type}).")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set commands for child bot: {e}")
    except Exception as e:
        logging.error(f"Unexpected error setting commands for child bot: {e}")

def set_webhook_on_startup():
    try:
        bot.delete_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Main bot webhook set successfully to {WEBHOOK_URL}")

        for token, info in in_memory_data["registered_bots"].items():
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
    init_in_memory_data()
    set_webhook_on_startup()
    set_bot_commands()

if __name__ == "__main__":
    if not os.path.exists("tts_audio_cache"):
        os.makedirs("tts_audio_cache")
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
