import logging
import sqlite3
import uuid
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import os

# ========== НАСТРОЙКИ ==========
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден!")
    exit(1)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('shopping_bot.db', check_same_thread=False)
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
                is_shared BOOLEAN DEFAULT 0,
                share_code TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT,
                is_checked BOOLEAN DEFAULT 0,
                reminder_time TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS shared_access (
                category_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (category_id, user_id)
            )
        ''')
        self.conn.commit()
    
    def add_user(self, user_id, username):
        self.cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
        self.conn.commit()
    
    def create_category(self, user_id, name):
        self.cursor.execute('INSERT INTO categories (user_id, name) VALUES (?, ?)', (user_id, name))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_categories(self, user_id):
        self.cursor.execute('''
            SELECT c.id, c.name,
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
    
    def add_item(self, category_id, name):
        self.cursor.execute('INSERT INTO items (category_id, name) VALUES (?, ?)', (category_id, name))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_items(self, category_id):
        self.cursor.execute('SELECT id, name, is_checked FROM items WHERE category_id = ? ORDER BY is_checked, id DESC', (category_id,))
        return self.cursor.fetchall()
    
    def toggle_item(self, item_id):
        self.cursor.execute('UPDATE items SET is_checked = NOT is_checked WHERE id = ?', (item_id,))
        self.conn.commit()
    
    def get_category_by_item(self, item_id):
        self.cursor.execute('SELECT category_id FROM items WHERE id = ?', (item_id,))
        res = self.cursor.fetchone()
        return res[0] if res else None
    
    def delete_item(self, item_id):
        self.cursor.execute('DELETE FROM items WHERE id = ?', (item_id,))
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

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Мои списки", callback_data="lists")],
        [InlineKeyboardButton("➕ Новый список", callback_data="new_list")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
    ])

def categories_keyboard(user_id):
    keyboard = []
    categories = db.get_categories(user_id)
    if not categories:
        keyboard.append([InlineKeyboardButton("📭 Нет списков", callback_data="noop")])
    for cat in categories:
        cat_id, name, active, total = cat
        keyboard.append([InlineKeyboardButton(f"📁 {name} ({active}/{total})", callback_data=f"view_{cat_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)

def items_keyboard(category_id, user_id):
    keyboard = []
    items = db.get_items(category_id)
    for item in items:
        item_id, name, is_checked = item
        check = "✅" if is_checked else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {name}", callback_data=f"toggle_{item_id}")])
    keyboard.append([
        InlineKeyboardButton("➕ Добавить", callback_data=f"add_{category_id}"),
        InlineKeyboardButton("🗑️ Очистить", callback_data=f"clear_{category_id}")
    ])
    keyboard.append([
        InlineKeyboardButton("🔗 Поделиться", callback_data=f"share_{category_id}"),
        InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{category_id}")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="lists")])
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    db.add_user(user_id, username)
    await update.message.reply_text(
        "🛒 *Добро пожаловать в Умный Список Покупок!*\n\n"
        "Создавайте списки и делитесь ими с друзьями!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    if data == "back":
        await query.message.edit_text("🏠 Главное меню", reply_markup=main_keyboard())
        return
    
    if data == "lists":
        await query.message.edit_text("📂 Ваши списки:", reply_markup=categories_keyboard(user_id))
        return
    
    if data == "new_list":
        context.user_data['action'] = 'create_list'
        await query.message.edit_text("✏️ Введите название нового списка:")
        return
    
    if data == "stats":
        categories = db.get_categories(user_id)
        total = sum(c[3] for c in categories)
        await query.message.edit_text(f"📊 Всего товаров: {total}", reply_markup=main_keyboard())
        return
    
    if data.startswith("view_"):
        cat_id = int(data.split('_')[1])
        await query.message.edit_text(f"📋 Список:", reply_markup=items_keyboard(cat_id, user_id))
        return
    
    if data.startswith("toggle_"):
        item_id = int(data.split('_')[1])
        db.toggle_item(item_id)
        cat_id = db.get_category_by_item(item_id)
        if cat_id:
            await query.message.edit_reply_markup(reply_markup=items_keyboard(cat_id, user_id))
        return
    
    if data.startswith("clear_"):
        cat_id = int(data.split('_')[1])
        db.clear_checked(cat_id)
        await query.message.edit_reply_markup(reply_markup=items_keyboard(cat_id, user_id))
        return
    
    if data.startswith("share_"):
        cat_id = int(data.split('_')[1])
        share_code = db.share_category(cat_id, user_id)
        await query.message.edit_text(f"🔗 Код доступа: `{share_code}`", reply_markup=items_keyboard(cat_id, user_id), parse_mode="Markdown")
        return
    
    if data.startswith("delete_"):
        cat_id = int(data.split('_')[1])
        db.delete_category(cat_id, user_id)
        await query.message.edit_text("🗑️ Список удален", reply_markup=main_keyboard())
        return
    
    if data.startswith("add_"):
        cat_id = int(data.split('_')[1])
        context.user_data['action'] = 'add_item'
        context.user_data['category_id'] = cat_id
        await query.message.edit_text("📝 Введите название товара:")
        return

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    action = context.user_data.get('action')
    
    if action == 'create_list':
        cat_id = db.create_category(user_id, text)
        context.user_data['action'] = None
        await update.message.reply_text(f"✅ Список «{text}» создан!", reply_markup=main_keyboard())
    
    elif action == 'add_item':
        cat_id = context.user_data.get('category_id')
        db.add_item(cat_id, text)
        context.user_data['action'] = None
        await update.message.reply_text(f"✅ Добавлено: {text}", reply_markup=items_keyboard(cat_id, user_id))
    
    else:
        await update.message.reply_text("❌ Используйте кнопки меню")

# ========== ЗАПУСК ==========
def main():
    print("🚀 Бот запускается...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("✅ Бот готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
