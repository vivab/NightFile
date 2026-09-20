import os

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, FSInputFile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import db
import logic
from config import ADMIN_IDS
from keyboards import main_menu_kb, requirements_kb
from states import SuggestionState

router = Router()


async def _send_file(bot: Bot, chat_id: int, file: dict) -> None:
    caption = file["caption"] or None
    ftype = file["file_type"]
    fid = file["file_id"]
    if ftype == "document":
        await bot.send_document(chat_id, fid, caption=caption)
    elif ftype == "video":
        await bot.send_video(chat_id, fid, caption=caption)
    elif ftype == "audio":
        await bot.send_audio(chat_id, fid, caption=caption)
    elif ftype == "photo":
        await bot.send_photo(chat_id, fid, caption=caption)
    else:
        await bot.send_document(chat_id, fid, caption=caption)


@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandObject, bot: Bot):
    payload = command.args or ""
    if not payload.startswith("f_"):
        await start_plain(message)
        return

    code = payload[2:]
    file = await db.get_file(code)
    if not file:
        await message.answer("⚠️ Файл не найден. Возможно, ссылка устарела или файл удалён.")
        return

    missing = await logic.get_missing_requirements(bot, message.from_user.id)
    if not missing:
        await _send_file(bot, message.chat.id, file)
        return

    await message.answer(
        "Чтобы получить файл, выполните оставшиеся условия и нажмите проверку:",
        reply_markup=requirements_kb(missing, code),
    )


@router.message(CommandStart())
async def start_plain(message: Message):
    text = await db.get_setting("welcome_text")
    photo = await db.get_setting("welcome_photo")
    btn_text = await db.get_setting("channel_button_text")
    btn_url = await db.get_setting("channel_button_url")
    kb = main_menu_kb(btn_text, btn_url)

    if photo and photo.startswith("local:"):
        rel_path = photo.split("local:", 1)[1]
        photo_input = FSInputFile(os.path.join(PROJECT_ROOT, rel_path))
        await message.answer_photo(photo_input, caption=text, reply_markup=kb)
    elif photo:
        await message.answer_photo(photo, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("check:"))
async def check_subscription(callback: CallbackQuery, bot: Bot):
    code = callback.data.split(":", 1)[1]
    file = await db.get_file(code)
    if not file:
        await callback.answer("Файл не найден или был удалён.", show_alert=True)
        return

    # Трастовое подтверждение bot-заданий при нажатии проверки
    await logic.confirm_all_bot_requirements(callback.from_user.id)

    missing = await logic.get_missing_requirements(bot, callback.from_user.id)
    if missing:
        await callback.answer(
            "Вы ещё не выполнили все условия:\n" + "\n".join(f"• {r['title']}" for r in missing),
            show_alert=True,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=requirements_kb(missing, code))
        except Exception:
            pass
        return

    await callback.answer("Готово! ✅")
    await _send_file(bot, callback.message.chat.id, file)


@router.callback_query(F.data == "suggest_start")
async def suggest_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "✍️ Напишите, какой чит/мод/конфиг вы хотите видеть на канале. "
        "Ваше сообщение будет отправлено администратору."
    )
    await state.set_state(SuggestionState.waiting_text)


@router.message(StateFilter(SuggestionState.waiting_text), F.text)
async def suggest_receive(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await db.add_suggestion(message.from_user.id, message.from_user.username, message.text)
    await message.answer("Спасибо! Ваша заявка отправлена 🙌")

    username = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💌 Новая предложка от {username}:\n\n{message.text}",
            )
        except Exception:
            pass
