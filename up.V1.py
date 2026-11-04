import telebot
import os
import pymongo
import json
from telebot import types
import random
import string
from datetime import datetime
import boto3
import requests
from boto3.s3.transfer import TransferConfig

# --- Hardcoded Config & Credentials ---
BOT_TOKEN = "8955533459:AAE9PZjpEGTH3taG_WS6s0ZJN7FHe4OZGNA"
API_ID = 30908660
API_HASH = "ee861ae49daea1486f10d3a45a1b3ea9"
LOG_CHANNEL = -1003825211371

INITIAL_ADMIN_IDS = [0]
OWNER_ID = INITIAL_ADMIN_IDS[0]

# --- Cloudflare R2 (S3 Compatible) Setup ---
R2_ENDPOINT = "https://1d019a88777f3e5ea2f04de9974f028d.r2.cloudflarestorage.com"
R2_ACCESS_KEY_ID = "ae9ff2ee5a83fd4f6d3db75297c0a76b"
R2_SECRET_ACCESS_KEY = "4b27c68e8e1599885cffe25e7d41217d2fae82e85a41aa02f0756d574d79ee87"
BUCKET_NAME = "relax-movizone-bucket"

# আপনার R2 বাক্কে পাবলিক ডোমেইন বা কাস্টম ডোমেইন এখানে বসাবেন
R2_PUBLIC_DOMAIN = "https://your-r2-public-domain.com" 

s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name='auto'
)

# বড় ফাইলের জন্য চংক কনফিগারেশন (৮ এমবি টুকরো)
transfer_config = TransferConfig(
    multipart_threshold=1024 * 1024 * 15,
    max_concurrency=4,
    multipart_chunksize=1024 * 1024 * 8,
    use_threads=True
)

# --- MongoDB Setup ---
MONGODB_URI = os.environ.get('MONGODB_URI', "mongodb+srv://tafsiralam2023_db_user:CUoWA0xoA8lzlXJQ@cluster0.k0uslgi.mongodb.net/")
try:
    client = pymongo.MongoClient(MONGODB_URI)
    db = client['uploader_bot_db']
    users_collection = db['users']
    admin_collection = db['admin_config']
    redeem_codes_collection = db['redeem_codes']
    files_collection = db['files']
    counters_collection = db['counters']
except Exception as e:
    print(f"MongoDB Connection Error: {e}")

DEFAULT_LANGUAGE = "en"
bot = telebot.TeleBot(BOT_TOKEN)

# --- Helper for Global IDs ---
def get_next_sequence_value(sequence_name):
    sequence_document = counters_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=pymongo.ReturnDocument.AFTER,
        upsert=True
    )
    return sequence_document['sequence_value']

# --- Admin Management Helpers ---
def get_admin_list():
    config = admin_collection.find_one({'_id': 'bot_config'})
    if config and 'admin_ids' in config: return config['admin_ids']
    else:
        if INITIAL_ADMIN_IDS:
            admin_collection.update_one({'_id': 'bot_config'},{'$set': {'admin_ids': INITIAL_ADMIN_IDS}},upsert=True)
            return INITIAL_ADMIN_IDS
        return []

def is_admin(user_id):
    return user_id in get_admin_list()

# --- Language Support ---
def load_language(lang_code):
    return {
        "upload_button": "📤 Upload Video",
        "delete_button": "🗑 Delete File",
        "get_file_button": "📥 Get File",
        "redeem_button": "🔑 Redeem",
        "caption_button": "📝 Caption",
        "support_button": "⚙️ Support",
        "profile_button": "👤 Profile",
        "admin_stats_button": "📊 Stats",
        "admin_bot_status_button": "⚡ Status",
        "admin_ban_button": "🚫 Ban",
        "admin_unban_button": "✅ Unban",
        "admin_broadcast_button": "📢 Broadcast",
        "admin_forward_broadcast_button": "↪️ Fwd Broadcast",
        "back_button": "🔙 Back",
        "start_message": "Welcome to Relax Movizone Bot! Send your large video to stream directly to Cloudflare R2.",
        "upload_invalid_media_type": "Please send a valid video or media file.",
        "default_caption": "Relax Movizone Video",
        "upload_success_message": "✅ **File Uploaded Successfully to Cloudflare R2!**\n\n🆔 **File ID:** `{file_id}`\n🔗 **Embed/Stream Link:**\n`{download_link}`",
        "download_link_error": "❌ Invalid or expired download link.",
        "delete_file_invalid_id": "Please enter a valid numeric File ID.",
        "file_not_found": "❌ File not found.",
        "delete_file_success": "✅ File ID {file_id} deleted successfully.",
        "profile_message": "👤 **User Profile**\n\nName: {first_name}\nID: `{user_id}`\nTotal Uploads: {file_count}",
        "main_menu_back": "Returned to main menu."
    }

def get_user_lang_code(user_id):
    return DEFAULT_LANGUAGE

def get_user_lang(user_id):
    return load_language(get_user_lang_code(user_id))

# --- Bot Functions ---
def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    try: bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e: print(f"Error sending message to {chat_id}: {e}")

# --- Keyboards ---
def main_keyboard(lang_code):
    lang_data = load_language(lang_code)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton(lang_data["upload_button"])
    btn2 = types.KeyboardButton(lang_data["delete_button"])
    btn3 = types.KeyboardButton(lang_data["get_file_button"])
    btn4 = types.KeyboardButton(lang_data["profile_button"])
    markup.add(btn1)
    markup.add(btn2, btn3)
    markup.add(btn4)
    return markup

# --- Command Handlers ---
@bot.message_handler(commands=['start'])
def start_command_handler(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    send_message(message.chat.id, lang_data["start_message"], reply_markup=main_keyboard(DEFAULT_LANGUAGE))

# --- Stream Upload & R2 Chunk Handler (RAM Safe) ---
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio'], func=lambda message: True)
def upload_media_handler(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    
    file_info = None
    file_extension = ".mp4"

    if message.video:
        file_info = bot.get_file(message.video.file_id)
        file_extension = ".mp4"
    elif message.document:
        file_info = bot.get_file(message.document.file_id)
        file_extension = os.path.splitext(message.document.file_name)[1] if message.document.file_name else ".mp4"
    elif message.photo:
        file_info = bot.get_file(message.photo[-1].file_id)
        file_extension = ".jpg"
    elif message.audio:
        file_info = bot.get_file(message.audio.file_id)
        file_extension = ".mp3"
    else:
        send_message(message.chat.id, lang_data["upload_invalid_media_type"])
        return

    status_msg = bot.send_message(message.chat.id, "📥 Streaming and uploading large file to Cloudflare R2...")
    
    local_path = None
    try:
        global_file_id = get_next_sequence_value('global_file_id')
        unique_filename = f"movie_{global_file_id}{file_extension}"
        local_path = f"temp_{unique_filename}"

        # টেলিগ্রাম থেকে সরাসরি স্ট্রিম করে ডাউনলোড (র‍্যাম বাঁচানোর জন্য)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        with requests.get(file_url, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        
        # Cloudflare R2-এ মাল্টিপার্ট চংক আকারে আপলোড
        s3_client.upload_file(
            local_path, 
            BUCKET_NAME, 
            unique_filename,
            Config=transfer_config
        )
        
        r2_file_url = f"{R2_PUBLIC_DOMAIN}/{unique_filename}"
        
        # MongoDB-তে ডাটা সেভ
        file_doc = {
            '_id': global_file_id,
            'uploader_id': user_id,
            'file_name': unique_filename,
            'r2_url': r2_file_url,
            'created_at': datetime.utcnow()
        }
        files_collection.insert_one(file_doc)
        
        try:
            bot.delete_message(message.chat.id, status_msg.message_id)
        except:
            pass
            
        success_text = lang_data["upload_success_message"].format(
            file_id=global_file_id, 
            download_link=r2_file_url
        )
        send_message(message.chat.id, success_text, reply_markup=main_keyboard(DEFAULT_LANGUAGE))
        
    except Exception as e:
        print(f"R2 Stream Upload Error: {e}")
        bot.edit_message_text(f"❌ Upload failed due to error: {e}", message.chat.id, status_msg.message_id)
        
    finally:
        # হোস্টিং সার্ভার থেকে ফাইল সাথে সাথে ডিলিট করার ব্যবস্থা (RAM Free রাখার জন্য)
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
                print(f"Temporary file {local_path} deleted from hosting server.")
            except Exception as del_err:
                print(f"Error deleting temp file: {del_err}")

# --- Main Execution ---
if __name__ == "__main__":
    try:
        if counters_collection.find_one({'_id': 'global_file_id'}) is None:
            counters_collection.insert_one({'_id': 'global_file_id', 'sequence_value': 0})
    except Exception as e:
        print(f"Counter init warning: {e}")
        
    print("Bot starting with R2 Multipart Streaming & Auto-Cleanup...")
    bot.infinity_polling()
