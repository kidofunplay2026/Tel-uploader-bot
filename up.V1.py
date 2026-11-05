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
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import re

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
R2_BUCKET_NAME = "relax-movizone-bucket"
R2_PUBLIC_DOMAIN = "https://pub-ee66f5d697d3475a99079f1a9c567f66.r2.dev" 

r2_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name='auto'
)

# --- Backblaze B2 (S3 Compatible) Setup ---
B2_ENDPOINT = "https://s3.us-west-005.backblazeb2.com"
B2_ACCESS_KEY_ID = "00529877a5de96a0000000001"
B2_SECRET_ACCESS_KEY = "K005tvFV6fS+ClJPMhS+VzRdCK6YD6I"
B2_BUCKET_NAME = "relex-movi-zone-bucket"

b2_client = boto3.client(
    's3',
    endpoint_url=B2_ENDPOINT,
    aws_access_key_id=B2_ACCESS_KEY_ID,
    aws_secret_access_key=B2_SECRET_ACCESS_KEY,
    region_name='us-west-005'
)

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

user_temp_data = {}

def get_next_sequence_value(sequence_name):
    sequence_document = counters_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=pymongo.ReturnDocument.AFTER,
        upsert=True
    )
    return sequence_document['sequence_value']

def load_language(lang_code):
    return {
        "upload_button": "📤 Upload Video",
        "delete_button": "🗑 Delete File",
        "get_file_button": "📥 Get File",
        "profile_button": "👤 Profile",
        "start_message": "Welcome to Relax Movizone Bot! Send your large video to stream directly to Cloudflare R2 or Backblaze B2.",
        "upload_invalid_media_type": "Please send a valid video or media file."
    }

def get_user_lang(user_id):
    return load_language(DEFAULT_LANGUAGE)

def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    try: bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e: print(f"Error sending message to {chat_id}: {e}")

def main_keyboard(lang_code):
    lang_data = load_language(lang_code)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton(lang_data["upload_button"]))
    markup.add(types.KeyboardButton(lang_data["delete_button"]), types.KeyboardButton(lang_data["get_file_button"]))
    markup.add(types.KeyboardButton(lang_data["profile_button"]))
    return markup

# --- Command Handlers ---
@bot.message_handler(commands=['start'])
def start_command_handler(message):
    lang_data = get_user_lang(message.from_user.id)
    send_message(message.chat.id, lang_data["start_message"], reply_markup=main_keyboard(DEFAULT_LANGUAGE))

# --- Reply Keyboard Menu Handlers ---
@bot.message_handler(func=lambda message: message.text == "📤 Upload Video")
def upload_btn_handler(message):
    send_message(message.chat.id, "দয়া করে আপনার বড় ভিডিও বা মিডিয়া ফাইলটি সরাসরি এখানে পাঠান।")

@bot.message_handler(func=lambda message: message.text == "🗑 Delete File")
def delete_btn_handler(message):
    send_message(message.chat.id, "ফাইল ডিলিট করতে delete বা ডিলিট লিখে সাথে File ID দিন (যেমন: delete 22)।")

@bot.message_handler(func=lambda message: message.text == "📥 Get File")
def get_file_btn_handler(message):
    send_message(message.chat.id, "ফাইল পেতে বা দেখতে আপনার File ID দিন (যেমন: 22)।")

@bot.message_handler(func=lambda message: message.text == "👤 Profile")
def profile_btn_handler(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    file_count = files_collection.count_documents({'uploader_id': user_id})
    profile_text = f"👤 **User Profile**\n\nName: {user_name}\nID: `{user_id}`\nTotal Uploads: {file_count}"
    send_message(message.chat.id, profile_text)

# --- Media Receiver & Storage Choice Prompt ---
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio'], func=lambda message: True)
def media_receiver_handler(message):
    user_id = message.from_user.id
    user_temp_data[user_id] = message
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("☁️ Cloudflare R2 (1)", callback_data="storage_r2"),
        types.InlineKeyboardButton("📦 Backblaze B2 (2)", callback_data="storage_b2")
    )
    send_message(message.chat.id, "📥 **ফাইল পাওয়া গেছে!**\n\nআপনি এই ফাইলটি কোথায় আপলোড করতে চান? নিচে থেকে সিলেক্ট করুন:", reply_markup=markup)

# --- Storage Selection Callback Handler ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("storage_"))
def storage_callback_handler(call):
    user_id = call.from_user.id
    if user_id not in user_temp_data:
        bot.answer_callback_query(call.id, "❌ সেশন মেয়াদোত্তীর্ণ হয়ে গেছে। দয়া করে ফাইলটি আবার পাঠান।", show_alert=True)
        return
        
    message = user_temp_data[user_id]
    choice = call.data
    storage_type = "Cloudflare R2" if choice == "storage_r2" else "Backblaze B2"
    
    bot.answer_callback_query(call.id, f"{storage_type} সিলেক্ট করা হয়েছে। আপলোড শুরু হচ্ছে...")
    bot.edit_message_text(f"🚀 ফাইলটি **{storage_type}**-এ আপলোড ও প্রসেসিং হচ্ছে, দয়া করে অপেক্ষা করুন...", call.message.chat.id, call.message.message_id)
    
    try:
        process_and_upload_file(message, choice, call.message.chat.id, call.message.message_id)
    except Exception as e:
        print(f"Error in callback processing: {e}")

def process_and_upload_file(message, choice, chat_id, message_id):
    user_id = message.from_user.id
    
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
        send_message(chat_id, "❌ অনুগ্রহ করে সঠিক মিডিয়া বা ভিডিও ফাইল পাঠান।")
        return

    local_path = None
    try:
        global_file_id = get_next_sequence_value('global_file_id')
        unique_filename = f"movie_{global_file_id}{file_extension}"
        local_path = f"temp_{unique_filename}"

        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        with requests.get(file_url, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk)
        
        if choice == "storage_r2":
            r2_client.upload_file(local_path, R2_BUCKET_NAME, unique_filename, Config=transfer_config)
            file_url_link = f"{R2_PUBLIC_DOMAIN}/{unique_filename}"
            download_url_link = file_url_link
            storage_name = "Cloudflare R2"
        else:
            b2_client.upload_file(local_path, B2_BUCKET_NAME, unique_filename, Config=transfer_config)
            file_url_link = f"{B2_ENDPOINT}/file/{B2_BUCKET_NAME}/{unique_filename}"
            download_url_link = file_url_link
            storage_name = "Backblaze B2"
        
        files_collection.insert_one({
            '_id': global_file_id,
            'uploader_id': user_id,
            'file_name': unique_filename,
            'storage_type': storage_name,
            'r2_url': file_url_link,
            'download_url': download_url_link,
            'created_at': datetime.utcnow()
        })
        
        try:
            bot.delete_message(chat_id, message_id)
        except:
            pass
            
        success_text = (
            f"✅ **File Uploaded Successfully to {storage_name}!**\n\n"
            f"🆔 **File ID:** `{global_file_id}`\n"
            f"📌 **File Name:** `{unique_filename}`\n\n"
            f"🔗 **Embed / Stream Link:**\n`{file_url_link}`\n\n"
            f"📥 **Download Link:**\n`{download_url_link}`"
        )
        send_message(chat_id, success_text, reply_markup=main_keyboard(DEFAULT_LANGUAGE))
    except Exception as e:
        print(f"Upload Error: {e}")
        send_message(chat_id, f"❌ আপলোড ব্যর্থ হয়েছে: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass
        if user_id in user_temp_data:
            del user_temp_data[user_id]

# --- File Delete Logic ---
@bot.message_handler(func=lambda message: message.text and ("delete" in message.text.lower() or "ডিলিট" in message.text))
def handle_delete_file_request(message):
    numbers = re.findall(r'\d+', message.text)
    if not numbers:
        send_message(message.chat.id, "❌ দয়া করে সঠিক File ID দিন যা ডিলিট করতে চান (যেমন: delete 22)।")
        return
    
    file_id = int(numbers[0])
    file_doc = files_collection.find_one({'_id': file_id})
    
    if not file_doc:
        send_message(message.chat.id, f"❌ File ID `{file_id}` ডাটাবেজে পাওয়া যায়নি।")
        return
        
    file_name = file_doc.get('file_name')
    storage_type = file_doc.get('storage_type', 'Cloudflare R2')
    
    try:
        if "Backblaze" in storage_type:
            b2_client.delete_object(Bucket=B2_BUCKET_NAME, Key=file_name)
        else:
            r2_client.delete_object(Bucket=R2_BUCKET_NAME, Key=file_name)
            
        files_collection.delete_one({'_id': file_id})
        send_message(message.chat.id, f"✅ File ID `{file_id}` (`{file_name}`) সফলভাবে `{storage_type}` এবং ডাটাবেজ থেকে চিরতরে ডিলিট করা হয়েছে!")
    except Exception as e:
        print(f"Delete Error: {e}")
        send_message(message.chat.id, f"❌ ক্লাউড থেকে ফাইল ডিলিট করার সময় সমস্যা হয়েছে: {e}")

# --- File ID Lookup Handler ---
@bot.message_handler(func=lambda message: message.text and any(char.isdigit() for char in message.text) and not "delete" in message.text.lower() and not "ডিলিট" in message.text)
def handle_file_id_input(message):
    numbers = re.findall(r'\d+', message.text)
    if not numbers:
        return
    
    file_id = int(numbers[0])
    file_doc = files_collection.find_one({'_id': file_id})
    
    if file_doc:
        file_url = file_doc.get('r2_url')
        download_url = file_doc.get('download_url', file_url)
        file_name = file_doc.get('file_name', 'Video')
        storage_type = file_doc.get('storage_type', 'Cloudflare R2')
        
        response_text = (
            f"📁 **File Found!**\n\n"
            f"🆔 **File ID:** `{file_id}`\n"
            f"📌 **File Name:** `{file_name}`\n"
            f"☁️ **Storage:** `{storage_type}`\n\n"
            f"🔗 **Embed/Stream Link:**\n`{file_url}`\n\n"
            f"📥 **Download Link:**\n`{download_url}`"
        )
        send_message(message.chat.id, response_text)
    else:
        send_message(message.chat.id, f"❌ File ID `{file_id}` database-এ পাওয়া যায়নি। সঠিক আইডি দিয়ে আবার চেষ্টা করুন।")

# --- Temporary Web Server for Fly.io Port Binding ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")
    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    print(f"Web server started on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    try:
        if counters_collection.find_one({'_id': 'global_file_id'}) is None:
            counters_collection.insert_one({'_id': 'global_file_id', 'sequence_value': 0})
    except Exception as e:
        print(f"Counter init warning: {e}")
        
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    print("Bot starting with Multi-Storage (Cloudflare R2 & Backblaze B2) Support...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            import time
            time.sleep(5)
