import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from sqlalchemy import select

from app.config.settings import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Contact, Merchant, SystemSetting

logger = logging.getLogger(__name__)

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
            
            # Select the FIRST worksheet tab (sheet1) so rows appear on the main visible sheet tab!
            try:
                self._sheet = await loop.run_in_executor(
                    None, lambda: spreadsheet.sheet1
                )
            except Exception:
                self._sheet = await loop.run_in_executor(
                    None, lambda: spreadsheet.get_worksheet(0)
                )

            # Ensure headers exist on row 1 if sheet is empty
            rows_count = await loop.run_in_executor(None, lambda: len(self._sheet.get_all_values()))
            if rows_count == 0:
                headers = ["UserNo", "Nickname", "Type", "Month Orders", "Finish Rate", "Contacts", "Remarks", "First Seen", "Last Seen"]
                await loop.run_in_executor(None, lambda: self._sheet.append_row(headers))

            logger.info(f"Successfully connected to Google Sheets main sheet1 ID: {self.spreadsheet_id}")
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

    async def sync_merchant(self, merchant: Merchant, contacts: list[Contact]):
        if await self.is_auto_export_enabled():
            await self.sync_merchants_batch([(merchant, contacts)])

    async def sync_merchants_batch(self, merchant_contacts_list: List[Tuple[Merchant, list[Contact]]]):
        if not self._sheet or not merchant_contacts_list:
            return

        rows = []
        for merchant, contacts in merchant_contacts_list:
            contacts_str = ", ".join([f"{c.type}:{c.value}" for c in contacts])
            row = [
                merchant.user_no,
                merchant.nickname or "",
                merchant.user_type or "",
                merchant.month_order_count,
                f"{merchant.month_finish_rate * 100:.1f}%",
                contacts_str,
                (merchant.remarks or "")[:300],
                merchant.first_seen_at.strftime("%Y-%m-%d %H:%M:%S") if merchant.first_seen_at else "",
                merchant.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if merchant.last_seen_at else "",
            ]
            rows.append(row)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._sheet.append_rows(rows))
            logger.info(f"Successfully batch synced {len(rows)} merchant rows to Google Sheets (sheet1).")
        except Exception as e:
            logger.error(f"Error batch appending rows to Google Sheets: {e}")
            if "429" in str(e):
                await asyncio.sleep(2.0)
                try:
                    await loop.run_in_executor(None, lambda: self._sheet.append_rows(rows))
                except Exception as retry_e:
                    logger.error(f"Retry batch append failed: {retry_e}")
