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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

TOKEN = "7790991731:AAHuk1pyLuwKp2qfbi8vdAxtiUKoUY6jKyQ"
ADMIN_ID = 5978150981
WEBHOOK_URL = "available-elga-wwmahe-605c287a.koyeb.app/"

REQUIRED_CHANNEL = "@guruubka_wasmada"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

ASSEMBLYAI_API_KEY = "894ad2705ab54e33bb011a87b658ede2"

in_memory_data = {
    "users": {},
    "tts_settings": {},
    "stt_settings": {},
    "registered_bots": {},
}

user_tts_mode = {}
user_pitch_input_mode = {}
user_rate_input_mode = {}
user_register_bot_mode = {}

admin_state = {}

processing_message_ids = {}

STT_LANGUAGES = {
    "English 🇬🇧": "en", "Deutsch 🇩🇪": "de", "Русский 🇷🇺": "ru", "فارسى 🇮🇷": "fa",
    "Indonesia 🇮🇩": "id", "Казакша 🇰🇿": "kk", "Azerbaijan 🇦🇿": "az", "Italiano 🇮🇹": "it",
    "Türkçe 🇹🇷": "tr", "Български 🇧🇬": "bg", "Sroski 🇷🇸": "sr", "Français 🇫🇷": "fr",
    "العربية 🇸🇦": "ar", "Español 🇪🇸": "es", "اردو 🇵🇰": "ur", "ไทย 🇹🇱": "th",
    "Tiếng Việt 🇻🇳": "vi", "日本語 🇯🇵": "ja", "한국어 🇰🇷": "ko", "中文 🇨🇳": "zh",
    "Nederlands 🇳🇱": "nl", "Svenska 🇸🇪": "sv", "Norsk 🇳🇴": "no", "Dansk 🇩🇰": "da",
    "Suomi 🇫🇮": "fi", "Polski 🇵🇱": "pl", "Cestina 🇨🇿": "cs", "Magyar 🇭🇺": "hu",
    "Română 🇷🇴": "ro", "Melayu 🇲🇾": "ms", "Uzbek 🇺🇿": "uz", "Tagalog 🇵🇱": "tl",
    "Português 🇵🇹": "pt", "हिन्दी 🇮🇳": "hi", "Swahili 🇰🇪": "sw"
}

# --- Kani waa qaamuuska cusub ee magacyada codadka oo la fududeeyay ---
VOICE_MAPPING = {
    "ar-DZ-AminaNeural": "Amina - Arabic (Algeria)", "ar-DZ-IsmaelNeural": "Ismael - Arabic (Algeria)",
    "ar-BH-AliNeural": "Ali - Arabic (Bahrain)", "ar-BH-LailaNeural": "Laila - Arabic (Bahrain)",
    "ar-EG-SalmaNeural": "Salma - Arabic (Egypt)", "ar-EG-ShakirNeural": "Shakir - Arabic (Egypt)",
    "ar-IQ-BasselNeural": "Bassel - Arabic (Iraq)", "ar-IQ-RanaNeural": "Rana - Arabic (Iraq)",
    "ar-JO-SanaNeural": "Sana - Arabic (Jordan)", "ar-JO-TaimNeural": "Taim - Arabic (Jordan)",
    "ar-KW-FahedNeural": "Fahed - Arabic (Kuwait)", "ar-KW-NouraNeural": "Noura - Arabic (Kuwait)",
    "ar-LB-LaylaNeural": "Layla - Arabic (Lebanon)", "ar-LB-RamiNeural": "Rami - Arabic (Lebanon)",
    "ar-LY-ImanNeural": "Iman - Arabic (Libya)", "ar-LY-OmarNeural": "Omar - Arabic (Libya)",
    "ar-MA-JamalNeural": "Jamal - Arabic (Morocco)", "ar-MA-MounaNeural": "Mouna - Arabic (Morocco)",
    "ar-OM-AbdullahNeural": "Abdullah - Arabic (Oman)", "ar-OM-AyshaNeural": "Aysha - Arabic (Oman)",
    "ar-QA-AmalNeural": "Amal - Arabic (Qatar)", "ar-QA-MoazNeural": "Moaz - Arabic (Qatar)",
    "ar-SA-HamedNeural": "Hamed - Arabic (Saudi Arabia)", "ar-SA-ZariyahNeural": "Zariyah - Arabic (Saudi Arabia)",
    "ar-SY-AmanyNeural": "Amany - Arabic (Syria)", "ar-SY-LaithNeural": "Laith - Arabic (Syria)",
    "ar-TN-HediNeural": "Hedi - Arabic (Tunisia)", "ar-TN-ReemNeural": "Reem - Arabic (Tunisia)",
    "ar-AE-FatimaNeural": "Fatima - Arabic (UAE)", "ar-AE-HamdanNeural": "Hamdan - Arabic (UAE)",
    "ar-YE-MaryamNeural": "Maryam - Arabic (Yemen)", "ar-YE-SalehNeural": "Saleh - Arabic (Yemen)",
    "en-AU-NatashaNeural": "Natasha - English (Australia)", "en-AU-WilliamNeural": "William - English (Australia)",
    "en-CA-ClaraNeural": "Clara - English (Canada)", "en-CA-LiamNeural": "Liam - English (Canada)",
    "en-HK-SamNeural": "Sam - English (Hongkong)", "en-HK-YanNeural": "Yan - English (Hongkong)",
    "en-IN-NeerjaNeural": "Neerja - English (India)", "en-IN-PrabhatNeural": "Prabhat - English (India)",
    "en-IE-ConnorNeural": "Connor - English (Ireland)", "en-IE-EmilyNeural": "Emily - English (Ireland)",
    "en-KE-AsiliaNeural": "Asilia - English (Kenya)", "en-KE-ChilembaNeural": "Chilemba - English (Kenya)",
    "en-NZ-MitchellNeural": "Mitchell - English (New Zealand)", "en-NZ-MollyNeural": "Molly - English (New Zealand)",
    "en-NG-AbeoNeural": "Abeo - English (Nigeria)", "en-NG-EzinneNeural": "Ezinne - English (Nigeria)",
    "en-PH-James": "James - English (Philippines)", "en-PH-RosaNeural": "Rosa - English (Philippines)",
    "en-SG-LunaNeural": "Luna - English (Singapore)", "en-SG-WayneNeural": "Wayne - English (Singapore)",
    "en-ZA-LeahNeural": "Leah - English (South Africa)", "en-ZA-LukeNeural": "Luke - English (South Africa)",
    "en-TZ-ElimuNeural": "Elimu - English (Tanzania)", "en-TZ-ImaniNeural": "Imani - English (Tanzania)",
    "en-GB-LibbyNeural": "Libby - English (United Kingdom)", "en-GB-MaisieNeural": "Maisie - English (United Kingdom)",
    "en-GB-RyanNeural": "Ryan - English (United Kingdom)", "en-GB-SoniaNeural": "Sonia - English (United Kingdom)",
    "en-GB-ThomasNeural": "Thomas - English (United Kingdom)",
    "en-US-AriaNeural": "Aria - English (United States)", "en-US-AnaNeural": "Ana - English (United States)",
    "en-US-ChristopherNeural": "Christopher - English (United States)", "en-US-EricNeural": "Eric - English (United States)",
    "en-US-GuyNeural": "Guy - English (United States)", "en-US-JennyNeural": "Jenny - English (United States)",
    "en-US-MichelleNeural": "Michelle - English (United States)", "en-US-RogerNeural": "Roger - English (United States)",
    "en-US-SteffanNeural": "Steffan - English (United States)",
    "es-AR-ElenaNeural": "Elena - Spanish (Argentina)", "es-AR-TomasNeural": "Tomas - Spanish (Argentina)",
    "es-BO-MarceloNeural": "Marcelo - Spanish (Bolivia)", "es-BO-SofiaNeural": "Sofia - Spanish (Bolivia)",
    "es-CL-CatalinaNeural": "Catalina - Spanish (Chile)", "es-CL-LorenzoNeural": "Lorenzo - Spanish (Chile)",
    "es-CO-GonzaloNeural": "Gonzalo - Spanish (Colombia)", "es-CO-SalomeNeural": "Salome - Spanish (Colombia)",
    "es-CR-JuanNeural": "Juan - Spanish (Costa Rica)", "es-CR-MariaNeural": "Maria - Spanish (Costa Rica)",
    "es-CU-BelkysNeural": "Belkys - Spanish (Cuba)", "es-CU-ManuelNeural": "Manuel - Spanish (Cuba)",
    "es-DO-EmilioNeural": "Emilio - Spanish (Dominican Republic)", "es-DO-RamonaNeural": "Ramona - Spanish (Dominican Republic)",
    "es-EC-AndreaNeural": "Andrea - Spanish (Ecuador)", "es-EC-LorenaNeural": "Lorena - Spanish (Ecuador)",
    "es-SV-RodrigoNeural": "Rodrigo - Spanish (El Salvador)", "es-SV-LorenaNeural": "Lorena - Spanish (El Salvador)",
    "es-GQ-JavierNeural": "Javier - Spanish (Equatorial Guinea)", "es-GQ-TeresaNeural": "Teresa - Spanish (Equatorial Guinea)",
    "es-GT-AndresNeural": "Andres - Spanish (Guatemala)", "es-GT-MartaNeural": "Marta - Spanish (Guatemala)",
    "es-HN-CarlosNeural": "Carlos - Spanish (Honduras)", "es-HN-KarlaNeural": "Karla - Spanish (Honduras)",
    "es-MX-DaliaNeural": "Dalia - Spanish (Mexico)", "es-MX-JorgeNeural": "Jorge - Spanish (Mexico)",
    "es-NI-FedericoNeural": "Federico - Spanish (Nicaragua)", "es-NI-YolandaNeural": "Yolanda - Spanish (Nicaragua)",
    "es-PA-MargaritaNeural": "Margarita - Spanish (Panama)", "es-PA-RobertoNeural": "Roberto - Spanish (Panama)",
    "es-PY-MarioNeural": "Mario - Spanish (Paraguay)", "es-PY-TaniaNeural": "Tania - Spanish (Paraguay)",
    "es-PE-AlexNeural": "Alex - Spanish (Peru)", "es-PE-CamilaNeural": "Camila - Spanish (Peru)",
    "es-PR-KarinaNeural": "Karina - Spanish (Puerto Rico)", "es-PR-VictorNeural": "Victor - Spanish (Puerto Rico)",
    "es-ES-AlvaroNeural": "Alvaro - Spanish (Spain)", "es-ES-ElviraNeural": "Elvira - Spanish (Spain)",
    "es-US-AlonsoNeural": "Alonso - Spanish (United States)", "es-US-PalomaNeural": "Paloma - Spanish (United States)",
    "es-UY-MateoNeural": "Mateo - Spanish (Uruguay)", "es-UY-ValentinaNeural": "Valentina - Spanish (Uruguay)",
    "es-VE-PaolaNeural": "Paola - Spanish (Venezuela)", "es-VE-SebastianNeural": "Sebastian - Spanish (Venezuela)",
    "hi-IN-SwaraNeural": "Swara - Hindi (India)", "hi-IN-MadhurNeural": "Madhur - Hindi (India)",
    "fr-FR-DeniseNeural": "Denise - French (France)", "fr-FR-HenriNeural": "Henri - French (France)",
    "fr-CA-SylvieNeural": "Sylvie - French (Canada)", "fr-CA-JeanNeural": "Jean - French (Canada)",
    "fr-CH-ArianeNeural": "Ariane - French (Switzerland)", "fr-CH-FabriceNeural": "Fabrice - French (Switzerland)",
    "fr-CH-GerardNeural": "Gerard - French (Switzerland)",
    "de-DE-KatjaNeural": "Katja - German (Germany)", "de-DE-ConradNeural": "Conrad - German (Germany)",
    "de-CH-LeniNeural": "Leni - German (Switzerland)", "de-CH-JanNeural": "Jan - German (Switzerland)",
    "de-AT-IngridNeural": "Ingrid - German (Austria)", "de-AT-JonasNeural": "Jonas - German (Austria)",
    "zh-CN-XiaoxiaoNeural": "Xiaoxiao - Chinese (Mandarin, Simplified)",
    "zh-CN-YunyangNeural": "Yunyang - Chinese (Mandarin, Simplified)",
    "zh-CN-YunjianNeural": "Yunjian - Chinese (Mandarin, Simplified)",
    "zh-TW-HsiaoChenNeural": "HsiaoChen - Chinese (Taiwan)", "zh-TW-YunJheNeural": "YunJhe - Chinese (Taiwan)",
    "zh-HK-HiuMaanNeural": "HiuMaan - Chinese (Hong Kong)", "zh-HK-WanLungNeural": "WanLung - Chinese (Hong Kong)",
    "ja-JP-NanamiNeural": "Nanami - Japanese (Japan)", "ja-JP-KeitaNeural": "Keita - Japanese (Japan)",
    "pt-BR-FranciscaNeural": "Francisca - Portuguese (Brazil)", "pt-BR-AntonioNeural": "Antonio - Portuguese (Brazil)",
    "pt-PT-RaquelNeural": "Raquel - Portuguese (Portugal)", "pt-PT-DuarteNeural": "Duarte - Portuguese (Portugal)",
    "ru-RU-SvetlanaNeural": "Svetlana - Russian (Russia)", "ru-RU-DmitryNeural": "Dmitry - Russian (Russia)",
    "ru-RU-LarisaNeural": "Larisa - Russian (Russia)", "ru-RU-MaximNeural": "Maxim - Russian (Russia)",
    "tr-TR-EmelNeural": "Emel - Turkish (Turkey)", "tr-TR-AhmetNeural": "Ahmet - Turkish (Turkey)",
    "ko-KR-SunHiNeural": "SunHi - Korean (Korea)", "ko-KR-InJoonNeural": "InJoon - Korean (Korea)",
    "it-IT-ElsaNeural": "Elsa - Italian (Italy)", "it-IT-DiegoNeural": "Diego - Italian (Italy)",
    "id-ID-GadisNeural": "Gadis - Indonesian (Indonesia)", "id-ID-ArdiNeural": "Ardi - Indonesian (Indonesia)",
    "vi-VN-HoaiMyNeural": "HoaiMy - Vietnamese (Vietnam)", "vi-VN-NamMinhNeural": "NamMinh - Vietnamese (Vietnam)",
    "th-TH-PremwadeeNeural": "Premwadee - Thai (Thailand)", "th-TH-NiwatNeural": "Niwat - Thai (Thailand)",
    "nl-NL-ColetteNeural": "Colette - Dutch (Netherlands)", "nl-NL-MaartenNeural": "Maarten - Dutch (Netherlands)",
    "pl-PL-ZofiaNeural": "Zofia - Polish (Poland)", "pl-PL-MarekNeural": "Marek - Polish (Poland)",
    "sv-SE-SofieNeural": "Sofie - Swedish (Sweden)", "sv-SE-MattiasNeural": "Mattias - Swedish (Sweden)",
    "fil-PH-BlessicaNeural": "Blessica - Filipino (Philippines)", "fil-PH-AngeloNeural": "Angelo - Filipino (Philippines)",
    "el-GR-AthinaNeural": "Athina - Greek (Greece)", "el-GR-NestorasNeural": "Nestoras - Greek (Greece)",
    "he-IL-AvriNeural": "Avri - Hebrew (Israel)", "he-IL-HilaNeural": "Hila - Hebrew (Israel)",
    "hu-HU-NoemiNeural": "Noemi - Hungarian (Hungary)", "hu-HU-AndrasNeural": "Andras - Hungarian (Hungary)",
    "cs-CZ-VlastaNeural": "Vlasta - Czech (Czech Republic)", "cs-CZ-AntoninNeural": "Antonin - Czech (Czech Republic)",
    "da-DK-ChristelNeural": "Christel - Danish (Denmark)", "da-DK-JeppeNeural": "Jeppe - Danish (Denmark)",
    "fi-FI-SelmaNeural": "Selma - Finnish (Finland)", "fi-FI-HarriNeural": "Harri - Finnish (Finland)",
    "nb-NO-PernilleNeural": "Pernille - Norwegian Bokmål (Norway)", "nb-NO-FinnNeural": "Finn - Norwegian Bokmål (Norway)",
    "ro-RO-AlinaNeural": "Alina - Romanian (Romania)", "ro-RO-EmilNeural": "Emil - Romanian (Romania)",
    "sk-SK-LukasNeural": "Lukas - Slovak (Slovakia)", "sk-SK-ViktoriaNeural": "Viktoria - Slovak (Slovakia)",
    "uk-UA-PolinaNeural": "Polina - Ukrainian (Ukraine)", "uk-UA-OstapNeural": "Ostap - Ukrainian (Ukraine)",
    "ms-MY-YasminNeural": "Yasmin - Malay (Malaysia)", "ms-MY-OsmanNeural": "Osman - Malay (Malaysia)",
    "bn-BD-NabanitaNeural": "Nabanita - Bengali (Bangladesh)", "bn-BD-BasharNeural": "Bashar - Bengali (Bangladesh)",
    "ur-PK-AsmaNeural": "Asma - Urdu (Pakistan)", "ur-PK-FaizanNeural": "Faizan - Urdu (Pakistan)",
    "ne-NP-SagarNeural": "Sagar - Nepali (Nepal)", "ne-NP-HemkalaNeural": "Hemkala - Nepali (Nepal)",
    "si-LK-SameeraNeural": "Sameera - Sinhala (Sri Lanka)", "si-LK-ThiliniNeural": "Thilini - Sinhala (Sri Lanka)",
    "lo-LA-ChanthavongNeural": "Chanthavong - Lao (Laos)", "lo-LA-KeomanyNeural": "Keomany - Lao (Laos)",
    "my-MM-NilarNeural": "Nilar - Burmese (Myanmar)", "my-MM-ThihaNeural": "Thiha - Burmese (Myanmar)",
    "ka-GE-EkaNeural": "Eka - Georgian (Georgia)", "ka-GE-GiorgiNeural": "Giorgi - Georgian (Georgia)",
    "hy-AM-AnahitNeural": "Anahit - Armenian (Armenia)", "hy-AM-AraratNeural": "Ararat - Armenian (Armenia)",
    "az-AZ-BabekNeural": "Babek - Azerbaijani (Azerbaijan)", "az-AZ-BanuNeural": "Banu - Azerbaijani (Azerbaijan)",
    "uz-UZ-MadinaNeural": "Madina - Uzbek (Uzbekistan)", "uz-UZ-SuhrobNeural": "Suhrob - Uzbek (Uzbekistan)",
    "sr-RS-NikolaNeural": "Nikola - Serbian (Serbia)", "sr-RS-SophieNeural": "Sophie - Serbian (Serbia)",
    "hr-HR-GabrijelaNeural": "Gabrijela - Croatian (Croatia)", "hr-HR-SreckoNeural": "Srecko - Croatian (Croatia)",
    "sl-SI-PetraNeural": "Petra - Slovenian (Slovenia)", "sl-SI-RokNeural": "Rok - Slovenian (Slovenia)",
    "lv-LV-EveritaNeural": "Everita - Latvian (Latvia)", "lv-LV-AnsisNeural": "Ansis - Latvian (Latvia)",
    "lt-LT-OnaNeural": "Ona - Lithuanian (Lithuania)", "lt-LT-LeonasNeural": "Leonas - Lithuanian (Lithuania)",
    "am-ET-MekdesNeural": "Mekdes - Amharic (Ethiopia)", "am-ET-AbebeNeural": "Abebe - Amharic (Ethiopia)",
    "sw-KE-ZuriNeural": "Zuri - Swahili (Kenya)", "sw-KE-RafikiNeural": "Rafiki - Swahili (Kenya)",
    "zu-ZA-ThandoNeural": "Thando - Zulu (South Africa)", "zu-ZA-ThembaNeural": "Themba - Zulu (South Africa)",
    "af-ZA-AdriNeural": "Adri - Afrikaans (South Africa)", "af-ZA-WillemNeural": "Willem - Afrikaans (South Africa)",
    "so-SO-UbaxNeural": "Ubax - Somali (Somalia)", "so-SO-MuuseNeural": "Muuse - Somali (Somalia)",
    "fa-IR-DilaraNeural": "Dilara - Persian (Iran)", "fa-IR-ImanNeural": "Iman - Persian (Iran)",
    "mn-MN-BataaNeural": "Bataa - Mongolian (Mongolia)", "mn-MN-YesuiNeural": "Yesui - Mongolian (Mongolia)",
    "mt-MT-GraceNeural": "Grace - Maltese (Malta)", "mt-MT-JosephNeural": "Joseph - Maltese (Malta)",
    "ga-IE-ColmNeural": "Colm - Irish (Ireland)", "ga-IE-OrlaNeural": "Orla - Irish (Ireland)",
    "sq-AL-AnilaNeural": "Anila - Albanian (Albania)", "sq-AL-IlirNeural": "Ilir - Albanian (Albania)"
}
# --- Dhammaadka qaamuuska cusub ---

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

def keep_recording(chat_id, stop_event, target_bot):
    while not stop_event.is_set():
        try:
            target_bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
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
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "Click here to join the group ",
                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
            )
        )
        bot.send_message(
            chat_id,
            "🔒 Access Restricted\n\nPlease join our group to use this bot.\n\nJoin and send /start again.",
            reply_markup=markup
        )

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
        admin_markup = InlineKeyboardMarkup()
        admin_markup.add(
            InlineKeyboardButton("Send Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("Total Users", callback_data="admin_total_users")
        )
        bot.send_message(
            message.chat.id,
            "Welcome to the Admin Panel! Choose an action:",
            reply_markup=admin_markup
        )
    else:
        welcome_message = (
            f"👋 Hello {user_first_name}! I am - your AI voice assistant that helps you convert text to audio or audio to text - for free! 🔊✍️\n\n"
            "✨ **Here's how to use it:**\n"
            "1. **Convert Text to Audio (TTS):**\n"
            "   - Choose the voice `/voice`\n"
            "   - Adjust your voice `/pitch` or `/rate`\n"
            "   - Send me text, I will convert it to audio!\n\n"
            "2. **Convert Audio to Text (STT):**\n"
            "   - Choose the language `/lang`\n"
            "   - Send me audio, video or file (up to 20MB)\n\n"
            "3. **Create a Custom Bot:**\n"
            "   - Use `/reg` to create your own bot!\n\n"
            "👉 You can also add me to your groups - click the button below!"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton(" Add to your Groups", url="https://t.me/mediatotextbot?startgroup=")
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
        "📖 **How to use This Bot?**\n\n"
        "This bot makes it easy to convert text to audio or audio/video to text. Here's how it works:\n\n"
        "⸻\n"
        "**1. Convert Text to Audio (TTS):**\n"
        "• **Choose Voice:** Use `/voice` to select the language and voice you want,\n"
        "• **Send your Text:** Once you choose the voice, any text you send me will be returned as audio,\n"
        "• **Adjust Voice:**\n"
        "  • Use `/pitch` to increase or decrease the pitch,\n"
        "  • Use `/rate` to speed up or slow down the speech,\n\n"
        "⸻\n"
        "**2. Convert Audio to Text (STT):**\n"
        "• **Choose Language:** Use `/lang` to specify the language of the audio or video you are sending – this helps with accuracy,\n"
        "• **Send Audio/Video:** Send me an audio message, audio file or video (up to 20MB),\n\n"
        "⸻\n"
        "**3. Create a Custom Bot:**\n"
        "• **Personal Bot:** Use `/reg` if you want to create your own bot for TTS or STT,\n"
        "  You only need your bot token,\n\n"
        "⸻\n"
        "**4. Your Data & Privacy:**\n"
        "• **Your Data is Private:** The text and audio you send are not saved – they are used temporarily,\n"
        "• **Your preferences are saved:** The voice, pitch, and rate you choose are saved until the bot restarts.\n\n"
        "👉 Questions or problems? Contact @kookabeela\n\n"
        "Enjoy creating and writing! ✨"
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
        "🔐 **Privacy Notice**\n\n"
        "If you have any questions or concerns about your privacy, please feel free to contact the bot administrator @user33230."
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.callback_query_handler(lambda c: c.data in ["admin_total_users", "admin_broadcast"] and c.from_user.id == ADMIN_ID)
def admin_menu_callback(call):
    chat_id = call.message.chat.id
    if call.data == "admin_total_users":
        total_registered = len(in_memory_data["users"])
        bot.send_message(chat_id, f"Total registered users (from memory): {total_registered}")
    elif call.data == "admin_broadcast":
        admin_state[call.from_user.id] = 'awaiting_broadcast_message'
        bot.send_message(chat_id, "Send the broadcast message now:")
    bot.answer_callback_query(call.id)

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
                     "Okay! To create your bot, send me your **Bot API Token**.\n\n"
                     "If you don't have one, get it from @BotFather:\n"
                     "1. Talk to @BotFather\n"
                     "2. Send `/newbot` and follow the instructions\n"
                     "3. When finished, you will get a token like `123456:ABC-DEF1234ghIkl-zyx57W2E1`\n\n"
                     "Now send me the token!")

@bot.message_handler(func=lambda m: user_register_bot_mode.get(str(m.from_user.id)) == "awaiting_token")
def process_bot_token(message):
    uid = str(message.from_user.id)
    bot_token = message.text.strip()

    if not (30 < len(bot_token) < 50 and ':' in bot_token):
        bot.send_message(message.chat.id, "That is not a valid Bot API Token. Please check and try again.")
        return

    try:
        test_bot = telebot.TeleBot(bot_token)
        bot_info = test_bot.get_me()
        user_register_bot_mode[uid] = {"state": "awaiting_service_type", "token": bot_token}

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("audio maker", callback_data="register_bot_service|tts"),
            InlineKeyboardButton("transcriber", callback_data="register_bot_service|stt")
        )
        bot.send_message(message.chat.id,
                         f"Great! I have verified the token for @**{bot_info.username}**.\n\n"
                         "Now choose what your bot will do:\n"
                         "• **TTS Bot**: Converts text to audio\n"
                         "• **STT Bot**: Converts audio to text\n\n"
                         "Choose one below:",
                         reply_markup=markup,
                         parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error validating token: {e}")
        bot.send_message(message.chat.id,
                         "❌ I cannot verify that token. It might be incorrect or deleted. Please check with @BotFather and try again.")
        user_register_bot_mode[uid] = None
    except Exception as e:
        logging.error(f"Unexpected error validating token: {e}")
        bot.send_message(message.chat.id, "An unexpected error occurred. Please try again.")
        user_register_bot_mode[uid] = None

@bot.callback_query_handler(lambda c: c.data.startswith("register_bot_service|") and user_register_bot_mode.get(str(c.from_user.id)) and user_register_bot_mode[str(c.from_user.id)].get("state") == "awaiting_service_type")
def on_register_bot_service_select(call):
    uid = str(call.from_user.id)
    data_state = user_register_bot_mode.get(uid)
    if not data_state or data_state.get("state") != "awaiting_service_type":
        bot.answer_callback_query(call.id, "Invalid state. Please start again with `/reg`.")
        return

    bot_token = data_state.get("token")
    _, service_type = call.data.split("|", 1)

    if not bot_token:
        bot.answer_callback_query(call.id, "Token not found. Start again.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="Something went wrong. Use `/reg` again.")
        user_register_bot_mode[uid] = None
        return

    register_child_bot_in_memory(bot_token, uid, service_type)

    try:
        child_bot_webhook_url = f"{WEBHOOK_URL}child_webhook/{bot_token}"
        temp_child_bot = telebot.TeleBot(bot_token)
        temp_child_bot.set_webhook(url=child_bot_webhook_url, drop_pending_updates=True)
        set_child_bot_commands(temp_child_bot, service_type)

        bot.answer_callback_query(call.id, f"✅ Your {service_type.upper()} bot has been registered!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🎉 Your new *{service_type.upper()}* bot has been activated!\n\n"
                 f"Find it here: https://t.me/{temp_child_bot.get_me().username}\n\n"
                 f"It will use your preferences (voice/pitch/rate for TTS, language for STT) from this main bot.\n"
                 f"• TTS: Use `/voice`, `/pitch`, `/rate`, and send text\n"
                 f"• STT: Use `/lang`, and send audio",
            parse_mode="Markdown"
        )
        logging.info(f"Webhook set for child bot {temp_child_bot.get_me().username} to {child_bot_webhook_url}")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Failed to set webhook for child bot: {e}")
        bot.answer_callback_query(call.id, "Failed to set up your bot. Try again.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text="❌ An error occurred while setting up your bot. Try again.")
    except Exception as e:
        logging.error(f"Unexpected error during child bot setup: {e}")
        bot.send_message(call.message.chat.id, "An unexpected error occurred. Try again.")
        bot.answer_callback_query(call.id, "An unexpected error occurred.")
    finally:
        user_register_bot_mode[uid] = None

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
    # Waxaan halkan ku beddelaynaa habka loo soo bandhigayo magacyada codadka
    for voice in voices:
        display_name = VOICE_MAPPING.get(voice, voice) # Soo qaado magaca fudud
        markup.add(InlineKeyboardButton(display_name, callback_data=f"tts_voice|{voice}"))
    
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="tts_back_to_languages"))
    return markup

def make_pitch_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⬆️ High", callback_data="pitch_set|+50"),
        InlineKeyboardButton("⬇️ Lower", callback_data="pitch_set|-50"),
        InlineKeyboardButton("🔄 Reset", callback_data="pitch_set|0")
    )
    return markup

def make_rate_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("⚡️ speed", callback_data="rate_set|+50"),
        InlineKeyboardButton("🐢 Slow down", callback_data="rate_set|-50"),
        InlineKeyboardButton("🔄 Reset", callback_data="rate_set|0")
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
        "How fast or slow should I speak? Choose one or send a number from -100 (slow) to +100 (fast), 0 is normal:",
        reply_markup=make_rate_keyboard()
    )

def handle_rate_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_rate_input_mode[user_id_for_settings] = None

    try:
        _, rate_value_str = call.data.split("|", 1)
        rate_value = int(rate_value_str)
        set_tts_user_rate_in_memory(user_id_for_settings, rate_value)
        target_bot.answer_callback_query(call.id, f"The rate is {rate_value}!")
        
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 Speech rate is *{rate_value}*.\n\nReady for text? Or use `/voice` to change the voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid rate.")
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
        "Let's adjust the pitch! Choose one or send a number from -100 (low) to +100 (high), 0 is normal:",
        reply_markup=make_pitch_keyboard()
    )

def handle_pitch_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None

    try:
        _, pitch_value_str = call.data.split("|", 1)
        pitch_value = int(pitch_value_str)
        set_tts_user_pitch_in_memory(user_id_for_settings, pitch_value)
        target_bot.answer_callback_query(call.id, f"The pitch is {pitch_value}!")
        target_bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🔊 The pitch is *{pitch_value}*.\n\nReady for text? Or use `/voice` to change the voice.",
            parse_mode="Markdown",
            reply_markup=None
        )
    except ValueError:
        target_bot.answer_callback_query(call.id, "Invalid pitch.")
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

    target_bot.send_message(chat_id, "First, choose the *language* of your voice. 👇", reply_markup=make_tts_language_keyboard(), parse_mode="Markdown")

def handle_tts_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    user_pitch_input_mode[user_id_for_settings] = None
    user_rate_input_mode[user_id_for_settings] = None
    user_register_bot_mode[user_id_for_settings] = None

    _, lang_name = call.data.split("|", 1)
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"Okay! Now choose a specific *voice* from {lang_name}. 👇",
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

    # Soo qaado magaca cusub ee fudud
    voice_display_name = VOICE_MAPPING.get(voice, voice)

    target_bot.answer_callback_query(call.id, f"✔️ The voice is {voice_display_name}")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🔊 Great! You are using: *{voice_display_name}*.\n\n"
             f"Current settings:\n"
             f"• Pitch: *{current_pitch}*\n"
             f"• Rate: *{current_rate}*\n\n"
             f"Ready to speak? Send me text!",
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
        text="Choose the *language* of your voice. 👇",
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
            target_bot.send_message(chat_id, "❌ I failed to create the audio. Try another text.")
            return

        with open(filename, "rb") as f:
            # Halkan ayaan ku cusboonaysiinay caption-ka
            voice_display_name = VOICE_MAPPING.get(voice, voice)
            
            caption_text = (
                f"🎧 *Halkan waa codkaaga!* \n\n"
                f"Voice: **{voice_display_name}**\n"
                f"Pitch: *{pitch}*\n"
                f"Rate: *{rate}*\n\n"
                f"Ku raaxayso dhageysiga! ✨"
            )

            target_bot.send_audio(
                chat_id,
                f,
                caption=caption_text,
                parse_mode="Markdown"
            )

    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        target_bot.send_message(chat_id, "❌ An error occurred while generating the audio. Try again or choose another voice.")
    except Exception as e:
        logging.exception("TTS error")
        target_bot.send_message(chat_id, "This voice is not available, please choose another one.")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
                logging.info(f"Deleted temporary TTS file: {filename}")
            except Exception as e:
                logging.error(f"Error deleting TTS file {filename}: {e}")

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

    target_bot.send_message(chat_id, "Choose the *language* for your Speech-to-Text:", reply_markup=build_stt_language_keyboard(), parse_mode="Markdown")

def handle_stt_language_select_callback(call, target_bot: telebot.TeleBot, user_id_for_settings: str):
    chat_id = call.message.chat.id
    _, lang_code = call.data.split("|", 1)
    lang_name = next((name for name, code in STT_LANGUAGES.items() if code == lang_code), "Unknown")
    set_stt_user_lang_in_memory(user_id_for_settings, lang_code)

    target_bot.answer_callback_query(call.id, f"✅ The language is {lang_name}!")
    target_bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ The transcription language is: *{lang_name}*\n\n🎙️ Send audio, file or video (up to 20MB) for me to transcribe.",
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
    downloaded_file_path = None
    try:
        processing_msg = target_bot.send_message(chat_id, " Processing...", reply_to_message_id=original_message_id)
        file_info = target_bot.get_file(file_id)
        if file_info.file_size > 20 * 1024 * 1024:
            target_bot.send_message(chat_id, "⚠️ The file is too large. Maximum size is 20MB. Send a smaller file.", reply_to_message_id=original_message_id)
            return

        downloaded_file_path = f"stt_temp_{uuid.uuid4()}_{file_info.file_path.split('/')[-1]}"
        downloaded_file = bot.download_file(file_info.file_path)
        with open(downloaded_file_path, 'wb') as f:
            f.write(downloaded_file)

        processing_start_time = datetime.now()

        with open(downloaded_file_path, "rb") as f:
            upload_res = requests.post("https://api.assemblyai.com/v2/upload",
                headers={"authorization": ASSEMBLYAI_API_KEY, "Content-Type": "application/octet-stream"},
                data=f)
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
                target_bot.send_message(chat_id, "ℹ️ No text extracted from this file.", reply_to_message_id=original_message_id)
            elif len(text) <= 4000:
                target_bot.send_message(chat_id, text, reply_to_message_id=original_message_id)
            else:
                import io
                f = io.BytesIO(text.encode("utf-8"))
                f.name = "transcript.txt"
                target_bot.send_document(chat_id, f, caption="Your transcript is too long. Here is the file:", reply_to_message_id=original_message_id)
            increment_processing_count_in_memory(user_id_for_settings, "stt")
            status = "success"
        else:
            error_msg = res.get("error", "Unknown transcription error.")
            target_bot.send_message(chat_id, f"❌ Transcription error: {error_msg}", parse_mode="Markdown", reply_to_message_id=original_message_id)
            status = "fail_assemblyai_error"
            logging.error(f"AssemblyAI transcription failed: {error_msg}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Network or API error during STT processing: {e}")
        target_bot.send_message(chat_id, "❌ A network error occurred. Try again.", reply_to_message_id=original_message_id)
    except Exception as e:
        logging.exception(f"Unhandled error during STT processing: {e}")
        target_bot.send_message(chat_id, "The file is too large, send one smaller than 20MB.", reply_to_message_id=original_message_id)
    finally:
        if processing_msg:
            try:
                target_bot.delete_message(chat_id, processing_msg.message_id)
            except Exception as e:
                logging.error(f"Could not delete processing message: {e}")
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
                logging.info(f"Deleted temporary STT file: {downloaded_file_path}")
            except Exception as e:
                logging.error(f"Error deleting STT file {downloaded_file_path}: {e}")

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
            target_bot.send_message(message.chat.id, "I'm sorry, I can only transcribe audio or video. Send a valid file.")
            return

    if not file_id:
        target_bot.send_message(message.chat.id, "I don't support this file type. Send audio, file or video.")
        return

    if user_id_for_settings not in in_memory_data["stt_settings"]:
        target_bot.send_message(message.chat.id, "❗First choose the transcription language /lang.")
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
                target_bot.send_message(message.chat.id, f"🔊 The speech rate is *{rate_val}*.", parse_mode="Markdown")
                user_rate_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Invalid rate. Send a number from -100 to +100 or 0. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "That is not a valid number. Send a number from -100 to +100 or 0. Try again:")
            return

    if user_pitch_input_mode.get(user_id_for_settings) == "awaiting_pitch_input":
        try:
            pitch_val = int(message.text)
            if -100 <= pitch_val <= 100:
                set_tts_user_pitch_in_memory(user_id_for_settings, pitch_val)
                target_bot.send_message(message.chat.id, f"🔊 The pitch is *{pitch_val}*.", parse_mode="Markdown")
                user_pitch_input_mode[user_id_for_settings] = None
            else:
                target_bot.send_message(message.chat.id, "❌ Invalid pitch. Send a number from -100 to +100 or 0. Try again:")
            return
        except ValueError:
            target_bot.send_message(message.chat.id, "That is not a valid number. Send a number from -100 to +100 or 0. Try again:")
            return

    current_voice = get_tts_user_voice_in_memory(user_id_for_settings)
    if current_voice:
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, user_id_for_settings, message.text, target_bot))
        ).start()
    else:
        target_bot.send_message(
            message.chat.id,
            "You haven't chosen a voice yet! Use `/voice` first, then send me text. 🗣️"
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
        "I'm sorry, I can only convert *text* to audio or *audio/files* to text. Send me one of those!"
    )

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
                            f"👋 Hello {user_first_name}!\n"
                            "• Send audio, video or file,\n"
                            "• I will convert it to text and send it back to you!\n"
                            "• Choose the language of your file `/lang`\n"
                        )
                    elif service_type == "tts":
                        welcome_message = (
                            f"👋 Hello {user_first_name}!\n"
                            "• Send me text, I will convert it to audio,\n"
                            "• And then send it back to you!\n"
                            "• Choose the language and voice `/voice`\n"
                        )
                    else:
                        welcome_message = f"👋 Welcome! I am your {service_type.upper()} bot."
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
                            child_bot_instance.send_message(chat_id, "I am an STT bot. Send audio, file or video for me to transcribe, or use `/lang`.")
                            return "", 200
                elif message.voice or message.audio or message.video or message.document:
                    if service_type == "stt":
                        handle_stt_media_types_common(message, child_bot_instance, user_id_for_settings)
                    else:
                        child_bot_instance.send_message(chat_id, "I am a TTS bot. Send text for me to convert to audio.")
                    return "", 200
                else:
                    child_bot_instance.send_message(chat_id, "I'm sorry, I can only work with certain message types. See `/start`.")
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
                child_bot_instance.answer_callback_query(call.id, "This action is not available for this bot type.")
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
        BotCommand("voice", "Change the TTS voice style"),
        BotCommand("pitch", "Change the sound"),
        BotCommand("rate", "Change speed"),
        BotCommand("lang", "Set File language"),
        BotCommand("reg", "Create your own bot"),
        BotCommand("help", "How to use"),
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
            BotCommand("voice", "Change TTS voice style"),
            BotCommand("pitch", "Change the sound"),
            BotCommand("rate", "Change speed")
        ]
    elif service_type == "stt":
        commands = [
            BotCommand("start", "Get Started"),
            BotCommand("lang", "Set File language")
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
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

