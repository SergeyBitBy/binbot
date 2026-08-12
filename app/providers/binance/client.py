import asyncio
import logging
import random
from typing import Any

import httpx

from app.config.settings import settings
from app.providers.binance.exceptions import (
    BinanceAPIError,
    BinanceNetworkError,
    BinanceRateLimitError,
)

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

class BinanceClient:
    def __init__(self, timeout: float = None):
        self.url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        self.timeout = timeout or settings.binance_request_timeout
        self.client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)

    def _get_headers() -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Content-Type": "application/json",
            "Origin": "https://p2p.binance.com",
            "User-Agent": random.choice(USER_AGENTS),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    async def search_ads(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._get_headers()
        max_retries = settings.binance_max_retries
        
        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.post(self.url, json=payload, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    if not data.get("success", False) and data.get("code") != "000000":
                        raise BinanceAPIError(
                            status_code=response.status_code,
                            message=data.get("message", "Unknown Binance Error"),
                            code=str(data.get("code")),
                        )
                    return data
                elif response.status_code == 429:
                    logger.warning(f"Rate limited by Binance P2P (429). Attempt {attempt}/{max_retries}")
                    if attempt == max_retries:
                        raise BinanceRateLimitError(429, "Rate limit exceeded")
                    await asyncio.sleep(2.0 * attempt)
                else:
                    logger.warning(f"Binance API returned HTTP {response.status_code}. Attempt {attempt}/{max_retries}")
                    if attempt == max_retries:
                        raise BinanceAPIError(response.status_code, response.text[:200])
                    await asyncio.sleep(1.0 * attempt)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.warning(f"Network error calling Binance API: {e}. Attempt {attempt}/{max_retries}")
                if attempt == max_retries:
                    raise BinanceNetworkError(f"Network request failed: {e}")
                await asyncio.sleep(1.5 * attempt)
        
        raise BinanceNetworkError("Max retries exceeded")

    async def close(self):
        await self.client.aclose()
