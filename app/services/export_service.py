import csv
import io
import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config.settings import DATA_DIR
from app.db.database import AsyncSessionLocal
from app.db.models import Merchant

logger = logging.getLogger(__name__)

class ExportService:
    @staticmethod
    async def export_merchants_csv() -> str:
        """Export all merchants and their extracted contacts into CSV format."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Merchant)
                .options(selectinload(Merchant.contacts))
                .order_by(Merchant.first_seen_at.desc())
            )
            res = await session.execute(stmt)
            merchants = res.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        
        headers = [
            "ID", "UserNo", "Nickname", "UserType", "MonthOrders", 
            "FinishRate", "PositiveRate", "Contacts", "Remarks", "FirstSeen", "LastSeen"
        ]
        writer.writerow(headers)

        for m in merchants:
            contacts_str = "; ".join([f"{c.type}:{c.value}" for c in m.contacts])
            writer.writerow([
                m.id,
                m.user_no,
                m.nickname or "",
                m.user_type or "",
                m.month_order_count,
                f"{m.month_finish_rate * 100:.1f}%",
                f"{m.positive_rate * 100:.1f}%",
                contacts_str,
                (m.remarks or "").replace("\n", " "),
                m.first_seen_at.strftime("%Y-%m-%d %H:%M:%S") if m.first_seen_at else "",
                m.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_seen_at else "",
            ])

        return output.getvalue()

    @staticmethod
    def create_database_backup() -> Path | None:
        """Create a timestamped backup copy of bot.db in data/backups/."""
        db_file = DATA_DIR / "bot.db"
        if not db_file.exists():
            logger.warning(f"Database file {db_file} does not exist for backup.")
            return None

        backup_dir = DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"bot_backup_{timestamp}.db"

        try:
            shutil.copy2(db_file, backup_file)
            logger.info(f"Database backup created successfully: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"Error creating database backup: {e}")
            return None
