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

ITEMS = [  # (키, 이름, 야후 기호, 설명, 묶음, 단위, 변화 방식: pct=%·diff=값 차이)
    ("ewy", "한국 ETF (EWY)", "EWY", "미국에 상장된 한국 대형주 묶음(달러). 밤사이 외국인이 한국을 어떻게 봤는지 — 환율 변화도 섞임", "kr", "달러", "pct"),
    ("sp500", "S&P 500", "^GSPC", "미국 대표 500개 회사", "idx", "", "pct"),
    ("nasdaq", "나스닥", "^IXIC", "미국 기술주 중심 지수", "idx", "", "pct"),
    ("sox", "미국 반도체", "^SOX", "필라델피아 반도체 지수. 삼성전자·SK하이닉스와 같이 움직이는 경우가 많음", "idx", "", "pct"),
    ("nikkei", "닛케이 225", "^N225", "일본", "idx", "", "pct"),
    ("twii", "대만 가권", "^TWII", "대만 (TSMC 비중 큼)", "idx", "", "pct"),
    ("shcomp", "상하이 종합", "000001.SS", "중국", "idx", "", "pct"),
    ("us10y", "미 국채 10년", "^TNX", "미국 10년 금리. 오르면 성장주에 부담", "rate", "%", "diff"),
    ("us30y", "미 국채 30년", "^TYX", "미국 30년 금리", "rate", "%", "diff"),
    ("usdkrw", "원/달러", "KRW=X", "오르면(원화 약세) 외국인 매도 압력", "rate", "원", "pct"),
    ("usdjpy", "엔/달러", "JPY=X", "엔화 값", "rate", "엔", "pct"),
    ("jpykrw", "원/엔 (100엔)", None, "원/달러 ÷ 엔/달러 × 100", "rate", "원", "pct"),
    ("eurkrw", "원/유로", "EURKRW=X", "", "rate", "원", "pct"),
    ("gold", "국제 금", "GC=F", "선물", "cmd", "달러", "pct"),
    ("wti", "WTI 유가", "CL=F", "선물", "cmd", "달러", "pct"),
    ("silver", "은", "SI=F", "선물", "cmd", "달러", "pct"),
    ("copper", "구리", "HG=F", "선물 (경기 체온계)", "cmd", "달러/파운드", "pct"),
    ("btc", "비트코인", "BTC-USD", "24시간 거래 — 한국 시간 아침 값", "cmd", "달러", "pct"),
    ("eth", "이더리움", "ETH-USD", "24시간 거래 — 한국 시간 아침 값", "cmd", "달러", "pct"),
    ("vix", "VIX (공포지수)", "^VIX", "오르면 불안, 내리면 안심", "idx", "", "pct"),
]


def _item(key, name, desc, group, unit, how, rows):
    (d0, c0), (d1, c1) = rows[-2], rows[-1]
    chg = round(c1 - c0, 3) if how == "diff" else round((c1 / c0 - 1) * 100, 2)
    return {"key": key, "name": name, "desc": desc, "group": group, "unit": unit, "how": how, "date": d1, "value": round(c1, 3 if c1 < 10 else 2),
            "prev": round(c0, 3 if c0 < 10 else 2), "change": chg, "spark": [round(c, 3 if c < 10 else 2) for _, c in rows[-20:]]}


def main():
    now = datetime.now(KST)
    items, errors, raw = [], [], {}
    for key, name, sym, desc, group, unit, how in ITEMS:
        if not sym:
            continue
        try:
            rows = sources.yahoo_daily(sym)
        except Exception as e:
            log(f"  {name} 실패: {type(e).__name__}: {e}")
            errors.append(f"{name}: {type(e).__name__}")
            continue
        if len(rows) < 2:
            continue
        raw[key] = rows
        items.append(_item(key, name, desc, group, unit, how, rows))
        log(f"  {name} {rows[-1][0]} {rows[-1][1]:,.2f} ({items[-1]['change']:+.2f})")
    # 원/엔(100엔): 같은 날짜끼리 원/달러 ÷ 엔/달러 × 100
    if "usdkrw" in raw and "usdjpy" in raw:
        jp = dict(raw["usdjpy"])
        rows = [(d, k / jp[d] * 100) for d, k in raw["usdkrw"] if jp.get(d)]
        if len(rows) >= 2:
            spec = next(x for x in ITEMS if x[0] == "jpykrw")
            items.append(_item("jpykrw", spec[1], spec[3], spec[4], spec[5], spec[6], rows))
    order = {x[0]: i for i, x in enumerate(ITEMS)}
    items.sort(key=lambda x: order.get(x["key"], 99))
    fg = None
    try:
        fg = sources.cnn_fear_greed_full()
    except Exception as e:
        errors.append(f"CNN 공포·탐욕: {type(e).__name__}")
        log(f"  CNN 실패: {type(e).__name__}: {e}")
    out = {"at": now.isoformat(timespec="minutes"), "date": now.strftime("%Y%m%d"),
           "items": items, "summary": logic.morning_summary(items), "fg": fg, "errors": errors}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "morning.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"저장 morning.json: {out['summary']['text']}")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
