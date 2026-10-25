"""ETF 가격 기록·구성 종목(PDF) 출처 시험: pykrx(KRX 로그인)와 네이버 모바일 API."""
import json
import os
from datetime import datetime, timedelta

import requests

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
end = datetime.now().strftime("%Y%m%d")
start = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
for code in ("091160", "0115D0"):
    print(f"\n######## {code}")
    try:
        from pykrx import stock
        df = stock.get_etf_ohlcv_by_date(start, end, code)
        print("pykrx 시세", df.shape, list(df.columns)); print(df.tail(3))
    except Exception as e:
        print("pykrx 시세 실패", type(e).__name__, e)
    try:
        from pykrx import stock
        d = stock.get_nearest_business_day_in_a_week() if hasattr(stock, "get_nearest_business_day_in_a_week") else end
        pdf = stock.get_etf_portfolio_deposit_file(code, d)
        print("pykrx 구성", pdf.shape, list(pdf.columns)); print(pdf.head(12))
    except Exception as e:
        print("pykrx 구성 실패", type(e).__name__, e)
    for url in (f"https://m.stock.naver.com/api/stock/{code}/price?page=1&pageSize=60",
                f"https://m.stock.naver.com/api/stock/{code}/etfAnalysis",
                f"https://m.stock.naver.com/api/stock/{code}/integration"):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            print("\n==", url, r.status_code, len(r.text))
            t = json.dumps(r.json(), ensure_ascii=False)
            i = max(t.find("구성"), t.find("etfTop10"), t.find("componentList"), 0)
            print(t[:700]); print("…", t[i:i + 1200] if i else "")
        except Exception as e:
            print("실패", url, type(e).__name__, e)
