"""코스피 지수 시간봉 확인 (4시간봉 만들기용): 야후 차트 API ^KS11 60분봉이 얼마나 길게, 몇 시 봉으로 오는지."""
from collections import Counter
from datetime import datetime, timezone

import requests

H = {"User-Agent": "Mozilla/5.0"}
for rng in ("730d", "2y", "1y", "60d"):
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11",
                         params={"range": rng, "interval": "60m"}, headers=H, timeout=30)
        res = r.json()["chart"]["result"][0]
        off = res["meta"].get("gmtoffset", 0)
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        rows = [(datetime.fromtimestamp(t + off, tz=timezone.utc), c) for t, c in zip(ts, q["close"]) if c is not None]
        print(f"== range {rng}: status {r.status_code}, 봉 {len(rows)}개, {rows[0][0] if rows else '-'} ~ {rows[-1][0] if rows else '-'}")
        print("   시각 분포:", sorted(Counter(d.strftime('%H:%M') for d, _ in rows).items()))
        days = sorted({d.date() for d, _ in rows})
        print("   거래일", len(days))
        for d, c in rows[-9:]:
            print("  ", d, round(c, 2))
        if rows:
            break
    except Exception as e:
        print(f"== range {rng}: 실패 {type(e).__name__}: {e}")
# 일봉 길이도 (주봉용)
r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11", params={"range": "5y", "interval": "1d"}, headers=H, timeout=30)
res = r.json()["chart"]["result"][0]
print("== 일봉 5y:", len(res.get("timestamp") or []))
