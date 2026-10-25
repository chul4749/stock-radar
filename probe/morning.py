"""아침 요약용 출처 시험: 미국 지수(나스닥·S&P500·필라델피아 반도체), 한국 ETF(EWY), 환율.

네이버 해외 지수 API와 야후 차트 API를 둘 다 찍어 본다.
"""
import json

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

URLS = [
    "https://api.stock.naver.com/index/.IXIC/basic",
    "https://api.stock.naver.com/index/.INX/basic",
    "https://api.stock.naver.com/index/.SOX/basic",
    "https://api.stock.naver.com/index/.IXIC/price?page=1&pageSize=3",
    "https://api.stock.naver.com/stock/EWY.K/basic",
    "https://api.stock.naver.com/stock/EWY/basic",
    "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=exchange&reutersCode=FX_USDKRW",
    "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW",
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/EWY?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/KRW%3DX?range=5d&interval=1d",
    "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?range=5d&interval=1d",
]

for url in URLS:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        print("\n==", url, r.status_code, len(r.text))
        try:
            d = r.json()
            print(json.dumps(d, ensure_ascii=False)[:1500])
        except ValueError:
            print(r.text[:300])
    except Exception as e:
        print("\n== 실패", url, type(e).__name__, e)
