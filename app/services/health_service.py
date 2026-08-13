import logging
import sys
from typing import Any

from sqlalchemy import func, select

from app.db.database import AsyncSessionLocal
from app.db.models import (
    Advertisement,
    Contact,
    Merchant,
    MonitoringProfile,
    ScanHistory,
    NotificationDelivery,
)

logger = logging.getLogger(__name__)

class HealthService:
    @staticmethod
    async def get_dashboard_metrics() -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            total_merchants = (await session.execute(select(func.count(Merchant.id)))).scalar_one()
            total_contacts = (await session.execute(select(func.count(Contact.id)))).scalar_one()
            total_ads = (await session.execute(select(func.count(Advertisement.id)))).scalar_one()
            active_profiles = (await session.execute(select(func.count(MonitoringProfile.id)).where(MonitoringProfile.is_active.is_(True)))).scalar_one()
            total_scans = (await session.execute(select(func.count(ScanHistory.id)))).scalar_one()
            failed_deliveries = (await session.execute(
                select(func.count(NotificationDelivery.id)).where(NotificationDelivery.status == "DEAD")
            )).scalar_one()

            last_scan_stmt = select(ScanHistory).order_by(ScanHistory.id.desc()).limit(1)
            last_scan_res = await session.execute(last_scan_stmt)
            last_scan = last_scan_res.scalar_one_or_none()

            return {
                "total_merchants": total_merchants,
                "total_contacts": total_contacts,
                "total_ads": total_ads,
                "active_profiles": active_profiles,
                "total_scans": total_scans,
                "failed_deliveries": failed_deliveries,
                "last_scan_status": last_scan.status if last_scan else "N/A",
                "last_scan_time": last_scan.finished_at.strftime("%Y-%m-%d %H:%M:%S") if last_scan and last_scan.finished_at else "N/A",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            }
