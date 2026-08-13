import asyncio
import logging
from dataclasses import dataclass, field

from app.config.settings import settings
from app.providers.base import BaseP2PProvider
from app.providers.binance.client import BinanceClient
from app.providers.binance.models import (
    BinanceSearchItem,
    BinanceSearchRequest,
    BinanceSearchResponse,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchResult:
    items: list[BinanceSearchItem] = field(default_factory=list)
    expected_total: int | None = None
    pages_fetched: int = 0
    complete: bool = False
    error: str | None = None

class BinanceP2PProvider(BaseP2PProvider):
    def __init__(self, client: BinanceClient | None = None):
        self.client = client or BinanceClient()

    async def fetch_advertisements(self, request: BinanceSearchRequest) -> BinanceSearchResponse:
        payload = request.model_dump(exclude_none=True)
        raw_response = await self.client.search_ads(payload)
        parsed_response = BinanceSearchResponse.model_validate(raw_response)
        return parsed_response

    async def fetch_all_pages(
        self,
        asset: str,
        fiat: str,
        trade_type: str,
        pay_types: list[str] | None = None,
        trans_amount: str | None = None,
        merchant_check: bool = False,
        max_pages: int | None = None,
        rows_per_page: int = 20,
    ) -> FetchResult:
        all_items: list[BinanceSearchItem] = []
        seen_adv_nos: set[str] = set()
        expected_total: int | None = None
        pages_fetched = 0
        max_pages = max_pages or settings.binance_max_pages
        
        for page in range(1, max_pages + 1):
            request = BinanceSearchRequest(
                asset=asset,
                fiat=fiat,
                tradeType=trade_type,
                payTypes=pay_types or [],
                transAmount=trans_amount,
                merchantCheck=merchant_check,
                publisherType="merchant" if merchant_check else None,
                page=page,
                rows=rows_per_page,
            )
            
            try:
                response = await self.fetch_advertisements(request)
                items = response.data
                expected_total = response.total
                pages_fetched += 1
                if not items:
                    logger.info(f"Binance P2P search page {page} returned empty list. Ending pagination.")
                    return FetchResult(all_items, expected_total, pages_fetched, True)
                
                for item in items:
                    if item.adv.advNo not in seen_adv_nos:
                        seen_adv_nos.add(item.adv.advNo)
                        all_items.append(item)

                if expected_total is not None and len(all_items) >= expected_total:
                    return FetchResult(all_items, expected_total, pages_fetched, True)
                
                if len(items) < rows_per_page:
                    logger.info(f"Page {page} returned {len(items)} items (< {rows_per_page}). Reached end.")
                    return FetchResult(all_items, expected_total, pages_fetched, True)
                    
                # Rate limit delay between pages
                await asyncio.sleep(settings.binance_rate_limit_delay)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error fetching Binance P2P page {page}: {e}")
                return FetchResult(all_items, expected_total, pages_fetched, False, str(e))

        complete = expected_total is not None and len(all_items) >= expected_total
        error = None if complete else f"pagination safety limit reached at {max_pages} pages"
        return FetchResult(all_items, expected_total, pages_fetched, complete, error)

    async def close(self):
        await self.client.close()
