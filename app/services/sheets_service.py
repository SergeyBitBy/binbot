import asyncio
import json
import logging
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
            
            spreadsheet = await loop.run_in_executor(
                None, lambda: self._client.open_by_key(self.spreadsheet_id)
            )
            
            try:
                self._sheet = await loop.run_in_executor(
                    None, lambda: spreadsheet.worksheet("Merchants")
                )
            except Exception:
                try:
                    self._sheet = await loop.run_in_executor(
                        None, lambda: spreadsheet.get_worksheet(0)
                    )
                except Exception:
                    self._sheet = await loop.run_in_executor(
                        None, lambda: spreadsheet.sheet1
                    )

            logger.info(f"Successfully connected to Google Sheets ID: {self.spreadsheet_id} (Worksheet: {self._sheet.title})")
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
        return self._sheet is not None

    def _col_letter(self, n: int) -> str:
        """Convert column index (1-based) to letter, e.g., 1 -> A, 10 -> J."""
        string = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            string = chr(65 + remainder) + string
        return string or "A"

    async def _apply_formatting(self, total_rows: int, col_count: int):
        """Apply bold headers, background color, center alignment, and text wrapping across dynamic columns."""
        if not self._sheet:
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
                lambda: self._sheet.format(
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
                lambda: self._sheet.format(
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
            logger.warning(f"Failed to apply formatting to Google Sheets: {e}")

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
        # SQLite returns UTC timestamps without tzinfo. Restore their meaning
        # before converting them to the configured display timezone.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")

    async def ensure_headers_exist(self):
        """Ensure row 1 contains active headers."""
        if not self._sheet:
            return

        config = await self.get_columns_config()
        enabled_cols = [c for c in config if c.get("enabled", True)]
        headers = [c.get("title", c["key"]) for c in enabled_cols]

        loop = asyncio.get_event_loop()
        try:
            all_vals = await loop.run_in_executor(None, lambda: self._sheet.get_all_values())
            if not all_vals:
                await loop.run_in_executor(None, lambda: self._sheet.update(range_name="A1", values=[headers]))
            elif all_vals[0] != headers:
                await loop.run_in_executor(None, lambda: self._sheet.insert_row(headers, 1))
        except Exception as e:
            logger.error(f"Error ensuring headers in Google Sheets: {e}")

    async def overwrite_all_merchants(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]]):
        """Clean sheet completely, write active headers at A1, write data starting at A2 with dynamic columns."""
        if not self._sheet:
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
            await loop.run_in_executor(None, lambda: self._sheet.clear())
            await loop.run_in_executor(None, lambda: self._sheet.update(range_name="A1", values=rows))
            logger.info(f"Successfully overwrote Google Sheets with {len(rows)-1} merchant rows starting at A1.")

            await self._apply_formatting(total_rows=len(rows), col_count=len(enabled_cols))
        except Exception as e:
            logger.error(f"Error overwriting Google Sheets: {e}")

    async def sync_merchant(self, merchant: Merchant, contacts: list[Contact]):
        if await self.is_auto_export_enabled():
            contacts_only = await self.is_auto_contacts_only_enabled()
            if not contacts_only or bool(contacts):
                await self.sync_merchants_batch([(merchant, contacts)])

    async def sync_merchants_batch(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]]):
        if not self._sheet or not merchant_contacts_list:
            return

        config = await self.get_columns_config()
        enabled_cols = [c for c in config if c.get("enabled", True)]

        await self.ensure_headers_exist()

        rows = []
        for merchant, contacts in merchant_contacts_list:
            row = self._build_row(merchant, contacts, enabled_cols)
            rows.append(row)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._sheet.append_rows(rows))
            logger.info(f"Successfully batch synced {len(rows)} merchant rows to Google Sheets.")
            
            all_vals = await loop.run_in_executor(None, lambda: self._sheet.get_all_values())
            await self._apply_formatting(total_rows=len(all_vals), col_count=len(enabled_cols))
        except Exception as e:
            logger.error(f"Error batch appending rows to Google Sheets: {e}")
