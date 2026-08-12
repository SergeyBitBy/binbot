from typing import List, Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import MonitoringProfile

def get_main_menu_keyboard(monitoring_enabled: bool = True) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Выключить Анализ" if monitoring_enabled else "▶️ Запустить Анализ"
    toggle_data = "toggle_global_monitoring"

    keyboard = [
        [
            InlineKeyboardButton(text="📊 Дашборд", callback_data="menu_dashboard"),
            InlineKeyboardButton(text="⚙️ Профили Мониторинга", callback_data="menu_profiles"),
        ],
        [
            InlineKeyboardButton(text="🔍 База Мерчантов", callback_data="menu_merchants"),
            InlineKeyboardButton(text=f"{toggle_text}", callback_data=toggle_data),
        ],
        [
            InlineKeyboardButton(text="👥 Управление Админами", callback_data="menu_admins"),
            InlineKeyboardButton(text="💬 Разрешенные Чаты", callback_data="menu_chats"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Глобальные Настройки", callback_data="menu_settings"),
            InlineKeyboardButton(text="📜 История Сканов", callback_data="menu_scan_history"),
        ],
        [
            InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="menu_export_csv"),
            InlineKeyboardButton(text="💾 Бэкап БД", callback_data="menu_backup_db"),
        ],
        [
            InlineKeyboardButton(text="⚡ Сканировать Сейчас", callback_data="menu_scan_now"),
            InlineKeyboardButton(text="ℹ️ Статус Сервера", callback_data="menu_status"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_profiles_keyboard(profiles: List[MonitoringProfile]) -> InlineKeyboardMarkup:
    buttons = []
    for p in profiles:
        status_icon = "🟢" if p.is_active else "🔴"
        merchant_check_icon = "🛡️" if p.merchant_check else "🌐"
        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {p.name} ({p.asset}/{p.fiat} {p.trade_type}) {merchant_check_icon}",
                callback_data=f"prof_view_{p.id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="➕ Создать Профиль", callback_data="prof_create")])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_detail_keyboard(profile_id: int, is_active: bool, merchant_check: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Приостановить" if is_active else "🟢 Активировать"
    check_text = "🛡️ Ток Проверенные: ВКЛ" if merchant_check else "🌐 Все Мерчанты: ВКЛ"

    buttons = [
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"prof_toggle_{profile_id}"),
            InlineKeyboardButton(text=check_text, callback_data=f"prof_check_{profile_id}"),
        ],
        [
            InlineKeyboardButton(text="⏱ Изменить Интервал", callback_data=f"prof_interval_{profile_id}"),
            InlineKeyboardButton(text="💳 Способы Оплаты", callback_data=f"prof_paytypes_{profile_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить Профиль", callback_data=f"prof_delete_confirm_{profile_id}"),
        ],
        [
            InlineKeyboardButton(text="⬅️ К Списку Профилей", callback_data="menu_profiles"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_paytypes_multiselect_keyboard(profile_id: int, selected_paytypes: List[str]) -> InlineKeyboardMarkup:
    ALL_BANKS = ["Monobank", "PrivatBank", "PUMB", "A-Bank", "Wise", "Revolut", "Raiffeisen Bank", "Izibank"]
    buttons = []

    row = []
    for bank in ALL_BANKS:
        is_sel = bank in (selected_paytypes or [])
        mark = "✅" if is_sel else "☐"
        row.append(InlineKeyboardButton(text=f"{mark} {bank}", callback_data=f"pay_toggle_{profile_id}_{bank}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="💾 Сохранить Изменения", callback_data=f"prof_view_{profile_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_wizard_nav_keyboard(prev_step_data: Optional[str] = None) -> InlineKeyboardMarkup:
    row = []
    if prev_step_data:
        row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=prev_step_data))
    row.append(InlineKeyboardButton(text="❌ Отмена", callback_data="prof_cancel"))
    return InlineKeyboardMarkup(inline_keyboard=[row])

def get_back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное Меню", callback_data="menu_main")]])
