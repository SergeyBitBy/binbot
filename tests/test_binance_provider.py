from decimal import Decimal

from app.providers.binance.models import (
    BinanceSearchResponse,
)


def test_binance_response_parsing():
    raw_data = {
        "code": "000000",
        "message": None,
        "total": 1,
        "success": True,
        "data": [
            {
                "adv": {
                    "advNo": "112233445566",
                    "price": "42.50",
                    "tradeType": "BUY",
                    "asset": "USDT",
                    "fiatUnit": "UAH",
                    "remarks": "Тестовая заметка",
                    "autoReplyMsg": None,
                    "payMethods": [{"payType": "Monobank", "payTypeStr": "Monobank"}],
                },
                "advertiser": {
                    "userNo": "user123456",
                    "nickName": "TestTrader",
                    "userType": "merchant",
                    "monthOrderCount": 150,
                    "monthFinishRate": 0.98,
                },
            }
        ],
    }

    parsed = BinanceSearchResponse.model_validate(raw_data)
    assert parsed.success is True
    assert parsed.code == "000000"
    assert len(parsed.data) == 1
    
    item = parsed.data[0]
    assert item.adv.advNo == "112233445566"
    assert item.adv.price == Decimal("42.50")
    assert item.advertiser.userNo == "user123456"
    assert item.advertiser.nickName == "TestTrader"
