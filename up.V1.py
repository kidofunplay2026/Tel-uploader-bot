import telebot
import os
import pymongo
from telebot import types
from datetime import datetime
import boto3
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

BOT_TOKEN = "8955533459:AAE9PZjpEGTH3taG_WS6s0ZJN7FHe4OZGNA"

# --- Cloudflare R2 Setup ---
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

# --- Backblaze B2 Setup ---
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

# --- MongoDB Setup ---
MONGODB_URI = os.environ.get('MONGODB_URI', "mongodb+srv://tafsiralam2023_db_user:CUoWA0xoA8lzlXJQ@cluster0.k0uslgi.mongodb.net/")
try:
    client = pymongo.MongoClient(MONGODB_URI)
    db = client['uploader_bot_db']
    files_collection = db['files']
    counters_collection = db['counters']
except Exception as e:
    print(f"MongoDB Connection Error: {e}")

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

def send_message(chat_id, text, reply_markup=None):
    try: bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e: print(f"Error sending message: {e}")

@bot.message_handler(commands=['start'])
def start_command_handler(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("📤 Upload Video"), types.KeyboardButton("🗑 Delete File"))
    markup.add(types.KeyboardButton("📥 Get File"), types.KeyboardButton("👤 Profile"))
    send_message(message.chat.id, "Welcome! Forward any movie/video here to upload to Cloudflare R2 or Backblaze B2.", reply_markup=markup)

@bot.message_handler(content_types=['photo', 'video', 'document', 'audio'])
def media_receiver_handler(message):
    user_temp_data[message.from_user.id] = message
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("☁️ Cloudflare R2", callback_data="storage_r2"),
        types.InlineKeyboardButton("📦 Backblaze B2", callback_data="storage_b2")
    )
    send_message(message.chat.id, "📥 **ফাইল বা ভিডিও পাওয়া গেছে!** কোথায় আপলোড করবেন সিলেক্ট করুন:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("storage_"))
def storage_callback_handler(call):
    user_id = call.from_user.id
    if user_id not in user_temp_data:
        bot.answer_callback_query(call.id, "❌ সেশন শেষ! আবার ভিডিও ফরোয়ার্ড করুন।", show_alert=True)
        return
        
    message = user_temp_data[user_id]
    choice = call.data
    storage_name = "Cloudflare R2" if choice == "storage_r2" else "Backblaze B2"
    
    bot.answer_callback_query(call.id, f"{storage_name} সিলেক্ট করা হয়েছে।")
    bot.edit_message_text(f"🚀 ফাইলটি ডাউনলোড করে **{storage_name}**-এ আপলোড হচ্ছে, অপেক্ষা করুন...", call.message.chat.id, call.message.message_id)
    
    try:
        process_and_upload_file(message, choice, call.message.chat.id, call.message.message_id)
    except Exception as e:
        print(f"Error: {e}")
        send_message(call.message.chat.id, f"❌ আপলোড এরর: {e}")

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
        send_message(chat_id, "❌ সঠিক মিডিয়া ফাইল দিন।")
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
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
        
        extra_args = {'ContentType': 'video/mp4', 'ContentDisposition': 'inline'}

        if choice == "storage_r2":
            r2_client.upload_file(local_path, R2_BUCKET_NAME, unique_filename, ExtraArgs=extra_args)
            file_url_link = f"{R2_PUBLIC_DOMAIN}/{unique_filename}"
            storage_name = "Cloudflare R2"
        else:
            b2_client.upload_file(local_path, B2_BUCKET_NAME, unique_filename, ExtraArgs=extra_args)
            file_url_link = f"{B2_ENDPOINT}/file/{B2_BUCKET_NAME}/{unique_filename}"
            storage_name = "Backblaze B2"
        
        files_collection.insert_one({
            '_id': global_file_id,
            'uploader_id': user_id,
            'file_name': unique_filename,
            'storage_type': storage_name,
            'r2_url': file_url_link,
            'created_at': datetime.utcnow()
        })
        
        try: bot.delete_message(chat_id, message_id)
        except: pass
            
        success_text = f"✅ **Uploaded to {storage_name}!**\n\n🆔 **ID:** `{global_file_id}`\n🔗 **Link:**\n`{file_url_link}`"
        send_message(chat_id, success_text)
    except Exception as e:
        print(f"Upload Fail: {e}")
        send_message(chat_id, f"❌ আপলোড ব্যর্থ: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass
        if user_id in user_temp_data:
            del user_temp_data[user_id]

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args): pass

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))) , SimpleHandler).serve_forever(), daemon=True).start()
    while True:
        try: bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            import time; time.sleep(5)
