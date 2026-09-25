import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

import database as db
from config import BOT_TOKEN, ADMIN_IDS

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

WELCOME_TEXT_KEY = "welcome_text"
CHANNEL_URL_KEY = "channel_url"      # публичная ссылка-приглашение (кнопка "Наш канал")
CHANNEL_CHAT_ID_KEY = "channel_chat_id"  # numeric id / @username для проверки подписки


# ---------------------------------------------------------------- helpers

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def channel_button_row() -> list[InlineKeyboardButton]:
    url = await db.get_setting(CHANNEL_URL_KEY)
    if url:
        return [InlineKeyboardButton(text="📢 Наш канал", url=url)]
    return []


async def build_subscribe_keyboard(payload: str | None) -> InlineKeyboardMarkup:
    rows = []
    url = await db.get_setting(CHANNEL_URL_KEY)
    if url:
        rows.append([InlineKeyboardButton(text="📢 Подписаться", url=url)])
    check_data = f"check_sub:{payload or ''}"
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data=check_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def is_subscribed(user_id: int) -> bool:
    chat_id = await db.get_setting(CHANNEL_CHAT_ID_KEY)
    if not chat_id:
        # проверка не настроена — считаем, что доступ открыт всем
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        logging.warning(f"Subscription check failed: {e}")
        # если бот не админ в канале или chat_id неверный — не блокируем пользователя
        return True


async def deliver_file(chat_id: int, file_db_id: int):
    row = await db.get_file(file_db_id)
    if not row:
        await bot.send_message(chat_id, "Файл не найден или был удалён.")
        return
    _, title, file_id, file_type, caption = row
    text = caption or title
    if file_type == "document":
        await bot.send_document(chat_id, file_id, caption=text)
    elif file_type == "photo":
        await bot.send_photo(chat_id, file_id, caption=text)
    elif file_type == "video":
        await bot.send_video(chat_id, file_id, caption=text)
    elif file_type == "audio":
        await bot.send_audio(chat_id, file_id, caption=text)
    else:
        await bot.send_message(chat_id, text)


# ---------------------------------------------------------------- /start

@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject):
    await db.add_user(message.from_user.id)
    payload = command.args or ""

    if payload.startswith("file_"):
        try:
            file_db_id = int(payload.split("_", 1)[1])
        except ValueError:
            await message.answer("Некорректная ссылка.")
            return

        if await is_subscribed(message.from_user.id):
            await deliver_file(message.chat.id, file_db_id)
        else:
            kb = await build_subscribe_keyboard(payload)
            await message.answer(
                "Чтобы получить файл, сначала подпишись на наш канал/группу, "
                "затем нажми «Я подписался».",
                reply_markup=kb,
            )
    else:
        await start_plain(message)


@dp.message(CommandStart())
async def start_plain(message: Message):
    await db.add_user(message.from_user.id)
    text = await db.get_setting(
        WELCOME_TEXT_KEY,
        "Привет! Нажми /start после перехода по нужной ссылке, чтобы получить файл.",
    )
    kb_rows = [await channel_button_row()]
    kb_rows = [r for r in kb_rows if r]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("check_sub:"))
async def check_sub_callback(call: CallbackQuery):
    payload = call.data.split(":", 1)[1]
    if await is_subscribed(call.from_user.id):
        await call.message.edit_text("Подписка подтверждена ✅")
        if payload.startswith("file_"):
            file_db_id = int(payload.split("_", 1)[1])
            await deliver_file(call.message.chat.id, file_db_id)
    else:
        await call.answer("Пока не вижу подписку, попробуй ещё раз чуть позже.", show_alert=True)


# ---------------------------------------------------------------- admin: файлы

@dp.message(Command("addfile"))
async def add_file_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not message.reply_to_message:
        await message.answer(
            "Ответь этой командой на сообщение с файлом (документ/фото/видео/аудио).\n"
            "Пример: ответь на файл командой /addfile Название файла"
        )
        return

    title = command.args or "Без названия"
    src = message.reply_to_message

    if src.document:
        file_id, file_type = src.document.file_id, "document"
    elif src.photo:
        file_id, file_type = src.photo[-1].file_id, "photo"
    elif src.video:
        file_id, file_type = src.video.file_id, "video"
    elif src.audio:
        file_id, file_type = src.audio.file_id, "audio"
    else:
        await message.answer("Не нашёл файл в этом сообщении.")
        return

    file_db_id = await db.add_file(title, file_id, file_type)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{file_db_id}"
    await message.answer(
        f"Файл добавлен, id={file_db_id}\nПрямая ссылка:\n{link}"
    )


@dp.message(Command("files"))
async def list_files_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = await db.list_files()
    if not rows:
        await message.answer("Файлов пока нет.")
        return
    me = await bot.get_me()
    lines = [
        f"#{fid} — {title}\nhttps://t.me/{me.username}?start=file_{fid}"
        for fid, title in rows
    ]
    await message.answer("\n\n".join(lines))


@dp.message(Command("delfile"))
async def del_file_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.isdigit():
        await message.answer("Использование: /delfile <id>")
        return
    await db.delete_file(int(command.args))
    await message.answer("Удалено.")


# ---------------------------------------------------------------- admin: настройки

@dp.message(Command("setchannel"))
async def set_channel_cmd(message: Message, command: CommandObject):
    """
    /setchannel <ссылка_для_кнопки> <chat_id_или_@username_для_проверки>
    Второй аргумент нужен только если хочешь ЖЁСТКУЮ проверку подписки
    (тогда бот должен быть админом в этом канале/группе).
    Пример: /setchannel https://t.me/mychannel @mychannel
    Если проверка не нужна — укажи только ссылку.
    """
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer(
            "Использование:\n/setchannel <ссылка> [chat_id или @username для проверки]"
        )
        return
    parts = command.args.split()
    url = parts[0]
    await db.set_setting(CHANNEL_URL_KEY, url)
    if len(parts) > 1:
        await db.set_setting(CHANNEL_CHAT_ID_KEY, parts[1])
        await message.answer(
            f"Готово.\nСсылка кнопки: {url}\nПроверка подписки на: {parts[1]}\n\n"
            "⚠️ Не забудь добавить бота админом в этот канал/группу, "
            "иначе проверка подписки работать не будет."
        )
    else:
        await message.answer(f"Готово. Ссылка кнопки установлена: {url}")


@dp.message(Command("setwelcome"))
async def set_welcome_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /setwelcome <текст>")
        return
    await db.set_setting(WELCOME_TEXT_KEY, command.args)
    await message.answer("Приветственный текст обновлён.")


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = await db.count_users()
    files = await db.list_files()
    await message.answer(f"Пользователей: {users}\nФайлов: {len(files)}")


@dp.message(Command("myid"))
async def myid_cmd(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ---------------------------------------------------------------- entrypoint

async def main():
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
