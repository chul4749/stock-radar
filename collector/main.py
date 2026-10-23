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

from . import logic, reindex, sources
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
    if os.environ.get("IF_MISSING") and os.path.exists(os.path.join(DATA, f"{base}.json")):
        log("  예비 실행: 이미 수집한 기준일이라 건너뜀")
        return 0

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
    quality = {
        "pension": pension_5d,
        "trust": safe("투신", lambda: sources.net_purchase(last5[0], base, "투신"), errors),
        "private": safe("사모", lambda: sources.net_purchase(last5[0], base, "사모"), errors),
        "fratio_now": safe("외국인 지분율", lambda: sources.foreign_ratio(base), errors),
        "fratio_prev": safe("외국인 지분율(20일 전)", lambda: sources.foreign_ratio(used_days[-21]), errors),
    }
    sectors = safe("업종", lambda: sources.sectors(base), errors)
    if all(len(fl["foreign"]) == 0 for _, fl in flows_daily):
        errors.append("일별 수급이 모두 비어 있음")

    t = step("뉴스·공시")
    iso = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    g_start = iso(used_days[-4])
    g_end = (datetime.strptime(base, "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    news = safe("구글 뉴스", lambda: sources.google_news(g_start, g_end), errors) or []
    log(f"  구글 뉴스 {len(news)} ({g_start} ~ {g_end} 전)")
    # 네이버 주요뉴스는 최신 목록만 주므로 기준일이 최근일 때만 쓴다
    backfill = (datetime.now(KST).date() - datetime.strptime(base, "%Y%m%d").date()).days > 3
    nv = [] if backfill else (safe("네이버 주요뉴스", sources.naver_main_news, errors) or [])
    log(f"  네이버 주요뉴스 {len(nv)}{' (과거 기준일이라 생략)' if backfill else ''}")
    dart = safe("DART", lambda: sources.dart_today(base), errors) or []
    log(f"  DART {len(dart)} (호재성 {sum(1 for x in dart if x['good'])})")
    all_items = news + nv + dart
    # 악재성 공시는 최근 5영업일을 본다 (기준일은 위에서 받음)
    dart_bad = [x for x in dart if x["bad"]]
    for d in used_days[-5:-1]:
        dart_bad += [x for x in (safe(f"DART {d}", lambda: sources.dart_today(d), errors) or []) if x["bad"]]

    matcher = logic.NameMatcher({c: v["name"] for c, v in universe.items()})
    base_dt = datetime.strptime(base, "%Y%m%d").replace(tzinfo=KST)
    prev_close = datetime.strptime(used_days[-2], "%Y%m%d").replace(hour=15, minute=30, tzinfo=KST)
    since_3d = datetime.strptime(used_days[-3], "%Y%m%d").replace(tzinfo=KST)
    until = base_dt.replace(hour=23, minute=59)
    news_today = logic.attach_news(all_items, matcher, prev_close, until)
    news_3d = logic.attach_news(all_items, matcher, since_3d, until)
    log(f"  기사 걸린 종목: 오늘 {len(news_today)} · 3일 {len(news_3d)}")

    risk = logic.risk_codes(dart_bad, matcher, {c: v["section"] for c, v in universe.items()})
    excluded = [{"code": c, "name": universe[c]["name"], "reasons": r[:3]} for c, r in risk.items() if c in universe]
    universe = {c: v for c, v in universe.items() if c not in risk}
    log(f"  위험 공시·투자주의환기로 제외 {len(excluded)}")

    t = step("종목 필터")
    scalp, swing = logic.screen(universe, closes, values, flows_daily, flows_5d, pension_5d,
                                news_today, news_3d)
    log(f"  단타 {len(scalp)} · 스윙 {len(swing)}")
    for x in swing[:5]:
        log(f"   스윙 {x['name']} {x['score']}")
    for x in scalp[:5]:
        log(f"   단타 {x['name']} {x['score']}")

    t = step("공포 속 저평가")
    value = []
    fund_now = safe("PER·PBR(기준일)", lambda: sources.fundamentals(base), errors)
    year_days = safe("1년 영업일", lambda: sources.business_days(base, 250), errors) or []
    fund_hist = []
    for d in [year_days[i] for i in (0, 62, 125, 187) if i < len(year_days)]:
        f = safe(f"PER·PBR({d})", lambda: sources.fundamentals(d), errors)
        if f is not None and len(f):
            fund_hist.append(f)
    if fund_now is not None and fund_hist:
        value = logic.value_screen(universe, closes, fund_now, fund_hist, flows_5d, news_3d)
    log(f"  저평가 {len(value)} (과거 스냅샷 {len(fund_hist)}개)")
    for x in value[:5]:
        log(f"   저평가 {x['name']} -{x['drop_60d']}% PBR {x['pbr']}/{x['pbr_med']}")
    overlap = logic.mark_overlap(scalp, swing, value)
    past = past_scores(base)
    for g, items in (("scalp", scalp), ("swing", swing), ("value", value)):
        logic.score_percentiles(items, past.get(g, []))
    log(f"  겹침 {len(overlap)}")
    for group in (scalp[:10], swing[:10], value[:10]):
        logic.enrich(group, closes, sectors, quality)
    sector_flow = logic.sector_flows(universe, sectors, flows_5d, closes)
    log(f"  업종 유입 상위 {[r['sector'] for r in sector_flow['inflow'][:5]]}")

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
        vix = [r for r in vix if r[0] < iso(base)]
        temp["vix"] = logic.series_stat(vix)
    hy = safe("하이일드", lambda: sources.fred("BAMLH0A0HYM2"), errors)
    hy = [r for r in hy if r[0] < iso(base)] if hy else hy
    if hy:
        temp["hy_spread"] = logic.series_stat(hy, days_back=20)
    temp["fear_greed"] = safe("CNN", lambda: sources.cnn_fear_greed(iso(base)), errors)
    uni_closes = closes[[c for c in universe if c in closes.columns]]
    b = logic.breadth(uni_closes, markets={c: v["market"] for c, v in universe.items()})
    temp["breadth"] = b
    flows = safe("시장 수급", lambda: sources.market_flows(used_days[-20], base), errors)
    if flows:
        temp["flows"] = {m: logic.flows_summary(df) for m, df in flows.items()}
    for key, sid in (("usdkrw", "DEXKOUS"), ("us10y", "DGS10")):
        rows = safe(sid, lambda: sources.fred(sid), errors)
        rows = [r for r in rows if r[0] < iso(base)] if rows else rows
        if rows:
            temp[key] = logic.series_stat(rows)
    # 신용잔고·반대매매: 금융투자협회 FreeSIS (T+1 공표라 기준일 전날까지가 최신). 1년 백분위를 위해 400일치
    kofia_from = (datetime.strptime(base, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")
    loans = safe("신용잔고", lambda: sources.kofia("STATSCU0100000070BO", kofia_from, base), errors)
    funds = safe("반대매매", lambda: sources.kofia("STATSCU0100000060BO", kofia_from, base), errors)
    temp["credit_balance"] = logic.credit_summary(loans, funds)
    temp["summary"] = logic.summarize_temperature(temp)
    temp["thermo"] = logic.thermometer(temp)
    kospi_long = safe("코스피 1년", lambda: sources.index_history("1001", year_ago, base), errors)
    if kospi_long is not None:
        temp["regime"] = logic.market_regime([float(v) for v in kospi_long.values], temp,
                                             dates=[d.strftime("%Y%m%d") for d in kospi_long.index])
    # 전일·1주 전·1달 전 온도: 이전 기록 파일에서
    prev_files = sorted(n[:8] for n in os.listdir(DATA) if n[:8].isdigit() and n.endswith(".json") and n[:8] < base)
    hist = {}
    for key, back in (("prev", 1), ("week", 5), ("month", 21)):
        if len(prev_files) >= back:
            d = prev_files[-back]
            with open(os.path.join(DATA, f"{d}.json"), encoding="utf-8") as f:
                old = json.load(f)["temperature"]
            th = old.get("thermo") or logic.thermometer(old)
            if th:
                hist[key] = {"date": d, "score": th["score"], "label": th["label"]}
    if temp["thermo"]:
        temp["thermo"]["history"] = hist

    out = {
        "version": 1,
        "base_date": base,
        "generated_at": datetime.now(KST).isoformat(timespec="minutes"),
        "backfill": backfill,
        "temperature": temp,
        "filter": {
            "rules": logic.RULES,
            "news_count": {"google": len(news), "naver": len(nv), "dart": len(dart),
                           "matched_today": len(news_today), "matched_3d": len(news_3d)},
            "scalp": scalp[:10], "scalp_total": len(scalp),
            "swing": swing[:10], "swing_total": len(swing),
            "value_rules": logic.VALUE_RULES,
            "value": value[:10], "value_total": len(value),
            "overlap": overlap,
            "excluded": excluded,
        },
        "sectors": sector_flow,
        "errors": errors,
    }
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"{base}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    dates = reindex.write_index()
    log(f"\n저장 {path} ({os.path.getsize(path) // 1024}KB), 오류 {errors or '없음'}")

    t = step("성적표")
    kospi = safe("코스피 기간", lambda: sources.index_history("1001", used_days[0], base), errors)
    if kospi is not None:
        kospi.index = [d.strftime("%Y%m%d") for d in kospi.index]
        history, regimes = [], {}
        # 장세는 그날 저장된 시장 온도 + 그날까지의 코스피로 다시 판정 (예전 기록에도 같은 규칙 적용)
        long_vals = kospi_long if kospi_long is not None else None
        long_days = [d.strftime("%Y%m%d") for d in long_vals.index] if long_vals is not None else []
        for d in dates:
            with open(os.path.join(DATA, f"{d}.json"), encoding="utf-8") as f:
                rec = json.load(f)
            flt = rec["filter"]
            history.append((d, {g: flt.get(g, []) for g in ("swing", "scalp", "value")}))
            if d in long_days:
                upto = [float(v) for v in long_vals.values[:long_days.index(d) + 1]]
                rg = logic.market_regime(upto, rec.get("temperature") or {})
                if rg:
                    regimes[d] = rg["key"]
        stats, recent, features, by_regime = logic.scorecard(history, closes, kospi, regimes)
        card = {"generated_at": out["generated_at"], "base_date": base, "days": len(history),
                "horizons": list(logic.HORIZONS), "stats": stats, "recent": recent, "features": features,
                "by_regime": by_regime, "regimes": regimes}
        with open(os.path.join(DATA, "scorecard.json"), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        log(f"  기록 {len(history)}일, 통계 {json.dumps(stats, ensure_ascii=False)}")
    return 0


def past_scores(base):
    """기준일 전 기록 파일들의 탭별 후보 점수 (파일엔 탭마다 상위 10개만 남아 있음)."""
    out = {}
    for n in os.listdir(DATA):
        if not (n[:8].isdigit() and n.endswith(".json") and n[:8] < base):
            continue
        with open(os.path.join(DATA, n), encoding="utf-8") as f:
            flt = json.load(f).get("filter", {})
        for g in ("scalp", "swing", "value"):
            out.setdefault(g, []).extend(x["score"] for x in flt.get(g) or [] if x.get("score") is not None)
    return out


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
