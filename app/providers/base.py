from abc import ABC, abstractmethod

from app.providers.binance.models import BinanceSearchItem, BinanceSearchRequest


class BaseP2PProvider(ABC):
    @abstractmethod
    async def fetch_advertisements(self, request: BinanceSearchRequest) -> list[BinanceSearchItem]:
        """Fetch advertisements from P2P platform based on search request filters."""
    
    @abstractmethod
    async def fetch_all_pages(
        self,
        asset: str,
        fiat: str,
        trade_type: str,
        pay_types: list[str] | None = None,
        trans_amount: str | None = None,
        merchant_check: bool = False,
        max_pages: int = 5,
        rows_per_page: int = 20,
    ) -> list[BinanceSearchItem]:
        """Fetch all pages of advertisements for given parameters up to max_pages."""
