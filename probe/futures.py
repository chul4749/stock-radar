"""외국인 코스피200 선물 순매수 출처(다음 금융 선물 API) 응답 모양 확인.

다음 금융은 선물 종목(ISIN)별 일별 데이터를 주고, 투자자별 순매수는 전 월물 합계라고 알려져 있다.
2026년 만기 코스피200 선물 ISIN: KR4A01{연도 끝자리}{3,6,9,C}000 + 검증 숫자.
"""
import json

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Referer": "https://finance.daum.net/domestic/futures"}


def isin_check(body):
    """ISIN 검증 숫자 (문자 → 10~35, 룬 알고리즘)."""
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


cands = []
for prefix, year in (("KR4A01", "6"), ("KR4A01", "7"), ("KR4101", "W"), ("KR4101", "X")):
    for m in "369C":
        body = f"{prefix}{year}{m}000"
        cands.append(body + isin_check(body))
print("후보 ISIN:", cands)

s = requests.Session()
s.headers.update(UA)
try:
    r = s.get("https://finance.daum.net/domestic/futures", timeout=20)
    print("화면", r.status_code, len(r.text))
except Exception as e:
    print("화면 실패", e)

for isin in cands:
    for url in (f"https://finance.daum.net/api/future/{isin}/days?page=1&perPage=5",
                f"https://finance.daum.net/api/future/{isin}/days?perPage=5&page=1&pagination=true"):
        try:
            r = s.get(url, timeout=20)
            ok = r.status_code == 200 and r.text.strip().startswith("{")
            print("\n==", url, r.status_code, len(r.text))
            if ok:
                d = r.json()
                print("키:", list(d.keys()))
                rows = d.get("data") or []
                print("행:", len(rows))
                for row in rows[:3]:
                    print(json.dumps(row, ensure_ascii=False)[:900])
                break
            print(r.text[:200])
        except Exception as e:
            print("실패", url, type(e).__name__, e)
