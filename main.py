import os
import sqlite3
import logging
import asyncio
import uuid
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== НАСТРОЙКИ И ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    print("❌ Ошибка: Переменная BOT_TOKEN не найдена!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== БАЗА ДАННЫХ (SQLite) ==========
class Database:
    def __init__(self, db_path="shopping_bot.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                share_code TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT,
                assigned_to TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                is_checked BOOLEAN DEFAULT 0
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
        share_code = str(uuid.uuid4())[:8]
        self.cursor.execute('INSERT INTO categories (user_id, name, share_code) VALUES (?, ?, ?)', (user_id, name, share_code))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_categories(self, user_id):
        self.cursor.execute('''
            SELECT DISTINCT c.id, c.name,
                   (SELECT COUNT(*) FROM items WHERE category_id = c.id AND is_checked = 0) as active,
                   (SELECT COUNT(*) FROM items WHERE category_id = c.id) as total
            FROM categories c
            LEFT JOIN shared_access sa ON c.id = sa.category_id
            WHERE c.user_id = ? OR sa.user_id = ?
            ORDER BY c.id DESC
        ''', (user_id, user_id))
        return self.cursor.fetchall()

    def get_category_by_id(self, cat_id):
        self.cursor.execute('SELECT id, name, share_code, user_id FROM categories WHERE id = ?', (cat_id,))
        return self.cursor.fetchone()

    def delete_category(self, cat_id):
        self.cursor.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
        self.cursor.execute('DELETE FROM items WHERE category_id = ?', (cat_id,))
        self.cursor.execute('DELETE FROM shared_access WHERE category_id = ?', (cat_id,))
        self.conn.commit()

    def add_item(self, category_id, name):
        self.cursor.execute('INSERT INTO items (category_id, name) VALUES (?, ?)', (category_id, name))
        self.conn.commit()

    def get_items(self, category_id):
        self.cursor.execute('SELECT id, name, assigned_to, comment, is_checked FROM items WHERE category_id = ? ORDER BY is_checked, id DESC', (category_id,))
        return self.cursor.fetchall()

    def toggle_item(self, item_id):
        self.cursor.execute('UPDATE items SET is_checked = NOT is_checked WHERE id = ?', (item_id,))
        self.conn.commit()

    def update_item_details(self, item_id, assigned_to=None, comment=None):
        if assigned_to is not None:
            self.cursor.execute('UPDATE items SET assigned_to = ? WHERE id = ?', (assigned_to, item_id))
        if comment is not None:
            self.cursor.execute('UPDATE items SET comment = ? WHERE id = ?', (comment, item_id))
        self.conn.commit()

    def clear_checked(self, category_id):
        self.cursor.execute('DELETE FROM items WHERE category_id = ? AND is_checked = 1', (category_id,))
        self.conn.commit()

    def get_item(self, item_id):
        self.cursor.execute('SELECT id, category_id, name, assigned_to, comment, is_checked FROM items WHERE id = ?', (item_id,))
        return self.cursor.fetchone()

    def join_by_code(self, user_id, share_code):
        self.cursor.execute('SELECT id FROM categories WHERE share_code = ?', (share_code,))
        res = self.cursor.fetchone()
        if res:
            cat_id = res[0]
            self.cursor.execute('INSERT OR IGNORE INTO shared_access (category_id, user_id) VALUES (?, ?)', (cat_id, user_id))
            self.conn.commit()
            return cat_id
        return None

db = Database()

# ========== FSM (СОСТОЯНИЯ ВВОДА) ==========
class Form(StatesGroup):
    waiting_for_category_name = State()
    waiting_for_item_name = State()
    waiting_for_assignee = State()
    waiting_for_comment = State()
    waiting_for_share_code = State()

# ========== КЛАВИАТУРЫ ==========
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои Списки", callback_data="list_cats")],
        [InlineKeyboardButton(text="➕ Создать Список", callback_data="new_cat")],
        [InlineKeyboardButton(text="🔗 Войти по коду", callback_data="join_code")]
    ])

def categories_kb(user_id):
    cats = db.get_categories(user_id)
    buttons = []
    for cat_id, name, active, total in cats:
        buttons.append([InlineKeyboardButton(text=f"📁 {name} [{active}/{total}]", callback_data=f"view_cat_{cat_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def items_kb(cat_id):
    items = db.get_items(cat_id)
    buttons = []
    for item_id, name, assigned, comment, is_checked in items:
        status = "✅" if is_checked else "⬜"
        text = f"{status} {name}"
        if assigned:
            text += f" (👤 {assigned})"
        buttons.append([
            InlineKeyboardButton(text=text, callback_data=f"toggle_{item_id}_{cat_id}"),
            InlineKeyboardButton(text="⚙️", callback_data=f"edit_item_{item_id}_{cat_id}")
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"add_item_{cat_id}"),
        InlineKeyboardButton(text="🧹 Очистить готовое", callback_data=f"clear_{cat_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="🔗 Поделиться", callback_data=f"share_{cat_id}"),
        InlineKeyboardButton(text="🗑️ Удалить список", callback_data=f"del_cat_{cat_id}")
    ])
    buttons.append([InlineKeyboardButton(text="🔙 К спискам", callback_data="list_cats")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def item_edit_kb(item_id, cat_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Назначить ответственного", callback_data=f"assign_{item_id}_{cat_id}")],
        [InlineKeyboardButton(text="💬 Добавить комментарий", callback_data=f"comment_{item_id}_{cat_id}")],
        [InlineKeyboardButton(text="🔙 Назад в список", callback_data=f"view_cat_{cat_id}")]
    ])

# ========== ОБРАБОТЧИКИ КОМАНД И КНОПОК ==========
@dp.message(CommandStart())
async def start_cmd(message: Message):
    db.add_user(message.from_user.id, message.from_user.username or "Пользователь")
    text = (
        "🛒 **Добро пожаловать в Умный Список Покупок!**\n\n"
        "✨ Создавайте категории (Продукты, Срочно, Пикник)\n"
        "👥 Делитесь списками с друзьями и назначайте, кто что покупает!\n"
        "✅ Вычеркивайте покупки в один клик."
    )
    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🏠 **Главное меню:**", reply_markup=main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "list_cats")
async def list_cats_cb(callback: CallbackQuery):
    await callback.message.edit_text("📂 **Ваши списки и категории:**", reply_markup=categories_kb(callback.from_user.id), parse_mode="Markdown")

@dp.callback_query(F.data == "new_cat")
async def new_cat_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_category_name)
    await callback.message.edit_text("✏️ Введите название нового списка (например: *День Рождения* или *Продукты*):", parse_mode="Markdown")

@dp.message(Form.waiting_for_category_name)
async def process_cat_name(message: Message, state: FSMContext):
    cat_id = db.create_category(message.from_user.id, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Список **«{message.text.strip()}»** успешно создан!", reply_markup=items_kb(cat_id), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("view_cat_"))
async def view_cat_cb(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[2])
    cat = db.get_category_by_id(cat_id)
    await callback.message.edit_text(f"📋 Список: **{cat[1]}**\nНажмите на пункт, чтобы вычеркнуть его, или ⚙️ для настроек:", reply_markup=items_kb(cat_id), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("add_item_"))
async def add_item_cb(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[2])
    await state.update_data(cat_id=cat_id)
    await state.set_state(Form.waiting_for_item_name)
    await callback.message.edit_text("📝 Введите название товара или пункта:")

@dp.message(Form.waiting_for_item_name)
async def process_item_name(message: Message, state: FSMContext):
    data = await state.get_data()
    cat_id = data.get("cat_id")
    db.add_item(cat_id, message.text.strip())
    await state.clear()
    await message.answer(f"➕ Добавлено: **{message.text.strip()}**", reply_markup=items_kb(cat_id), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_item_cb(callback: CallbackQuery):
    _, item_id, cat_id = callback.data.split("_")
    db.toggle_item(int(item_id))
    await callback.message.edit_reply_markup(reply_markup=items_kb(int(cat_id)))

@dp.callback_query(F.data.startswith("clear_"))
async def clear_cb(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    db.clear_checked(cat_id)
    await callback.message.edit_reply_markup(reply_markup=items_kb(cat_id))

@dp.callback_query(F.data.startswith("share_"))
async def share_cb(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    cat = db.get_category_by_id(cat_id)
    code = cat[2]
    await callback.message.edit_text(
        f"🔗 **Совместный доступ к списку «{cat[1]}»**\n\n"
        f"Отправьте другу этот код доступа:\n`{code}`\n\n"
        f"Ему нужно будет нажать кнопку **«Войти по коду»** в главном меню и вписать этот код!",
        reply_markup=items_kb(cat_id),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "join_code")
async def join_code_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_share_code)
    await callback.message.edit_text("🔑 Введите 8-значный код доступа, который вам прислал друг:")

@dp.message(Form.waiting_for_share_code)
async def process_share_code(message: Message, state: FSMContext):
    code = message.text.strip()
    cat_id = db.join_by_code(message.from_user.id, code)
    await state.clear()
    if cat_id:
        cat = db.get_category_by_id(cat_id)
        await message.answer(f"🎉 Вы успешно подключились к списку **«{cat[1]}»**!", reply_markup=items_kb(cat_id), parse_mode="Markdown")
    else:
        await message.answer("❌ Неверный код доступа.", reply_markup=main_kb())

@dp.callback_query(F.data.startswith("edit_item_"))
async def edit_item_cb(callback: CallbackQuery):
    _, _, item_id, cat_id = callback.data.split("_")
    item = db.get_item(int(item_id))
    text = f"⚙️ **Настройки пункта:** {item[2]}\n"
    if item[3]:
        text += f"👤 Ответственный: **{item[3]}**\n"
    if item[4]:
        text += f"💬 Комментарий: _{item[4]}_\n"
    await callback.message.edit_text(text, reply_markup=item_edit_kb(int(item_id), int(cat_id)), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("assign_"))
async def assign_cb(callback: CallbackQuery, state: FSMContext):
    _, item_id, cat_id = callback.data.split("_")
    await state.update_data(item_id=int(item_id), cat_id=int(cat_id))
    await state.set_state(Form.waiting_for_assignee)
    await callback.message.edit_text("👤 Введите имя или юзернейм того, кто покупает этот пункт:")

@dp.message(Form.waiting_for_assignee)
async def process_assignee(message: Message, state: FSMContext):
    data = await state.get_data()
    db.update_item_details(data["item_id"], assigned_to=message.text.strip())
    await state.clear()
    await message.answer("✅ Ответственный назначен!", reply_markup=items_kb(data["cat_id"]))

@dp.callback_query(F.data.startswith("comment_"))
async def comment_cb(callback: CallbackQuery, state: FSMContext):
    _, item_id, cat_id = callback.data.split("_")
    await state.update_data(item_id=int(item_id), cat_id=int(cat_id))
    await state.set_state(Form.waiting_for_comment)
    await callback.message.edit_text("💬 Введите ваш комментарий (например: *Взял по акции за 100р*):")

@dp.message(Form.waiting_for_comment)
async def process_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    db.update_item_details(data["item_id"], comment=message.text.strip())
    await state.clear()
    await message.answer("✅ Комментарий сохранен!", reply_markup=items_kb(data["cat_id"]))

@dp.callback_query(F.data.startswith("del_cat_"))
async def del_cat_cb(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[2])
    db.delete_category(cat_id)
    await callback.message.edit_text("🗑️ Список удален.", reply_markup=categories_kb(callback.from_user.id))

# ========== ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER WEB SERVICE ==========
async def handle_ping(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ========== ЗАПУСК ==========
async def main():
    await start_web_server()
    print("🚀 Бот запущен и открыл порт для Render!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
