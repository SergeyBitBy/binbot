import asyncio
import logging

from app.config.settings import settings
from app.providers.base import BaseP2PProvider
from app.providers.binance.client import BinanceClient
from app.providers.binance.models import (
    BinanceSearchItem,
    BinanceSearchRequest,
    BinanceSearchResponse,
)

logger = logging.getLogger(__name__)

class BinanceP2PProvider(BaseP2PProvider):
    def __init__(self, client: BinanceClient | None = None):
        self.client = client or BinanceClient()

    async def fetch_advertisements(self, request: BinanceSearchRequest) -> list[BinanceSearchItem]:
        payload = request.model_dump(exclude_none=True)
        raw_response = await self.client.search_ads(payload)
        parsed_response = BinanceSearchResponse.model_validate(raw_response)
        return parsed_response.data

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
        all_items: list[BinanceSearchItem] = []
        
        for page in range(1, max_pages + 1):
            request = BinanceSearchRequest(
                asset=asset,
                fiat=fiat,
                tradeType=trade_type,
                payTypes=pay_types or [],
                transAmount=trans_amount,
                merchantCheck=merchant_check,
                page=page,
                rows=rows_per_page,
            )
            
            try:
                items = await self.fetch_advertisements(request)
                if not items:
                    logger.info(f"Binance P2P search page {page} returned empty list. Ending pagination.")
                    break
                
                all_items.extend(items)
                
                if len(items) < rows_per_page:
                    logger.info(f"Page {page} returned {len(items)} items (< {rows_per_page}). Reached end.")
                    break
                    
                # Rate limit delay between pages
                await asyncio.sleep(settings.binance_rate_limit_delay)
            except Exception as e:
                logger.error(f"Error fetching Binance P2P page {page}: {e}")
                break
                
        return all_items

    async def close(self):
        await self.client.close()
