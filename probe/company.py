"""종목 상세(재무 요약·회사 설명) 출처 응답 모양 확인. 삼성전자·HMM으로 시험.

- 네이버 모바일 증권 integration / basic: 시총·PER·PBR·52주·배당 등
- FnGuide 스냅샷: 회사 설명(기업 개요) 문장
"""
import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

for code in ("005930", "011200"):
    print(f"\n######## {code}")
    for url in (f"https://m.stock.naver.com/api/stock/{code}/integration",
                f"https://m.stock.naver.com/api/stock/{code}/basic",
                f"https://m.stock.naver.com/api/stock/{code}/finance/annual"):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            print("\n==", url, r.status_code, len(r.text))
            d = r.json()
            print("키:", list(d.keys()) if isinstance(d, dict) else type(d))
            print(json.dumps(d, ensure_ascii=False)[:2500])
        except Exception as e:
            print("실패", url, type(e).__name__, e)
    try:
        url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"
        r = requests.get(url, headers=UA, timeout=20)
        print("\n== fnguide", r.status_code, len(r.text))
        m = re.search(r'id="bizSummaryContent"(.*?)</ul>', r.text, re.S)
        print("개요:", re.sub(r"<[^>]+>|\s+", " ", m.group(1))[:800] if m else "못 찾음")
        m = re.search(r'id="bizSummaryHeader"(.*?)</h', r.text, re.S)
        print("헤더:", re.sub(r"<[^>]+>|\s+", " ", m.group(1))[:300] if m else "못 찾음")
    except Exception as e:
        print("fnguide 실패", type(e).__name__, e)
