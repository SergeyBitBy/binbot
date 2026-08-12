class BinanceException(Exception):
    """Base exception for Binance provider operations."""

class BinanceAPIError(BinanceException):
    """Raised when Binance API returns an error status code or business error."""
    def __init__(self, status_code: int, message: str, code: str = None):
        self.status_code = status_code
        self.message = message
        self.code = code
        super().__init__(f"Binance API Error [{status_code}] {code or ''}: {message}")

class BinanceRateLimitError(BinanceAPIError):
    """Raised when request is rate limited (HTTP 429)."""

class BinanceNetworkError(BinanceException):
    """Raised when connection timeouts or network failures occur."""
