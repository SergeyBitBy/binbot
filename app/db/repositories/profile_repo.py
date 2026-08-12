import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MonitoringProfile, ScanHistory

logger = logging.getLogger(__name__)

class ProfileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self, only_active: bool = False) -> list[MonitoringProfile]:
        stmt = select(MonitoringProfile)
        if only_active:
            stmt = stmt.where(MonitoringProfile.is_active.is_(True))
        stmt = stmt.order_by(MonitoringProfile.id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_by_id(self, profile_id: int) -> MonitoringProfile | None:
        stmt = select(MonitoringProfile).where(MonitoringProfile.id == profile_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_name(self, name: str) -> MonitoringProfile | None:
        stmt = select(MonitoringProfile).where(MonitoringProfile.name == name)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(
        self,
        name: str,
        asset: str = "USDT",
        fiat: str = "UAH",
        trade_type: str = "BUY",
        pay_types: list[str] | None = None,
        trans_amount: str | None = None,
        merchant_check: bool = False,
        scan_interval_seconds: int = 60,
    ) -> MonitoringProfile:
        profile = MonitoringProfile(
            name=name,
            asset=asset,
            fiat=fiat,
            trade_type=trade_type,
            pay_types=pay_types or [],
            trans_amount=trans_amount,
            merchant_check=merchant_check,
            scan_interval_seconds=scan_interval_seconds,
            is_active=True,
            is_locked=False,
            is_baseline_completed=False,
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def update_lock(self, profile_id: int, is_locked: bool):
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.is_locked = is_locked
            await self.session.commit()

    async def mark_baseline_completed(self, profile_id: int):
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.is_baseline_completed = True
            await self.session.commit()

    async def delete(self, profile_id: int) -> bool:
        profile = await self.get_by_id(profile_id)
        if profile:
            await self.session.delete(profile)
            await self.session.commit()
            return True
        return False

    async def add_scan_history(self, history: ScanHistory):
        self.session.add(history)
        await self.session.commit()
