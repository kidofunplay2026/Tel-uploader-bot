import os
import pymongo
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
from datetime import datetime
import boto3
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import asyncio

# --- B2SDK Native Import ---
from b2sdk.v2 import *

# --- Credentials ---
API_ID = 30908660
API_HASH = "ee861ae49daea1486f10d3a45a1b3ea9"
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

# --- Backblaze B2 Native Setup ---
B2_KEY_ID = "00529877a5de96a0000000001"
B2_APPLICATION_KEY = "K005tvFV6fS+ClJPMhS+VzRdCK6YD6I"
B2_BUCKET_NAME = "relex-movi-zone-Bucket"

# b2sdk অথরাইজেশন সেটআপ
info = InMemoryAccountInfo()
b2_api = B2Api(info)
b2_api.authorize_account("production", B2_KEY_ID, B2_APPLICATION_KEY)

# --- MongoDB Setup ---
MONGODB_URI = os.environ.get('MONGODB_URI', "mongodb+srv://tafsiralam2023_db_user:CUoWA0xoA8lzlXJQ@cluster0.k0uslgi.mongodb.net/")
try:
    client = pymongo.MongoClient(MONGODB_URI)
    db = client['uploader_bot_db']
    files_collection = db['files']
    counters_collection = db['counters']
except Exception as e:
    print(f"MongoDB Connection Error: {e}")

bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def get_next_sequence_value(sequence_name):
    sequence_document = counters_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=pymongo.ReturnDocument.AFTER,
        upsert=True
    )
    return sequence_document['sequence_value']

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("Welcome! Forward any large movie/video here to upload to Cloudflare R2 or Backblaze B2.")

@bot.on(events.NewMessage(incoming=True))
async def media_handler(event):
    if not event.media:
        return
    
    from telethon import Button
    buttons = [
        [Button.inline("☁️ Cloudflare R2", b"r2"), Button.inline("📦 Backblaze B2", b"b2")]
    ]
    
    await event.respond("📥 **ফাইল পাওয়া গেছে!** কোথায় আপলোড করবেন সিলেক্ট করুন:", buttons=buttons)
    event.client._temp_media = event.message

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode('utf-8')
    if data not in ["r2", "b2"]:
        return
        
    original_msg = getattr(event.client, '_temp_media', None)
    if not original_msg:
        await event.answer("সেশন শেষ! আবার ভিডিও ফরোয়ার্ড করুন।", alert=True)
        return
        
    storage_name = "Cloudflare R2" if data == "r2" else "Backblaze B2"
    status_msg = await event.edit(f"🚀 ব্যাকগ্রাউন্ডে ফাইল ডাউনলোড ও **{storage_name}**-এ আপলোড শুরু হয়েছে...")
    
    asyncio.create_task(process_upload_task(original_msg, data, storage_name, event.chat_id, status_msg.id))

async def process_upload_task(original_msg, data, storage_name, chat_id, message_id):
    local_path = None
    try:
        global_file_id = get_next_sequence_value('global_file_id')
        
        file_extension = ".mp4"
        if original_msg.media.document and original_msg.media.document.attributes:
            for attr in original_msg.media.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    file_extension = os.path.splitext(attr.file_name)[1]
                
        unique_filename = f"movie_{global_file_id}{file_extension}"
        local_path = f"temp_{unique_filename}"
        
        # চংক আকারে টেলিগ্রাম থেকে ফাইল ডাউনলোড
        with open(local_path, 'wb') as f:
            async for chunk in bot.iter_download(original_msg.media, chunk_size=512 * 1024):
                f.write(chunk)
        
        if data == "r2":
            extra_args = {'ContentType': 'video/mp4', 'ContentDisposition': 'inline'}
            r2_client.upload_file(local_path, R2_BUCKET_NAME, unique_filename, ExtraArgs=extra_args)
            file_url_link = f"{R2_PUBLIC_DOMAIN}/{unique_filename}"
        else:
            # ব্যাকব্লেজের নিজস্ব নেটিভ SDK দিয়ে আপলোড
            bucket = b2_api.get_bucket_by_name(B2_BUCKET_NAME)
            bucket.upload_local_file(
                local_file=local_path,
                file_name=unique_filename
            )
            file_url_link = f"https://f005.backblazeb2.com/file/{B2_BUCKET_NAME}/{unique_filename}"
            
        files_collection.insert_one({
            '_id': global_file_id,
            'file_name': unique_filename,
            'storage_type': storage_name,
            'r2_url': file_url_link,
            'created_at': datetime.utcnow()
        })
        
        await bot.edit_message(chat_id, message_id, f"✅ **Uploaded to {storage_name}!**\n\n🆔 **ID:** `{global_file_id}`\n🔗 **Link:**\n`{file_url_link}`")
    except Exception as e:
        print(f"Error: {e}")
        try:
            await bot.edit_message(chat_id, message_id, f"❌ আপলোড ব্যর্থ: {e}")
        except:
            pass
    finally:
        # কাজ শেষ হওয়া মাত্রই লোকাল সার্ভার থেকে টেম্পোরারি ফাইল ডিলিট করার লজিক
        if local_path and os.path.exists(local_path):
            try: 
                os.remove(local_path)
            except: 
                pass

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args): pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    def run_server():
        server = HTTPServer(('0.0.0.0', port), SimpleHandler)
        server.serve_forever()
        
    threading.Thread(target=run_server, daemon=True).start()
    print(f"HTTP Server started on port {port}")
    print("Bot is starting...")
    bot.run_until_disconnected()
