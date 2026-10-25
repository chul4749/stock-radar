"""아침 요약: 새벽에 끝난 미국장 결과로 오늘 한국장 출발 분위기를 site/data/morning.json 에 쓴다.

실행: python -m collector.morning  (평일 아침 7~8시 KST)
"""
import json
import os
import sys
from datetime import datetime

from . import logic, sources
from .util import KST, log

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site", "data")

ITEMS = [  # (키, 이름, 야후 기호, 설명)
    ("ewy", "한국 ETF (EWY)", "EWY", "미국에 상장된 한국 주식 묶음. 밤사이 외국인이 한국을 어떻게 봤는지"),
    ("nasdaq", "나스닥", "^IXIC", "미국 기술주 중심 지수"),
    ("sp500", "S&P 500", "^GSPC", "미국 대표 500개 회사"),
    ("sox", "미국 반도체", "^SOX", "삼성전자·SK하이닉스와 같이 움직이는 경우가 많음"),
    ("vix", "VIX (공포지수)", "^VIX", "오르면 불안, 내리면 안심"),
    ("usdkrw", "원/달러 환율", "KRW=X", "오르면(원화 약세) 외국인 매도 압력"),
]


def main():
    now = datetime.now(KST)
    items, errors = [], []
    for key, name, sym, desc in ITEMS:
        try:
            rows = sources.yahoo_daily(sym)
        except Exception as e:
            log(f"  {name} 실패: {type(e).__name__}: {e}")
            errors.append(f"{name}: {type(e).__name__}")
            continue
        if len(rows) < 2:
            continue
        (d0, c0), (d1, c1) = rows[-2], rows[-1]
        items.append({"key": key, "name": name, "desc": desc, "date": d1, "value": round(c1, 2),
                      "change": round((c1 / c0 - 1) * 100, 2), "spark": [round(c, 2) for _, c in rows[-20:]]})
        log(f"  {name} {d1} {c1:,.2f} ({items[-1]['change']:+.2f}%)")
    out = {"at": now.isoformat(timespec="minutes"), "date": now.strftime("%Y%m%d"),
           "items": items, "summary": logic.morning_summary(items), "errors": errors}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "morning.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"저장 morning.json: {out['summary']['text']}")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
