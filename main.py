import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    logging.error("❌ Нет токена!")
    exit(1)

app = Client("shopping_bot", bot_token=BOT_TOKEN)

# БАЗА ДАННЫХ
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('shopping_bot.db')
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, timezone TEXT DEFAULT 'Europe/Moscow')''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, 
            color TEXT DEFAULT '#3498db', is_shared BOOLEAN DEFAULT 0, share_code TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER, name TEXT, 
            note TEXT, assigned_to TEXT, is_checked BOOLEAN DEFAULT 0, 
            reminder_time TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS shared_access (
            category_id INTEGER, user_id INTEGER, role TEXT DEFAULT 'viewer',
            PRIMARY KEY (category_id, user_id))''')
        self.conn.commit()
    
    def add_user(self, user_id, username):
        self.cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        self.conn.commit()
    
    def create_category(self, user_id, name):
        self.cursor.execute('INSERT INTO categories (user_id, name) VALUES (?, ?)', (user_id, name))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_categories(self, user_id):
        self.cursor.execute('''SELECT c.id, c.name, c.color, c.is_shared, c.share_code,
            (SELECT COUNT(*) FROM items WHERE category_id = c.id AND is_checked = 0) as active,
            (SELECT COUNT(*) FROM items WHERE category_id = c.id) as total
            FROM categories c WHERE c.user_id = ? OR c.id IN (
            SELECT category_id FROM shared_access WHERE user_id = ?) ORDER BY c.id DESC''', (user_id, user_id))
        return self.cursor.fetchall()
    
    def delete_category(self, category_id, user_id):
        self.cursor.execute('DELETE FROM categories WHERE id = ? AND user_id = ?', (category_id, user_id))
        self.conn.commit()
    
    def add_item(self, category_id, name):
        self.cursor.execute('INSERT INTO items (category_id, name) VALUES (?, ?)', (category_id, name))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_items(self, category_id):
        self.cursor.execute('SELECT id, name, note, assigned_to, is_checked, reminder_time FROM items WHERE category_id = ? ORDER BY is_checked, id DESC', (category_id,))
        return self.cursor.fetchall()
    
    def toggle_item(self, item_id):
        self.cursor.execute('UPDATE items SET is_checked = NOT is_checked WHERE id = ?', (item_id,))
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

db = Database()

# КЛАВИАТУРЫ
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Мои списки", callback_data="my_lists")],
        [InlineKeyboardButton("➕ Новый список", callback_data="create_category")],
        [InlineKeyboardButton("🔗 Подключиться", callback_data="join_shared")],
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
        keyboard.append([InlineKeyboardButton(f"{emoji} {name} ({active}/{total})", callback_data=f"view_{cat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)

def items_keyboard(category_id, user_id):
    keyboard = []
    items = db.get_items(category_id)
    for item in items:
        item_id, name, note, assigned, is_checked, reminder = item
        check = "✅" if is_checked else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {name}", callback_data=f"toggle_{item_id}")])
    keyboard.append([
        InlineKeyboardButton("➕ Добавить", callback_data=f"add_{category_id}"),
        InlineKeyboardButton("🗑️ Очистить", callback_data=f"clear_{category_id}")
    ])
    keyboard.append([
        InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{category_id}"),
        InlineKeyboardButton("🗑️ Удалить список", callback_data=f"delete_{category_id}")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="my_lists")])
    return InlineKeyboardMarkup(keyboard)

# ОБРАБОТЧИКИ
@app.on_message(filters.command("start"))
async def start_command(client, message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    db.add_user(user_id, username)
    await message.reply("🛒 *Добро пожаловать в Умный Список!*", reply_markup=main_keyboard())

@app.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "back":
        await callback_query.message.edit_text("🏠 Главное меню", reply_markup=main_keyboard())
        await callback_query.answer()
        return
    
    if data == "my_lists":
        await callback_query.message.edit_text("📂 Ваши списки:", reply_markup=categories_keyboard(user_id))
        await callback_query.answer()
        return
    
    if data == "create_category":
        await callback_query.message.edit_text("✏️ Введите название списка:")
        await callback_query.answer()
        return
    
    if data.startswith("view_"):
        cat_id = int(data.split('_')[1])
        await callback_query.message.edit_text("📋 Список:", reply_markup=items_keyboard(cat_id, user_id))
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
    
    if data.startswith("clear_"):
        cat_id = int(data.split('_')[1])
        db.clear_checked(cat_id)
        await callback_query.message.edit_reply_markup(reply_markup=items_keyboard(cat_id, user_id))
        await callback_query.answer("✅ Очищено!")
        return
    
    if data.startswith("share_"):
        cat_id = int(data.split('_')[1])
        share_code = db.share_category(cat_id, user_id)
        await callback_query.message.edit_text(f"🔗 Код доступа: `{share_code}`", reply_markup=items_keyboard(cat_id, user_id))
        await callback_query.answer()
        return
    
    if data.startswith("delete_"):
        cat_id = int(data.split('_')[1])
        db.delete_category(cat_id, user_id)
        await callback_query.message.edit_text("🗑️ Список удален", reply_markup=main_keyboard())
        await callback_query.answer()
        return
    
    if data == "stats":
        categories = db.get_categories(user_id)
        total_items = sum(c[5] for c in categories)
        await callback_query.message.edit_text(f"📊 Статистика:\nВсего товаров: {total_items}", reply_markup=main_keyboard())
        await callback_query.answer()
        return
    
    if data == "join_shared":
        await callback_query.message.edit_text("🔑 Введите код доступа:")
        await callback_query.answer()
        return
    
    await callback_query.answer("⏳ В разработке")

@app.on_message(filters.text & ~filters.command)
async def text_handler(client, message):
    if message.text and len(message.text) > 2:
        user_id = message.from_user.id
        categories = db.get_categories(user_id)
        if categories:
            first_cat = categories[0][0]
            db.add_item(first_cat, message.text)
            await message.reply(f"✅ Добавлено: {message.text}")
        else:
            await message.reply("❌ Сначала создайте список через меню")

# ЗАПУСК
if __name__ == "__main__":
    print("🚀 Бот запускается...")
    app.run()
