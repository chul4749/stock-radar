"""이미 수집한 기준일 파일에 시장 지표만 덧붙인다 (200일선 위·아래 종목 수, 내 지표 차트·검증, 급등 전조 찾기).
후보·기업 정보·기록 출처는 건드리지 않는다 — 다시 수집하면 그날 보던 '지금' 정보가 빠지기 때문.
실행: python -m collector.patch_market YYYYMMDD"""
import json
import os
import socket
import sys
from datetime import datetime, timedelta

import pandas as pd

from . import logic, sources
from .main import DATA, _json_default, _limit_requests_timeout, long_history, safe
from .util import log


def main(argv):
    socket.setdefaulttimeout(60)
    _limit_requests_timeout(60)
    base = argv[1]
    path = os.path.join(DATA, f"{base}.json")
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    errors = []
    days = sources.business_days(base, 82)
    daily = sources.daily_ohlcv(days)
    used = sorted(daily)
    closes = pd.DataFrame({d: daily[d]["종가"] for d in used}).T.astype(float).replace(0, float("nan"))
    chg = pd.DataFrame({d: daily[d]["등락률"] for d in used}).T.astype(float) if all("등락률" in daily[d] for d in used) else None
    values = pd.DataFrame({d: daily[d]["거래대금"] for d in used}).T.astype(float)
    long_closes, long_breaks, long_values = safe("긴 종가 기록", lambda: long_history(base, closes, chg, values), errors) or (None, None, None)
    listing = sources.listing(base)
    universe = {c: v for c, v in listing.items() if logic.is_common_stock(c, v["name"], v["section"])}
    temp = out.setdefault("temperature", {})
    if long_closes is not None:
        lc = long_closes[[c for c in universe if c in long_closes.columns]]
        temp["ma200"] = logic.ma_breadth(lc, long_breaks[lc.columns], {c: v["market"] for c, v in universe.items()})
        elig = {c for c, v in universe.items() if v["cap"] >= logic.RULES["min_cap"]}
        temp["tv_check"] = safe("내 지표 검증", lambda: logic.tv_signal_check(long_closes, long_breaks, eligible=elig), errors)
        if out.get("replay") is not None:
            out["replay"]["precursor"] = safe("급등 전조", lambda: logic.surge_precursors(long_closes, long_values, long_breaks, eligible=set(universe)), errors)
    start = (datetime.strptime(base, "%Y%m%d") - timedelta(days=1100)).strftime("%Y%m%d")
    k3 = safe("코스피 3년", lambda: sources.index_history("1001", start, base), errors)
    hourly = safe("코스피 시간봉", lambda: sources.yahoo_hourly("^KS11"), errors) or []
    hourly = [(t, c) for t, c in hourly if t.strftime("%Y%m%d") <= base]
    if k3 is not None:
        ks = pd.Series([float(v) for v in k3.values], index=[d.strftime("%Y%m%d") for d in k3.index])
        tfs = safe("내 지표 코스피", lambda: logic.tv_timeframes(ks, hourly), errors) or {}
        temp["tv"] = tfs.get("d")
        temp["tv_tf"] = tfs
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    for e in errors:
        log(f"  오류: {e}")
    log(f"  {base}: " + ", ".join(k for k in ("ma200", "tv", "tv_tf", "tv_check") if temp.get(k)))
    return 1 if not any(temp.get(k) for k in ("ma200", "tv")) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
