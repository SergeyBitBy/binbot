import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Contact, Merchant, SystemSetting

logger = logging.getLogger(__name__)

HEADERS = [
    "UserNo",
    "Никнейм",
    "Статус",
    "Ордеров/Месяц",
    "Выполнение %",
    "Извлеченные Контакты",
    "Описание / Условия",
    "Впервые Замечен",
    "Последняя Активность",
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
            
            # Select 'Merchants' or default to index 0 (main sheet)
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

    async def _apply_formatting(self, total_rows: int):
        """Apply bold headers, background color, and center alignment across all cells."""
        if not self._sheet:
            return

        loop = asyncio.get_event_loop()
        try:
            # 1. Format Header Row (A1:I1)
            await loop.run_in_executor(
                None,
                lambda: self._sheet.format(
                    "A1:I1",
                    {
                        "textFormat": {"bold": True, "fontSize": 10},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "backgroundColorStyle": {"rgbColor": {"red": 0.88, "green": 0.92, "blue": 0.98}},
                    },
                ),
            )

            # 2. Format Data Rows (A2:I{total_rows+1}) - Center Alignment
            end_row = max(2, total_rows + 1)
            await loop.run_in_executor(
                None,
                lambda: self._sheet.format(
                    f"A1:I{end_row}",
                    {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                    },
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to apply formatting to Google Sheets: {e}")

    async def ensure_headers_exist(self):
        """Ensure row 1 contains HEADERS. If row 1 is empty or contains merchant data, insert HEADERS at top."""
        if not self._sheet:
            return

        loop = asyncio.get_event_loop()
        try:
            all_vals = await loop.run_in_executor(None, lambda: self._sheet.get_all_values())
            if not all_vals:
                await loop.run_in_executor(None, lambda: self._sheet.update(range_name="A1", values=[HEADERS]))
            elif all_vals[0] != HEADERS:
                # If first row is merchant data (not header title), insert HEADERS at top
                if all_vals[0][0] != HEADERS[0]:
                    await loop.run_in_executor(None, lambda: self._sheet.insert_row(HEADERS, 1))
        except Exception as e:
            logger.error(f"Error ensuring headers in Google Sheets: {e}")

    async def overwrite_all_merchants(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]]):
        """Clean sheet completely, write headers at A1, write data starting at A2, and center-align all cells."""
        if not self._sheet:
            return

        rows = [HEADERS]
        for merchant, contacts in merchant_contacts_list:
            contacts_str = ", ".join([f"{c.type}:{c.value}" for c in contacts])
            user_badge = "Проверенный" if merchant.user_type and "merchant" in merchant.user_type.lower() else "Пользователь"
            
            row = [
                merchant.user_no,
                merchant.nickname or "",
                user_badge,
                merchant.month_order_count,
                f"{merchant.month_finish_rate * 100:.1f}%",
                contacts_str,
                (merchant.remarks or "").replace("\n", " ")[:300],
                merchant.first_seen_at.strftime("%Y-%m-%d %H:%M") if merchant.first_seen_at else "",
                merchant.last_seen_at.strftime("%Y-%m-%d %H:%M") if merchant.last_seen_at else "",
            ]
            rows.append(row)

        loop = asyncio.get_event_loop()
        try:
            # Clear sheet completely to remove misplaced old columns and rows
            await loop.run_in_executor(None, lambda: self._sheet.clear())

            # Update sheet starting at A1 in a single batch call
            await loop.run_in_executor(None, lambda: self._sheet.update(range_name="A1", values=rows))
            logger.info(f"Successfully overwrote Google Sheets with {len(rows)-1} merchant rows starting at A1.")

            # Apply Center Alignment and Bold Header Formatting
            await self._apply_formatting(total_rows=len(rows))
        except Exception as e:
            logger.error(f"Error overwriting Google Sheets: {e}")

    async def sync_merchant(self, merchant: Merchant, contacts: list[Contact]):
        if await self.is_auto_export_enabled():
            await self.sync_merchants_batch([(merchant, contacts)])

    async def sync_merchants_batch(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]]):
        if not self._sheet or not merchant_contacts_list:
            return

        # Always ensure row 1 has headers!
        await self.ensure_headers_exist()

        rows = []
        for merchant, contacts in merchant_contacts_list:
            contacts_str = ", ".join([f"{c.type}:{c.value}" for c in contacts])
            user_badge = "Проверенный" if merchant.user_type and "merchant" in merchant.user_type.lower() else "Пользователь"
            
            row = [
                merchant.user_no,
                merchant.nickname or "",
                user_badge,
                merchant.month_order_count,
                f"{merchant.month_finish_rate * 100:.1f}%",
                contacts_str,
                (merchant.remarks or "").replace("\n", " ")[:300],
                merchant.first_seen_at.strftime("%Y-%m-%d %H:%M") if merchant.first_seen_at else "",
                merchant.last_seen_at.strftime("%Y-%m-%d %H:%M") if merchant.last_seen_at else "",
            ]
            rows.append(row)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._sheet.append_rows(rows))
            logger.info(f"Successfully batch synced {len(rows)} merchant rows to Google Sheets.")
            
            # Format and center all rows!
            all_vals = await loop.run_in_executor(None, lambda: self._sheet.get_all_values())
            await self._apply_formatting(total_rows=len(all_vals))
        except Exception as e:
            logger.error(f"Error batch appending rows to Google Sheets: {e}")
