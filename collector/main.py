"""하루치 시장 온도 + 종목 필터 결과를 site/data/{기준일}.json 으로 만든다.

실행: python -m collector.main [기준일 YYYYMMDD]
기준일을 안 주면 18시 이후는 오늘, 그 전은 전 영업일.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from . import logic, sources
from .util import KST, log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")


def pick_base_date(arg=None):
    if arg:
        return arg
    now = datetime.now(KST)
    d = now if now.hour >= 18 else now - timedelta(days=1)
    return d.strftime("%Y%m%d")


def step(name):
    log(f"\n== {name}")
    return time.time()


def safe(name, fn, errors):
    try:
        return fn()
    except Exception as e:
        log(f"  {name} 실패: {type(e).__name__}: {e}")
        errors.append(f"{name}: {type(e).__name__}")
        return None


def main(argv):
    errors = []
    wanted = pick_base_date(argv[1] if len(argv) > 1 else None)

    t = step("영업일")
    days = sources.business_days(wanted, 82)
    base = days[-1]
    log(f"  기준일 {base} (요청 {wanted}), 영업일 {len(days)}개 {days[0]}~{base}")

    t = step("전종목 목록")
    listing = sources.listing(base)
    if sum(v["value"] for v in listing.values()) == 0:
        log("  기준일 거래 없음 — 종료")
        return 0
    universe = {c: v for c, v in listing.items() if logic.is_common_stock(c, v["name"], v["section"])}
    log(f"  전체 {len(listing)}, 보통주 {len(universe)}")

    t = step("일별 시세 (약 80영업일)")
    daily = sources.daily_ohlcv(days)
    used_days = sorted(daily)
    closes = pd.DataFrame({d: daily[d]["종가"] for d in used_days}).T.astype(float)
    values = pd.DataFrame({d: daily[d]["거래대금"] for d in used_days}).T.astype(float)
    closes = closes.replace(0, float("nan"))
    log(f"  {len(used_days)}일, {time.time() - t:.0f}초")

    t = step("종목별 수급")
    last5 = used_days[-5:]
    flows_daily = []
    for d in last5:
        fl = {"foreign": sources.net_purchase(d, d, "외국인"), "inst": sources.net_purchase(d, d, "기관합계")}
        log(f"  {d}: 외국인 {len(fl['foreign'])} · 기관 {len(fl['inst'])}")
        flows_daily.append((d, fl))
    flows_5d = {"foreign": sources.net_purchase(last5[0], base, "외국인"),
                "inst": sources.net_purchase(last5[0], base, "기관합계")}
    pension_5d = safe("연기금", lambda: sources.net_purchase(last5[0], base, "연기금"), errors)
    if all(len(fl["foreign"]) == 0 for _, fl in flows_daily):
        errors.append("일별 수급이 모두 비어 있음")

    t = step("뉴스·공시")
    news = safe("구글 뉴스", sources.google_news, errors) or []
    log(f"  구글 뉴스 {len(news)}")
    nv = safe("네이버 주요뉴스", sources.naver_main_news, errors) or []
    log(f"  네이버 주요뉴스 {len(nv)}")
    dart = safe("DART", lambda: sources.dart_today(base), errors) or []
    log(f"  DART {len(dart)} (호재성 {sum(1 for x in dart if x['good'])})")
    all_items = news + nv + dart

    matcher = logic.NameMatcher({c: v["name"] for c, v in universe.items()})
    base_dt = datetime.strptime(base, "%Y%m%d").replace(tzinfo=KST)
    prev_close = datetime.strptime(used_days[-2], "%Y%m%d").replace(hour=15, minute=30, tzinfo=KST)
    since_3d = datetime.strptime(used_days[-3], "%Y%m%d").replace(tzinfo=KST)
    until = base_dt.replace(hour=23, minute=59)
    news_today = logic.attach_news(all_items, matcher, prev_close, until)
    news_3d = logic.attach_news(all_items, matcher, since_3d, until)
    log(f"  기사 걸린 종목: 오늘 {len(news_today)} · 3일 {len(news_3d)}")

    t = step("종목 필터")
    scalp, swing = logic.screen(universe, closes, values, flows_daily, flows_5d, pension_5d,
                                news_today, news_3d)
    log(f"  단타 {len(scalp)} · 스윙 {len(swing)}")
    for x in swing[:5]:
        log(f"   스윙 {x['name']} {x['score']}")
    for x in scalp[:5]:
        log(f"   단타 {x['name']} {x['score']}")

    t = step("시장 온도")
    temp = {}
    year_ago = (datetime.strptime(base, "%Y%m%d") - timedelta(days=420)).strftime("%Y%m%d")
    k200 = safe("코스피200", lambda: sources.index_history("1028", year_ago, base), errors)
    if k200 is not None:
        temp["korea_vol"] = logic.korea_vol(k200)
    idx = {}
    for key, ticker in (("kospi", "1001"), ("kosdaq", "2001")):
        s = safe(key, lambda: sources.index_history(ticker, used_days[-21], base), errors)
        if s is not None and len(s) >= 2:
            idx[key] = {"close": logic.r1(s.iloc[-1]), "change": logic.r1((s.iloc[-1] / s.iloc[-2] - 1) * 100),
                        "change_20d": logic.r1((s.iloc[-1] / s.iloc[0] - 1) * 100),
                        "spark": [logic.r1(v) for v in s.values]}
    temp["index"] = idx
    vix = safe("VIX", sources.vix, errors)
    if vix:
        # 기준일(한국) 이전 미국 장 마감값까지만
        vix = [r for r in vix if r[0] <= f"{base[:4]}-{base[4:6]}-{base[6:]}"]
        temp["vix"] = logic.series_stat(vix)
    hy = safe("하이일드", lambda: sources.fred("BAMLH0A0HYM2"), errors)
    if hy:
        temp["hy_spread"] = logic.series_stat(hy, days_back=20)
    temp["fear_greed"] = safe("CNN", sources.cnn_fear_greed, errors)
    uni_closes = closes[[c for c in universe if c in closes.columns]]
    b = logic.breadth(uni_closes)
    temp["breadth"] = b
    flows = safe("시장 수급", lambda: sources.market_flows(used_days[-20], base), errors)
    if flows:
        temp["flows"] = {m: logic.flows_summary(df) for m, df in flows.items()}
    temp["credit_balance"] = None  # 신용잔고·반대매매: 수집 경로 미확인
    temp["summary"] = logic.summarize_temperature(temp)

    out = {
        "version": 1,
        "base_date": base,
        "generated_at": datetime.now(KST).isoformat(timespec="minutes"),
        "temperature": temp,
        "filter": {
            "rules": logic.RULES,
            "news_count": {"google": len(news), "naver": len(nv), "dart": len(dart),
                           "matched_today": len(news_today), "matched_3d": len(news_3d)},
            "scalp": scalp[:10], "scalp_total": len(scalp),
            "swing": swing[:10], "swing_total": len(swing),
        },
        "errors": errors,
    }
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"{base}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    dates = sorted(n[:8] for n in os.listdir(DATA) if n[:8].isdigit() and n.endswith(".json"))
    with open(os.path.join(DATA, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": dates[::-1], "latest": dates[-1]}, f, ensure_ascii=False)
    log(f"\n저장 {path} ({os.path.getsize(path) // 1024}KB), 오류 {errors or '없음'}")
    return 0


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
