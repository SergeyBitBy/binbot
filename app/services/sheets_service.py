import asyncio
import json
import logging
import re
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Contact, Merchant, SystemSetting

logger = logging.getLogger(__name__)

DEFAULT_COLUMNS_CONFIG = [
    {"key": "user_no", "title": "UserNo", "enabled": True},
    {"key": "profile_url", "title": "Ссылка на Профиль", "enabled": True},
    {"key": "nickname", "title": "Никнейм", "enabled": True},
    {"key": "status", "title": "Статус", "enabled": True},
    {"key": "month_orders", "title": "Ордеров/Месяц", "enabled": True},
    {"key": "finish_rate", "title": "Выполнение %", "enabled": True},
    {"key": "contacts", "title": "Извлеченные Контакты", "enabled": True},
    {"key": "remarks", "title": "Описание / Условия", "enabled": True},
    {"key": "first_seen", "title": "Впервые Замечен", "enabled": True},
    {"key": "last_seen", "title": "Последняя Активность", "enabled": True},
]

class GoogleSheetsService:
    def __init__(self):
        self.credentials_path = None
        self.spreadsheet_id = None
        self._client = None
        self._spreadsheet = None
        self._sheet = None

    def _find_credentials_file(self) -> Optional[Path]:
        candidates = [
            Path(settings.google_service_account_file),
            Path("service_account.json"),
            Path("credentials.json"),
            Path("google_credentials.json"),
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    @staticmethod
    def sanitize_sheet_title(profile_name: str) -> str:
        """Sanitize profile name to be a valid Google Sheets worksheet title (max 80 chars, no forbidden symbols)."""
        if not profile_name or not profile_name.strip():
            return "Лист1"
        # Google Sheets disallows: * ? : [ ] \ / '
        clean = re.sub(r"[\*\?\:\[\]\\/\']", "-", profile_name.strip())
        clean = re.sub(r"-+", "-", clean).strip("- ")
        return clean[:80] or "Лист1"

    async def get_columns_config(self) -> List[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "google_sheets_columns_config"))
            setting = res.scalar_one_or_none()
            if setting and setting.value:
                try:
                    return json.loads(setting.value)
                except Exception as e:
                    logger.error(f"Error parsing google_sheets_columns_config: {e}")
        return DEFAULT_COLUMNS_CONFIG

    async def save_columns_config(self, config: List[Dict[str, Any]]):
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "google_sheets_columns_config"))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = json.dumps(config, ensure_ascii=False)
            else:
                session.add(SystemSetting(key="google_sheets_columns_config", value=json.dumps(config, ensure_ascii=False)))
            await session.commit()

    async def get_effective_spreadsheet_id(self) -> str:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "google_spreadsheet_id"))
            setting = res.scalar_one_or_none()
            if setting and setting.value and setting.value != "Не задан":
                return setting.value.strip()
        return settings.google_spreadsheet_id or ""

    async def is_auto_export_enabled(self) -> bool:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "google_sheets_auto_export"))
            setting = res.scalar_one_or_none()
            return setting is not None and setting.value.lower() == "true"

    async def is_auto_contacts_only_enabled(self) -> bool:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "google_sheets_auto_contacts_only"))
            setting = res.scalar_one_or_none()
            return setting is not None and setting.value.lower() == "true"

    async def initialize_with_status(self) -> Tuple[bool, str]:
        self.credentials_path = self._find_credentials_file()
        self.spreadsheet_id = await self.get_effective_spreadsheet_id()

        if not self.credentials_path:
            return False, "⚠️ Файл service_account.json не найден в папке бота."

        if not self.spreadsheet_id:
            return False, "⚠️ ID/Ссылка Google Таблицы не задана в настройках."

        try:
            import gspread
            loop = asyncio.get_event_loop()
            self._client = await loop.run_in_executor(
                None, lambda: gspread.service_account(filename=str(self.credentials_path))
            )
            
            self._spreadsheet = await loop.run_in_executor(
                None, lambda: self._client.open_by_key(self.spreadsheet_id)
            )
            
            try:
                self._sheet = await loop.run_in_executor(
                    None, lambda: self._spreadsheet.get_worksheet(0)
                )
            except Exception:
                self._sheet = await loop.run_in_executor(
                    None, lambda: self._spreadsheet.sheet1
                )

            logger.info(f"Successfully connected to Google Sheets ID: {self.spreadsheet_id} (Spreadsheet: {self._spreadsheet.title})")
            return True, "OK"
        except ModuleNotFoundError:
            return False, "⚠️ Пакет gspread не установлен. Выполните: pip install gspread google-auth"
        except Exception as e:
            err_str = str(e)
            if "Sheets API has not been used" in err_str or "403" in err_str:
                msg = "⚠️ Google Sheets API выключен в вашем проекте Google Cloud. Нажмите «Включить API» по ссылке в инструкции."
            elif "404" in err_str or "SpreadsheetNotFound" in type(e).__name__:
                msg = "⚠️ Таблица не найдена или не открыт доступ для email сервисного аккаунта."
            else:
                msg = f"⚠️ Ошибка Google API: {err_str[:150]}"
            logger.exception(f"Failed to initialize Google Sheets service: {e}")
            return False, msg

    async def initialize(self) -> bool:
        success, _ = await self.initialize_with_status()
        return success

    def is_configured(self) -> bool:
        return self._spreadsheet is not None

    async def get_or_create_worksheet(self, profile_name: str = ""):
        """Get or create worksheet by profile name."""
        if not self._spreadsheet:
            await self.initialize()
            if not self._spreadsheet:
                return None

        clean_title = self.sanitize_sheet_title(profile_name)
        loop = asyncio.get_event_loop()

        def _get_or_create():
            try:
                return self._spreadsheet.worksheet(clean_title)
            except Exception:
                # Create new worksheet
                try:
                    return self._spreadsheet.add_worksheet(title=clean_title, rows=1000, cols=20)
                except Exception:
                    return self._spreadsheet.get_worksheet(0)

        return await loop.run_in_executor(None, _get_or_create)

    def _col_letter(self, n: int) -> str:
        string = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            string = chr(65 + remainder) + string
        return string or "A"

    async def _apply_formatting(self, worksheet, total_rows: int, col_count: int):
        """Apply bold headers, background color, center alignment, and text wrapping on the target worksheet."""
        if not worksheet:
            return

        last_col_letter = self._col_letter(max(1, col_count))
        loop = asyncio.get_event_loop()
        try:
            end_row = max(2, total_rows + 1)
            full_range = f"A1:{last_col_letter}{end_row}"
            header_range = f"A1:{last_col_letter}1"

            # 1. Format Data Cells - Center Alignment + Text Wrap
            await loop.run_in_executor(
                None,
                lambda: worksheet.format(
                    full_range,
                    {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    },
                ),
            )

            # 2. Format Header Row - Bold + Light Blue Background + Text Wrap
            await loop.run_in_executor(
                None,
                lambda: worksheet.format(
                    header_range,
                    {
                        "textFormat": {"bold": True, "fontSize": 10},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                        "backgroundColorStyle": {"rgbColor": {"red": 0.88, "green": 0.92, "blue": 0.98}},
                    },
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to apply formatting to worksheet {worksheet.title}: {e}")

    def _build_row(self, merchant: Merchant, contacts: list[Contact], enabled_cols: List[Dict[str, Any]]) -> List[Any]:
        contacts_str = ", ".join(c.value for c in contacts)
        user_badge = "Проверенный" if merchant.user_type and "merchant" in merchant.user_type.lower() else "Пользователь"
        profile_url = f"https://p2p.binance.com/advertiserDetail?advertiserNo={merchant.user_no}"

        field_map = {
            "user_no": merchant.user_no,
            "profile_url": profile_url,
            "nickname": merchant.nickname or "",
            "status": user_badge,
            "month_orders": merchant.month_order_count,
            "finish_rate": f"{merchant.month_finish_rate * 100:.1f}%",
            "contacts": contacts_str,
            "remarks": (merchant.remarks or "")[:300],
            "first_seen": self._format_local_datetime(merchant.first_seen_at),
            "last_seen": self._format_local_datetime(merchant.last_seen_at),
        }

        row = []
        for col in enabled_cols:
            k = col["key"]
            row.append(field_map.get(k, ""))
        return row

    @staticmethod
    def _format_local_datetime(value) -> str:
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")

    async def ensure_headers_exist(self, worksheet):
        """Ensure row 1 contains active headers on target worksheet."""
        if not worksheet:
            return

        config = await self.get_columns_config()
        enabled_cols = [c for c in config if c.get("enabled", True)]
        headers = [c.get("title", c["key"]) for c in enabled_cols]

        loop = asyncio.get_event_loop()
        try:
            all_vals = await loop.run_in_executor(None, lambda: worksheet.get_all_values())
            if not all_vals:
                await loop.run_in_executor(None, lambda: worksheet.update(range_name="A1", values=[headers]))
            elif all_vals[0] != headers:
                await loop.run_in_executor(None, lambda: worksheet.insert_row(headers, 1))
        except Exception as e:
            logger.error(f"Error ensuring headers on worksheet {worksheet.title}: {e}")

    async def overwrite_profile_merchants(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]], profile_name: str = ""):
        """Clean profile worksheet completely, write headers at A1, and write data starting at A2."""
        ws = await self.get_or_create_worksheet(profile_name)
        if not ws:
            return

        config = await self.get_columns_config()
        enabled_cols = [c for c in config if c.get("enabled", True)]
        headers = [c.get("title", c["key"]) for c in enabled_cols]

        rows = [headers]
        for merchant, contacts in merchant_contacts_list:
            row = self._build_row(merchant, contacts, enabled_cols)
            rows.append(row)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: ws.clear())
            await loop.run_in_executor(None, lambda: ws.update(range_name="A1", values=rows))
            logger.info(f"Successfully overwrote worksheet '{ws.title}' with {len(rows)-1} merchant rows.")

            await self._apply_formatting(worksheet=ws, total_rows=len(rows), col_count=len(enabled_cols))
        except Exception as e:
            logger.error(f"Error overwriting worksheet '{ws.title}': {e}")

    async def overwrite_all_merchants(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]], profile_name: str = ""):
        """Alias for backward compatibility."""
        await self.overwrite_profile_merchants(merchant_contacts_list, profile_name=profile_name)

    async def append_merchants(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]], profile_name: str = ""):
        """Append new rows to the designated profile worksheet with headers & formatting."""
        if not merchant_contacts_list:
            return

        ws = await self.get_or_create_worksheet(profile_name)
        if not ws:
            return

        config = await self.get_columns_config()
        enabled_cols = [c for c in config if c.get("enabled", True)]

        await self.ensure_headers_exist(ws)

        rows = []
        for merchant, contacts in merchant_contacts_list:
            row = self._build_row(merchant, contacts, enabled_cols)
            rows.append(row)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: ws.append_rows(rows))
            logger.info(f"Successfully appended {len(rows)} merchant rows to worksheet '{ws.title}'.")
            
            all_vals = await loop.run_in_executor(None, lambda: ws.get_all_values())
            await self._apply_formatting(worksheet=ws, total_rows=len(all_vals), col_count=len(enabled_cols))
        except Exception as e:
            logger.error(f"Error appending rows to worksheet '{ws.title}': {e}")

    async def sync_merchant(self, merchant: Merchant, contacts: list[Contact], profile_name: str = ""):
        if await self.is_auto_export_enabled():
            contacts_only = await self.is_auto_contacts_only_enabled()
            if not contacts_only or bool(contacts):
                await self.append_merchants([(merchant, contacts)], profile_name=profile_name)
