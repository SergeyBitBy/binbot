import asyncio
import json
import logging
import sys

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://p2p.binance.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

async def run_binance_diagnostic():
    payload = {
        "asset": "USDT",
        "fiat": "UAH",
        "merchantCheck": False,
        "page": 1,
        "payTypes": [],
        "publisherType": None,
        "rows": 5,
        "tradeType": "BUY",
    }
    
    logging.info("Sending request to Binance P2P API...")
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            response = await client.post(URL, json=payload)
            logging.info(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                code = data.get("code")
                message = data.get("message")
                success = data.get("success")
                total = data.get("total")
                items = data.get("data", [])
                
                logging.info(f"Response success: {success}, code: {code}, total ads: {total}, fetched items count: {len(items)}")
                
                if items:
                    sample = items[0]
                    adv = sample.get("adv", {})
                    advertiser = sample.get("advertiser", {})
                    
                    logging.info("--- Sample Data ---")
                    logging.info(f"Ad No: {adv.get('advNo')}")
                    logging.info(f"Price: {adv.get('price')}")
                    logging.info(f"Merchant Name: {advertiser.get('nickName')}")
                    logging.info(f"User No: {advertiser.get('userNo')}")
                    logging.info(f"User Type: {advertiser.get('userType')}")
                    logging.info(f"Month Order Count: {advertiser.get('monthOrderCount')}")
                    logging.info(f"Month Finish Rate: {advertiser.get('monthFinishRate')}")
                    logging.info(f"Remarks: {adv.get('remarks')}")
                    logging.info(f"Auto Reply: {adv.get('autoReplyMsg')}")
                    
                    with open("scripts/sample_response.json", "w", encoding="utf-8") as f:
                        json.dump(sample, f, indent=2, ensure_ascii=False)
                    logging.info("Sample ad saved to scripts/sample_response.json")
                else:
                    logging.warning("No items returned in 'data' array.")
            else:
                logging.error(f"HTTP Error: {response.status_code} - {response.text[:500]}")
        except Exception as e:
            logging.exception(f"Request failed: {e}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_binance_diagnostic())
