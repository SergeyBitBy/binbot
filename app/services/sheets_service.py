import asyncio
import logging
from pathlib import Path

from app.config.settings import settings
from app.db.models import Contact, Merchant

logger = logging.getLogger(__name__)

class GoogleSheetsService:
    def __init__(self):
        self.enabled = settings.google_sheets_enabled
        self.credentials_path = Path(settings.google_service_account_file)
        self.spreadsheet_id = settings.google_spreadsheet_id
        self._client = None
        self._sheet = None

    def is_configured(self) -> bool:
        return (
            self.enabled
            and self.credentials_path.exists()
            and bool(self.spreadsheet_id.strip())
        )

    async def initialize(self) -> bool:
        if not self.is_configured():
            logger.info("Google Sheets integration is disabled or credentials missing.")
            return False

        try:
            import gspread
            self._client = gspread.service_account(filename=str(self.credentials_path))
            spreadsheet = self._client.open_by_key(self.spreadsheet_id)
            
            # Select or create worksheet
            try:
                self._sheet = spreadsheet.worksheet("Merchants")
            except Exception:
                self._sheet = spreadsheet.add_worksheet(title="Merchants", rows="1000", cols="10")
                headers = ["UserNo", "Nickname", "Type", "Month Orders", "Finish Rate", "Contacts", "Remarks", "First Seen", "Last Seen"]
                self._sheet.append_row(headers)

            logger.info("Successfully connected to Google Sheets.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {e}")
            return False

    async def sync_merchant(self, merchant: Merchant, contacts: list[Contact]):
        if not self._sheet:
            return

        contacts_str = ", ".join([f"{c.type}:{c.value}" for c in contacts])
        row = [
            merchant.user_no,
            merchant.nickname or "",
            merchant.user_type or "",
            merchant.month_order_count,
            f"{merchant.month_finish_rate * 100:.1f}%",
            contacts_str,
            (merchant.remarks or "")[:200],
            merchant.first_seen_at.strftime("%Y-%m-%d %H:%M:%S") if merchant.first_seen_at else "",
            merchant.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if merchant.last_seen_at else "",
        ]

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sheet.append_row, row)
        except Exception as e:
            logger.error(f"Error appending row to Google Sheets: {e}")
