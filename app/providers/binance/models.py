from decimal import Decimal

from pydantic import AliasChoices, BaseModel, Field


class BinanceSearchRequest(BaseModel):
    asset: str = "USDT"
    fiat: str = "UAH"
    merchantCheck: bool = False
    page: int = 1
    payTypes: list[str] = Field(default_factory=list)
    publisherType: str | None = None
    rows: int = 10
    tradeType: str = "BUY"  # BUY or SELL
    transAmount: str | None = None

class BinanceAdvertiser(BaseModel):
    userNo: str
    realName: str | None = None
    nickName: str | None = None
    margin: str | None = None
    userType: str | None = None
    userStatsRet: dict | None = None
    monthOrderCount: int | None = 0
    monthFinishRate: float | None = 0.0
    positiveRate: float | None = 0.0

class BinanceAd(BaseModel):
    advNo: str
    classify: str | None = None
    price: Decimal
    surplusAmount: Decimal | None = None
    maxSingleTransAmount: Decimal | None = None
    minSingleTransAmount: Decimal | None = None
    tradeType: str
    asset: str
    fiatUnit: str
    remarks: str | None = None
    autoReplyMsg: str | None = None
    # Binance currently returns this collection as ``tradeMethods``. Keep the
    # internal name for compatibility with the rest of the application and
    # accept the legacy field as a fallback.
    payMethods: list[dict] = Field(
        default_factory=list,
        validation_alias=AliasChoices("tradeMethods", "payMethods"),
    )

class BinanceSearchItem(BaseModel):
    adv: BinanceAd
    advertiser: BinanceAdvertiser

class BinanceSearchResponse(BaseModel):
    code: str
    message: str | None = None
    data: list[BinanceSearchItem] = Field(default_factory=list)
    total: int = 0
    success: bool = True
