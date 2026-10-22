"""수집기 작성 전에 KRX·DART 응답 모양을 찍어 보는 일회용 스크립트."""
import re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from pykrx import stock
from pykrx.website.krx.market.core import 전종목시세

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)

KST = timezone(timedelta(hours=9))
d = datetime.now(KST)
while d.weekday() >= 5:
    d -= timedelta(days=1)
YMD = d.strftime("%Y%m%d")
FROM = (d - timedelta(days=30)).strftime("%Y%m%d")


def show(title, fn):
    t0 = time.time()
    try:
        r = fn()
        print(f"\n### {title} ({time.time() - t0:.1f}s)")
        if isinstance(r, pd.DataFrame):
            print(r.shape, list(r.columns), r.index.name)
            print(r.head(4))
        else:
            print(str(r)[:1500])
    except Exception as e:
        print(f"\n### {title} 실패: {type(e).__name__}: {e}")


show("전종목시세 raw", lambda: 전종목시세().fetch(YMD, "ALL"))
show("get_market_ohlcv ALL", lambda: stock.get_market_ohlcv(YMD, market="ALL"))
show("get_market_cap ALL", lambda: stock.get_market_cap(YMD, market="ALL"))
show("ticker name", lambda: stock.get_market_ticker_name("005930"))
show("net 기관합계", lambda: stock.get_market_net_purchases_of_equities(YMD, YMD, "ALL", "기관합계"))
show("net 외국인 30일", lambda: stock.get_market_net_purchases_of_equities(FROM, YMD, "ALL", "외국인"))
show("net 연기금", lambda: stock.get_market_net_purchases_of_equities(YMD, YMD, "ALL", "연기금"))
show("trading value by date KOSPI", lambda: stock.get_market_trading_value_by_date(FROM, YMD, "KOSPI"))
show("trading value by date KOSDAQ", lambda: stock.get_market_trading_value_by_date(FROM, YMD, "KOSDAQ"))
show("index 1001", lambda: stock.get_index_ohlcv(FROM, YMD, "1001"))
show("index 2001", lambda: stock.get_index_ohlcv(FROM, YMD, "2001"))
show("index 1028", lambda: stock.get_index_ohlcv(FROM, YMD, "1028"))
show("sector KOSPI", lambda: stock.get_market_sector_classifications(YMD, "KOSPI"))
show("holiday 20261009", lambda: stock.get_market_ohlcv("20261009", market="ALL"))
show("business days", lambda: stock.get_previous_business_days(fromdate=FROM, todate=YMD))


def dart():
    r = requests.get("https://dart.fss.or.kr/dsac001/mainAll.do",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    t = r.text
    i = t.find("openReportViewer")
    return t[i - 1500:i + 800]


show("DART html", dart)
