"""ETF 목록(네이버 etfItemList) 시험: 업종별 대표 ETF를 고르려고 이름·거래대금·시총을 본다."""
import json

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
r = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", params={"etfType": 0}, headers=UA, timeout=20)
print(r.status_code, r.headers.get("content-type"), len(r.text))
r.encoding = "euc-kr"
d = json.loads(r.text)
items = d["result"]["etfItemList"]
print("개수", len(items), "키", list(items[0].keys()))
bad = ("레버리지", "인버스", "2X", "선물", "커버드콜", "채권", "국채", "달러", "미국", "중국", "일본", "인도", "베트남", "S&P", "나스닥",
       "글로벌", "금", "원유", "TR", "액티브", "합성", "혼합", "머니마켓", "CD", "KOFR", "단기", "회사채", "리츠")
keep = [x for x in items if not any(b in x["itemname"] for b in bad)]
keep.sort(key=lambda x: -(x.get("amonut") or 0))
for x in keep[:220]:
    print(x["itemcode"], x["itemname"], "거래대금(백만)", x.get("amonut"), "시총(억)", x.get("marketSum"), "3달", x.get("threeMonthEarnRate"))
