from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

import db
from config import ADMIN_IDS
from states import AddRequirementState, SettingsState
from keyboards import (
    admin_menu_kb,
    back_to_admin_kb,
    files_list_kb,
    reqs_list_kb,
    req_detail_kb,
    choose_req_type_kb,
    settings_menu_kb,
)

router = Router()
router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


def _bot_username_ctx(bot: Bot) -> str:
    # bot.username кэшируется aiogram-ом после первого get_me(); используем как есть
    return bot.username if hasattr(bot, "username") and bot.username else "your_bot"


async def _make_link(bot: Bot, code: str) -> str:
    me = await bot.get_me()
    return f"https://t.me/{me.username}?start=f_{code}"


# ---------- Вход в админку ----------

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 Админ-панель", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm:menu")
async def adm_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_kb())
    await callback.answer()


# ---------- Загрузка файла (просто прислать боту) ----------

@router.message(
    StateFilter(None),
    F.content_type.in_({"document", "video", "audio", "photo"}),
)
async def upload_file(message: Message, bot: Bot):
    if message.document:
        file_id, ftype = message.document.file_id, "document"
    elif message.video:
        file_id, ftype = message.video.file_id, "video"
    elif message.audio:
        file_id, ftype = message.audio.file_id, "audio"
    elif message.photo:
        file_id, ftype = message.photo[-1].file_id, "photo"
    else:
        return

    code = await db.add_file(file_id, ftype, message.caption, message.from_user.id)
    link = await _make_link(bot, code)
    await message.answer(
        f"✅ Файл добавлен.\n\n🔗 Ссылка для выдачи:\n{link}",
    )


# ---------- Список / удаление файлов ----------

@router.callback_query(F.data == "adm:files")
async def adm_files(callback: CallbackQuery):
    files = await db.list_files()
    if not files:
        await callback.message.edit_text("Файлов пока нет. Пришлите файл боту, чтобы добавить.", reply_markup=back_to_admin_kb())
    else:
        await callback.message.edit_text(f"📁 Файлов: {len(files)}\nНажмите, чтобы удалить:", reply_markup=files_list_kb(files))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:delfile:"))
async def adm_delfile(callback: CallbackQuery):
    code = callback.data.split(":", 2)[2]
    await db.delete_file(code)
    await callback.answer("Удалено", show_alert=False)
    files = await db.list_files()
    if not files:
        await callback.message.edit_text("Файлов больше нет.", reply_markup=back_to_admin_kb())
    else:
        await callback.message.edit_text(f"📁 Файлов: {len(files)}\nНажмите, чтобы удалить:", reply_markup=files_list_kb(files))


# ---------- Обязательные подписки: список ----------

@router.callback_query(F.data == "adm:reqs")
async def adm_reqs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    reqs = await db.list_requirements()
    text = "📋 Обязательные подписки:" if reqs else "Заданий пока нет."
    await callback.message.edit_text(text, reply_markup=reqs_list_kb(reqs))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:req:"))
async def adm_req_detail(callback: CallbackQuery):
    req_id = int(callback.data.split(":", 2)[2])
    req = await db.get_requirement(req_id)
    if not req:
        await callback.answer("Не найдено", show_alert=True)
        return
    status = "включено ✅" if req["enabled"] else "выключено ⏸"
    await callback.message.edit_text(
        f"Тип: {req['type']}\nНазвание: {req['title']}\nСсылка: {req['url']}\nСтатус: {status}",
        reply_markup=req_detail_kb(req_id, bool(req["enabled"])),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:toggle:"))
async def adm_req_toggle(callback: CallbackQuery):
    req_id = int(callback.data.split(":", 2)[2])
    await db.toggle_requirement(req_id)
    req = await db.get_requirement(req_id)
    status = "включено ✅" if req["enabled"] else "выключено ⏸"
    await callback.message.edit_text(
        f"Тип: {req['type']}\nНазвание: {req['title']}\nСсылка: {req['url']}\nСтатус: {status}",
        reply_markup=req_detail_kb(req_id, bool(req["enabled"])),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:delreq:"))
async def adm_req_delete(callback: CallbackQuery):
    req_id = int(callback.data.split(":", 2)[2])
    await db.delete_requirement(req_id)
    reqs = await db.list_requirements()
    await callback.answer("Удалено")
    text = "📋 Обязательные подписки:" if reqs else "Заданий пока нет."
    await callback.message.edit_text(text, reply_markup=reqs_list_kb(reqs))


# ---------- Добавление задания ----------

@router.callback_query(F.data == "adm:addreq")
async def adm_addreq(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddRequirementState.choosing_type)
    await callback.message.edit_text("Выберите тип задания:", reply_markup=choose_req_type_kb())
    await callback.answer()


@router.callback_query(StateFilter(AddRequirementState.choosing_type), F.data.startswith("adm:addtype:"))
async def adm_addreq_type(callback: CallbackQuery, state: FSMContext):
    type_ = callback.data.split(":", 2)[2]
    await state.update_data(type=type_)
    if type_ == "bot":
        await state.set_state(AddRequirementState.waiting_bot_title)
        await callback.message.edit_text(
            "Введите название кнопки (например: 🤖 Наш бот):",
            reply_markup=back_to_admin_kb(),
        )
    else:
        await state.set_state(AddRequirementState.waiting_forward)
        label = "канала" if type_ == "channel" else "группы"
        await callback.message.edit_text(
            f"Перешлите сюда любое сообщение из вашего {label} "
            f"(бот должен быть добавлен туда админом), "
            f"либо отправьте @username или ID чата вместе со ссылкой на него через пробел, "
            f"например: @mychannel https://t.me/mychannel",
            reply_markup=back_to_admin_kb(),
        )
    await callback.answer()


@router.message(StateFilter(AddRequirementState.waiting_forward))
async def adm_addreq_forward(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    type_ = data["type"]

    chat_id = None
    title = None
    url = None

    if message.forward_from_chat:
        chat = message.forward_from_chat
        chat_id = chat.id
        title = chat.title
        if chat.username:
            url = f"https://t.me/{chat.username}"
    elif message.text:
        parts = message.text.strip().split()
        if len(parts) >= 2:
            chat_id, url = parts[0], parts[1]
        elif len(parts) == 1:
            chat_id = parts[0]
            url = f"https://t.me/{chat_id.lstrip('@')}"

    if not chat_id:
        await message.answer("Не удалось распознать чат. Перешлите сообщение из чата или пришлите @username и ссылку.")
        return

    if not title:
        try:
            chat = await bot.get_chat(chat_id)
            title = chat.title or chat.username or str(chat_id)
            if not url and chat.username:
                url = f"https://t.me/{chat.username}"
        except Exception:
            title = str(chat_id)

    if not url:
        await message.answer("Не удалось определить ссылку на чат. Пришлите @username и ссылку через пробел.")
        return

    await db.add_requirement(type_, title, url, str(chat_id))
    await state.clear()
    await message.answer(f"✅ Задание добавлено: {title}", reply_markup=back_to_admin_kb())


@router.message(StateFilter(AddRequirementState.waiting_bot_title), F.text)
async def adm_addreq_bot_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AddRequirementState.waiting_bot_url)
    await message.answer("Теперь пришлите ссылку на бота (например: https://t.me/some_bot?start=ref):")


@router.message(StateFilter(AddRequirementState.waiting_bot_url), F.text)
async def adm_addreq_bot_url(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.add_requirement("bot", data["title"], message.text.strip(), None)
    await state.clear()
    await message.answer(f"✅ Задание добавлено: {data['title']}\n\n⚠️ Напоминаю: подписка на бота не проверяется технически, задание засчитывается по нажатию \"Проверить\" (на доверии).", reply_markup=back_to_admin_kb())


# ---------- Настройки ----------

@router.callback_query(F.data == "adm:settings")
async def adm_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("⚙️ Настройки:", reply_markup=settings_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:set:photo")
async def adm_set_photo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_photo)
    await callback.message.edit_text("Пришлите новое фото приветствия:", reply_markup=back_to_admin_kb())
    await callback.answer()


@router.message(StateFilter(SettingsState.waiting_photo), F.photo)
async def adm_set_photo_receive(message: Message, state: FSMContext):
    await db.set_setting("welcome_photo", message.photo[-1].file_id)
    await state.clear()
    await message.answer("✅ Фото обновлено.", reply_markup=back_to_admin_kb())


@router.callback_query(F.data == "adm:set:text")
async def adm_set_text(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_text)
    await callback.message.edit_text("Пришлите новый текст приветствия:", reply_markup=back_to_admin_kb())
    await callback.answer()


@router.message(StateFilter(SettingsState.waiting_text), F.text)
async def adm_set_text_receive(message: Message, state: FSMContext):
    await db.set_setting("welcome_text", message.text)
    await state.clear()
    await message.answer("✅ Текст обновлён.", reply_markup=back_to_admin_kb())


@router.callback_query(F.data == "adm:set:btntext")
async def adm_set_btntext(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_channel_button_text)
    await callback.message.edit_text("Пришлите новый текст кнопки канала:", reply_markup=back_to_admin_kb())
    await callback.answer()


@router.message(StateFilter(SettingsState.waiting_channel_button_text), F.text)
async def adm_set_btntext_receive(message: Message, state: FSMContext):
    await db.set_setting("channel_button_text", message.text)
    await state.clear()
    await message.answer("✅ Текст кнопки обновлён.", reply_markup=back_to_admin_kb())


@router.callback_query(F.data == "adm:set:btnurl")
async def adm_set_btnurl(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_channel_button_url)
    await callback.message.edit_text("Пришлите новую ссылку на канал:", reply_markup=back_to_admin_kb())
    await callback.answer()


@router.message(StateFilter(SettingsState.waiting_channel_button_url), F.text)
async def adm_set_btnurl_receive(message: Message, state: FSMContext):
    await db.set_setting("channel_button_url", message.text.strip())
    await state.clear()
    await message.answer("✅ Ссылка обновлена.", reply_markup=back_to_admin_kb())


# ---------- Статистика ----------

@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery):
    files_count = await db.count_files()
    sugg_count = await db.count_suggestions()
    reqs = await db.list_requirements()
    active_reqs = sum(1 for r in reqs if r["enabled"])
    await callback.message.edit_text(
        f"📊 Статистика:\n\n"
        f"📁 Файлов: {files_count}\n"
        f"📋 Активных заданий: {active_reqs} из {len(reqs)}\n"
        f"💌 Предложений на канал: {sugg_count}",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()
