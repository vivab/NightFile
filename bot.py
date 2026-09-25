import asyncio
import io
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
)

import database as db
from config import BOT_TOKEN, ADMIN_IDS

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

WELCOME_TEXT_KEY = "welcome_text"
CHANNEL_URL_KEY = "channel_url"  # ссылка для кнопки "Наш канал" (не связана с ОП)

OP_TYPE_LABEL = {"channel": "📢 Канал", "group": "👥 Группа", "bot": "🤖 Бот"}


class AdminStates(StatesGroup):
    waiting_file_upload = State()
    waiting_file_title = State()

    waiting_rename_upload = State()
    waiting_rename_name = State()

    waiting_op_link = State()
    waiting_op_title = State()
    waiting_op_checkid = State()

    waiting_channel_url = State()
    waiting_welcome_text = State()


# ---------------------------------------------------------------- helpers

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def extract_file(message: Message):
    """Возвращает (file_id, file_type, suggested_name) либо (None, None, None)."""
    if message.document:
        return message.document.file_id, "document", message.document.file_name
    if message.photo:
        return message.photo[-1].file_id, "photo", None
    if message.video:
        return message.video.file_id, "video", message.video.file_name
    if message.audio:
        return message.audio.file_id, "audio", message.audio.file_name
    return None, None, None


async def channel_footer_keyboard() -> InlineKeyboardMarkup | None:
    """Кнопка «Наш канал», которая цепляется под каждым выданным файлом."""
    url = await db.get_setting(CHANNEL_URL_KEY)
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Наш канал", url=url)]]
    )


async def build_subscribe_keyboard(payload: str | None) -> InlineKeyboardMarkup:
    targets = await db.list_op_targets()
    rows = []
    for op_id, type_, title, url, check_id in targets:
        icon = "🤖" if type_ == "bot" else "📢"
        rows.append([InlineKeyboardButton(text=f"{icon} {title}", url=url)])
    rows.append(
        [InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_sub:{payload or ''}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def is_subscribed(user_id: int) -> bool:
    """
    Каналы/группы проверяются через Telegram API (бот должен быть там админом).
    Боты — на доверии, их всегда считаем пройденными (проверить членство в чужом
    боте технически невозможно).
    """
    targets = await db.list_op_targets()
    for op_id, type_, title, url, check_id in targets:
        if type_ == "bot" or not check_id:
            continue
        try:
            member = await bot.get_chat_member(chat_id=check_id, user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            logging.warning(f"Не удалось проверить подписку на {check_id}: {e}")
            # если бот не админ в чате или chat_id неверный — не блокируем доступ
            continue
    return True


async def deliver_file(chat_id: int, file_db_id: int):
    row = await db.get_file(file_db_id)
    if not row:
        await bot.send_message(chat_id, "Файл не найден или был удалён.")
        return
    _, title, file_id, file_type, caption = row
    text = caption or title
    kb = await channel_footer_keyboard()

    send_map = {
        "document": bot.send_document,
        "photo": bot.send_photo,
        "video": bot.send_video,
        "audio": bot.send_audio,
    }
    sender = send_map.get(file_type)
    if sender:
        await sender(chat_id, file_id, caption=text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


# ---------------------------------------------------------------- админ-меню

def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить файл", callback_data="adm:addfile"),
                InlineKeyboardButton(text="🗑 Удалить файл", callback_data="adm:delfile"),
            ],
            [InlineKeyboardButton(text="✏️ Переименовать файл", callback_data="adm:renamefile")],
            [InlineKeyboardButton(text="📃 Список файлов", callback_data="adm:listfiles")],
            [InlineKeyboardButton(text="📡 Управление ОП", callback_data="adm:op_manage")],
            [InlineKeyboardButton(text="📢 Ссылка «Наш канал»", callback_data="adm:setchannel")],
            [InlineKeyboardButton(text="✉️ Приветственный текст", callback_data="adm:setwelcome")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        ]
    )


def back_kb(callback: str = "adm:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=callback)]]
    )


@dp.message(Command("admin"))
async def admin_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("Админ-панель:", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "adm:menu")
async def adm_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("Админ-панель:", reply_markup=admin_menu_kb())


# ---------- добавить файл ----------

@dp.callback_query(F.data == "adm:addfile")
async def adm_addfile(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_file_upload)
    await call.message.edit_text(
        "Пришли или перешли мне файл (документ/фото/видео/аудио), который нужно сохранить.",
        reply_markup=back_kb(),
    )


@dp.message(AdminStates.waiting_file_upload)
async def got_file_for_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    file_id, file_type, suggested_name = extract_file(message)
    if not file_id:
        await message.answer("Это не файл. Пришли документ, фото, видео или аудио.")
        return
    await state.update_data(file_id=file_id, file_type=file_type)
    await state.set_state(AdminStates.waiting_file_title)
    await message.answer("Как назвать файл? (это будет подпись у выдаваемого файла)")


@dp.message(AdminStates.waiting_file_title)
async def got_title_for_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    file_db_id = await db.add_file(message.text.strip(), data["file_id"], data["file_type"])
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{file_db_id}"
    await state.clear()
    await message.answer(
        f"✅ Файл добавлен, id={file_db_id}\nПрямая ссылка:\n{link}",
        reply_markup=back_kb(),
    )


# ---------- удалить файл ----------

@dp.callback_query(F.data == "adm:delfile")
async def adm_delfile(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    rows = await db.list_files()
    if not rows:
        await call.message.edit_text("Файлов пока нет.", reply_markup=back_kb())
        return
    kb_rows = [
        [InlineKeyboardButton(text=f"🗑 #{fid} {title}", callback_data=f"delfile_ask:{fid}")]
        for fid, title in rows
    ]
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:menu")])
    await call.message.edit_text(
        "Выбери файл для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


@dp.callback_query(F.data.startswith("delfile_ask:"))
async def adm_delfile_ask(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    fid = call.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delfile_do:{fid}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="adm:delfile"),
            ]
        ]
    )
    await call.message.edit_text(f"Удалить файл #{fid}?", reply_markup=kb)


@dp.callback_query(F.data.startswith("delfile_do:"))
async def adm_delfile_do(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    fid = int(call.data.split(":", 1)[1])
    await db.delete_file(fid)
    await call.answer("Удалено")
    await adm_delfile(call)


# ---------- список файлов ----------

@dp.callback_query(F.data == "adm:listfiles")
async def adm_listfiles(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    rows = await db.list_files()
    me = await bot.get_me()
    if not rows:
        text = "Файлов пока нет."
    else:
        lines = [
            f"#{fid} — {title}\nhttps://t.me/{me.username}?start=file_{fid}"
            for fid, title in rows
        ]
        text = "\n\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_kb())


# ---------- переименовать файл (универсальная утилита) ----------

@dp.callback_query(F.data == "adm:renamefile")
async def adm_renamefile(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_rename_upload)
    await call.message.edit_text(
        "Пришли или перешли файл (документ), который нужно переименовать.\n"
        "Я скачаю его и верну с новым именем.",
        reply_markup=back_kb(),
    )


@dp.message(AdminStates.waiting_rename_upload)
async def got_file_for_rename(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    file_id, file_type, suggested_name = extract_file(message)
    if not file_id or file_type != "document":
        await message.answer(
            "Для переименования пришли файл как документ "
            "(фото/видео/аудио переименовать нельзя — у них нет произвольного имени)."
        )
        return
    await state.update_data(file_id=file_id)
    await state.set_state(AdminStates.waiting_rename_name)
    await message.answer(
        "Как назвать файл? Укажи имя с расширением, например: config.netcfg"
    )


@dp.message(AdminStates.waiting_rename_name)
async def got_name_for_rename(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_name = message.text.strip()
    data = await state.get_data()
    file_id = data["file_id"]

    buf = io.BytesIO()
    try:
        await bot.download(file_id, destination=buf)
    except Exception as e:
        await state.clear()
        await message.answer(f"Не удалось скачать файл: {e}", reply_markup=back_kb())
        return
    buf.seek(0)

    await state.clear()
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=new_name),
        caption=f"✅ Переименовано: {new_name}",
        reply_markup=back_kb(),
    )


# ---------- управление ОП (обязательная подписка) ----------

@dp.callback_query(F.data == "adm:op_manage")
async def adm_op_manage(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    targets = await db.list_op_targets()
    kb_rows = []
    for op_id, type_, title, url, check_id in targets:
        label = OP_TYPE_LABEL.get(type_, type_)
        kb_rows.append(
            [InlineKeyboardButton(text=f"🗑 {label}: {title}", callback_data=f"op_del:{op_id}")]
        )
    kb_rows.append(
        [
            InlineKeyboardButton(text="➕ Канал", callback_data="op_add:channel"),
            InlineKeyboardButton(text="➕ Группа", callback_data="op_add:group"),
            InlineKeyboardButton(text="➕ Бот", callback_data="op_add:bot"),
        ]
    )
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:menu")])

    text = (
        "Текущий список обязательной подписки (ОП):\n"
        "Каналы и группы проверяются через Telegram (бот должен быть там админом).\n"
        "Боты проверить нельзя — добавляются на доверии.\n\n"
        "Нажми на пункт, чтобы удалить его, либо добавь новый ниже."
        if targets
        else "Список ОП пуст. Добавь канал, группу или бота ниже."
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data.startswith("op_del:"))
async def adm_op_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    op_id = int(call.data.split(":", 1)[1])
    await db.delete_op_target(op_id)
    await call.answer("Удалено")
    await adm_op_manage(call)


@dp.callback_query(F.data.startswith("op_add:"))
async def adm_op_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    type_ = call.data.split(":", 1)[1]
    await state.update_data(op_type=type_)
    await state.set_state(AdminStates.waiting_op_link)
    label = OP_TYPE_LABEL.get(type_, type_)
    await call.message.edit_text(
        f"Добавляем: {label}\n\nПришли ссылку-приглашение (то, что откроется по кнопке).",
        reply_markup=back_kb("adm:op_manage"),
    )


@dp.message(AdminStates.waiting_op_link)
async def got_op_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(op_url=message.text.strip())
    await state.set_state(AdminStates.waiting_op_title)
    await message.answer("Название для кнопки (например: Наш чат / Основной канал)")


@dp.message(AdminStates.waiting_op_title)
async def got_op_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.update_data(op_title=message.text.strip())

    if data["op_type"] == "bot":
        # боты — на доверии, проверку подписки не делаем
        await db.add_op_target("bot", message.text.strip(), data["op_url"], None)
        await state.clear()
        await message.answer("✅ Бот добавлен в ОП (на доверии).", reply_markup=back_kb("adm:op_manage"))
    else:
        await state.set_state(AdminStates.waiting_op_checkid)
        await message.answer(
            "Теперь пришли chat_id или @username канала/группы для проверки подписки.\n"
            "⚠️ Бот должен быть добавлен туда админом, иначе проверка не сработает."
        )


@dp.message(AdminStates.waiting_op_checkid)
async def got_op_checkid(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    check_id = message.text.strip()
    await db.add_op_target(data["op_type"], data["op_title"], data["op_url"], check_id)
    await state.clear()
    await message.answer(
        "✅ Добавлено в ОП с проверкой подписки.", reply_markup=back_kb("adm:op_manage")
    )


# ---------- настройка "Наш канал" ----------

@dp.callback_query(F.data == "adm:setchannel")
async def adm_setchannel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_channel_url)
    await call.message.edit_text(
        "Пришли ссылку для кнопки «Наш канал» "
        "(она будет в стартовом сообщении и под каждым выданным файлом).",
        reply_markup=back_kb(),
    )


@dp.message(AdminStates.waiting_channel_url)
async def got_channel_url(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.set_setting(CHANNEL_URL_KEY, message.text.strip())
    await state.clear()
    await message.answer("✅ Ссылка обновлена.", reply_markup=back_kb())


# ---------- приветственный текст ----------

@dp.callback_query(F.data == "adm:setwelcome")
async def adm_setwelcome(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_welcome_text)
    await call.message.edit_text("Пришли новый текст стартового сообщения.", reply_markup=back_kb())


@dp.message(AdminStates.waiting_welcome_text)
async def got_welcome_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.set_setting(WELCOME_TEXT_KEY, message.text)
    await state.clear()
    await message.answer("✅ Текст обновлён.", reply_markup=back_kb())


# ---------- статистика ----------

@dp.callback_query(F.data == "adm:stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    users = await db.count_users()
    files = await db.list_files()
    targets = await db.list_op_targets()
    await call.message.edit_text(
        f"Пользователей: {users}\nФайлов: {len(files)}\nЦелей ОП: {len(targets)}",
        reply_markup=back_kb(),
    )


# ---------------------------------------------------------------- обычные пользователи

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
                "Чтобы получить файл, подпишись на всё ниже, затем нажми «Я подписался».",
                reply_markup=kb,
            )
    else:
        await start_plain(message)


@dp.message(CommandStart())
async def start_plain(message: Message):
    await db.add_user(message.from_user.id)
    text = await db.get_setting(
        WELCOME_TEXT_KEY,
        "Привет! Перейди по нужной ссылке, чтобы получить файл.",
    )
    kb = await channel_footer_keyboard()
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
