"""리포트 상세(목표가·투자의견)·목록 범위·검색 상위 개수 시험."""
import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}


def get(url):
    r = requests.get(url, headers=UA, timeout=20)
    print("\n==", url, r.status_code, len(r.text), r.headers.get("content-type"))
    return r


lst = get("https://m.stock.naver.com/api/research/company?page=1&pageSize=100").json()
print("100개 요청 →", len(lst), "가장 오래된", lst[-1]["writeDate"] if lst else None)
r = get("https://m.stock.naver.com/api/research/company?page=10&pageSize=100")
d = r.json()
print("10쪽 →", len(d), d[-1]["writeDate"] if d else None)
for u in ("https://m.stock.naver.com/api/research/company?itemCode=028050&page=1&pageSize=5",
          "https://m.stock.naver.com/api/research/company/item/028050?page=1&pageSize=5"):
    try:
        x = get(u).json()
        print(json.dumps(x, ensure_ascii=False)[:600])
    except Exception as e:
        print("실패", type(e).__name__)
rid = lst[0]["researchId"] if lst else 97011
for u in (f"https://m.stock.naver.com/api/research/company/{rid}",
          f"https://m.stock.naver.com/front-api/research/detail?category=company&researchId={rid}",
          f"https://m.stock.naver.com/front-api/research/company/{rid}"):
    try:
        r = get(u)
        try:
            print(json.dumps(r.json(), ensure_ascii=False)[:2000])
        except ValueError:
            print(r.text[:300])
    except Exception as e:
        print("실패", type(e).__name__)
r = get(f"https://finance.naver.com/research/company_read.naver?nid={rid}")
t = r.text
for key in ("목표가", "투자의견", "view_sbj", "view_cnt", "class=\"money\"", "class=\"coment\""):
    i = t.find(key)
    print(key, "→", re.sub(r"<[^>]+>|\s+", " ", t[i - 100:i + 500])[:400] if i >= 0 else "없음")
r = get(f"https://m.stock.naver.com/research/company/{rid}")
t = r.text
for key in ("목표가", "투자의견", "goalPrice", "opinion", "targetPrice"):
    i = t.find(key)
    print("모바일", key, "→", re.sub(r"<[^>]+>|\s+", " ", t[i - 100:i + 400])[:300] if i >= 0 else "없음")
top = get("https://m.stock.naver.com/api/stocks/searchTop?page=1&pageSize=100").json()
st = top.get("stocks", [])
print("검색 상위 개수", len(st), [(s["itemCode"], s["stockName"]) for s in st[:12]], top.get("totalCount"))
for k in ("KOSPI", "KOSDAQ"):
    try:
        x = get(f"https://m.stock.naver.com/api/stocks/searchTop/{k}?page=1&pageSize=100").json()
        print(k, len(x.get("stocks", [])))
    except Exception as e:
        print(k, "실패", type(e).__name__)
