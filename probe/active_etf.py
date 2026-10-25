"""액티브 ETF(운용사가 직접 종목을 고르는 ETF) 목록과, 날짜별 구성 종목 수(계약수) 변화로 '운용사가 산 종목·판 종목'을 볼 수 있는지 시험."""
import json

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
r = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", params={"etfType": 0}, headers=UA, timeout=20)
r.encoding = "euc-kr"
items = json.loads(r.text)["result"]["etfItemList"]
bad = ("채권", "국채", "금리", "머니마켓", "CD", "KOFR", "단기", "회사채", "미국", "글로벌", "차이나", "중국", "일본", "인도", "베트남",
       "S&P", "나스닥", "커버드콜", "TDF", "달러", "원유", "금현물", "리츠", "혼합", "인버스", "레버리지", "2X")
act = [x for x in items if "액티브" in x["itemname"] and not any(b in x["itemname"] for b in bad)]
act.sort(key=lambda x: -(x.get("marketSum") or 0))
print("국내 주식 액티브 ETF", len(act))
for x in act[:60]:
    print(x["itemcode"], x["itemname"], "시총(억)", x.get("marketSum"), "거래대금(백만)", x.get("amonut"))

from pykrx import stock
days = stock.get_previous_business_days(fromdate="20261001", todate="20261023") if hasattr(stock, "get_previous_business_days") else None
print("영업일", days[-3:] if days else None)
d1, d0 = "20261023", "20261022"
for x in act[:4]:
    code = x["itemcode"]
    try:
        a = stock.get_etf_portfolio_deposit_file(code, d0)
        b = stock.get_etf_portfolio_deposit_file(code, d1)
        print(f"\n## {x['itemname']} {d0} {len(a)}종목 → {d1} {len(b)}종목")
        j = a[["구성종목명", "계약수", "비중"]].join(b[["계약수", "비중"]], how="outer", lsuffix="_0", rsuffix="_1")
        j["변화%"] = (j["계약수_1"] / j["계약수_0"] - 1) * 100
        print(j.sort_values("비중_1", ascending=False).head(15).to_string())
        print("새로 담음:", list(j[j["계약수_0"].isna()].index)[:10], "뺌:", list(j[j["계약수_1"].isna()].index)[:10])
    except Exception as e:
        print("실패", code, type(e).__name__, e)
