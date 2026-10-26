"""하루치 시장 온도 + 종목 필터 결과를 site/data/{기준일}.json 으로 만든다.

실행: python -m collector.main [기준일 YYYYMMDD]
기준일을 안 주면 18시 이후는 오늘, 그 전은 전 영업일.
"""
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from . import etf, logic, reindex, sources
from .util import EOK, KST, log

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


def _limit_requests_timeout(sec):
    import requests
    if getattr(requests.Session.request, "_radar_timeout", False):
        return
    orig = requests.Session.request

    def request(self, method, url, **kw):
        if kw.get("timeout") is None:
            kw["timeout"] = sec
        return orig(self, method, url, **kw)

    request._radar_timeout = True
    requests.Session.request = request


def main(argv):
    # 시간 제한 없이 부르는 라이브러리(pykrx 등)가 끊긴 연결에서 15분 넘게 멈추던 것 막기: 응답 없으면 60초에 실패 → 재시도.
    # requests는 timeout=None이면 socket 기본값도 무시하고 무한정 기다리므로, Session.request에서 직접 기본값을 넣는다
    socket.setdefaulttimeout(60)
    _limit_requests_timeout(60)
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
    highs = pd.DataFrame({d: daily[d]["고가"] for d in used_days}).T.astype(float).replace(0, float("nan"))
    log(f"  {len(used_days)}일, {time.time() - t:.0f}초")

    t = step("종목별 수급")
    last5 = used_days[-5:]
    flows_daily = []
    for d in last5:
        fl = {"foreign": sources.net_purchase(d, d, "외국인"), "inst": sources.net_purchase(d, d, "기관합계")}
        log(f"  {d}: 외국인 {len(fl['foreign'])} · 기관 {len(fl['inst'])}")
        flows_daily.append((d, fl))
    # 급등주 복기·신호 검증용: 그 전 20영업일도 날마다 (총 25일)
    flows_hist = []
    for d in used_days[-25:-5]:
        fl = safe(f"수급 {d}", lambda: {"foreign": sources.net_purchase(d, d, "외국인"), "inst": sources.net_purchase(d, d, "기관합계")}, errors)
        if fl:
            flows_hist.append((d, fl))
    flows_hist += flows_daily
    log(f"  복기용 일별 수급 {len(flows_hist)}일")
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
    dart = safe("DART", lambda: sources.dart_today(base), errors)
    dart_failed = dart is None
    dart = dart or []
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
        got = safe(f"DART {d}", lambda: sources.dart_today(d), errors)
        dart_failed = dart_failed or got is None
        dart_days[d] = got or []
        dart_bad += [x for x in dart_days[d] if x["bad"]]
    if dart_failed:
        dart_days = {d: v for d, v in dart_days.items() if v}   # 못 받은 날은 '확인한 날'로 치지 않음
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
    if dart_failed:
        # DART를 못 받은 날은 위험 공시 제외가 비게 되므로, 바로 전 기록의 제외 목록을 이어서 쓴다
        prev = sorted(n[:8] for n in os.listdir(DATA) if n[:8].isdigit() and n.endswith(".json") and n[:8] < base)
        if prev:
            with open(os.path.join(DATA, f"{prev[-1]}.json"), encoding="utf-8") as f:
                carried = (json.load(f).get("filter") or {}).get("excluded") or []
            for e in carried:
                if e["code"] not in risk:
                    risk[e["code"]] = list(e.get("reasons") or []) + [f"{prev[-1][4:6]}/{prev[-1][6:]} 기록에서 이어옴 (DART 접속 실패)"]
            log(f"  DART 실패 → {prev[-1]} 제외 목록 {len(carried)}개 이어서 씀")
    excluded = [{"code": c, "name": universe[c]["name"], "reasons": r[:3]} for c, r in risk.items() if c in universe]
    universe_all = universe
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
    # 전 종목 재무(검색 화면용, fin/ — data-cache 브랜치에 보관): 아직 없는 종목은 전부, 이미 있으면 오래된 것부터 FIN_BATCH개.
    # 여러 개를 동시에 받되 FIN_BUDGET초가 넘으면 남은 건 다음 날로 (예전 기준일을 다시 계산할 땐 안 받음)
    fin_cache = load_fin_cache()
    stale = sorted((c for c in universe_all if len(c) == 6 and c not in profiles),
                   key=lambda c: ((fin_cache.get(c) or {}).get("as_of") or "", -universe_all[c]["cap"]))
    missing = sum(1 for c in stale if c not in fin_cache)
    todo = [] if backfill else stale[:max(FIN_BATCH, missing)]
    got, t0 = fetch_profiles(todo)
    profiles.update(got)
    log(f"  전 종목 재무: {len(got)}/{len(todo)}개 받음 ({time.time() - t0:.0f}초), 캐시에 없던 것 {missing}개")
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
    attention = update_attention(base, search_top)
    dart_sig = update_dart_signals(base, used_days, errors) if not backfill else load_dart_signals()
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
    save_fin_cache(fin_cache, profiles, base)
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

    t = step("업종·테마 ETF")
    themes = build_themes(base, used_days, closes, flows_daily, flows_5d, listing, reports, temp.get("regime"), errors, flows_hist)
    log(f"  테마 {len(themes['themes'])}개: " + ", ".join(f"{x['name']}({x['key']})" for x in themes["themes"][:6]))

    t = step("급등주 복기·신호 검증")
    replay = build_replay(base, closes, values, flows_hist, listing, universe_all, {"scalp": scalp, "swing": swing, "value": value}, reports, opens, highs, lows, reported, attention,
                          {d.strftime("%Y%m%d"): float(v) for d, v in kospi_long.items()} if kospi_long is not None else None, dart_sig)
    log(f"  급등주 {len(replay['movers'])}개: " + ", ".join(f"{m['name']}({m['verdict']})" for m in replay["movers"][:8]))

    t = step("운용사 방향 (액티브 ETF)")
    managers = build_managers(base, used_days, listing, closes, errors, sectors=sectors, trust=quality.get("trust"))
    mg20 = managers["moves"].get("20") or {"buys": [], "sells": []}
    mg_of = {}
    for side in ("buys", "sells"):
        for r in mg20[side]:
            mg_of.setdefault(r["code"], {"buy": [], "sell": []})["buy" if side == "buys" else "sell"] = r["managers"]
    for x in scalp[:10] + swing[:10] + value[:10]:
        x["managers"] = mg_of.get(x["code"])
    log(f"  액티브 ETF {len(managers['etfs'])}개, 20일 매수 {len(mg20['buys'])}종목(2곳 이상 {sum(1 for r in mg20['buys'] if r['n'] >= 2)}) · 매도 {len(mg20['sells'])}종목")

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
        "themes": themes,
        "managers": managers,
        "replay": replay,
        "errors": errors,
    }
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"{base}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    dates = reindex.write_index()
    write_prices(base, closes, listing, temp.get("index") or {})
    # 전체 종목 검색용: 조건별 통과 여부 (가장 최근 기준일만)
    picked = {}
    for g, items in (("swing", swing), ("scalp", scalp), ("value", value)):
        for x in items:
            picked.setdefault(x["code"], []).append(g)
    idx = logic.stock_index(universe_all, {c: r for c, r in risk.items()}, closes, values, flows_daily, flows_5d,
                            news_today, news_3d, fund_now, fund_hist, sectors, picked)
    write_chart_shards(base, closes, logic.net_frame(flows_hist), idx["stocks"], highs, lows, values)
    # 검색 팝업·카드: 최근 10영업일 OpenDART '사는' 공시 (임원·주요주주 매수, 5% 큰손 장내매수, 자사주 취득 결정)
    recent_ds = {}
    for x in (dart_sig or {}).get("events") or []:
        if x.get("code") and x["date"] >= used_days[-10]:
            recent_ds.setdefault(x["code"], []).append({"k": x["kind"], "d": x["date"], "w": x.get("who"), "r": x.get("rate")})
    for c, v in recent_ds.items():
        if c in idx["stocks"]:
            idx["stocks"][c]["ds"] = v[-5:]
    for g, items in (("scalp", scalp), ("swing", swing), ("value", value)):
        for x in items:
            if x["code"] in recent_ds:
                x["dart_sig"] = recent_ds[x["code"]][-5:]
    for c, m in mg_of.items():   # 검색 화면에도 '운용사가 20일 동안 산·판' 표시
        if c in idx["stocks"]:
            idx["stocks"][c]["mg"] = m
    write_latest("stocks.json", base, {**idx, "date": base, "generated_at": out["generated_at"]})
    log(f"  전체 종목 요약 {len(idx['stocks'])}개")
    log(f"\n저장 {path} ({os.path.getsize(path) // 1024}KB), 오류 {errors or '없음'}")

    t = step("성적표")
    kospi = safe("코스피 기간", lambda: sources.index_history("1001", used_days[0], base), errors)
    if kospi is not None:
        kospi.index = [d.strftime("%Y%m%d") for d in kospi.index]
        history, regimes, backfill_dates, signal_levels, theme_recs, mover_recs = [], {}, [], {}, [], {}
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
            # 운용사 여러 곳(2곳 이상)이 20일 동안 같이 산 종목도 같은 방식으로 성적을 본다
            picks = (rec.get("managers") or {}).get("picks")
            if picks:
                groups["mgr"] = picks
            # 리포트 기록은 150일 치가 있으니, 리포트를 저장하기 전 날짜의 후보에도 그날 기준으로 다시 붙여 조건별 성적에 쓴다
            for x in (y for items in groups.values() for y in items):
                if x.get("reports") is None and reports:
                    x["reports"] = logic.report_attention(x["code"], reports, d)
            history.append((d, groups))
            theme_recs.append((d, rec.get("themes")))
            if d in long_days:
                upto = [float(v) for v in long_vals.values[:long_days.index(d) + 1]]
                rg = logic.market_regime(upto, rec.get("temperature") or {})
                if rg:
                    regimes[d] = rg["key"]
            for m in (rec.get("replay") or {}).get("movers") or []:
                mover_recs[(m["code"], m["surge"])] = m   # 같은 급등은 여러 날 나와도 한 번 (가장 나중 기록)
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
        stops = logic.stop_scorecard(history, closes, lows, opens=opens, bench=bench or None, index_closes=kospi)
        card = {"generated_at": out["generated_at"], "base_date": base, "days": len(history), "stops": stops,
                "themes": logic.theme_scorecard(theme_recs),
                "movers": logic.mover_scorecard(list(mover_recs.values())),
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


def build_replay(base, closes, values, flows_hist, listing, universe, today, reports, opens=None, highs=None, lows=None,
                 reported=None, attention=None, kospi_idx=None, dart_sig=None):
    """최근 급등주를 거슬러 보고(언제 어떤 신호가 먼저 있었나·우리가 언제 잡았나), 신호별로 급등을 앞섰는지 잰다."""
    net = logic.net_frame(flows_hist)
    caps = {c: v["cap"] for c, v in listing.items()}
    tab = {"swing": "스윙", "scalp": "단타", "value": "저평가"}
    cand_by_day = {base: {}}
    for g, items in today.items():
        for x in items:
            cand_by_day[base].setdefault(x["code"], []).append(tab[g])
    for n in sorted(os.listdir(DATA)):
        if n[:8].isdigit() and n.endswith(".json") and n[:8] < base and n[:8] >= closes.index[-logic.SURGE["lookback"]]:
            with open(os.path.join(DATA, n), encoding="utf-8") as f:
                flt = json.load(f).get("filter") or {}
            day = cand_by_day.setdefault(n[:8], {})
            for g in tab:
                for x in flt.get(g) or []:
                    day.setdefault(x["code"], []).append(tab[g])
    movers = []
    for c in logic.find_movers(closes, universe):
        try:
            movers.append(logic.replay_mover(c, listing[c]["name"], closes, values, net, caps, cand_by_day, reports))
        except Exception as e:
            log(f"  복기 {c} 실패: {type(e).__name__}: {e}")
    elig = {c for c, v in universe.items() if v["cap"] >= logic.RULES["min_cap"] and v["value"] >= logic.RULES["min_value"]}
    try:
        val = logic.signal_validation(closes, values, net, caps, elig)
    except Exception as e:
        log(f"  신호 검증 실패: {type(e).__name__}: {e}")
        val = None
    try:
        follow = logic.follow_through(closes, opens, elig, highs=highs, values=values, net=net, caps=caps) if opens is not None else None
    except Exception as e:
        log(f"  급등 뒤 흐름 실패: {type(e).__name__}: {e}")
        follow = None
    try:
        hold = logic.hold_after_surge(closes, opens, lows, elig, values=values) if opens is not None and lows is not None else None
    except Exception as e:
        log(f"  급등 뒤 보유 기록 실패: {type(e).__name__}: {e}")
        hold = None
    if follow is not None:
        follow["hold"] = hold
    try:
        markets = {c: {"KOSPI": "P", "KOSDAQ": "D"}.get(v.get("market"), "") for c, v in listing.items()}
        myths = (logic.myth_check(closes, opens, highs, lows, values, elig, reports, markets=markets, caps=caps,
                                  reported=(reported or {}).get("reported"), attention=attention, index=kospi_idx,
                                  dart_events=(dart_sig or {}).get("events"))
                 if opens is not None and highs is not None and lows is not None else None)
        if myths:
            log("  속설 검증: " + ", ".join(f"{x['title']}({x['verdict']}, {x['n']}건)" for x in myths["items"]))
    except Exception as e:
        log(f"  속설 검증 실패: {type(e).__name__}: {e}")
        myths = None
    return {"movers": movers, "validation": val, "follow": follow, "myths": myths, "rules": logic.SURGE}


def build_managers(base, days, listing, closes, errors, sectors=None, trust=None):
    """액티브 ETF마다 오늘 구성과 5·20영업일 전 구성을 비교해 운용사가 사고판 종목을 모은다 (collector/etf.py ACTIVE)."""
    windows = {"5": days[-6], "20": days[-21]} if len(days) >= 21 else {}
    known = set(listing)
    per = {w: [] for w in windows}
    etfs, fails = [], 0
    for mgr, code, name in etf.ACTIVE:
        try:
            now = sources.etf_pdf(code, base)
        except Exception as e:
            fails += 1
            log(f"  {name} 구성 실패: {type(e).__name__}")
            continue
        if not now:
            continue
        entry = {"manager": mgr, "code": code, "name": name, "n": len(now),
                 "top": [[c, n, round(w, 1)] for c, (n, _, w) in sorted(now.items(), key=lambda kv: -kv[1][2])[:10]]}
        # 운용사 실력: 이 ETF의 20·60일 등락 (KODEX 200과 비교는 아래에서)
        try:
            h = sources.etf_history(code, days[-61] if len(days) >= 61 else days[0], base)["종가"]
            entry["r20"] = logic.r1((h.iloc[-1] / h.iloc[-21] - 1) * 100) if len(h) >= 21 else None
            entry["r60"] = logic.r1((h.iloc[-1] / h.iloc[-61] - 1) * 100) if len(h) >= 61 else None
        except Exception:
            entry["r20"] = entry["r60"] = None
        for w, d in windows.items():
            try:
                before = sources.etf_pdf(code, d)
            except Exception:
                before = None
            mv = logic.holdings_diff(now, before) if before else None
            entry["moves_" + w] = mv[:20] if mv is not None else None   # None = 그때는 상장 전이거나 자료 없음
            if mv is not None:
                per[w].append({"manager": mgr, "code": code, "name": name, "moves": mv})
            time.sleep(0.15)
        etfs.append(entry)
    if etf.ACTIVE and fails > len(etf.ACTIVE) / 2:
        errors.append(f"액티브 ETF 구성: {fails}/{len(etf.ACTIVE)} 실패")
    bench = safe("KODEX 200", lambda: sources.etf_history("069500", days[-61] if len(days) >= 61 else days[0], base)["종가"], errors)
    b20 = logic.r1((bench.iloc[-1] / bench.iloc[-21] - 1) * 100) if bench is not None and len(bench) >= 21 else None
    b60 = logic.r1((bench.iloc[-1] / bench.iloc[-61] - 1) * 100) if bench is not None and len(bench) >= 61 else None
    for e in etfs:
        e["b20"], e["b60"] = b20, b60
    skill = logic.manager_skill([e for e in etfs if e.get("r20") is not None and b20 is not None])
    moves = {}
    for w in windows:
        r = logic.manager_moves(per[w], known)
        moves[w] = {"buys": r["buys"], "sells": r["sells"]}
    if "5" in moves:
        logic.enrich_manager_moves(moves["5"], None, closes, windows["5"], sectors, trust, skill)
    if "20" in moves:
        logic.enrich_manager_moves(moves["20"], moves.get("5"), closes, windows["20"], sectors, trust, skill)
    tilt = logic.sector_tilt(moves.get("20") or {}, sectors)
    for w in moves:
        moves[w] = {"buys": moves[w]["buys"][:30], "sells": moves[w]["sells"][:30]}
    # 성적표용: 20일 동안 2곳 이상이 같이 산 종목 (그날 종가·시장과 함께)
    picks = []
    for r in (moves.get("20") or {}).get("buys", []):
        if r["n"] >= 2 and r["code"] in closes.columns and r["code"] in listing:
            picks.append({"code": r["code"], "name": r["name"], "close": float(closes[r["code"]].iloc[-1]),
                          "market": listing[r["code"]]["market"], "score": r["n"]})
    return {"windows": windows, "etfs": etfs, "moves": moves, "picks": picks, "skill": skill, "tilt": tilt, "bench": {"r20": b20, "r60": b60}}


def build_themes(base, days, closes, flows_daily, flows_5d, listing, reports, regime, errors, flows_hist=None):
    """테마마다 ETF 흐름(KRX 시세)과 ETF 구성 종목의 큰손 돈 흐름으로 판정 (collector/etf.py 표)."""
    start = days[-40] if len(days) >= 40 else days[0]
    bench = {}
    for code, name in etf.BENCH:
        h = safe(f"ETF {name}", lambda: sources.etf_history(code, start, base), errors)
        if h is not None:
            bench[code] = h["종가"]
    kospi_etf = bench.get("069500")
    caps = {c: v["cap"] for c, v in listing.items()}
    out, fails, total = [], 0, 0
    heat_src = {}   # 히트맵용: 테마별 구성 종목과 대표 ETF 일별 등락
    for name, etfs in etf.THEMES:
        rows, members = [], {}
        main_ret = None
        for code, ename in etfs:
            total += 1
            tr = None
            try:
                h = sources.etf_history(code, start, base)
                tr = logic.etf_trend(h["종가"], kospi_etf)
                if tr:
                    tr["value_eok"] = round(float(h["거래대금"].iloc[-1]) / EOK)
                    if main_ret is None:
                        main_ret = h["종가"].pct_change() * 100
            except Exception as e:
                fails += 1
                log(f"  ETF {ename} 시세 실패: {type(e).__name__}")
            try:
                hold = sources.etf_holdings(code, base)
            except Exception as e:
                log(f"  ETF {ename} 구성 실패: {type(e).__name__}")
                hold = []
            for c, n, w in hold:
                members.setdefault(c, n)
            rows.append({"code": code, "name": ename, "trend": tr, "top": [[c, n, round(w, 1)] for c, n, w in hold[:10]]})
            time.sleep(0.2)
        th = logic.theme_strength(name, rows, members, caps, closes, flows_daily, flows_5d, reports, base, regime)
        if th:
            out.append(th)
            heat_src[name] = (list(members), main_ret)
    if total and fails > total / 2:
        errors.append(f"ETF 시세: {fails}/{total} 실패")
    order = {"early": 0, "strong": 1, "watch": 2, "hot": 3, "fading": 4, "weak": 5}
    out.sort(key=lambda x: (order[x["key"]], -x["flow_pct"]))
    heat = logic.theme_heat(heat_src, logic.net_frame(flows_hist or []), caps, days=20)
    return {"themes": out, "bench": {c: logic.etf_trend(sr) for c, sr in bench.items()}, "heat": heat}


def load_dart_signals():
    try:
        with open(os.path.join(DATA, "dart_signals.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"v": 1, "checked": [], "events": []}


def update_dart_signals(base, days, errors, keep_days=150, max_detail=400):
    """OpenDART로 '사는' 공시(임원·주요주주 매수, 5% 큰손 장내매수, 자기주식 취득 결정)를 날마다 쌓는다 (dart_signals.json).
    처음엔 최근 영업일(3개월 안)까지 거슬러 받고, 회사별 상세 호출이 한도(max_detail)에 걸리면 남은 날은 다음 실행에서 이어 받음."""
    store = load_dart_signals()
    if not sources.opendart_key():
        return store
    checked = set(store.get("checked") or [])
    oldest = (datetime.strptime(base, "%Y%m%d") - timedelta(days=88)).strftime("%Y%m%d")
    todo = sorted(d for d in days if d >= oldest and d <= base and d not in checked)
    if not todo:
        return store
    try:
        events, calls, stopped = sources.opendart_signals(todo[0], todo[-1], max_detail=max_detail)
    except Exception as e:
        errors.append(f"OpenDART 매수 공시: {type(e).__name__}")
        log(f"  OpenDART 매수 공시 실패: {type(e).__name__}: {e}")
        return store
    done = [d for d in todo if stopped is None or d > stopped]
    seen = {x["rcept"] for x in store["events"]}
    store["events"] += [x for x in events if x["rcept"] not in seen and (stopped is None or x["date"] > stopped or x["kind"] == "buyback")]
    cut = (datetime.strptime(base, "%Y%m%d") - timedelta(days=keep_days)).strftime("%Y%m%d")
    store["events"] = sorted((x for x in store["events"] if x["date"] >= cut), key=lambda x: x["rcept"])
    store["checked"] = sorted(d for d in checked | set(done) if d >= cut)
    with open(os.path.join(DATA, "dart_signals.json"), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    kinds = {k: sum(1 for x in events if x["kind"] == k) for k in ("insider", "major", "buyback")}
    log(f"  OpenDART 매수 공시 {todo[0]}~{todo[-1]}: 임원·주요주주 매수 {kinds['insider']} · 5% 큰손 장내매수 {kinds['major']} · 자사주 취득 결정 {kinds['buyback']}"
        f" (상세 {calls}번{f', {stopped} 이전은 다음에' if stopped else ''})")
    return store


def update_attention(base, search_top, keep_days=150):
    """네이버 검색 상위 100을 날마다 쌓는다 (attention.json) — '모두가 볼 때는 늦었다' 속설을 나중에 검증하려고."""
    path = os.path.join(DATA, "attention.json")
    try:
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    if search_top:
        store[base] = list(search_top)
    cut = (datetime.strptime(base, "%Y%m%d") - timedelta(days=keep_days)).strftime("%Y%m%d")
    store = {d: v for d, v in sorted(store.items()) if d >= cut}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    return store


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


FIN_BATCH, FIN_WORKERS, FIN_BUDGET = 300, 4, 900


def fetch_profiles(codes):
    """네이버 기업 정보를 FIN_WORKERS개씩 동시에 받는다. 실패는 빼고, FIN_BUDGET초가 지나면 남은 건 건너뜀."""
    t0 = time.time()

    def one(code):
        if time.time() - t0 > FIN_BUDGET:
            return code, None
        try:
            p = logic.parse_profile(*sources.naver_profile(code))
            p["as_of"] = datetime.now(KST).strftime("%Y-%m-%d")
            return code, p
        except Exception:
            return code, None
        finally:
            time.sleep(0.2)

    with ThreadPoolExecutor(FIN_WORKERS) as ex:
        return {c: p for c, p in ex.map(one, codes) if p}, t0
CHART_DAYS, NET_DAYS = 60, 20


SHARDS = 16


def _shard(code):
    """코드 글자 합 % 16 (보통주 코드는 거의 0으로 끝나 끝자리로 나누면 한 파일에 몰림). site/app.js shardOf와 같아야 함."""
    return str(sum(map(ord, code)) % SHARDS)


def _clear_json(d):
    for n in os.listdir(d):
        if n.endswith(".json"):
            os.remove(os.path.join(d, n))


def write_chart_shards(base, closes, net, stocks, highs=None, lows=None, values=None):
    """전 종목 60일 종가 + 20일 큰손 순매수(억) + 60일 매물대를 _shard로 16개 파일에 나눠 쓴다 (charts/, git 밖, 최신 기준일만)."""
    d = os.path.join(DATA, "charts")
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(d):
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                if json.load(f).get("date", "") > base:
                    return
        except (OSError, ValueError):
            pass
        break
    days = list(closes.index[-CHART_DAYS:])
    ndays = list(net.index[-NET_DAYS:]) if net is not None and not net.empty else []
    _clear_json(d)
    shards = {}
    for c in stocks:
        if c not in closes.columns:
            continue
        col = closes[c].iloc[-CHART_DAYS:]
        rec = {"c": [None if v != v else round(float(v)) for v in col]}
        if ndays and c in net.columns:
            rec["n"] = [None if v != v else round(float(v) / EOK) for v in net[c].reindex(ndays)]
        if highs is not None and lows is not None and values is not None and c in highs.columns and c in lows.columns and c in values.columns:
            vp = logic.volume_profile(highs[c].iloc[-CHART_DAYS:], lows[c].iloc[-CHART_DAYS:], col, values[c].iloc[-CHART_DAYS:])
            if vp:
                rec["vp"] = vp
        shards.setdefault(_shard(c), {})[c] = rec
    for k, v in shards.items():
        with open(os.path.join(d, f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": base, "days": days, "net_days": ndays, "stocks": v}, f, ensure_ascii=False, separators=(",", ":"))


def load_fin_cache():
    out = {}
    d = os.path.join(DATA, "fin")
    if os.path.isdir(d):
        for n in os.listdir(d):
            try:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    out.update(json.load(f).get("stocks") or {})
            except (OSError, ValueError):
                pass
    return out


def save_fin_cache(cache, profiles, base):
    """이번에 받은 기업 정보(후보 + 돌아가며 받은 종목)를 재무 캐시에 합쳐 끝자리별로 나눠 쓴다."""
    for c, p in profiles.items():
        if p:
            cache[c] = p
    d = os.path.join(DATA, "fin")
    os.makedirs(d, exist_ok=True)
    _clear_json(d)
    shards = {}
    for c, p in cache.items():
        shards.setdefault(_shard(c), {})[c] = p
    for k, v in shards.items():
        with open(os.path.join(d, f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": base, "stocks": v}, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  재무 캐시 저장 {len(cache)}개")


def write_latest(name, base, obj):
    """가장 최근 기준일 것만 남기는 파일 (예전 기준일을 다시 계산할 때는 덮어쓰지 않음)."""
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("date", "") > base:
                return
    except (OSError, ValueError):
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), default=_json_default)


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
