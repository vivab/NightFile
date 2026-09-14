import os
import secrets
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.getenv("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "/app/data/bot.db")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

router = Router()

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        telegram_file_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        username TEXT,
        invite_link TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS deliveries (
        user_id INTEGER NOT NULL,
        file_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, file_id)
    );
    """)
    con.commit()
    con.close()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

class AddFile(StatesGroup):
    waiting_file = State()

class AddChannel(StatesGroup):
    waiting_channel = State()

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Добавить файл", callback_data="add_file")],
        [InlineKeyboardButton(text="📋 Файлы", callback_data="files")],
        [InlineKeyboardButton(text="📢 Добавить канал", callback_data="add_channel")],
        [InlineKeyboardButton(text="📢 Список каналов", callback_data="channels")],
    ])

def check_kb(code):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data=f"check:{code}")]
    ])

def channel_kb(channels):
    rows = []
    for c in channels:
        if c["invite_link"]:
            rows.append([InlineKeyboardButton(text=f"📢 {c['title']}", url=c["invite_link"])])
        elif c["username"]:
            rows.append([InlineKeyboardButton(text=f"📢 {c['title']}", url=f"https://t.me/{c['username'].lstrip('@')}")])
    return rows

async def missing_channels(bot: Bot, user_id: int):
    con = db()
    channels = con.execute("SELECT * FROM channels ORDER BY id").fetchall()
    con.close()

    missing = []
    for c in channels:
        try:
            member = await bot.get_chat_member(c["chat_id"], user_id)
            # Member is accepted as subscribed if they are a member,
            # administrator, owner, or have a pending join request.
            if member.status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
            }:
                continue
            if member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False):
                continue
            missing.append(c)
        except Exception:
            # For private channels, get_chat_member can fail for users who
            # have only a pending request. We cannot reliably infer a request
            # from this method alone, so the channel should be configured with
            # an invite link and the user can request/subscribe.
            missing.append(c)
    return missing

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not message.from_user:
        return
    con = db()
    con.execute(
        "INSERT OR REPLACE INTO users(telegram_id, username) VALUES(?, ?)",
        (message.from_user.id, message.from_user.username)
    )
    con.commit()

    arg = (message.text or "").split(maxsplit=1)
    code = arg[1] if len(arg) > 1 else None

    if code:
        f = con.execute("SELECT * FROM files WHERE code=?", (code,)).fetchone()
        con.close()
        if not f:
            await message.answer("❌ Ссылка недействительна или файл удалён.")
            return
        await show_file_gate(message, code, f["name"])
        return

    con.close()
    if is_admin(message.from_user.id):
        await message.answer("🛠 Админ-панель", reply_markup=admin_kb())
    else:
        await message.answer("Привет! Перейди по ссылке на файл, чтобы получить его.")

async def show_file_gate(message: Message, code: str, name: str):
    con = db()
    channels = con.execute("SELECT * FROM channels ORDER BY id").fetchall()
    con.close()
    text = f"📁 <b>{name}</b>\n\nЧтобы получить файл, подпишись на обязательные каналы:"
    kb = channel_kb(channels)
    kb.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data=f"check:{code}")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@router.callback_query(F.data.startswith("check:"))
async def check_subscription(callback: CallbackQuery, bot: Bot):
    code = callback.data.split(":", 1)[1]
    if not callback.from_user:
        return
    con = db()
    f = con.execute("SELECT * FROM files WHERE code=?", (code,)).fetchone()
    con.close()
    if not f:
        await callback.answer("Файл больше не существует.", show_alert=True)
        return

    missing = await missing_channels(bot, callback.from_user.id)
    if missing:
        await callback.answer("❌ Не все обязательные каналы выполнены.", show_alert=True)
        await callback.message.answer(
            "❌ <b>Осталось подписаться:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=channel_kb(missing) + [
                    [InlineKeyboardButton(text="🔄 Проверить ещё раз", callback_data=f"check:{code}")]
                ]
            ),
            parse_mode="HTML"
        )
        return

    try:
        await callback.message.answer_document(f["telegram_file_id"], caption=f"📁 {f['name']}")
        con = db()
        con.execute(
            "INSERT OR IGNORE INTO deliveries(user_id, file_id) VALUES(?, ?)",
            (callback.from_user.id, f["id"])
        )
        con.commit()
        con.close()
        await callback.answer("✅ Файл отправлен!")
    except Exception:
        await callback.answer("Не удалось отправить файл.", show_alert=True)

@router.message(Command("admin"))
async def admin(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("🛠 Админ-панель", reply_markup=admin_kb())

@router.callback_query(F.data == "add_file")
async def add_file_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AddFile.waiting_file)
    await callback.message.answer("📁 Перешли мне файл одним сообщением.")

@router.message(AddFile.waiting_file)
async def add_file_receive(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    doc = message.document
    if not doc:
        await message.answer("❌ Нужен документ/файл. Перешли файл ещё раз.")
        return

    code = secrets.token_urlsafe(7)
    name = doc.file_name or "file"
    con = db()
    con.execute(
        "INSERT INTO files(code, name, telegram_file_id) VALUES(?,?,?)",
        (code, name, doc.file_id)
    )
    con.commit()
    con.close()
    await state.clear()

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    await message.answer(
        f"✅ <b>Файл добавлен</b>\n\n"
        f"📁 {name}\n"
        f"🔗 <code>{link}</code>\n\n"
        f"Эту ссылку можно вставить в пост канала.",
        parse_mode="HTML",
        reply_markup=admin_kb()
    )

@router.callback_query(F.data == "files")
async def list_files(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    con = db()
    rows = con.execute("SELECT * FROM files ORDER BY id DESC").fetchall()
    con.close()
    if not rows:
        await callback.message.answer("Файлов пока нет.")
        return
    text = "📋 <b>Файлы:</b>\n\n"
    for f in rows:
        text += f"#{f['id']} — {f['name']}\n/start {f['code']}\n\n"
    await callback.message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "add_channel")
async def add_channel_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AddChannel.waiting_channel)
    await callback.message.answer(
        "📢 Добавь бота администратором канала, затем перешли сюда сообщение из этого канала.\n\n"
        "Для публичного канала можно также отправить @username."
    )

@router.message(AddChannel.waiting_channel)
async def add_channel_receive(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    chat = None
    if message.forward_from_chat:
        chat = message.forward_from_chat
    else:
        text = (message.text or "").strip()
        if text.startswith("@"):
            try:
                chat = await bot.get_chat(text)
            except Exception:
                pass

    if not chat:
        await message.answer("❌ Не смог определить канал. Перешли сообщение из канала или отправь @username.")
        return

    username = getattr(chat, "username", None)
    invite_link = None
    if username:
        invite_link = f"https://t.me/{username}"
    else:
        try:
            invite_link = await bot.create_chat_invite_link(chat.id)
            invite_link = invite_link.invite_link
        except Exception:
            pass

    con = db()
    con.execute(
        "INSERT OR REPLACE INTO channels(chat_id,title,username,invite_link) VALUES(?,?,?,?)",
        (str(chat.id), chat.title or "Канал", username, invite_link)
    )
    con.commit()
    con.close()
    await state.clear()
    await message.answer(f"✅ Канал добавлен: {chat.title}", reply_markup=admin_kb())

@router.callback_query(F.data == "channels")
async def list_channels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    con = db()
    rows = con.execute("SELECT * FROM channels ORDER BY id").fetchall()
    con.close()
    if not rows:
        await callback.message.answer("Каналов пока нет.")
        return
    text = "📢 <b>Обязательные каналы:</b>\n\n"
    for c in rows:
        text += f"#{c['id']} — {c['title']} — {c['chat_id']}\n"
    await callback.message.answer(text, parse_mode="HTML")

async def main():
    if TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи BOT_TOKEN в переменных окружения.")
    if not ADMIN_ID:
        raise RuntimeError("Укажи ADMIN_ID в переменных окружения.")
    init_db()
    bot = Bot(TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
