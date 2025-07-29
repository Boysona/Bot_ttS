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
TOKEN = "7790991731:AAEX7SyxJv0mjH2fZBbZFZ95sAR_6DCYO10"  # Main Bot Token
ADMIN_ID = 5978150981
WEBHOOK_URL = "available-elga-wwmahe-605c287a.koyeb.app/"  # Main Bot Webhook

REQUIRED_CHANNEL = "@news_channals"

bot = telebot.TeleBot(TOKEN, threaded=True)  # Main Bot instance
app = Flask(__name__)

# --- API KEYS ---
ASSEMBLYAI_API_KEY = "894ad2705ab54e33bb011a87b658ede2"  # AssemblyAI for STT

# --- In-memory data storage ---
in_memory_data = {
    "users": {},
    "tts_settings": {},
    "stt_settings": {},
    "registered_bots": {},
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
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaijan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇱": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "Uzbek 🇺🇿": "uz", "Tagalog 🇵🇭": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Swahili 🇰🇪": "sw"
}

# --- IN-MEMORY HELPER FUNCTIONS ---
def init_in_memory_data():
    logging.info("Initializing in-memory data structures.")

def update_user_activity_in_memory(user_id: int):
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
    return in_memory_data["users"].get(user_id)

def increment_processing_count_in_memory(user_id: str, service_type: str):
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
    return in_memory_data["tts_settings"].get(user_id, {}).get("voice", "so-SO-MuuseNeural")

def set_tts_user_voice_in_memory(user_id: str, voice: str):
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["voice"] = voice

def get_tts_user_pitch_in_memory(user_id: str) -> int:
    return in_memory_data["tts_settings"].get(user_id, {}).get("pitch", 0)

def set_tts_user_pitch_in_memory(user_id: str, pitch: int):
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["pitch"] = pitch

def get_tts_user_rate_in_memory(user_id: str) -> int:
    return in_memory_data["tts_settings"].get(user_id, {}).get("rate", 0)

def set_tts_user_rate_in_memory(user_id: str, rate: int):
    if user_id not in in_memory_data["tts_settings"]:
        in_memory_data["tts_settings"][user_id] = {}
    in_memory_data["tts_settings"][user_id]["rate"] = rate

def get_stt_user_lang_in_memory(user_id: str) -> str:
    return in_memory_data["stt_settings"].get(user_id, {}).get("language_code", "en")

def set_stt_user_lang_in_memory(user_id: str, lang_code: str):
    if user_id not in in_memory_data["stt_settings"]:
        in_memory_data["stt_settings"][user_id] = {}
    in_memory_data["stt_settings"][user_id]["language_code"] = lang_code

def register_child_bot_in_memory(token: str, owner_id: str, service_type: str):
    in_memory_data["registered_bots"][token] = {
        "owner_id": owner_id,
        "service_type": service_type,
        "registration_date": datetime.now().isoformat()
    }
    logging.info(f"Child bot {token[:5]}... registered for owner {owner_id} with service {service_type} in memory.")
    return True

def get_child_bot_info_in_memory(token: str) -> dict | None:
    return in_memory_data["registered_bots"].get(token)

def add_processing_stat_in_memory(stat: dict):
    in_memory_data["processing_stats"].append(stat)

# --- UTILITIES ---
def keep_recording(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
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

# --- SUBSCRIPTION CHECK ---
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
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "Click here to join the channel",
                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
            )
        )
        bot.send_message(
            chat_id,
            "🔒 Access Restricted\n\nPlease join our channel to use this bot.\n\nJoin and send /start again.",
            reply_markup=markup
        )

# --- BOT HANDLERS (Main Bot) ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    user_first_name = message.from_user.first_name if message.from_user.first_name else "User"

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
            f"👋 Sala {user_first_name}! Waxaan ahay Kadhig - kaaliyahaaga codka AI ee kaa caawinaya inaad u beddesho qoraalka cod ama codka qoraal - bilaash! 🔊✍️\n\n"
            "✨ **Halkan waa sida loo isticmaalo:**\n"
            "1. **Qoraal u beddel Cod (TTS):**\n"
            "   - Dooro codka `/voice`\n"
            "   - Codkaaga u hagaaji `/pitch` ama `/rate`\n"
            "   - Qoraal ii soo dir, waxaan u beddelayaa cod!\n\n"
            "2. **Cod u beddel Qoraal (STT):**\n"
            "   - Dooro luqadda `/lang`\n"
            "   - Cod, muuqaal ama fayl (ilaa 20MB) ii soo dir\n\n"
            "3. **Samee Bot Gaar ah:**\n"
            "   - Isticmaal `/reg` si aad u abuurto bot kaaga!\n\n"
            "👉 Waxaad igu dari kartaa kooxahaaga sidoo kale - guji badhanka hoose!"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("➕ Ku dar Kooxahaaga", url="https://t.me/mediatotextbot?startgroup=")
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
        "📖 **Sida loo isticmaalo Kadhig Bot?**\n\n"
        "Botkani wuxuu fududeeyaa inaad qoraalka u beddesho cod ama codka/video u beddesho qoraal. Waa kan sida uu u shaqeeyo:\n\n"
        "⸻\n"
        "**1. Qoraal u beddel Cod (TTS):**\n"
        "• **Dooro Cod:** Isticmaal `/voice` si aad u doorato luqadda iyo codka aad rabto,\n"
        "• **Qoraalkaaga soo dir:** Markaad codka doorato, qoraal kasta oo aad ii soo dirto waxaan u soo celin doonaa cod ahaan,\n"
        "• **Hagaaji Codka:**\n"
        "  • Isticmaal `/pitch` si aad u kordhiso ama u yareyso codka,\n"
        "  • Isticmaal `/rate` si aad u dedejiso ama u gaabiso hadalka,\n\n"
        "⸻\n"
        "**2. Cod u beddel Qoraal (STT):**\n"
        "• **Dooro Luqadda:** Isticmaal `/lang` si aad u sheegto luqadda codka ama muuqaalka aad soo dirayso – tani waxay ka caawisaa saxnaanta,\n"
        "• **Soo dir Codka/Muuqaalka:** U soo dir fariin cod, fayl cod ama muuqaal (ilaa 20MB), waxaan kuu soo celin doonaa qoraal ahaan,\n\n"
        "⸻\n"
        "**3. Samee Bot Gaar ah:**\n"
        "• **Bot Shakhsi ah:** Isticmaal `/reg` haddii aad rabto inaad samaysato bot kaaga u shaqeeya TTS ama STT,\n"
        "  Waxaad u baahan tahay oo keliya bot token-kaaga,\n\n"
        "⸻\n"
        "**4. Xogtaada & Asturnaanta:**\n"
        "• **Xogtaadu waa Gaar:** Qoraalka iyo codka aad soo dirto lama keydin – si ku meel gaar ah ayaa loo isticmaalaa,\n"
        "• **Xulashooyinkaaga waa la keydiyaa:** Codka, pitch, iyo xawaaraha aad doorato waxaa lagu keydiyaa ilaa botku dib u bilowdo.\n\n"
        "👉 Su'aal ama dhibaato? La xiriir @user33230\n\n"
        "Ku raaxayso abuurista iyo qorista! ✨"
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
        "🔐 **Ogeysiiska Asturnaanta**\n\n"
        "Haddii aad qabtid su'aalo ama walaac ku saabsan asturnaantaada, fadlan si xor ah ula xiriir maamulaha botka @user33230."
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

# --- REGISTER BOT FEATURE ---
@bot.message_handler(commands=['reg'])
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
                     "Waa yahay! Si aad u abuurto bot kaaga, ii soo dir **Bot API Token**.\n\n"
                     "Haddii aadan haysan, ka hel @BotFather:\n"
                     "1. La hadal @BotFather\n"
                     "2. Dir `/newbot` oo raac tilmaamaha\n"
                     "3. Markuu dhammaado, waxaad heli doontaa token sida `123456:ABC-DEF1234ghIkl-zyx57W2E1`\n\n"
                     "Hadda ii soo dir token-ka!")

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    if not (30 < len(bot_token) < 50 and ':' in bot_token):
        bot.send_message(message.chat.id, "Taasi ma aha Bot API Token sax ah. Fadlan hubi oo isku day mar kale.")
        return

    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Qoraal u beddel Cod (TTS)", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("Cod u beddel Qoraal (STT)", callback_data="register_bot_service|stt")
        )
        bot.send_message(message.chat.id,
                         f"Fiican! Waxaan xaqiijiyay token-ka @**{bot_info.username}**.\n\n"
                         "Hadda dooro waxa botkaagu samayn doono:\n"
                         "• **TTS Bot**: Qoraalka u beddela cod\n"
                         "• **STT Bot**: Codka u beddela qoraal\n\n"
                         "Mid ka door hoose:",
                         reply_markup=markup,
                         parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error validating token: {e}")
        bot.send_message(message.chat.id,
                         "❌ Ma xaqiijin karo token-kaas. Waa laga yaabaa inuu khaldan yahay ama la tirtiray. Fadlan ka hubi @BotFather oo isku day mar kale.")
        user_register_bot_mode[uid] = None
    except Exception as e:
        logging.error(f"Unexpected error validating token: {e}")
        bot.send_message(message.chat.id, "Cilad aan la filayn ayaa dhacday. Fadlan isku day mar kale.")
        user_register_bot_mode[uid] = None

@bot.callback_query_handler(lambda c: c.data.startswith("register_bot_service|") and user_register_bot_mode.get(str(c.from_user.id)) and user_register_bot_mode[str(c.from_user.id)].get("state") == "awaiting_service_type")
def on_register_bot_service_select(call):
    uid = str(call.from_user.id)
    data_state = user_register_bot_mode.get(uid)
    if not data_state or data_state.get("state") != "awaiting_service_type":
        bot.answer_callback_query(call.id, "Xaalad khaldan. Fadlan bilow mar kale `/reg`.")
        return

    bot_token = data_state.get("token")
    _, service_type = call.data.split("|", 1)

    if not bot_token:
        bot.answer_callback_query(call.id, "Token lama helin. Bilow mar kale.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="Waxbaa khaldamay. Isticmaal `/reg` mar kale.")
        user_register_bot_mode[uid] = None
        return

    register_child_bot_in_memory(bot_token, uid, service_type)

    try:
        child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
        temp_child_bot = telebot.TeleBot(bot_token)
        temp_child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)
        set_child_bot_commands(temp_child_bot, service_type)

        bot.answer_callback_query(call.id, f"✅ Botkaaga {service_type.upper()} waa la diiwaangeliyay!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Botkaaga cusub *{service_type.upper()}* waa la dhaqaajiyay!\n\n"
                 f"Ka hel halkan: https://t.me/{temp_child_bot.get_me().username}\n\n"
                 f"Waxay isticmaali doontaa xulashooyinkaaga (codka/pitch/rate ee TTS, luqadda ee STT) ee botkan weyn.\n"
                 f"• TTS: Isticmaal `/voice`, `/pitch`, `/rate`, oo qoraal soo dir\n"
                 f"• STT: Isticmaal `/lang` oo cod soo dir",
            parse_mode="Markdown"
        )
        logging.info(f"Webhook set for child bot {temp_child_bot.get_me().username} to {child_bot_webhook_url}")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set webhook for child bot: {e}")
        bot.answer_callback_query(call.id, "Ku guuldareystay in la dejiyo botkaaga. Isku day mar kale.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text="❌ Cilad ayaa dhacday markii la dejinayay botkaaga. Isku day mar kale.")
    except Exception as e:
        logging.error(f"Unexpected error during child bot setup: {e}")
        bot.send_message(call.message.chat.id, "Cilad aan la filayn ayaa dhacday. Isku day mar kale.")
        bot.answer_callback_query(call.id, "Cilad aan la filayn ayaa dhacday.")
    finally:
        user_register_bot_mode[uid] = None

# --- TTS FUNCTIONS ---
TTS_VOICES_BY_LANGUAGE = {
    "Arabic": ["ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", "ar-BH-AliNeural", "ar-BH-LailaNeural", "ar-EG-SalmaNeural", "ar-EG-ShakirNeural", "ar-IQ-BasselNeural", "ar-IQ-RanaNeural", "ar-JO-SanaNeural", "ar-JO-TaimNeural", "ar-KW-FahedNeural", "ar-KW-NouraNeural", "ar-LB-LaylaNeural", "ar-LB-RamiNeural", "ar-LY-ImanNeural", "ar-LY-OmarNeural", "ar-MA-JamalNeural", "ar-MA-MounaNeural", "ar-OM-AbdullahNeural", "ar-OM-AyshaNeural", "ar-QA-AmalNeural", "ar-QA-MoazNeural", "ar-SA-HamedNeural", "ar-SA-ZariyahNeural", "ar-SY-AmanyNeural", "ar-SY-LaithNeural", "ar-TN-HediNeural", "ar-TN-ReemNeural", "ar-AE-FatimaNeural", "ar-AE-HamdanNeural", "ar-YE-MaryamNeural", "ar-YE-SalehNeural"],
    "English": ["en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-ClaraNeural", "en-CA-LiamNeural", "en-HK-SamNeural", "en-HK-YanNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural", "en-IE-ConnorNeural", "en-IE-EmilyNeural", "en-KE-AsiliaNeural", "en-KE-ChilembaNeural", "en-NZ-MitchellNeural", "en-NZ-MollyNeural", "en-NG-AbeoNeural", "en-NG-EzinneNeural", "en-PH-James", "en-PH-RosaNeural", "en-SG-LunaNeural", "en-SG-WayneNeural", "en-ZA-LeahNeural", "en-ZA-LukeNeural", "en-TZ-ElimuNeural", "en-TZ-ImaniNeural", "en-GB-LibbyNeural", "en-GB-MaisieNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural", "en-GB-ThomasNeural", "en-US-AriaNeural", "en-US-AnaNeural", "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-MichelleNeural", "en-US-RogerNeural", "en-US-SteffanNeural"],
    "Spanish": ["es-AR-ElenaNeural", "es-AR-TomasNeural", "es-BO-MarceloNeural", "es-BO-SofiaNeural", "es-CL-CatalinaNeural", "es-CL-LorenzoNeural", "es-CO-GonzaloNeural", "es-CO-SalomeNeural", "es-CR-JuanNeural", "es-CR-MariaNeural", "es-CU-BelkysNeural", "es-CU-ManuelNeural", "es-DO-EmilioNeural", "es-DO-RamonaNeural", "es-EC-AndreaNeural", "es-EC-LorenaNeural", "es-SV-RodrigoNeural", "es-SV-LorenaNeural", "es-GQ-JavierNeural", "es-GQ-TeresaNeural", "es-GT-AndresNeural", "es-GT-MartaNeural", "es-HN-CarlosNeural", "es-HN-KarlaNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural", "es-NI-FedericoNeural", "es-NI-YolandaNeural", "es-PA-MargaritaNeural", "es-PA-RobertoNeural", "es-PY-MarioNeural", "es-PY-TaniaNeural", "es-PE-AlexNeural", "es-PE-CamilaNeural", "es-PR-KarinaNeural", "es-PR-VictorNeural", "es-ES-AlvaroNeural", "es-ES-ElviraNeural", "es-US-AlonsoNeural", "es-US-PalomaNeural", "es-UY-MateoNeural", "es-UY-ValentinaNeural", "es-VE-PaolaNeural", "es-VE-SebastianNeural"],
    "Hindi": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "French": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural", "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-GerardNeural"],
    "German": ["de-DE-KatjaNeural", "de-DE-ConradNeural", "de-CH-LeniNeural", "de-CH-JanNeural", "de-AT-IngridNeural", "de-AT-JonasNeural"],
    "Chinese": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-YunjianNeural", "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural"],
    "Japanese": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"],
    "Portuguese": ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"],
    "Russian": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"],
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
    "Greek": ["el-GR-AthinaNeural", "el-GR-NestorasNeural"],
    "Hebrew": ["he-IL-AvriNeural", "he-IL-HilaNeural"],
    "Hungarian": ["hu-HU-NoemiNeural", "hu-HU-AndrasNeural"],
    "Czech": ["cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"],
    "Danish": ["da-DK-ChristelNeural", "da-DK-JeppeNeural"],
    "Finnish": ["fi-FI-SelmaNeural", "fi-FI-HarriNeural"],
    "Norwegian": ["nb-NO-PernilleNeural", "nb-NO-FinnNeural"],
    "Romanian": ["ro-RO-AlinaNeural", "ro-RO-EmilNeural"],
    "Slovak": ["sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"],
    "Ukrainian": ["uk-UA-PolinaNeural", "uk-UA-OstapNeural"],
    "Malay": ["ms-MY-YasminNeural", "ms-MY-OsmanNeural"],
    "Bengali": ["bn-BD-NabanitaNeural", "bn-BD-BasharNeural"],
    "Urdu": ["ur-PK-AsmaNeural", "ur-PK-FaizanNeural"],
    "Nepali": ["ne-NP-SagarNeural", "ne-NP-HemkalaNeural"],
    "Sinhala": ["si-LK-SameeraNeural", "si-LK-ThiliniNeural"],
    "Lao": ["lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"],
    "Myanmar": ["my-MM-NilarNeural", "my-MM-ThihaNeural"],
    "Georgian": ["ka-GE-EkaNeural", "ka-GE-GiorgiNeural"],
    "Armenian": ["hy-AM-AnahitNeural", "hy-AM-AraratNeural"],
    "Azerbaijani": ["az-AZ-BabekNeural", "az-AZ-BanuNeural"],
    "Uzbek": ["uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"],
    "Serbian": ["sr-RS-NikolaNeural", "sr-RS-SophieNeural"],
    "Croatian": ["hr-HR-GabrijelaNeural", "hr-HR-SreckoNeural"],
    "Slovenian": ["sl-SI-PetraNeural", "sl-SI-RokNeural"],
    "Latvian": ["lv-LV-EveritaNeural", "lv-LV-AnsisNeural"],
    "Lithuanian": ["lt-LT-OnaNeural", "lt-LT-LeonasNeural"],
    "Amharic": ["am-ET-MekdesNeural", "am-ET-AbebeNeural"],
    "Swahili": ["sw-KE-ZuriNeural", "sw-KE-RafikiNeural"],
    "Zulu": ["zu-ZA-ThandoNeural", "zu-ZA-ThembaNeural"],
    "Afrikaans": ["af-ZA-AdriNeural", "af-ZA-WillemNeural"],
    "Somali": ["so-SO-UbaxNeural", "so-SO-MuuseNeural"],
    "Persian": ["fa-IR-DilaraNeural", "fa-IR-ImanNeural"],
    "Mongolian": ["mn-MN-BataaNeural", "mn-MN-YesuiNeural"],
    "Maltese": ["mt-MT-GraceNeural", "mt-MT-JosephNeural"],
    "Irish": ["ga-IE-ColmNeural", "ga-IE-OrlaNeural"],
    "Albanian": ["sq-AL-AnilaNeural", "sq-AL-IlirNeural"]
}

ORDERED_TTS_LANGUAGES = [
    "English", "Arabic", "Spanish", "French", "German", "Chinese", "Japanese", "Portuguese", "Russian", "Turkish",
    "Hindi", "Somali", "Italian", "Indonesian", "Vietnamese", "Thai", "Korean", "Dutch", "Polish", "Swedish",
    "Filipino", "Greek", "Hebrew", "Hungarian", "Czech", "Danish", "Finnish", "Norwegian", "Romanian", "Slovak",
    "Ukrainian", "Malay", "Bengali", "Urdu", "Nepali", "Sinhala", "Lao", "Myanmar", "Georgian", "Armenian",
    "Azerbaijani", "Uzbek", "Serbian", "Croatian", "Slovenian", "Latvian", "Lithuanian", "Amharic", "Swahili", "Zulu",
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
    markup.add(InlineKeyboardButton("⬅️ Ku noqo Luqadaha", callback_data="tts_back_to_languages"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⬆️ Sare", callback_data="pitch_set|+50"),
        InlineKeyboardButton("⬇️ Hoose", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Dib u deji", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ Degdeg", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Gaabis", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Dib u deji", callback_data="rate_set|0")
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
        "Sidee baan ugu hadlaa degdeg ama gaabis? Dooro mid ama soo dir nambar -100 (gaabis) ilaa +100 (degdeg), 0 waa caadi:",
        reply_markup=make_rate_keyboard()
    )

def handle_rate_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_rate_input_mode[user_id_for_settings] = None

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)
        set_tts_user_rate_in_memory(user_id_for_settings, rate_value)
        target_bot.answer_callback_query(call.id, f"Xawaaraha waa {rate_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 Xawaaraha hadalka waa *{rate_value}*.\n\nDiyaar ma u tahay qoraal? Ama isticmaal `/voice` si aad u beddesho codka.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Xawaare khaldan.")
    except Exception as e:
        logging.error(f"Error setting rate: {e}")
        target_bot.answer_callback_query(call.id, "Cilad ayaa dhacday.")

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
        "Aan hagaajino codka! Dooro mid ama soo dir nambar -100 (hoose) ilaa +100 (sare), 0 waa caadi:",
        reply_markup=make_pitch_keyboard()
    )

def handle_pitch_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)
        set_tts_user_pitch_in_memory(user_id_for_settings, pitch_value)
        target_bot.answer_callback_query(call.id, f"Codka waa {pitch_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 Codka waa *{pitch_value}*.\n\nDiyaar ma u tahay qoraal? Ama isticmaal `/voice` si aad u beddesho codka.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Cod khaldan.")
    except Exception as e:
        logging.error(f"Error setting pitch: {e}")
        target_bot.answer_callback_query(call.id, "Cilad ayaa dhacday.")

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

    target_bot.send_message(chat_id, "Marka hore, dooro *luqadda* codkaaga. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

def handle_tts_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    _, lang_name = call.data.split("|", 1)
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"Waa yahay! Hadda dooro *cod* gaar ah oo ka mid ah {lang_name}. 👇",
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

    target_bot.answer_callback_query(call.id, f"✔️ Codka waa {voice}")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🔊 Wanaagsan! Waxaad isticmaalaysaa: *{voice}*.\n\n"
             f"Xulashooyinka hadda:\n"
             f"• Codka: *{current_pitch}*\n"
             f"• Xawaaraha: *{current_rate}*\n\n"
             f"Diyaar ma u tahay inaad hadasho? Qoraal ii soo dir!",
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
        text="Dooro *luqadda* codkaaga. 👇",
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
            target_bot.send_message(chat_id, "❌ Waan ku guuldareystay inaan sameeyo codka. Isku day qoraal kale.")
            return

        with open(filename, "rb") as f:
            target_bot.send_audio(
                chat_id,
                f,
                caption=f"🎧 *Waa kan codkaaga!* \n\n"
                        f"Codka: *{voice}*\n"
                        f"Pitch: *{pitch}*\n"
                        f"Xawaare: *{rate}*\n\n"
                        f"Ku raaxayso dhageysiga! ✨",
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
        target_bot.send_message(chat_id, "❌ Cilad ayaa dhacday markii la samaynayay codka. Isku day mar kale ama dooro cod kale.")
    except Exception as e:
        logging.exception("TTS error")
        target_bot.send_message(chat_id, "Codkani ma heli karo, fadlan dooro mid kale.")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting TTS file: {e}")

# --- STT FUNCTIONS ---
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

    target_bot.send_message(chat_id, "Dooro *luqadda* ee qoraalkaaga Cod-u-Qoraal:", reply_markup=build_stt_language_keyboard(), parse_mode="Markdown")

def handle_stt_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang_in_memory(user_id_for_settings, lang_code)

    target_bot.answer_callback_query(call.id, f"✅ Luqadda waa {lang_name}!")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ Luqadda qoraalka waa: *{lang_name}*\n\n🎙️ Soo dir cod, fayl ama muuqaal (ilaa 20MB) si aan u qoro.",
        parse_mode="Markdown",
        reply_markup=None
    )

@bot.message_handler(commands=['lang'])
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
        processing_msg = target_bot.send_message(chat_id, " Wax ka qabanayaa...", reply_to_message_id=original_message_id)
        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ Faylka waa weyn yahay. Cabirka ugu badan waa 20MB. Soo dir fayl yar.", reply_to_message_id=original_message_id)
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
                target_bot.send_message(chat_id, "ℹ️ Ma jiro qoraal laga soo qaatay faylkan.", reply_to_message_id=original_message_id)
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, text, reply_to_message_id=original_message_id)
            else:
                import io
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="Qoraalkaagu waa dheer yahay. Waa kan faylka:", reply_to_message_id=original_message_id)
            increment_processing_count_in_memory(user_id_for_settings, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Cilad qoraal: {error_msg}", parse_mode="Markdown", reply_to_message_id=original_message_id)
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
        target_bot.send_message(chat_id, "❌ Cilad shabakadeed ayaa dhacday. Isku day mar kale.", reply_to_message_id=original_message_id)
    except Exception as e:
        logging.exception(f"Unhandled error during STT processing: {e}")
        target_bot.send_message(chat_id, "Faylka waa weyn yahay, soo dir mid ka yar 20MB.", reply_to_message_id=original_message_id)
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
            target_bot.send_message(message.chat.id, "Waan ka xumahay, kaliya waxaan qori karaa cod ama muuqaal. Soo dir fayl sax ah.")
            return

    if not file_id:
        target_bot.send_message(message.chat.id, "Nooca faylkan ma taageersani. Soo dir cod, fayl ama muuqaal.")
        return

    if user_id_for_settings not in in_memory_data["stt_settings"]:
        target_bot.send_message(message.chat.id, "❗ Marka hore dooro luqadda isticmaalka `/lang`.")
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
                target_bot.send_message(message.chat.id, f"🔊 Xawaaraha codka waa *{rate_val}*.", parse_mode="Markdown")
                user_rate_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Xawaare khaldan. Soo dir nambar -100 ilaa +100 ama 0. Isku day mar kale:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "Taasi maaha nambar sax ah. Soo dir nambar -100 ilaa +100 ama 0. Isku day mar kale:")
            return

    if user_pitch_input_mode.get(user_id_for_settings) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_in_memory(user_id_for_settings, pitch_val)
                target_bot.send_message(message.chat.id, f"🔊 Codka waa *{pitch_val}*.", parse_mode="Markdown")
                user_pitch_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Cod khaldan. Soo dir nambar -100 ilaa +100 ama 0. Isku day mar kale:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "Taasi maaha nambar sax ah. Soo dir nambar -100 ilaa +100 ama 0. Isku day mar kale:")
            return

    current_voice = get_tts_user_voice_in_memory(user_id_for_settings)
    if current_voice:
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id_for_settings, message.text, target_bot))
        ).start()
    else:
        target_bot.send_message(
            message.chat.id,
            "Wali ma aadan dooran cod! Isticmaal `/voice` marka hore, ka dibna qoraal ii soo dir. 🗣️"
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
        "Waan ka xumahay, kaliya waxaan u beddeli karaa *qoraalka* cod ama *codka/faylka* qoraal. Soo dir mid ka mid ah!"
    )

# --- FLASK ROUTES ---
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
            user_first_name = "User"
            if message:
                user_id_for_settings = str(message.from_user.id)
                user_first_name = message.from_user.first_name if message.from_user.first_name else "User"
            elif callback_query:
                user_id_for_settings = str(callback_query.from_user.id)
                user_first_name = callback_query.from_user.first_name if callback_query.from_user.first_name else "User"

            if not user_id_for_settings:
                return "", 200

            if message:
                chat_id = message.chat.id
                if message.text and message.text.startswith('/start'):
                    if service_type == "stt":
                        welcome_message = (
                            f"👋 Sala {user_first_name}!\n"
                            "• Soo dir cod, muuqaal ama fayl,\n"
                            "• Waxaan u beddelayaa qoraal oo kuu soo celinayaa!\n"
                            "• Dooro luqadda faylkaaga `/lang`\n"
                        )
                    elif service_type == "tts":
                        welcome_message = (
                            f"👋 Sala {user_first_name}!\n"
                            "• Qoraal ii soo dir, waxaan u beddelayaa cod,\n"
                            "• Ka dibna kuu soo celinayaa!\n"
                            "• Dooro luqadda iyo codka `/voice`\n"
                        )
                    else:
                        welcome_message = f"👋 Soo dhawoow! Waxaan ahay botkaaga {service_type.upper()}."
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
                        if message.text.startswith('/lang'):
                            handle_language_stt_command(message, child_bot_instance, user_id_for_settings)
                            return "", 200
                        else:
                            child_bot_instance.send_message(chat_id, "Waxaan ahay STT bot. Soo dir cod, fayl ama muuqaal si aan u qoro, ama isticmaal `/lang`.")
                            return "", 200
                elif message.voice or message.audio or message.video or message.document:
                    if service_type == "stt":
                        handle_stt_media_types_common(message, child_bot_instance, user_id_for_settings)
                    else:
                        child_bot_instance.send_message(chat_id, "Waxaan ahay TTS bot. Soo dir qoraal si aan u beddelo cod.")
                    return "", 200
                else:
                    child_bot_instance.send_message(chat_id, "Waan ka xumahay, waxaan ka shaqayn karaa noocyada fariimaha qaarkood. Eeg `/start`.")
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
                child_bot_instance.answer_callback_query(call.id, "Tallaabadani ma heli karto nooca botkan.")
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
        BotCommand("start", "Bilow"),
        BotCommand("voice", "Dooro codka TTS"),
        BotCommand("pitch", "Beddel codka"),
        BotCommand("rate", "Beddel xawaaraha"),
        BotCommand("lang", "Dooro luqadda STT"),
        BotCommand("reg", "Samee bot kaaga"),
        BotCommand("help", "Sida loo isticmaalo"),
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
            BotCommand("start", "Bilow"),
            BotCommand("voice", "Beddel codka TTS"),
            BotCommand("pitch", "Beddel codka"),
            BotCommand("rate", "Beddel xawaaraha")
        ]
    elif service_type == "stt":
        commands = [
            BotCommand("start", "Bilow"),
            BotCommand("lang", "Beddel luqadda")
        ]
    try:
        child_bot_instance.set_my_commands(commands)
        logging.info(f"Commands set successfully for child bot {child_bot_instance.get_me().username} ({service_type}).")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set commands for child bot: {e}")

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
