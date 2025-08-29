import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CB_MENU = "menu"
CB_STATUS = "status"
CB_TOGGLE = "toggle"
CB_SETTINGS_MENU = "settings_menu"
CB_SET_N = "set_n"
CB_SET_N_GROUP = "set_n_group"
CB_SET_COOLDOWN = "set_cd"
CB_EXCLUSIONS_MENU = "exclusions_menu"
CB_EXCLUSIONS_LIST = "exclusions_list"
CB_EXCLUSIONS_ADD = "exclusions_add"
CB_EXCLUSIONS_REMOVE_PREFIX = "exclusions_rem_"
CB_RESTRICTIONS_MENU = "restrictions_menu"
CB_RESTRICTIONS_LIST = "restrictions_list"
CB_RESTRICTIONS_ADD = "restrictions_add"
CB_RESTRICTIONS_REMOVE_PREFIX = "restrictions_rem_"
CB_PRIORITY_MENU = "priority_menu"
CB_PRIORITY_LIST = "priority_list"
CB_PRIORITY_ADD = "priority_add"
CB_PRIORITY_REMOVE_PREFIX = "priority_rem_"
CB_BACK = "back_to_"
CB_PAGE_PREFIX = "page_"
ITEMS_PER_PAGE = 5


def get_main_menu_keyboard(is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить Личность" if is_active else "🟢 Включить Личность"
    builder.row(InlineKeyboardButton(text="📊 Статус", callback_data=CB_STATUS))
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=CB_TOGGLE))
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS_MENU),
        InlineKeyboardButton(text="🚫 Исключения", callback_data=CB_EXCLUSIONS_MENU),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔓 Снятие Ограничений", callback_data=CB_RESTRICTIONS_MENU
        ),
        InlineKeyboardButton(
            text="⭐ Приоритет Инициации", callback_data=CB_PRIORITY_MENU
        ),
    )
    return builder.as_markup()


def get_settings_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Лимит истории (N)", callback_data=CB_SET_N)
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 Частота в группах (N_group)", callback_data=CB_SET_N_GROUP
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⏳ Кулдаун детекции (Cooldown)", callback_data=CB_SET_COOLDOWN
        )
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_BACK}{CB_MENU}")
    )
    return builder.as_markup()


def get_list_management_keyboard(
    list_type_prefix: str,
    back_target: str = CB_MENU,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📄 Показать список", callback_data=f"{list_type_prefix}_list"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить чат", callback_data=f"{list_type_prefix}_add"
        )
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_BACK}{back_target}")
    )
    return builder.as_markup()


def get_chats_list_keyboard(
    chat_items: list[tuple[int, str]],
    remove_callback_prefix: str,
    page_callback_prefix: str,
    back_callback_data: str,
    current_page: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_items = len(chat_items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    current_page = max(0, min(current_page, total_pages - 1))

    start_index = current_page * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    items_on_page = chat_items[start_index:end_index]

    if not items_on_page and total_items > 0:
        current_page = max(0, current_page - 1)
        start_index = current_page * ITEMS_PER_PAGE
        end_index = start_index + ITEMS_PER_PAGE
        items_on_page = chat_items[start_index:end_index]

    if not items_on_page:
        builder.row(
            InlineKeyboardButton(text="Список пуст", callback_data="dummy_empty")
        )
    else:
        for chat_id, display_name in items_on_page:
            callback_data = f"{remove_callback_prefix}{chat_id}_{current_page}"
            text = f"{display_name}"
            builder.row(
                InlineKeyboardButton(text=f"❌ {text}", callback_data=callback_data)
            )

    pagination_buttons = []
    if current_page > 0:
        pagination_buttons.append(
            InlineKeyboardButton(
                text="<<<", callback_data=f"{page_callback_prefix}{current_page - 1}"
            )
        )
    if items_on_page:
        pagination_buttons.append(
            InlineKeyboardButton(
                text=f"{current_page + 1}/{total_pages}",
                callback_data="dummy_page_info",
            )
        )
    if current_page < total_pages - 1:
        pagination_buttons.append(
            InlineKeyboardButton(
                text=">>>", callback_data=f"{page_callback_prefix}{current_page + 1}"
            )
        )

    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback_data))
    return builder.as_markup()


def get_back_button_keyboard(back_callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback_data))
    return builder.as_markup()


def get_cancel_button_keyboard(cancel_callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_callback_data)
    )
    return builder.as_markup()
