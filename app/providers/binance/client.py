import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
import httpx

from app.config.settings import settings
from app.providers.binance.exceptions import BinanceAPIError, BinanceNetworkError, BinanceRateLimitError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DetailFetchResult:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: str | None = None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

class BinanceClient:
    def __init__(self, timeout: float = None):
        self.url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        self.detail_url = "https://p2p.binance.com/bapi/c2c/v2/public/c2c/adv/detail-with-advertiser"
        self.timeout_val = timeout or settings.binance_request_timeout
        self.timeout = httpx.Timeout(connect=10.0, read=self.timeout_val, write=10.0, pool=30.0)
        self.limits = httpx.Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=10.0)
        self._client_lock = asyncio.Lock()
        self._last_recreated: float = 0.0
        self.client = self._create_client()

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            limits=self.limits,
            follow_redirects=True,
        )

    async def _recreate_client(self):
        """Safely recreate HTTP client and connection pool on PoolTimeout with rate limiting."""
        async with self._client_lock:
            now = time.monotonic()
            if now - self._last_recreated < 60.0:
                return
            self._last_recreated = now
            try:
                old_client = self.client
                self.client = self._create_client()
                asyncio.create_task(old_client.aclose())
                logger.info("Binance HTTP client connection pool was refreshed successfully.")
            except Exception as e:
                logger.debug("Error refreshing httpx client: %s", e)

    @staticmethod
    def _get_headers() -> Dict[str, str]:
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

    async def search_ads(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
                    retry_after = float(response.headers.get("Retry-After", 2.0 * attempt))
                    await asyncio.sleep(retry_after + random.uniform(0, 0.5))
                else:
                    logger.warning(f"Binance API returned HTTP {response.status_code}. Attempt {attempt}/{max_retries}")
                    if attempt == max_retries:
                        raise BinanceAPIError(response.status_code, response.text[:200])
                    await asyncio.sleep(1.0 * attempt)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                err_str = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"Network error calling Binance API ({err_str}). Attempt {attempt}/{max_retries}")
                if isinstance(e, httpx.PoolTimeout):
                    await self._recreate_client()
                if attempt == max_retries:
                    raise BinanceNetworkError(f"Network request failed: {err_str}")
                await asyncio.sleep(1.5 * attempt)
            except (ValueError, TypeError) as e:
                logger.warning("Invalid Binance API response. Attempt %s/%s: %s", attempt, max_retries, e)
                if attempt == max_retries:
                    raise BinanceAPIError(200, f"Invalid JSON response: {e}") from e
                await asyncio.sleep(attempt + random.uniform(0, 0.5))
        
        raise BinanceNetworkError("Max retries exceeded")

    async def get_adv_detail(self, adv_no: str) -> DetailFetchResult:
        """Fetch full advertisement detail including remarks and autoReplyMsg using public GET endpoint."""
        url = f"{self.detail_url}?channel=c2c&advNo={adv_no}&area=p2pZone"
        headers = self._get_headers()
        max_retries = settings.binance_max_retries
        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("success") and data.get("code") == "000000":
                        return DetailFetchResult(success=True, data=data.get("data"))
                    error = f"business error {data.get('code')}: {data.get('message')}"
                else:
                    error = f"HTTP {response.status_code}"

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", attempt * 2))
                    await asyncio.sleep(retry_after + random.uniform(0, 0.5))
                elif response.status_code >= 500 and attempt < max_retries:
                    await asyncio.sleep(attempt + random.uniform(0, 0.5))
                else:
                    return DetailFetchResult(success=False, error=error)
            except asyncio.CancelledError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, httpx.PoolTimeout):
                    await self._recreate_client()
                if attempt < max_retries:
                    await asyncio.sleep(attempt * 1.5 + random.uniform(0, 0.5))
                else:
                    return DetailFetchResult(success=False, error=error)
            except (ValueError, TypeError) as exc:
                return DetailFetchResult(success=False, error=f"invalid response: {exc}")

        return DetailFetchResult(success=False, error="maximum retries exceeded")

    async def close(self):
        async with self._client_lock:
            await self.client.aclose()
