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
    opens = pd.DataFrame({d: daily[d]["시가"] for d in used_days}).T.astype(float).replace(0, float("nan"))
    lows = pd.DataFrame({d: daily[d]["저가"] for d in used_days}).T.astype(float).replace(0, float("nan"))
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
    # 실적 공시는 제목만으로 좋고 나쁨을 모르니 본문에서 영업이익 방향을 읽어, 줄었거나 적자면 재료에서 뺀다
    read_ok = 0
    for x in [x for x in dart if x.get("earnings")][:40]:
        try:
            e = logic.parse_earnings(sources.dart_document_text(x["url"].split("rcpNo=")[1]))
        except Exception as err:
            log(f"  실적 본문 {x['corp']} 실패: {type(err).__name__}")
            e = None
        if e:
            read_ok += 1
            x["earn"], x["earn_text"] = e["dir"], e["label"]
            if e["dir"] == "down":
                x["good"] = False
        time.sleep(0.3)
    log(f"  실적 공시 {sum(1 for x in dart if x.get('earnings'))}건 중 방향 읽음 {read_ok}건")
    log(f"  DART {len(dart)} (재료성 {sum(1 for x in dart if x['good'])})")
    all_items = news + nv + dart
    # 악재성 공시는 최근 5영업일을 본다 (기준일은 위에서 받음)
    dart_bad = [x for x in dart if x["bad"]]
    dart_days = {base: dart}
    for d in used_days[-5:-1]:
        dart_days[d] = safe(f"DART {d}", lambda: sources.dart_today(d), errors) or []
        dart_bad += [x for x in dart_days[d] if x["bad"]]
    season, reported = update_season(base, dart_days, {v["name"]: c for c, v in listing.items()}, errors)

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
        value = logic.value_screen(universe, closes, fund_now, fund_hist, flows_5d, news_3d, values=values)
    log(f"  저평가 {len(value)} (과거 스냅샷 {len(fund_hist)}개)")
    for x in value[:5]:
        log(f"   저평가 {x['name']} -{x['drop_60d']}% PBR {x['pbr']}/{x['pbr_med']}")
    # 기업 정보(네이버): 저평가 후보 전체 + 화면에 나갈 후보. 저평가는 추정 EPS를 점수에 반영
    profiles, failed = {}, 0

    def profile(code):
        nonlocal failed
        if code not in profiles:
            try:
                profiles[code] = logic.parse_profile(*sources.naver_profile(code))
                profiles[code]["as_of"] = datetime.now(KST).strftime("%Y-%m-%d")
            except Exception as e:
                profiles[code], failed = None, failed + 1
                log(f"  상세 {code} 실패: {type(e).__name__}")
            time.sleep(0.2)
        return profiles[code]

    for x in value:
        profile(x["code"])
    logic.apply_forward_eps(value, profiles)
    overlap = logic.mark_overlap(scalp, swing, value)
    # 며칠째 걸려 있는지: 기준일 전 기록을 최근 날짜부터 거슬러 본다
    prev = []
    for n in sorted((n for n in os.listdir(DATA) if n[:8].isdigit() and n.endswith(".json") and n[:8] < base), reverse=True)[:30]:
        with open(os.path.join(DATA, n), encoding="utf-8") as f:
            prev.append((n[:8], json.load(f).get("filter", {})))
    logic.mark_streaks({"scalp": scalp, "swing": swing, "value": value}, prev)
    past = past_scores(base)
    for g, items in (("scalp", scalp), ("swing", swing), ("value", value)):
        logic.score_percentiles(items, past.get(g, []))
    log(f"  겹침 {len(overlap)}")
    for group in (scalp[:10], swing[:10], value[:10]):
        logic.enrich(group, closes, sectors, quality)
    for x in value[:10]:   # 저평가도 상세 화면에서 일별 수급을 보이게
        x["flow"] = logic.flow_detail(x["code"], flows_daily, flows_5d, pension_5d)
    # 종목 상세(눌렀을 때)용 기업 정보: 화면에 나가는 후보만, 네이버 지금 시점 값
    for x in scalp[:10] + swing[:10] + value[:10]:
        if profile(x["code"]):
            x["profile"] = profiles[x["code"]]
    # 증권사 리포트(주목도: 오랜만에 다뤄지기 시작했나)와 네이버 검색 상위(이미 알려짐)
    reports = update_reports(base, errors)
    search_top = [] if backfill else (safe("검색 상위", sources.naver_search_top, errors) or [])
    rank = {c: i + 1 for i, c in enumerate(search_top)}
    details = {}
    for g, items in (("scalp", scalp[:10]), ("swing", swing[:10]), ("value", value[:10])):
        for x in items:
            att = logic.report_attention(x["code"], reports, base)
            if att and att["items"]:
                top = att["items"][0]
                if top["id"] not in details:
                    details[top["id"]] = safe(f"리포트 {top['id']}", lambda: sources.naver_research_detail(top["id"]), errors)
                    time.sleep(0.2)
                att["detail"] = details[top["id"]]
            x["reports"] = att
            if search_top:
                x["search_rank"] = rank.get(x["code"])
    for x in value[:10]:   # 리포트는 저평가 주목 신호에도 넣는다
        logic.add_report_wake(x)
    log(f"  리포트 기록 {len((reports or {}).get('items', []))}건 (since {(reports or {}).get('since')}), 상세 {len(details)}건, 검색 상위 {len(search_top)}")
    # 언제 정리할지(정리 가격)와 이번 실적 시즌 발표 여부
    for g, items in (("scalp", scalp[:10]), ("swing", swing[:10]), ("value", value[:10])):
        for x in items:
            if x["code"] in closes.columns:
                x["exit"] = logic.exit_level(g, closes[x["code"]], lows[x["code"]] if x["code"] in lows.columns else None)
            x["earn_season"] = logic.earnings_status(x["code"], base, season, reported)
    log(f"  기업 정보 {len(profiles) - failed}/{len(profiles)}")
    if profiles and failed == len(profiles):
        errors.append("기업 정보: 전부 실패")
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
        for m, v in temp["flows"].items():
            if not v:
                errors.append(f"시장 수급 {m}: 빈 응답")
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
    fut = safe("외국인 선물", lambda: sources.daum_futures(base), errors)
    temp["futures"] = logic.futures_summary(fut) if fut else None
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
    if temp.get("regime"):
        temp["transition"] = logic.regime_transition(temp["regime"])
        temp["signals"] = logic.entry_signals(temp)

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
            # 강도(역대 후보 중 위치) 계산용: 통과한 후보 전체의 점수. score_ver 2 = 저평가 새 공식, 3 = 단타 새 공식
            "all_scores": {g: [x["score"] for x in items] for g, items in (("scalp", scalp), ("swing", swing), ("value", value))},
            "score_ver": 3,
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
    write_prices(base, closes, listing, temp.get("index") or {})
    log(f"\n저장 {path} ({os.path.getsize(path) // 1024}KB), 오류 {errors or '없음'}")

    t = step("성적표")
    kospi = safe("코스피 기간", lambda: sources.index_history("1001", used_days[0], base), errors)
    if kospi is not None:
        kospi.index = [d.strftime("%Y%m%d") for d in kospi.index]
        history, regimes, backfill_dates, signal_levels = [], {}, [], {}
        # 장세는 그날 저장된 시장 온도 + 그날까지의 코스피로 다시 판정 (예전 기록에도 같은 규칙 적용)
        long_vals = kospi_long if kospi_long is not None else None
        long_days = [d.strftime("%Y%m%d") for d in long_vals.index] if long_vals is not None else []
        for d in dates:
            with open(os.path.join(DATA, f"{d}.json"), encoding="utf-8") as f:
                rec = json.load(f)
            flt = rec["filter"]
            # 기준일 이틀 뒤 이후에 계산된 기록 = 나중에 다시 계산한 날 (그날 실제로 보던 뉴스와 다를 수 있음)
            made = (rec.get("generated_at") or "")[:10].replace("-", "")
            if rec.get("backfill") or (made and (datetime.strptime(made, "%Y%m%d") - datetime.strptime(d, "%Y%m%d")).days >= 2):
                backfill_dates.append(d)
            groups = {g: flt.get(g, []) for g in ("swing", "scalp", "value")}
            # 리포트 기록은 150일 치가 있으니, 리포트를 저장하기 전 날짜의 후보에도 그날 기준으로 다시 붙여 조건별 성적에 쓴다
            for x in (y for items in groups.values() for y in items):
                if x.get("reports") is None and reports:
                    x["reports"] = logic.report_attention(x["code"], reports, d)
            history.append((d, groups))
            if d in long_days:
                upto = [float(v) for v in long_vals.values[:long_days.index(d) + 1]]
                rg = logic.market_regime(upto, rec.get("temperature") or {})
                if rg:
                    regimes[d] = rg["key"]
            sig = (rec.get("temperature") or {}).get("signals")
            if sig:
                signal_levels[d] = sig["level"]
        # 다음 날 시가에 사서 종목이 속한 시장 지수와 비교 (코스닥 종목은 코스닥 지수)
        bench = {}
        for mk, ticker in (("KOSPI", "1001"), ("KOSDAQ", "2001")):
            oc = safe(f"{mk} 시가·종가", lambda: sources.index_open_close(ticker, used_days[0], base), errors)
            if oc is not None:
                bench[mk] = oc
        stats, recent, features, by_regime, score_check = logic.scorecard(
            history, closes, kospi, regimes, opens=opens, bench=bench or None)
        stops = logic.stop_scorecard(history, closes, lows, opens=opens, bench=bench or None)
        card = {"generated_at": out["generated_at"], "base_date": base, "days": len(history), "stops": stops,
                "horizons": list(logic.HORIZONS), "stats": stats, "recent": recent, "features": features,
                "by_regime": by_regime, "regimes": regimes, "score_check": score_check,
                "method": {"entry": "다음 날 시가", "bench": "종목이 속한 시장 지수", "cost": logic.COST, "dedup": True},
                "backfill_dates": backfill_dates, "signal_levels": signal_levels}
        with open(os.path.join(DATA, "scorecard.json"), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        log(f"  기록 {len(history)}일, 통계 {json.dumps(stats, ensure_ascii=False)}")
    return 0


def update_season(base, dart_days, name_to_code, errors):
    """이번 실적 시즌에 실적을 낸 회사 목록(reported.json)을 갱신한다. 처음이면 시즌 시작일부터 DART 목록을 거슬러 받는다."""
    season = logic.report_season(base)
    path = os.path.join(DATA, "reported.json")
    try:
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    if not season:
        return None, store
    if store.get("season") and store["season"] > season["key"]:   # 지난 시즌 기준일을 다시 계산할 때는 건드리지 않음
        return season, {}
    store = logic.update_reported(store, season, [], name_to_code)
    days = [d for d in sources.business_days(base, 40) if season["start"] <= d <= base]
    todo = [d for d in days if d not in store["checked"] and d not in dart_days][-30:]
    for d in todo:
        dart_days[d] = safe(f"DART {d}", lambda: sources.dart_today(d), errors)
        if dart_days[d] is None:
            del dart_days[d]
    for d, items in dart_days.items():
        if items and d >= season["start"]:
            store = logic.update_reported(store, season, items, name_to_code)
            store["checked"] = sorted(set(store["checked"]) | {d})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  {season['label']} 실적 시즌: 발표 {len(store['reported'])}곳 · 예고 {len(store['notice'])}곳 (확인한 날 {len(store['checked'])}, 이번에 더 받은 날 {len(todo)})")
    return season, store


def update_reports(base, errors, keep_days=150):
    """증권사 리포트 기록(reports.json)을 이어 받는다. 처음이면 150일 전부터, 그 뒤엔 마지막 기준일 사흘 전부터."""
    path = os.path.join(DATA, "reports.json")
    try:
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    start = (datetime.strptime(base, "%Y%m%d") - timedelta(days=keep_days)).strftime("%Y%m%d")
    if store.get("updated") and store.get("since", "99999999") <= start:
        start = max(start, (datetime.strptime(min(store["updated"], base), "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d"))
    new = safe("증권사 리포트", lambda: sources.naver_research(start), errors)
    if new is None:
        return store
    store = logic.merge_reports(store, new, base, start, keep_days)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    return store


def write_prices(base, closes, listing, index):
    """관심 종목 성적용: 가장 최근 기준일의 전 종목 종가 (예전 기준일을 다시 계산할 때는 덮어쓰지 않음)."""
    path = os.path.join(DATA, "prices.json")
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("date", "") > base:
                return
    except (OSError, ValueError):
        pass
    last = closes.iloc[-1]
    px = {c: int(v) for c, v in last.items() if c in listing and v == v and v > 0}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": base, "close": px, "index": {k: v.get("close") for k, v in index.items()}},
                  f, ensure_ascii=False, separators=(",", ":"))


def past_scores(base):
    """기준일 전 기록 파일들의 탭별 후보 점수 (파일엔 탭마다 상위 10개만 남아 있음)."""
    out = {}
    for n in os.listdir(DATA):
        if not (n[:8].isdigit() and n.endswith(".json") and n[:8] < base):
            continue
        with open(os.path.join(DATA, n), encoding="utf-8") as f:
            flt = json.load(f).get("filter", {})
        for g in ("scalp", "swing", "value"):
            full = (flt.get("all_scores") or {}).get(g)
            if full is not None and flt.get("score_ver", 1) >= {"value": 2, "scalp": 3}.get(g, 1):
                out.setdefault(g, []).extend(full)
            else:  # 예전 파일: 상위 10개만, 저평가·단타는 지금 공식으로 다시 계산
                out.setdefault(g, []).extend(v for v in (logic.rescore(g, x) for x in flt.get(g) or []) if v is not None)
    return out


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
