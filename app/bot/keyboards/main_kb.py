from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="📊 Дашборд", callback_data="menu_dashboard"),
            InlineKeyboardButton(text="⚙️ Профили Мониторинга", callback_data="menu_profiles"),
        ],
        [
            InlineKeyboardButton(text="🔍 Поиск Мерчантов", callback_data="menu_merchants"),
            InlineKeyboardButton(text="⚡ Ручной Сканирование", callback_data="menu_scan_now"),
        ],
        [
            InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="menu_export_csv"),
            InlineKeyboardButton(text="💾 Бэкап БД", callback_data="menu_backup_db"),
        ],
        [
            InlineKeyboardButton(text="📝 Логи Системы", callback_data="menu_logs"),
            InlineKeyboardButton(text="ℹ️ Статус Сервисов", callback_data="menu_status"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_profiles_keyboard(profiles: list) -> InlineKeyboardMarkup:
    keyboard = []
    for p in profiles:
        status_icon = "🟢" if p.is_active else "🔴"
        lock_icon = " 🔒" if p.is_locked else ""
        text = f"{status_icon} {p.name} ({p.asset}/{p.fiat} {p.trade_type}){lock_icon}"
        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"prof_view_{p.id}")])

    keyboard.append([
        InlineKeyboardButton(text="➕ Создать Профиль", callback_data="prof_create"),
        InlineKeyboardButton(text="🔙 Главное Меню", callback_data="menu_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_profile_detail_keyboard(profile_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Приостановить" if is_active else "▶️ Активировать"
    keyboard = [
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"prof_toggle_{profile_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"prof_delete_{profile_id}"),
        ],
        [
            InlineKeyboardButton(text="⚡ Сканировать Сейчас", callback_data=f"prof_scan_{profile_id}"),
            InlineKeyboardButton(text="🔙 К Профилям", callback_data="menu_profiles"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
