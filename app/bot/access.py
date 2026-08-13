VALID_ROLES = frozenset({"superadmin", "admin", "viewer"})

SUPERADMIN_CALLBACK_PREFIXES = (
    "menu_admins",
    "adm_",
    "menu_chats",
    "chat_",
    "menu_settings",
    "menu_google_sheets",
    "menu_sheets_columns",
    "col_",
    "set_",
    "upload_service_account",
    "toggle_global_monitoring",
    "toggle_quiet_hours",
    "toggle_contact_extraction",
    "toggle_sheets_",
    "run_google_sheets_",
    "menu_backup_db",
    "merch_delete_",
    "merch_clear_all_",
    "prof_delete_",
)

VIEWER_CALLBACK_PREFIXES = SUPERADMIN_CALLBACK_PREFIXES + (
    "prof_toggle_",
    "prof_check_",
    "prof_interval_",
    "prof_paytypes_",
    "pay_toggle_",
    "prof_create",
    "prof_step",
    "type_",
    "menu_scan_now",
    "merch_export_",
    "merch_edit_",
)

SUPERADMIN_STATE_PREFIXES = (
    "AdminForm:",
    "ChatForm:",
    "QuietHoursForm:",
    "GoogleSheetsForm:",
    "ColumnRenameForm:",
)

ADMIN_STATE_PREFIXES = SUPERADMIN_STATE_PREFIXES + (
    "ProfileEditForm:",
    "ProfileForm:",
    "MerchantEditForm:",
)


def normalize_role(role: str | None) -> str:
    return role if role in VALID_ROLES else "viewer"


def is_action_allowed(
    role: str | None,
    *,
    callback_data: str = "",
    command: str = "",
    state: str = "",
) -> bool:
    role = normalize_role(role)
    if role == "superadmin":
        return True
    if command in ("/backup", "/retry_notifications"):
        return False
    if callback_data.startswith(SUPERADMIN_CALLBACK_PREFIXES):
        return False
    if state.startswith(SUPERADMIN_STATE_PREFIXES):
        return False
    if role == "admin":
        return True
    if callback_data.startswith(VIEWER_CALLBACK_PREFIXES):
        return False
    if state.startswith(ADMIN_STATE_PREFIXES):
        return False
    return command in ("", "/start", "/status", "/logs")
