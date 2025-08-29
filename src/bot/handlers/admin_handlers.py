import asyncio
import logging
import re
from typing import Optional, Tuple, Union

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import inline as ikb
from src.bot.states import AdminStates
from src.core.data_collector import DataCollector
from src.core.interaction import InteractionModule
from src.utils.config import SettingsManager
from src.utils.helpers import parse_chat_id

logger = logging.getLogger(__name__)

admin_router = Router()


async def _get_main_menu_content(
    interaction_module: InteractionModule,
) -> tuple[str, types.InlineKeyboardMarkup]:
    is_active = interaction_module.is_active()
    status_text = "🟢 Активна" if is_active else "🔴 Неактивна"
    telethon_user_id = interaction_module.settings_manager.get_telethon_user_id()
    user_info = (
        f"Аккаунт Telethon: `{telethon_user_id}`"
        if telethon_user_id
        else "Аккаунт Telethon: Неизвестен"
    )

    text = (
        f"🤖 **Меню Управления Цифровой Личностью**\n\n"
        f"Статус: {status_text}\n"
        f"{user_info}\n\n"
        "Выберите действие:"
    )
    keyboard = ikb.get_main_menu_keyboard(is_active)
    return text, keyboard


async def get_chat_display_info(bot: Bot, chat_id: int) -> Tuple[int, str]:
    display_name = f"Chat ID: {chat_id}"
    try:
        logger.debug(f"Attempting bot.get_chat({chat_id}) for display info")
        chat_info = await bot.get_chat(chat_id)
        logger.debug(
            f"Successfully got chat info for {chat_id}: Type={type(chat_info)}, Title='{chat_info.title}', User='{chat_info.username}', FullName='{chat_info.full_name}'"
        )

        if chat_info.title:
            display_name = chat_info.title
        elif chat_info.username:
            display_name = (
                f"{chat_info.full_name} (@{chat_info.username})"
                if chat_info.full_name and chat_info.full_name.strip()
                else f"@{chat_info.username}"
            )
        elif chat_info.full_name and chat_info.full_name.strip():
            display_name = chat_info.full_name

        if display_name == f"Chat ID: {chat_id}":
            logger.debug(
                f"Could not find suitable title/username/fullname for {chat_id}, using ID."
            )
    except TelegramBadRequest as e:
        logger.warning(
            f"Could not get chat info for {chat_id} via bot API: {e}. Full error text: {e.message}. Using ID only."
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in get_chat_display_info for {chat_id}: {e}",
            exc_info=True,
        )
    return chat_id, display_name


@admin_router.message(CommandStart())
@admin_router.message(Command("menu"))
async def handle_start_menu(message: Message, interaction_module: InteractionModule):
    logger.info(f"Admin {message.from_user.id} requested /start or /menu")
    text, keyboard = await _get_main_menu_content(interaction_module)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.message(Command("collect_history"))
async def handle_collect_history(message: Message, data_collector: DataCollector):
    user_id = message.from_user.id
    logger.info(f"Admin {user_id} initiated history collection via /collect_history")
    await message.answer(
        "⏳ Начинаю сбор истории чатов. Это может занять некоторое время..."
        "\nПодробности процесса будут отображаться в логах."
    )

    try:
        await data_collector.collect_all_chats_history()
        logger.info(f"History collection initiated by admin {user_id} finished.")
        await message.answer("✅ Сбор истории завершен.")
    except Exception as e:
        logger.error(
            f"Error during history collection initiated by admin {user_id}: {e}",
            exc_info=True,
        )
        await message.answer(
            f"❌ Произошла ошибка во время сбора истории: {e}"
            "\nПроверьте логи для получения подробной информации."
        )


@admin_router.callback_query(F.data == f"{ikb.CB_BACK}{ikb.CB_MENU}")
async def handle_back_to_main_menu(
    callback: CallbackQuery, interaction_module: InteractionModule, state: FSMContext
):
    logger.debug(f"Admin {callback.from_user.id} pressed Back to Main Menu")
    await state.clear()
    text, keyboard = await _get_main_menu_content(interaction_module)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when returning to main menu, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to main menu: {e}")
            await callback.message.answer(
                text, reply_markup=keyboard, parse_mode="Markdown"
            )
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == ikb.CB_STATUS)
async def handle_status_button(
    callback: CallbackQuery,
    interaction_module: InteractionModule,
    settings_manager: SettingsManager,
):
    logger.debug(f"Admin {callback.from_user.id} requested Status")
    is_active = interaction_module.is_active()
    status_text = "🟢 Активна" if is_active else "🔴 Неактивна"
    telethon_user_id = settings_manager.get_telethon_user_id()
    user_info = (
        f"Аккаунт Telethon: `{telethon_user_id}`"
        if telethon_user_id
        else "Аккаунт Telethon: Не подключен"
    )

    limit_n = settings_manager.get_download_limit()
    n_group = settings_manager.get_group_reply_frequency()
    cooldown = settings_manager.get_ai_detection_cooldown_hours()

    text = (
        f"📊 **Статус Системы**\n\n"
        f"Цифровая Личность: {status_text}\n"
        f"{user_info}\n\n"
        f"**Текущие настройки:**\n"
        f" - Лимит истории (N): `{limit_n}`\n"
        f" - Частота в группах (N\\_group): `{n_group}`\n"
        f" - Кулдаун детекции: `{cooldown.get('min', '?')}-{cooldown.get('max', '?')}` ч."
    )
    keyboard = ikb.get_back_button_keyboard(f"{ikb.CB_BACK}{ikb.CB_MENU}")
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when showing status, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to status screen: {e}")
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == ikb.CB_TOGGLE)
async def handle_toggle_button(
    callback: CallbackQuery, interaction_module: InteractionModule
):
    is_currently_active = interaction_module.is_active()
    logger.info(
        f"Admin {callback.from_user.id} pressed Toggle button. Current state: {is_currently_active}"
    )
    action_text = ""

    if is_currently_active:
        await interaction_module.stop_persona()
        action_text = "🔴 Личность выключена"
    else:
        if not interaction_module._me_id:
            logger.error(
                f"Admin {callback.from_user.id} tried to activate persona, but Telethon client is not ready."
            )
            await callback.answer(
                "❌ Ошибка: Клиент Telethon не готов! Пожалуйста, дождитесь подключения.",
                show_alert=True,
            )
            return
        else:
            await interaction_module.start_persona()
            action_text = "🟢 Личность включена"

    await callback.answer(action_text)

    text, keyboard = await _get_main_menu_content(interaction_module)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Message not modified after toggle, ignoring edit error.")
        else:
            logger.warning(f"Failed to edit message after toggle: {e}")


@admin_router.callback_query(F.data == ikb.CB_SETTINGS_MENU)
async def handle_settings_menu_button(callback: CallbackQuery, state: FSMContext):
    logger.debug(f"Admin {callback.from_user.id} requested Settings menu")
    await state.clear()
    text = "⚙️ **Настройки Параметров**\n\nВыберите параметр для изменения:"
    keyboard = ikb.get_settings_menu_keyboard()
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when showing settings menu, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to settings menu: {e}")
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == ikb.CB_EXCLUSIONS_MENU)
async def handle_exclusions_menu_button(callback: CallbackQuery, state: FSMContext):
    logger.debug(f"Admin {callback.from_user.id} requested Exclusions menu")
    await state.clear()
    text = "🚫 **Управление Исключениями**\n\nВ чатах из этого списка общение полностью запрещено."
    list_prefix = ikb.CB_EXCLUSIONS_ADD.split("_")[0]
    keyboard = ikb.get_list_management_keyboard(list_prefix, ikb.CB_MENU)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when showing exclusions menu, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to exclusions menu: {e}")
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == ikb.CB_RESTRICTIONS_MENU)
async def handle_restrictions_menu_button(callback: CallbackQuery, state: FSMContext):
    logger.debug(f"Admin {callback.from_user.id} requested Restrictions menu")
    await state.clear()
    text = (
        "🔓 **Управление Снятием Ограничений Контента**\n\n"
        "В чатах из этого списка **сняты** стандартные ограничения на генерацию контента "
        "(политика, религия, оскорбления и т.д.).\n"
        "Будьте осторожны при добавлении чатов."
    )
    list_prefix = ikb.CB_RESTRICTIONS_ADD.split("_")[0]
    keyboard = ikb.get_list_management_keyboard(list_prefix, ikb.CB_MENU)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when showing restrictions menu, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to restrictions menu: {e}")
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == ikb.CB_PRIORITY_MENU)
async def handle_priority_menu_button(callback: CallbackQuery, state: FSMContext):
    logger.debug(f"Admin {callback.from_user.id} requested Priority menu")
    await state.clear()
    text = (
        "⭐ **Управление Приоритетом Инициации**\n\n"
        "Чаты из этого списка получают значительный бонус к 'Elo' для более частой инициации общения."
    )
    list_prefix = ikb.CB_PRIORITY_ADD.split("_")[0]
    keyboard = ikb.get_list_management_keyboard(list_prefix, ikb.CB_MENU)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                "Message not modified when showing priority menu, ignoring edit error."
            )
        else:
            logger.warning(f"Failed to edit message to priority menu: {e}")
    finally:
        await callback.answer()


@admin_router.callback_query(F.data == "cancel_fsm", StateFilter("*"))
async def handle_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    interaction_module: InteractionModule,
):
    current_state = await state.get_state()
    logger.info(f"Admin {callback.from_user.id} cancelled FSM state {current_state}")
    await state.clear()
    text, keyboard = await _get_main_menu_content(interaction_module)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("Message not modified during FSM cancel, ignoring edit error.")
        else:
            logger.warning(f"Failed to edit message after FSM cancel: {e}")
    await callback.answer("Действие отменено.")


@admin_router.callback_query(F.data == ikb.CB_SET_N, StateFilter(None))
async def handle_set_n_request(
    callback: CallbackQuery, state: FSMContext, settings_manager: SettingsManager
):
    logger.debug(f"Admin {callback.from_user.id} requested to set N (download limit)")
    current_val = settings_manager.get_download_limit()
    await state.set_state(AdminStates.waiting_for_n_value)
    text = (
        f"Текущий лимит истории (N): `{current_val}`\n\n"
        "Это максимальное количество сообщений, загружаемых из истории чата за один раз.\n"
        "Введите новое значение (целое положительное число):"
    )
    keyboard = ikb.get_cancel_button_keyboard("cancel_fsm")
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        logger.warning(
            f"Failed to edit message in handle_set_n_request (possibly not modified): {e}"
        )
    finally:
        await callback.answer()


@admin_router.message(StateFilter(AdminStates.waiting_for_n_value))
async def handle_set_n_value(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    user_id = message.from_user.id
    try:
        new_n = int(message.text.strip())
        if new_n <= 0:
            raise ValueError("N must be positive")

        await settings_manager.set("download_limit_n", new_n)
        logger.info(f"Admin {user_id} set download_limit_n to {new_n}")
        await message.answer(
            f"✅ Лимит истории (N) успешно изменен на `{new_n}`.", parse_mode="Markdown"
        )
        await state.clear()
        text = "⚙️ **Настройки Параметров**\n\nВыберите параметр для изменения:"
        keyboard = ikb.get_settings_menu_keyboard()
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    except (ValueError, TypeError):
        logger.warning(f"Admin {user_id} entered invalid value for N: {message.text}")
        await message.reply("❌ Ошибка: Введите целое положительное число.")


@admin_router.callback_query(F.data == ikb.CB_SET_N_GROUP, StateFilter(None))
async def handle_set_n_group_request(
    callback: CallbackQuery, state: FSMContext, settings_manager: SettingsManager
):
    logger.debug(f"Admin {callback.from_user.id} requested to set N_group")
    current_val = settings_manager.get_group_reply_frequency()
    await state.set_state(AdminStates.waiting_for_n_group_value)
    text = (
        f"Текущая частота ответа в группах (N\\_group): `{current_val}`\n\n"
        "Личность ответит примерно раз в N\\_group сообщений от других в групповых чатах.\n"
        "Введите новое значение (целое положительное число, >= 1):"
    )
    keyboard = ikb.get_cancel_button_keyboard("cancel_fsm")
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        logger.warning(
            f"Failed to edit message in handle_set_n_group_request (possibly not modified): {e}"
        )
    finally:
        await callback.answer()


@admin_router.message(StateFilter(AdminStates.waiting_for_n_group_value))
async def handle_set_n_group_value(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    user_id = message.from_user.id
    try:
        new_val = int(message.text.strip())
        if new_val < 1:
            raise ValueError("N_group must be >= 1")

        await settings_manager.set("group_reply_frequency_n", new_val)
        logger.info(f"Admin {user_id} set group_reply_frequency_n to {new_val}")
        await message.answer(
            f"✅ Частота ответов в группах (N\\_group) изменена на `{new_val}`.",
            parse_mode="Markdown",
        )
        await state.clear()
        text = "⚙️ **Настройки Параметров**\n\nВыберите параметр для изменения:"
        keyboard = ikb.get_settings_menu_keyboard()
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    except (ValueError, TypeError):
        logger.warning(
            f"Admin {user_id} entered invalid value for N_group: {message.text}"
        )
        await message.reply("❌ Ошибка: Введите целое положительное число (>= 1).")


@admin_router.callback_query(F.data == ikb.CB_SET_COOLDOWN, StateFilter(None))
async def handle_set_cooldown_request(
    callback: CallbackQuery, state: FSMContext, settings_manager: SettingsManager
):
    logger.debug(f"Admin {callback.from_user.id} requested to set Cooldown")
    current_val = settings_manager.get_ai_detection_cooldown_hours()
    await state.set_state(AdminStates.waiting_for_cooldown_value)
    text = (
        f"Текущий диапазон кулдауна при детекции ИИ: `{current_val.get('min', '?')}-{current_val.get('max', '?')}` ч.\n\n"
        "Введите новый диапазон в формате `min-max` (например, `2-24`), "
        "где min и max - целые положительные часы, min <= max."
    )
    keyboard = ikb.get_cancel_button_keyboard("cancel_fsm")
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@admin_router.message(StateFilter(AdminStates.waiting_for_cooldown_value))
async def handle_set_cooldown_value(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    user_id = message.from_user.id
    input_text = message.text.strip()
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", input_text)

    if not match:
        await message.reply(
            "❌ Ошибка: Неверный формат. Введите диапазон как `min-max` (например, `2-24`)."
        )
        return

    try:
        min_h = int(match.group(1))
        max_h = int(match.group(2))

        if not (0 < min_h <= max_h):
            raise ValueError("Неверный диапазон: min должен быть > 0 и min <= max")

        new_cooldown = {"min": min_h, "max": max_h}
        await settings_manager.set("ai_detection_cooldown_hours", new_cooldown)
        logger.info(
            f"Admin {user_id} set ai_detection_cooldown_hours to {new_cooldown}"
        )
        await message.answer(
            f"✅ Диапазон кулдауна изменен на `{min_h}-{max_h}` ч.",
            parse_mode="Markdown",
        )
        await state.clear()
        text = "⚙️ **Настройки Параметров**\n\nВыберите параметр для изменения:"
        keyboard = ikb.get_settings_menu_keyboard()
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    except (ValueError, TypeError) as e:
        logger.warning(
            f"Admin {user_id} entered invalid value for Cooldown: {input_text} ({e})"
        )
        await message.reply(f"❌ Ошибка: {e}. Попробуйте снова.")


async def show_chat_list(
    callback_or_message: Union[CallbackQuery, Message],
    settings_manager: SettingsManager,
    list_key: str,
    title: str,
    remove_prefix: str,
    page_prefix: str,
    back_callback: str,
    list_type_prefix: str,
    current_page: int = 0,
):
    chat_ids = settings_manager.get(list_key, [])
    logger.debug(
        f"Admin {callback_or_message.from_user.id} requested page {current_page} of list '{list_key}'. Found IDs: {chat_ids}"
    )

    message = (
        callback_or_message.message
        if isinstance(callback_or_message, CallbackQuery)
        else callback_or_message
    )

    if not chat_ids:
        text = f"{title}\n\nСписок пуст."

        keyboard = ikb.get_list_management_keyboard(
            list_type_prefix,
            back_callback.replace(ikb.CB_BACK, ""),
        )
    else:
        text = f"{title}\n\nНажмите на чат для удаления из списка:"
        chat_ids.sort()
        tasks = [get_chat_display_info(message.bot, chat_id) for chat_id in chat_ids]
        chat_items = await asyncio.gather(*tasks)
        chat_items.sort(key=lambda item: item[1].lower())

        keyboard = ikb.get_chats_list_keyboard(
            chat_items, remove_prefix, page_prefix, back_callback, current_page
        )

    try:
        if message.text == text and message.reply_markup == keyboard:
            logger.debug(
                f"Message content for list '{list_key}' page {current_page} is identical, skipping edit."
            )
            if isinstance(callback_or_message, CallbackQuery):
                await callback_or_message.answer()
            return

        await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(
                f"Message not modified when showing list '{list_key}' page {current_page}, ignoring edit error."
            )
        else:
            logger.warning(
                f"Failed to edit message to show list '{list_key}' page {current_page}: {e}"
            )

            await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    finally:
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.answer()


async def request_chat_for_list(
    callback: CallbackQuery,
    state: FSMContext,
    target_state: str,
    prompt_text: str,
    cancel_callback: str,
):
    logger.debug(
        f"Admin {callback.from_user.id} requested to add chat for state {target_state}"
    )
    await state.set_state(target_state)
    text = (
        f"{prompt_text}\n\n"
        "Отправьте ID чата/пользователя, @username или перешлите сообщение из нужного чата/от пользователя."
    )
    keyboard = ikb.get_cancel_button_keyboard(cancel_callback)
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        logger.warning(
            f"Failed to edit message to request chat input (possibly not modified): {e}"
        )
    finally:
        await callback.answer()


async def handle_chat_input_for_list(
    message: Message,
    state: FSMContext,
    settings_manager: SettingsManager,
    list_key: str,
    success_message_format: str,
    page_prefix: str,
    back_callback: str,
    list_title: str,
    remove_prefix: str,
    list_type_prefix: str,
):
    user_id = message.from_user.id
    parsed_chat_id: Optional[int] = None

    parsed_chat_id = await parse_chat_id(message)

    if parsed_chat_id is None and message.text:
        input_text = message.text.strip()

        try:
            logger.debug(f"Attempting to resolve '{input_text}' via bot.get_chat()")
            entity = await message.bot.get_chat(input_text)
            parsed_chat_id = entity.id
            logger.info(f"Resolved '{input_text}' to entity ID {parsed_chat_id}")
        except TelegramBadRequest as e:
            logger.warning(
                f"Could not resolve '{input_text}' via bot API: {e}. Full error text: {e.message}"
            )
            if "chat not found" in str(e).lower() or "user not found" in str(e).lower():
                await message.reply(
                    f"❌ Не удалось найти чат/пользователя по '{input_text}' (ответ API: {e.message})."
                )
            elif "peer_id_invalid" in str(e).lower():
                await message.reply(
                    "❌ Указанный ID недействителен или недоступен боту."
                )
            else:
                await message.reply(
                    f"❌ Произошла ошибка Telegram при поиске '{input_text}': {e.message}"
                )
            return
        except Exception as e:
            logger.error(
                f"Unexpected error resolving '{input_text}' via bot API: {e}",
                exc_info=True,
            )
            await message.reply(
                f"❌ Произошла непредвиденная ошибка при поиске '{input_text}'."
            )
            return

    if parsed_chat_id is None:
        logger.warning(
            f"Failed to parse or resolve chat ID from input: '{message.text}'"
        )
        await message.reply(
            "❌ Не удалось распознать ID чата/пользователя. Попробуйте снова: отправьте ID, @username, ссылку или перешлите сообщение."
        )
        return

    current_list = settings_manager.get(list_key, [])
    if parsed_chat_id in current_list:
        await message.reply("ℹ️ Этот чат/пользователь уже есть в данном списке.")
    else:
        _, display_name = await get_chat_display_info(message.bot, parsed_chat_id)

        current_list.append(parsed_chat_id)
        await settings_manager.set(list_key, current_list)
        logger.info(
            f"Admin {user_id} added chat_id {parsed_chat_id} ('{display_name}') to '{list_key}'"
        )

        success_text = success_message_format.format(
            display_name=f"`{display_name}` ({parsed_chat_id})"
        )
        await message.answer(success_text, parse_mode="Markdown")

    await state.clear()

    await show_chat_list(
        message,
        settings_manager,
        list_key,
        list_title,
        remove_prefix,
        page_prefix,
        back_callback,
        list_type_prefix,
        current_page=0,
    )


async def handle_remove_chat_from_list(
    callback: CallbackQuery,
    settings_manager: SettingsManager,
    list_key: str,
    remove_prefix: str,
    title: str,
    page_prefix: str,
    back_callback: str,
    list_type_prefix: str,
):
    chat_id_to_remove = -1
    current_page = 0
    try:
        if not remove_prefix.endswith("_"):
            remove_prefix += "_"

        data_parts = callback.data.split(remove_prefix)[1].split("_")
        chat_id_to_remove = int(data_parts[0])
        if len(data_parts) > 1:
            try:
                current_page = int(data_parts[1])
            except (ValueError, TypeError):
                logger.warning(
                    f"Could not parse page number from callback data part: {data_parts[1]}"
                )
                current_page = 0

    except (IndexError, ValueError, TypeError):
        logger.error(
            f"Failed to parse chat_id/page from callback data: {callback.data} with prefix {remove_prefix}"
        )
        await callback.answer(
            "❌ Ошибка: Неверный формат callback data.", show_alert=True
        )
        return

    logger.info(
        f"Admin {callback.from_user.id} requested removal of chat {chat_id_to_remove} from '{list_key}' (from page {current_page})"
    )
    current_list = settings_manager.get(list_key, [])

    if chat_id_to_remove in current_list:
        _, display_name = await get_chat_display_info(callback.bot, chat_id_to_remove)

        current_list.remove(chat_id_to_remove)
        await settings_manager.set(list_key, current_list)
        await callback.answer(
            f"✅ Чат/Пользователь `{display_name}` удален из списка.", show_alert=False
        )
        logger.info(f"Chat {chat_id_to_remove} removed from '{list_key}'.")

        await show_chat_list(
            callback,
            settings_manager,
            list_key,
            title,
            remove_prefix,
            page_prefix,
            back_callback,
            list_type_prefix,
            current_page,
        )
    else:
        logger.warning(
            f"Chat {chat_id_to_remove} not found in list '{list_key}' for removal."
        )
        await callback.answer(
            f"ℹ️ Чат/Пользователь {chat_id_to_remove} не найден в списке.",
            show_alert=True,
        )

        await show_chat_list(
            callback,
            settings_manager,
            list_key,
            title,
            remove_prefix,
            page_prefix,
            back_callback,
            list_type_prefix,
            current_page,
        )


LIST_KEY_EXCLUSIONS = "excluded_chats"
TITLE_EXCLUSIONS = "🚫 Список исключенных чатов"
PREFIX_EXCLUSIONS_REMOVE = ikb.CB_EXCLUSIONS_REMOVE_PREFIX  # "exclusions_rem_"
PREFIX_EXCLUSIONS_PAGE = (
    f"{ikb.CB_EXCLUSIONS_LIST}{ikb.CB_PAGE_PREFIX}"  # "exclusions_listpage_"
)
CALLBACK_EXCLUSIONS_MENU = ikb.CB_EXCLUSIONS_MENU
TYPE_PREFIX_EXCLUSIONS = ikb.CB_EXCLUSIONS_ADD.split("_")[0]  # "exclusions"


@admin_router.callback_query(F.data.startswith(ikb.CB_EXCLUSIONS_LIST))
async def handle_exclusions_list_pages(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    current_page = 0
    page_data_prefix = PREFIX_EXCLUSIONS_PAGE.replace(ikb.CB_EXCLUSIONS_LIST, "")
    if page_data_prefix in callback.data:
        try:
            page_str = callback.data.split(page_data_prefix)[1]
            current_page = int(page_str)
        except (IndexError, ValueError):
            logger.warning(
                f"Could not parse page number from callback data: {callback.data}"
            )
            current_page = 0

    await show_chat_list(
        callback,
        settings_manager,
        LIST_KEY_EXCLUSIONS,
        TITLE_EXCLUSIONS,
        PREFIX_EXCLUSIONS_REMOVE,
        PREFIX_EXCLUSIONS_PAGE,
        CALLBACK_EXCLUSIONS_MENU,
        TYPE_PREFIX_EXCLUSIONS,
        current_page,
    )


@admin_router.callback_query(F.data == ikb.CB_EXCLUSIONS_ADD)
async def handle_exclusions_add_req(callback: CallbackQuery, state: FSMContext):
    await request_chat_for_list(
        callback,
        state,
        AdminStates.waiting_for_exclusion_chat,
        "🚫 Добавление в исключения",
        CALLBACK_EXCLUSIONS_MENU,
    )


@admin_router.message(StateFilter(AdminStates.waiting_for_exclusion_chat))
async def handle_exclusions_add_val(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    await handle_chat_input_for_list(
        message,
        state,
        settings_manager,
        LIST_KEY_EXCLUSIONS,
        "✅ Чат/Пользователь {display_name} добавлен в исключения.",
        PREFIX_EXCLUSIONS_PAGE,
        CALLBACK_EXCLUSIONS_MENU,
        TITLE_EXCLUSIONS,
        PREFIX_EXCLUSIONS_REMOVE,
        TYPE_PREFIX_EXCLUSIONS,
    )


@admin_router.callback_query(F.data.startswith(PREFIX_EXCLUSIONS_REMOVE))
async def handle_exclusions_remove(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    await handle_remove_chat_from_list(
        callback,
        settings_manager,
        LIST_KEY_EXCLUSIONS,
        PREFIX_EXCLUSIONS_REMOVE,
        TITLE_EXCLUSIONS,
        PREFIX_EXCLUSIONS_PAGE,
        CALLBACK_EXCLUSIONS_MENU,
        TYPE_PREFIX_EXCLUSIONS,
    )


LIST_KEY_RESTRICTIONS = "content_restriction_removed_chats"
TITLE_RESTRICTIONS = "🔓 Список чатов со снятыми ограничениями контента"
PREFIX_RESTRICTIONS_REMOVE = ikb.CB_RESTRICTIONS_REMOVE_PREFIX  # "restrictions_rem_"
PREFIX_RESTRICTIONS_PAGE = (
    f"{ikb.CB_RESTRICTIONS_LIST}{ikb.CB_PAGE_PREFIX}"  # "restrictions_listpage_"
)
CALLBACK_RESTRICTIONS_MENU = ikb.CB_RESTRICTIONS_MENU
TYPE_PREFIX_RESTRICTIONS = ikb.CB_RESTRICTIONS_ADD.split("_")[0]  # "restrictions"


@admin_router.callback_query(F.data.startswith(ikb.CB_RESTRICTIONS_LIST))
async def handle_restrictions_list_pages(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    current_page = 0
    page_data_prefix = PREFIX_RESTRICTIONS_PAGE.replace(ikb.CB_RESTRICTIONS_LIST, "")
    if page_data_prefix in callback.data:
        try:
            page_str = callback.data.split(page_data_prefix)[1]
            current_page = int(page_str)
        except (IndexError, ValueError):
            logger.warning(
                f"Could not parse page number from callback data: {callback.data}"
            )
            current_page = 0

    await show_chat_list(
        callback,
        settings_manager,
        LIST_KEY_RESTRICTIONS,
        TITLE_RESTRICTIONS,
        PREFIX_RESTRICTIONS_REMOVE,
        PREFIX_RESTRICTIONS_PAGE,
        CALLBACK_RESTRICTIONS_MENU,
        TYPE_PREFIX_RESTRICTIONS,
        current_page,
    )


@admin_router.callback_query(F.data == ikb.CB_RESTRICTIONS_ADD)
async def handle_restrictions_add_req(callback: CallbackQuery, state: FSMContext):
    await request_chat_for_list(
        callback,
        state,
        AdminStates.waiting_for_restriction_chat,
        "🔓 Добавление чата для снятия ограничений",
        CALLBACK_RESTRICTIONS_MENU,
    )


@admin_router.message(StateFilter(AdminStates.waiting_for_restriction_chat))
async def handle_restrictions_add_val(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    await handle_chat_input_for_list(
        message,
        state,
        settings_manager,
        LIST_KEY_RESTRICTIONS,
        "✅ Сняты ограничения контента для чата/пользователя {display_name}.",
        PREFIX_RESTRICTIONS_PAGE,
        CALLBACK_RESTRICTIONS_MENU,
        TITLE_RESTRICTIONS,
        PREFIX_RESTRICTIONS_REMOVE,
        TYPE_PREFIX_RESTRICTIONS,
    )


@admin_router.callback_query(F.data.startswith(PREFIX_RESTRICTIONS_REMOVE))
async def handle_restrictions_remove(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    await handle_remove_chat_from_list(
        callback,
        settings_manager,
        LIST_KEY_RESTRICTIONS,
        PREFIX_RESTRICTIONS_REMOVE,
        TITLE_RESTRICTIONS,
        PREFIX_RESTRICTIONS_PAGE,
        CALLBACK_RESTRICTIONS_MENU,
        TYPE_PREFIX_RESTRICTIONS,
    )


LIST_KEY_PRIORITY = "priority_initiation_chats"
TITLE_PRIORITY = "⭐ Список приоритетных чатов для инициации"
PREFIX_PRIORITY_REMOVE = ikb.CB_PRIORITY_REMOVE_PREFIX  # "priority_rem_"
PREFIX_PRIORITY_PAGE = (
    f"{ikb.CB_PRIORITY_LIST}{ikb.CB_PAGE_PREFIX}"  # "priority_listpage_"
)
CALLBACK_PRIORITY_MENU = ikb.CB_PRIORITY_MENU
TYPE_PREFIX_PRIORITY = ikb.CB_PRIORITY_ADD.split("_")[0]  # "priority"


@admin_router.callback_query(F.data.startswith(ikb.CB_PRIORITY_LIST))
async def handle_priority_list_pages(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    current_page = 0
    page_data_prefix = PREFIX_PRIORITY_PAGE.replace(ikb.CB_PRIORITY_LIST, "")
    if page_data_prefix in callback.data:
        try:
            page_str = callback.data.split(page_data_prefix)[1]
            current_page = int(page_str)
        except (IndexError, ValueError):
            logger.warning(
                f"Could not parse page number from callback data: {callback.data}"
            )
            current_page = 0

    await show_chat_list(
        callback,
        settings_manager,
        LIST_KEY_PRIORITY,
        TITLE_PRIORITY,
        PREFIX_PRIORITY_REMOVE,
        PREFIX_PRIORITY_PAGE,
        CALLBACK_PRIORITY_MENU,
        TYPE_PREFIX_PRIORITY,
        current_page,
    )


@admin_router.callback_query(F.data == ikb.CB_PRIORITY_ADD)
async def handle_priority_add_req(callback: CallbackQuery, state: FSMContext):
    await request_chat_for_list(
        callback,
        state,
        AdminStates.waiting_for_priority_chat,
        "⭐ Добавление чата в приоритет инициации",
        CALLBACK_PRIORITY_MENU,
    )


@admin_router.message(StateFilter(AdminStates.waiting_for_priority_chat))
async def handle_priority_add_val(
    message: Message, state: FSMContext, settings_manager: SettingsManager
):
    await handle_chat_input_for_list(
        message,
        state,
        settings_manager,
        LIST_KEY_PRIORITY,
        "✅ Чат/Пользователь {display_name} добавлен в приоритетные для инициации.",
        PREFIX_PRIORITY_PAGE,
        CALLBACK_PRIORITY_MENU,
        TITLE_PRIORITY,
        PREFIX_PRIORITY_REMOVE,
        TYPE_PREFIX_PRIORITY,
    )


@admin_router.callback_query(F.data.startswith(PREFIX_PRIORITY_REMOVE))
async def handle_priority_remove(
    callback: CallbackQuery, settings_manager: SettingsManager
):
    await handle_remove_chat_from_list(
        callback,
        settings_manager,
        LIST_KEY_PRIORITY,
        PREFIX_PRIORITY_REMOVE,
        TITLE_PRIORITY,
        PREFIX_PRIORITY_PAGE,
        CALLBACK_PRIORITY_MENU,
        TYPE_PREFIX_PRIORITY,
    )


@admin_router.callback_query(StateFilter(None))
async def handle_unknown_callback(callback: CallbackQuery):
    logger.warning(
        f"Received unknown callback query from {callback.from_user.id} in default state. EXACT CALLBACK DATA: '{callback.data}'"
    )
    await callback.answer("Неизвестное действие.", show_alert=True)
