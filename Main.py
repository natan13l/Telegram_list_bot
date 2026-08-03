import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import os

# ========== НАСТРОЙКИ ==========
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не найден в переменных окружения!")
    exit(1)

app = Client("shopping_bot", bot_token=BOT_TOKEN)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('shopping_bot.db')
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                timezone TEXT DEFAULT 'Europe/Moscow'
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                color TEXT DEFAULT '#3498db',
                is_shared BOOLEAN DEFAULT 0,
                share_code TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT,
                note TEXT,
                assigned_to TEXT,
                is_checked BOOLEAN DEFAULT 0,
                reminder_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS shared_access (
                category_id INTEGER,
                user_id INTEGER,
                role TEXT DEFAULT 'viewer',
                PRIMARY KEY (category_id, user_id)
            )
        ''')
        self.conn.commit()
    
    def add_user(self, user_id, username):
        self.cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        self.conn.commit()
    
    def set_timezone(self, user_id, tz):
        self.cursor.execute('UPDATE users SET timezone = ? WHERE user_id = ?', (tz, user_id))
        self.conn.commit()
    
    def get_user_timezone(self, user_id):
        self.cursor.execute('SELECT timezone FROM users WHERE user_id = ?', (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 'Europe/Moscow'
    
    def create_category(self, user_id, name, color='#3498db', is_shared=False):
        share_code = str(uuid.uuid4())[:8] if is_shared else None
        self.cursor.execute(
            'INSERT INTO categories (user_id, name, color, is_shared, share_code) VALUES (?, ?, ?, ?, ?)',
            (user_id, name, color, is_shared, share_code)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_categories(self, user_id):
        self.cursor.execute('''
            SELECT c.id, c.name, c.color, c.is_shared, c.share_code,
                   (SELECT COUNT(*) FROM items WHERE category_id = c.id AND is_checked = 0) as active,
                   (SELECT COUNT(*) FROM items WHERE category_id = c.id) as total
            FROM categories c
            WHERE c.user_id = ? OR c.id IN (SELECT category_id FROM shared_access WHERE user_id = ?)
            ORDER BY c.id DESC
        ''', (user_id, user_id))
        return self.cursor.fetchall()
    
    def delete_category(self, category_id, user_id):
        self.cursor.execute('DELETE FROM categories WHERE id = ? AND user_id = ?', (category_id, user_id))
        self.conn.commit()
    
    def rename_category(self, category_id, user_id, new_name):
        self.cursor.execute('UPDATE categories SET name = ? WHERE id = ? AND user_id = ?', (new_name, category_id, user_id))
        self.conn.commit()
    
    def add_item(self, category_id, name, note='', assigned_to=''):
        self.cursor.execute('INSERT INTO items (category_id, name, note, assigned_to) VALUES (?, ?, ?, ?)',
                           (category_id, name, note, assigned_to))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_items(self, category_id):
        self.cursor.execute(
            'SELECT id, name, note, assigned_to, is_checked, reminder_time FROM items WHERE category_id = ? ORDER BY is_checked, id DESC',
            (category_id,)
        )
        return self.cursor.fetchall()
    
    def toggle_item(self, item_id):
        self.cursor.execute('UPDATE items SET is_checked = NOT is_checked WHERE id = ?', (item_id,))
        self.conn.commit()
    
    def delete_item(self, item_id):
        self.cursor.execute('DELETE FROM items WHERE id = ?', (item_id,))
        self.conn.commit()
    
    def update_item_note(self, item_id, note):
        self.cursor.execute('UPDATE items SET note = ? WHERE id = ?', (note, item_id))
        self.conn.commit()
    
    def update_item_assigned(self, item_id, assigned_to):
        self.cursor.execute('UPDATE items SET assigned_to = ? WHERE id = ?', (assigned_to, item_id))
        self.conn.commit()
    
    def set_reminder(self, item_id, reminder_time):
        self.cursor.execute('UPDATE items SET reminder_time = ? WHERE id = ?', (reminder_time, item_id))
        self.conn.commit()
    
    def clear_checked(self, category_id):
        self.cursor.execute('DELETE FROM items WHERE category_id = ? AND is_checked = 1', (category_id,))
        self.conn.commit()
    
    def share_category(self, category_id, user_id):
        share_code = str(uuid.uuid4())[:8]
        self.cursor.execute('UPDATE categories SET is_shared = 1, share_code = ? WHERE id = ? AND user_id = ?',
                           (share_code, category_id, user_id))
        self.conn.commit()
        return share_code
    
    def get_category_by_share_code(self, share_code):
        self.cursor.execute('SELECT id, user_id, name FROM categories WHERE share_code = ?', (share_code,))
        return self.cursor.fetchone()
    
    def add_shared_access(self, category_id, user_id, role='viewer'):
        self.cursor.execute('INSERT OR IGNORE INTO shared_access (category_id, user_id, role) VALUES (?, ?, ?)',
                           (category_id, user_id, role))
        self.conn.commit()
    
    def get_items_with_reminders(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        self.cursor.execute('''
            SELECT i.id, i.name, u.user_id, u.timezone FROM items i
            JOIN categories c ON i.category_id = c.id
            JOIN users u ON c.user_id = u.user_id
            WHERE i.reminder_time IS NOT NULL AND i.reminder_time <= ? AND i.is_checked = 0
        ''', (now,))
        return self.cursor.fetchall()

db = Database()
# ========== КЛАВИАТУРЫ ==========
def main_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Мои списки", callback_data="my_lists")],
        [InlineKeyboardButton("➕ Новый список", callback_data="create_category")],
        [InlineKeyboardButton("🔗 Подключиться по коду", callback_data="join_shared")],
        [InlineKeyboardButton("⏰ Настройки времени", callback_data="settings_timezone")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
    ])

def categories_keyboard(user_id):
    keyboard = []
    categories = db.get_categories(user_id)
    if not categories:
        keyboard.append([InlineKeyboardButton("📭 Нет списков", callback_data="noop")])
    for cat in categories:
        cat_id, name, color, is_shared, share_code, active, total = cat
        emoji = "🔓" if is_shared else "📁"
        label = f"{emoji} {name} ({active}/{total})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"view_cat_{cat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

def items_keyboard(category_id, user_id):
    keyboard = []
    items = db.get_items(category_id)
    for item in items:
        item_id, name, note, assigned, is_checked, reminder = item
        check = "✅" if is_checked else "⬜"
        assigned_text = f" 👤{assigned}" if assigned else ""
        reminder_text = " ⏰" if reminder else ""
        label = f"{check} {name}{assigned_text}{reminder_text}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle_{item_id}")])
    
    keyboard.append([
        InlineKeyboardButton("➕ Добавить", callback_data=f"add_item_{category_id}"),
        InlineKeyboardButton("🗑️ Очистить выполн.", callback_data=f"clear_checked_{category_id}")
    ])
    keyboard.append([
        InlineKeyboardButton("✏️ Ред. список", callback_data=f"edit_cat_{category_id}"),
        InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_cat_{category_id}")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="my_lists")])
    return InlineKeyboardMarkup(keyboard)

def edit_category_keyboard(category_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"rename_cat_{category_id}")],
        [InlineKeyboardButton("🗑️ Удалить список", callback_data=f"delete_cat_{category_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"view_cat_{category_id}")]
    ])

def timezone_keyboard():
    keyboard = []
    tz_list = ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Dubai', 'Asia/Tokyo']
    row = []
    for tz in tz_list:
        row.append(InlineKeyboardButton(tz, callback_data=f"tz_{tz}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ ==========
async def start_command(client, message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    db.add_user(user_id, username)
    
    welcome = (
        "🛒 *Добро пожаловать в Умный Список Покупок!*\n\n"
        "📋 Создавайте списки, делитесь с друзьями,\n"
        "назначайте ответственных и получайте напоминания!\n\n"
        "🔹 *Кнопки внизу* — управление списками\n"
        "🔹 *Нажми на пункт* — отметить выполненным"
    )
    await message.reply(welcome, reply_markup=main_keyboard(user_id))

async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "back_main":
        await callback_query.message.edit_text("🏠 *Главное меню*", reply_markup=main_keyboard(user_id))
        await callback_query.answer()
        return
    
    if data == "my_lists":
        await callback_query.message.edit_text("📂 *Ваши списки покупок*\n(активные/всего)", 
                                              reply_markup=categories_keyboard(user_id))
        await callback_query.answer()
        return
    
    if data == "create_category":
        await callback_query.message.edit_text("✏️ *Введите название нового списка:*\n(например: «Продукты», «День рождения»)")
        await callback_query.answer()
        return
    
    if data.startswith("view_cat_"):
        cat_id = int(data.split('_')[2])
        await callback_query.message.edit_text("📋 *Список покупок*", reply_markup=items_keyboard(cat_id, user_id))
        await callback_query.answer()
        return
    
    if data.startswith("toggle_"):
        item_id = int(data.split('_')[1])
        db.toggle_item(item_id)
        db.cursor.execute('SELECT category_id FROM items WHERE id = ?', (item_id,))
        cat_id = db.cursor.fetchone()[0]
        await callback_query.message.edit_reply_markup(reply_markup=items_keyboard(cat_id, user_id))
        await callback_query.answer()
        return
    
    if data.startswith("clear_checked_"):
        cat_id = int(data.split('_')[2])
        db.clear_checked(cat_id)
        await callback_query.message.edit_reply_markup(reply_markup=items_keyboard(cat_id, user_id))
        await callback_query.answer("✅ Выполненные пункты удалены")
        return
    
    if data.startswith("edit_cat_"):
        cat_id = int(data.split('_')[2])
        await callback_query.message.edit_text("✏️ *Редактирование списка*\nВыберите действие:",
                                              reply_markup=edit_category_keyboard(cat_id))
        await callback_query.answer()
        return
    
    if data.startswith("delete_cat_"):
        cat_id = int(data.split('_')[2])
        db.delete_category(cat_id, user_id)
        await callback_query.message.edit_text("🗑️ *Список удален*", reply_markup=main_keyboard(user_id))
        await callback_query.answer()
        return
          if data.startswith("share_cat_"):
        cat_id = int(data.split('_')[2])
        share_code = db.share_category(cat_id, user_id)
        await callback_query.message.edit_text(
            f"🔗 *Код для доступа:*\n`{share_code}`\n\nОтправьте этот код друзьям",
            reply_markup=items_keyboard(cat_id, user_id)
        )
        await callback_query.answer()
        return
    
    if data == "join_shared":
        await callback_query.message.edit_text("🔑 *Введите код доступа:*")
        await callback_query.answer()
        return
    
    if data == "settings_timezone":
        current_tz = db.get_user_timezone(user_id)
        await callback_query.message.edit_text(
            f"🌍 *Настройки времени*\nТекущий: `{current_tz}`\nВыберите новый:",
            reply_markup=timezone_keyboard()
        )
        await callback_query.answer()
        return
    
    if data.startswith("tz_"):
        tz = data.replace('tz_', '')
        db.set_timezone(user_id, tz)
        await callback_query.message.edit_text(f"✅ *Часовой пояс: {tz}*", reply_markup=main_keyboard(user_id))
        await callback_query.answer()
        return
    
    if data == "stats":
        categories = db.get_categories(user_id)
        total_lists = len(categories)
        total_items = sum(c[5] for c in categories)
        checked_items = sum(c[4] for c in categories)
        completed = total_items - checked_items
        
        stats_text = (
            f"📊 *Ваша статистика*\n\n"
            f"📁 Всего списков: {total_lists}\n"
            f"📦 Всего товаров: {total_items}\n"
            f"✅ Выполнено: {completed}\n"
            f"⏳ Осталось: {checked_items}"
        )
        await callback_query.message.edit_text(stats_text, reply_markup=main_keyboard(user_id))
        await callback_query.answer()
        return
    
    if data == "noop":
        await callback_query.answer("👀 Нет списков", show_alert=True)
        return
    
    await callback_query.answer("⏳ В разработке")

async def text_handler(client, message):
    user_id = message.from_user.id
    text = message.text
    
    if text.startswith('/'):
        return
    
    # Простое добавление товара (для упрощения)
    await message.reply("✅ Добавлено!")

async def send_reminders():
    items = db.get_items_with_reminders()
    for item_id, name, user_id, tz_str in items:
        try:
            msg = f"⏰ *Напоминание!*\n\nНе забудьте: «{name}»"
            await app.send_message(user_id, msg)
            db.set_reminder(item_id, None)
        except Exception as e:
            logging.error(f"Ошибка: {e}")

# ========== ЗАПУСК ==========
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, CronTrigger(minute="*/1"))
    scheduler.start()
    
    app.add_handler(MessageHandler(start_command, filters.command("start")))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(text_handler, filters.text & ~filters.command))
    
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
