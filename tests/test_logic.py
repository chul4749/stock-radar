import math
from datetime import datetime

import pandas as pd

from collector import logic
from collector.util import EOK, KST


def test_matcher_prefers_longest_name():
    m = logic.NameMatcher({"003550": "LG", "066570": "LG전자", "000660": "SK하이닉스", "034730": "SK"})
    assert m.find("LG전자, 북미 공급계약") == ["066570"]
    assert m.find("SK하이닉스 신고가… SK 지주도 강세") == ["000660", "034730"]


def test_matcher_english_word_boundary():
    m = logic.NameMatcher({"030200": "KT"})
    assert m.find("KTX 증편 소식") == []
    assert m.find("KT, AI 데이터센터 수주") == ["030200"]


def test_matcher_skips_stopwords():
    m = logic.NameMatcher({"001680": "대상", "005930": "삼성전자"})
    assert m.find("수혜 대상 확대에 삼성전자 강세") == ["005930"]


def test_matcher_short_hangul_names_need_boundary():
    m = logic.NameMatcher({"026960": "동서", "000000": "효성"})
    assert m.find("동서대학교 앵커사업단") == []
    assert m.find("동서, 3분기 실적 개선") == ["026960"]
    assert m.find("동서는 배당 확대") == ["026960"]
    assert m.find("나동서 씨") == []


def test_common_stock_filter():
    assert logic.is_common_stock("005930", "삼성전자")
    assert not logic.is_common_stock("005935", "삼성전자우")
    assert not logic.is_common_stock("123450", "하나스팩30호")
    assert not logic.is_common_stock("123450", "어떤회사", "관리종목(소속부없음)")


def test_attach_news_drops_list_articles_and_window():
    m = logic.NameMatcher({"A00000": "가나전자", "B00000": "다라화학", "C00000": "마바건설", "D00000": "사아바이오"})
    t0 = datetime(2026, 10, 21, 15, 30, tzinfo=KST)
    t1 = datetime(2026, 10, 22, 23, 59, tzinfo=KST)
    items = [
        {"title": "가나전자 1조 공급계약", "at": "2026-10-22T09:10+09:00", "kind": "뉴스"},
        {"title": "가나전자 어제 기사", "at": "2026-10-21T10:00+09:00", "kind": "뉴스"},
        {"title": "가나전자·다라화학·마바건설·사아바이오 급등", "at": "2026-10-22T10:00+09:00", "kind": "뉴스"},
        {"corp": "다라화학", "title": "단일판매ㆍ공급계약체결", "good": True, "at": "2026-10-22T16:00+09:00", "kind": "공시"},
        {"corp": "마바건설", "title": "유상증자결정", "good": False, "at": "2026-10-22T16:00+09:00", "kind": "공시"},
    ]
    got = logic.attach_news(items, m, t0, t1)
    assert set(got) == {"A00000", "B00000"}
    assert len(got["A00000"]) == 1


def _market(n_days=70, codes=("A00000", "B00000")):
    days = [f"2026{(i // 28) + 7:02d}{(i % 28) + 1:02d}" for i in range(n_days)]
    return days, codes


def test_screen_swing_and_scalp():
    days, codes = _market()
    # A: 꾸준히 오르는 종목, B: 내리는 종목
    closes = pd.DataFrame({"A00000": [10000 * (1.003 ** i) for i in range(len(days))],
                           "B00000": [10000 * (0.997 ** i) for i in range(len(days))]}, index=days)
    values = pd.DataFrame({"A00000": [20 * EOK] * (len(days) - 1) + [80 * EOK],
                           "B00000": [20 * EOK] * len(days)}, index=days)
    universe = {
        "A00000": {"name": "가나전자", "market": "KOSPI", "section": "", "close": closes["A00000"].iloc[-1],
                   "change": 3.5, "value": 80 * EOK, "cap": 2000 * EOK},
        "B00000": {"name": "다라화학", "market": "KOSDAQ", "section": "", "close": closes["B00000"].iloc[-1],
                   "change": -1.0, "value": 20 * EOK, "cap": 2000 * EOK},
    }
    pos = pd.Series({"A00000": 1 * EOK, "B00000": -1 * EOK})
    flows_daily = [(d, {"foreign": pos, "inst": pos}) for d in days[-5:]]
    flows_5d = {"foreign": pos * 5, "inst": pos * 5}
    news = {"A00000": [{"title": "가나전자 수주"}], "B00000": [{"title": "다라화학 계약"}]}
    scalp, swing = logic.screen(universe, closes, values, flows_daily, flows_5d, pos * 5, news, news)
    assert [x["code"] for x in swing] == ["A00000"]
    assert [x["code"] for x in scalp] == ["A00000"]
    assert swing[0]["flow"]["days_pos"] == 5 and swing[0]["flow"]["streak"] == 5


def test_screen_requires_news():
    days, _ = _market()
    closes = pd.DataFrame({"A00000": [10000 * (1.003 ** i) for i in range(len(days))]}, index=days)
    values = pd.DataFrame({"A00000": [80 * EOK] * len(days)}, index=days)
    universe = {"A00000": {"name": "가나전자", "market": "KOSPI", "section": "", "close": 1,
                           "change": 3.5, "value": 80 * EOK, "cap": 2000 * EOK}}
    pos = pd.Series({"A00000": 1 * EOK})
    scalp, swing = logic.screen(universe, closes, values, [(d, {"foreign": pos, "inst": pos}) for d in days[-5:]],
                                {"foreign": pos, "inst": pos}, pos, {}, {})
    assert scalp == [] and swing == []


def test_breadth_counts_new_lows():
    days = [str(i) for i in range(70)]
    closes = pd.DataFrame({"A": list(range(70, 0, -1)), "B": list(range(1, 71))}, index=days, dtype=float)
    b = logic.breadth(closes)
    assert b["up"] == 1 and b["down"] == 1
    assert b["new_lows"][-1] == 1 and b["new_highs"][-1] == 1


def test_breadth_by_market():
    closes = pd.DataFrame({"A": [1, 2], "B": [2, 1], "C": [1, 3]}, dtype=float)
    b = logic.breadth(closes, window=2, markets={"A": "KOSPI", "B": "KOSPI", "C": "KOSDAQ"})
    assert b["up"] == 2 and b["down"] == 1
    assert b["by_market"] == {"KOSPI": {"up": 1, "down": 1}, "KOSDAQ": {"up": 1, "down": 0}}


def test_pct_rank_and_summary():
    assert logic.pct_rank([1, 2, 3, 4], 4) == 88
    t = {"vix": {"pct_1y": 90}, "korea_vol": {"pct_1y": 80}, "fear_greed": {"score": 20},
         "breadth": {"new_lows": [10, 10, 10, 10, 10, 50]},
         "hy_spread": {"value": 3.3, "pct_1y": 40, "change": 0.1}}
    s = {x["key"]: x for x in logic.summarize_temperature(t)}
    assert s["fear"]["level"] == "큼"
    assert s["selling"]["level"] == "커지는 중"
    assert s["credit"]["level"] == "안정"


def _value_setup(drop=0.7, pbr_now=0.6, eps_now=900):
    days = [f"d{i:03d}" for i in range(80)]
    # 50일째 고점 뒤로 30% 하락
    path = [10000 + 100 * i for i in range(50)] + [14900 * (1 - (1 - drop) * k / 29) for k in range(30)]
    closes = pd.DataFrame({"A00000": path}, index=days)
    universe = {"A00000": {"name": "가나전자", "market": "KOSPI", "section": "", "close": path[-1],
                           "change": -1.0, "value": 30 * EOK, "cap": 3000 * EOK}}
    now = pd.DataFrame({"PBR": [pbr_now], "PER": [8.0], "EPS": [eps_now]}, index=["A00000"])
    hist = [pd.DataFrame({"PBR": [p], "PER": [10.0], "EPS": [1000.0]}, index=["A00000"]) for p in (1.0, 1.1, 0.9, 1.0)]
    flows = {"foreign": pd.Series({"A00000": 2 * EOK}), "inst": pd.Series({"A00000": -1 * EOK})}
    return universe, closes, now, hist, flows


def test_value_screen_passes_cheap_stock_with_steady_eps():
    u, c, now, hist, fl = _value_setup()
    got = logic.value_screen(u, c, now, hist, fl, {})
    assert [x["code"] for x in got] == ["A00000"]
    assert got[0]["drop_60d"] >= 20 and got[0]["pbr_med"] == 1.0 and got[0]["flow5_eok"] == 1.0


def test_value_screen_rejects_falling_earnings_or_not_cheap():
    u, c, now, hist, fl = _value_setup(eps_now=500)
    assert logic.value_screen(u, c, now, hist, fl, {}) == []
    u, c, now, hist, fl = _value_setup(pbr_now=0.95)
    assert logic.value_screen(u, c, now, hist, fl, {}) == []
    u, c, now, hist, fl = _value_setup(drop=0.95)
    assert logic.value_screen(u, c, now, hist, fl, {}) == []


def test_overlap_marks_both_groups():
    swing = [{"code": "A"}, {"code": "B"}]
    value = [{"code": "B"}, {"code": "C"}]
    assert logic.mark_overlap([], swing, value) == ["B"]
    assert [x["overlap"] for x in swing] == [False, True]


def test_scorecard_returns_and_excess():
    days = ["d0", "d1", "d2", "d3", "d4", "d5", "d6"]
    closes = pd.DataFrame({"A": [100, 110, 110, 110, 110, 120, 120.0]}, index=days)
    kospi = pd.Series([1000, 1000, 1000, 1000, 1000, 1050, 1050.0], index=days)
    hist = [("d0", {"swing": [{"code": "A", "name": "가", "close": 100.0}], "scalp": [], "value": []}),
            ("d6", {"swing": [{"code": "A", "name": "가", "close": 120.0}], "scalp": [], "value": []})]
    stats, recent, feats, _, _ = logic.scorecard(hist, closes, kospi, cost=0)
    assert stats["swing"]["1"] == {"n": 1, "avg": 10.0, "win": 100, "excess": 10.0}
    assert stats["swing"]["5"]["avg"] == 20.0 and stats["swing"]["5"]["excess"] == 15.0
    assert "20" not in stats["swing"]
    assert recent[0]["date"] == "d6" and "r1" not in recent[0]
    streak = next(f for f in feats if f["key"] == "streak3")
    assert streak["yes"] is None and streak["no"]["n"] == 1 and streak["no"]["avg"] == 15.0  # 코스피 +5% 대비 초과


def test_risk_codes_and_enrich():
    m = logic.NameMatcher({"A00000": "가나전자", "B00000": "다라화학"})
    items = [{"corp": "가나전자", "title": "주요사항보고서(유상증자결정)", "bad": True, "at": "2026-10-20T16:00+09:00"},
             {"corp": "다라화학", "title": "단일판매ㆍ공급계약체결", "bad": False, "at": "2026-10-20T16:00+09:00"}]
    risk = logic.risk_codes(items, m, {"B00000": "투자주의환기종목"})
    assert set(risk) == {"A00000", "B00000"} and "유상증자" in risk["A00000"][0]
    days = [f"d{i:02d}" for i in range(70)]
    closes = pd.DataFrame({"A00000": [100.0 + i for i in range(70)]}, index=days)
    x = logic.enrich([{"code": "A00000"}], closes, pd.Series({"A00000": "반도체"}),
                     {"pension": pd.Series({"A00000": 3 * EOK}), "fratio_now": pd.Series({"A00000": 12.5}),
                      "fratio_prev": pd.Series({"A00000": 12.0})})[0]
    assert x["sector"] == "반도체" and len(x["chart"]["close"]) == 60
    assert x["quality"]["pension_5d"] == 3.0 and x["quality"]["fratio_chg"] == 0.5 and x["quality"]["trust_5d"] == 0


def test_sector_flows_groups_and_sorts():
    days = [f"d{i:02d}" for i in range(10)]
    codes = ["A", "B", "C", "D", "E", "F"]
    closes = pd.DataFrame({c: [100.0 + i for i in range(10)] for c in codes}, index=days)
    universe = {c: {"name": c + "사"} for c in codes}
    sec = pd.Series({"A": "조선", "B": "조선", "C": "조선", "D": "은행", "E": "은행", "F": "은행"})
    flows = {"foreign": pd.Series({"A": 5 * EOK, "B": 1 * EOK, "C": 1 * EOK, "D": -2 * EOK, "E": -2 * EOK, "F": -2 * EOK}),
             "inst": pd.Series(dtype=float)}
    got = logic.sector_flows(universe, sec, flows, closes)
    assert got["inflow"][0]["sector"] == "조선" and got["inflow"][0]["flow_eok"] == 7
    assert got["inflow"][0]["top"][0] == "A사"
    assert got["outflow"][0]["sector"] == "은행" and got["outflow"][0]["flow_eok"] == -6


def test_realized_vol_flat_is_zero():
    s = pd.Series([100.0] * 30)
    assert logic.realized_vol(s).dropna().iloc[-1] == 0
    assert not math.isnan(logic.realized_vol(pd.Series([100.0 * (1.01 ** i) for i in range(30)])).iloc[-1])


def test_thermometer_averages_parts_and_labels():
    t = {"korea_vol": {"value": 30, "pct_1y": 90}, "vix": {"value": 25, "pct_1y": 80},
         "fear_greed": {"score": 20}, "breadth": {"new_highs": [10], "new_lows": [90]},
         "index": {"kospi": {"change_20d": -10.0}}, "flows": {"KOSPI": {"pos_days20": 5, "days20": 20}},
         "hy_spread": {"value": 4, "pct_1y": 95}}
    th = logic.thermometer(t)
    scores = {p["key"]: p["score"] for p in th["parts"]}
    assert scores == {"korea_vol": 10, "vix": 20, "cnn": 20, "breadth": 10, "momentum": 20, "flows": 25, "credit": 5}
    assert th["score"] == 16 and th["label"] == "극단적 공포"
    assert logic.thermometer({"vix": {"value": 1, "pct_1y": 1}}) is None
    assert logic.thermo_label(50) == ("중립", "neutral") and logic.thermo_label(80)[0] == "극단적 탐욕"


def test_credit_summary_units_and_ratio():
    loans = [{"TMPV1": f"202610{d:02d}", "TMPV2": 30000000 + d * 100000, "TMPV3": 23000000, "TMPV4": 7000000} for d in range(1, 11)]
    funds = [{"TMPV1": f"202610{d:02d}", "TMPV5": 1000000, "TMPV6": 10000 * d, "TMPV7": d / 10} for d in range(1, 11)]
    c = logic.credit_summary(loans, funds)
    assert c["loan"]["value"] == 310000.0 and c["loan"]["date"] == "2026-10-10"
    assert c["loan"]["change"] == 5000.0 and c["loan"]["kospi"] == 230000.0
    assert c["forced"]["value"] == 1000.0 and c["forced"]["unpaid"] == 10000.0
    assert c["forced"]["ratio"] == 1.0 and c["forced"]["avg20"] == 500.0  # 오늘(1000억) 뺀 앞 9일 평균
    assert logic.credit_summary(None, None) is None


def test_forced_selling_spike_raises_selling_pressure():
    b = {"new_lows": [100] * 6, "new_highs": [100] * 6, "up": 1, "down": 1}
    calm = {"breadth": b, "credit_balance": {"forced": {"value": 300, "avg20": 250, "pct_1y": 50}}}
    s = {x["key"]: x for x in logic.summarize_temperature(calm)}
    assert s["selling"]["level"] == "비슷함" and "(반대매매) 300억" in s["selling"]["text"]
    spike = {"breadth": b, "credit_balance": {"forced": {"value": 900, "avg20": 300, "pct_1y": 98}}}
    s = {x["key"]: x for x in logic.summarize_temperature(spike)}
    assert s["selling"]["level"] == "커지는 중" and "3.0배" in s["selling"]["text"]


def test_thermometer_includes_credit_parts():
    t = {"vix": {"value": 20, "pct_1y": 50}, "korea_vol": {"value": 20, "pct_1y": 50},
         "fear_greed": {"score": 50},
         "credit_balance": {"forced": {"value": 900, "pct_1y": 90}, "loan": {"value": 302710, "pct_1y": 30}}}
    parts = {p["key"]: p["score"] for p in logic.thermometer(t)["parts"]}
    assert parts["forced"] == 10 and "loan" not in parts


def _kospi(trend, n=80, start=3000.0):
    return [start * (1 + trend) ** i for i in range(n)]


def test_regime_up_down_range():
    calm = {"thermo": {"score": 60}}
    assert logic.market_regime(_kospi(0.003), calm)["key"] == "up"
    assert logic.market_regime(_kospi(-0.002), {"thermo": {"score": 40}})["key"] == "down"
    flat = [3000 + (10 if i % 2 else -10) for i in range(80)]
    assert logic.market_regime(flat, calm)["key"] == "range"
    assert logic.market_regime(_kospi(0.003, n=30), calm) is None


def test_regime_panic_blocks_inverse_chase():
    t = {"thermo": {"score": 20}, "flows": {"KOSPI": {"pos_days20": 5, "days20": 20}},
         "breadth": {"new_lows": [300], "new_highs": [50]}, "hy_spread": {"pct_1y": 95}}
    r = logic.market_regime(_kospi(-0.01), t)
    assert r["key"] == "panic" and r["inverse"]["met"] == 5
    assert r["inverse"]["verdict"] == "caution" and r["fit"]["swing"]["grade"] == "bad"
    t["thermo"]["score"] = 35
    r = logic.market_regime(_kospi(-0.002), t)
    assert r["key"] == "down" and r["inverse"]["verdict"] == "consider"


def test_scorecard_splits_by_regime():
    days = [f"2026100{i}" for i in range(1, 8)]
    closes = pd.DataFrame({"A": [100, 101, 102, 103, 104, 105, 106]}, index=days, dtype=float)
    kospi = pd.Series([1000.0] * 7, index=days)
    hist = [(days[0], {"swing": [{"code": "A", "name": "a", "close": 100.0}]}),
            (days[1], {"value": [{"code": "A", "name": "a", "close": 101.0}]})]
    _, _, _, by, _ = logic.scorecard(hist, closes, kospi, {days[0]: "up", days[1]: "panic"}, cost=0)
    assert by["up"]["days"] == 1 and by["up"]["groups"]["swing"]["5"]["n"] == 1
    assert by["panic"]["groups"]["value"]["1"]["avg"] == 1.0


def test_regime_explains_decision_and_chart():
    t = {"thermo": {"score": 35}}
    r = logic.market_regime(_kospi(-0.002, n=200), t, dates=[f"d{i}" for i in range(200)])
    assert r["key"] == "down" and "하락장" in r["reason"] and "20일 평균 가격" in r["next"]
    assert [c["ok"] for c in r["trend_checks"]] == [False, False, False]
    assert len(r["chart"]["close"]) == 120 and r["chart"]["ma60"][-1] == r["trend"]["ma60"]
    assert r["fit"]["swing"]["evidence"] and r["fit"]["swing"]["source"]
    t["thermo"]["score"] = 20
    r = logic.market_regime(_kospi(-0.002, n=200), t)
    # 극단적 공포 하나만으로는 패닉이 아님 (두 개 이상)
    assert r["key"] == "down" and r["panic_met"] and "하나 더" in r["next"] and r["inverse"]["blockers"]
    t["korea_vol"] = {"pct_1y": 95}
    r = logic.market_regime(_kospi(-0.002, n=200), t)
    assert r["key"] == "panic" and r["trend_key"] == "down" and "하락장" in r["next"] and "chart" not in r


def test_score_percentiles_common_scale():
    items = [{"score": 1.1}, {"score": 0.2}]
    logic.score_percentiles(items, [0.1, 0.3, 0.5, 0.9])
    assert items[0]["score_pct"] == 92 and items[0]["score_pool"] == 6
    assert items[1]["score_pct"] == 25


def test_value_score_weights_fundamentals_not_just_drop():
    # 많이 빠지고 거품 꺼진 종목 vs 덜 빠졌지만 PER 낮고 이익 늘어난 종목
    crash, _ = logic.value_score(45, 0.3, 14, 0.85)
    solid, parts = logic.value_score(25, 0.75, 5, 1.6)
    assert solid > crash
    assert parts == {"drop": 17, "cheap": 10, "per": 100, "eps": 100}
    assert logic.value_score(50, 0.2, 5, 1.5)[0] == 100.0


def test_scorecard_score_halves():
    days = [f"2026100{i}" for i in range(1, 8)]
    closes = pd.DataFrame({"A": [100, 100, 100, 100, 100, 110, 110], "B": [100, 100, 100, 100, 100, 95, 95]},
                          index=days, dtype=float)
    kospi = pd.Series([1000.0] * 7, index=days)
    hist = [(days[0], {"swing": [{"code": "A", "name": "a", "close": 100.0, "score": 2.0},
                                 {"code": "B", "name": "b", "close": 100.0, "score": 1.0}]})]
    *_, check = logic.scorecard(hist, closes, kospi, cost=0)
    assert check["swing"]["hi"]["5"]["avg"] == 10.0 and check["swing"]["lo"]["5"]["avg"] == -5.0


def test_parse_profile_naver_shapes():
    integ = {"totalInfos": [{"code": "marketValue", "value": "21조 3,172억"}, {"code": "per", "value": "15.19배"},
                            {"code": "dividendYieldRatio", "value": "3.10%"}, {"code": "lowPriceOf52Weeks", "value": "17,910"}]}
    fin = {"corporationSummary": "HMM은 컨테이너선과 벌크선을 운영하는 해운사.",
           "financeInfo": {"trTitleList": [{"isConsensus": "N", "title": "2025.12.", "key": "202512"},
                                           {"isConsensus": "Y", "title": "2026.12.", "key": "202612"}],
                           "rowList": [{"title": "영업이익", "columns": {"202512": {"value": "14,612"}, "202612": {"value": "-"}}}]}}
    p = logic.parse_profile(integ, fin)
    assert p["cap_eok"] == 213172 and p["per"] == 15.19 and p["div_yield"] == 3.1 and p["low52"] == 17910
    assert p["summary"].startswith("HMM은")
    assert p["annual"]["years"][1] == {"label": "2026.12 (추정)", "est": True}
    assert p["annual"]["rows"] == [{"title": "영업이익", "values": [14612.0, None]}]
    assert logic.parse_profile({}, {})["summary"] is None


def test_scorecard_next_open_market_bench_dedup_cost():
    days = [f"d{i}" for i in range(8)]
    # A(코스닥): 기준일 종가 100, 다음 날 시가 110(갭), 5일째 종가 121
    closes = pd.DataFrame({"A": [100, 112, 115, 118, 119, 121, 121, 121.0]}, index=days)
    opens = pd.DataFrame({"A": [100, 110, 112, 115, 118, 119, 121, 121.0]}, index=days)
    kospi = pd.Series([1000.0] * 8, index=days)
    kq_open = pd.Series([800, 808, 800, 800, 800, 800, 800, 800.0], index=days)
    kq_close = pd.Series([800, 800, 800, 800, 800, 824, 824, 824.0], index=days)
    a = {"code": "A", "name": "가", "close": 100.0, "market": "KOSDAQ"}
    hist = [("d0", {"scalp": [a]}), ("d1", {"scalp": [{**a, "close": 112.0}]})]
    stats, recent, *_ = logic.scorecard(hist, closes, kospi, opens=opens,
                                        bench={"KOSDAQ": (kq_open, kq_close)}, cost=0.2)
    s5 = stats["scalp"]["5"]
    # 다음 날 시가 110에 사서 121에 팜: +10% − 0.2%. 코스닥은 808 → 824 (+1.98%)
    assert s5["n"] == 1 and s5["avg"] == 9.8 and s5["excess"] == round(9.8 - (824 / 808 - 1) * 100, 1)
    # 하루 뒤 다시 걸린 같은 종목은 5일 성적에 중복으로 세지 않음 (1일 성적은 따로 셈)
    assert stats["scalp"]["1"]["n"] == 2
    assert next(e for e in recent if e["date"] == "d1")["dup"] == [5]


def test_forward_eps_reorders_value():
    base = {"drop_60d": 30, "pbr": 1.0, "pbr_med": 1.6, "per": 8, "eps_ratio": 1.2}
    a = {**base, "code": "A"}
    b = {**base, "code": "B"}
    a["score"], a["score_parts"] = logic.value_score(30, 1 / 1.6, 8, 1.2)
    b["score"], b["score_parts"] = logic.value_score(30, 1 / 1.6, 8, 1.2)
    # A는 올해 이익이 반토막 날 전망, B는 늘어날 전망
    out = logic.apply_forward_eps([a, b], {"A": {"eps": 1000, "cns_eps": 500}, "B": {"eps": 1000, "cns_eps": 1600}})
    assert [x["code"] for x in out] == ["B", "A"]
    assert a["eps_fwd_ratio"] == 0.5 and a["score_parts"]["eps"] == 29
    assert logic.rescore("value", a) == a["score"]


def test_futures_summary_and_inverse_check():
    rows = [(f"202610{d:02d}", -1000 if d % 2 else 400, 0, 0, 900.0) for d in range(1, 23)]
    f = logic.futures_summary(rows)
    assert f["today"] == 400 and f["d5"] == -1000 * 2 + 400 * 3 and len(f["cum20"]) == 20
    assert f["today_eok"] == round(400 * 900 * 250000 / 1e8) and f["streak"] == 1 and len(f["daily"]) == 20
    assert f["d20_eok"] == sum(x["foreign_eok"] for x in f["daily"]) and f["up_days20"] == 10
    t = {"thermo": {"score": 35}, "futures": f, "flows": {"KOSPI": {"pos_days20": 5, "days20": 20}},
         "breadth": {"new_lows": [300], "new_highs": [50]}, "hy_spread": {"pct_1y": 95}}
    r = logic.market_regime(_kospi(-0.002), t)
    assert r["inverse"]["met"] == 6 and r["inverse"]["need"] == 5 and r["inverse"]["verdict"] == "consider"


def test_futures_isin_candidates():
    from collector import sources
    assert sources._isin_check("KR700593000") == "3"
    assert sources._isin_check("KR4A017C000") == "3"


def test_scalp_score_prefers_confirmed_over_overheated():
    hot, _ = logic.scalp_score(28, 29.5, 0.02)      # 거래 폭발 + 상한가 근처 + 큰손 거의 없음
    solid, parts = logic.scalp_score(6, 9, 0.25)    # 적당한 급등 + 큰손 순매수 비중 25%
    assert solid > hot and parts["flow"] == 100 and parts["move"] == 100
    x = {"value_mult": 6, "change": 9, "value_eok": 100, "flow": {"foreign_today": 15, "inst_today": 10}}
    assert logic.rescore("scalp", x) == solid


def test_thermometer_halves_overlapping_price_signals():
    t = {"vix": {"value": 20, "pct_1y": 50}, "korea_vol": {"value": 20, "pct_1y": 50}, "fear_greed": {"score": 50},
         "index": {"kospi": {"change_20d": -20}}, "breadth": {"new_lows": [100], "new_highs": [0]}}
    th = logic.thermometer(t)
    # 50·50·50 (무게 1) + 0·0 (무게 0.5씩) → (150 + 0) / 4 = 37.5 → 38 (같은 무게였다면 30)
    assert th["score"] == 38


def test_scalp_needs_meaningful_big_hand_buying():
    assert logic.RULES["scalp_min_net_ratio"] == 2.0


def test_parse_earnings_from_dart_viewer_text():
    up = "영업이익 | 당해실적 | 8,972 | 1,398 | 541.67 | - | 2,830 | 217.05 | - | 누계실적 | 10,306"
    down = "영업이익 | 당해실적 | 4,815 | 47,954 | -90.0 | - | 10,765 | -55.3 | - | 누계실적 | 63,071"
    turn = "영업이익 | 당해실적 | 1,176 | 301 | 290.7 | - | -2,626 | - | 흑자전환 | 누계실적 | 225"
    loss = "영업이익 | 당해실적 | -500 | 300 | - | 적자전환 | 200 | - | 적자전환 | 누계실적 | 1"
    assert logic.parse_earnings(up)["label"] == "영업이익 전년 같은 분기 대비 +217%"
    assert logic.parse_earnings(down)["dir"] == "down"
    assert logic.parse_earnings(turn)["label"] == "영업이익 흑자 전환"
    assert logic.parse_earnings(loss)["label"] == "영업이익 적자 전환"
    assert logic.parse_earnings("관계없는 문서") is None


def test_mark_streaks_counts_consecutive_days():
    today = {"value": [{"code": "A"}, {"code": "B"}], "swing": [{"code": "A"}]}
    prev = [("20261022", {"value": [{"code": "A"}], "swing": []}),
            ("20261021", {"value": [{"code": "A"}, {"code": "B"}]}),
            ("20261020", {"value": [{"code": "A"}]})]
    logic.mark_streaks(today, prev)
    a, b = today["value"]
    assert (a["streak_days"], a["since"]) == (4, "20261020")
    assert (b["streak_days"], b["since"]) == (1, None)   # 어제 빠졌다가 다시 들어오면 새로 시작
    assert today["swing"][0]["streak_days"] == 1


def _signal_temp(close_above=False, rising=False):
    rg = logic.market_regime(_kospi(-0.002, n=80), {"thermo": {"score": 30}})
    rg["trend"].update({"close": 6500 if close_above else 6000, "ma20": 6400, "ma20_rising": rising,
                        "ma20_slope": 0.5 if rising else -1.0})
    return {"regime": rg,
            "breadth": {"new_lows": [300, 300, 300, 300, 300, 150], "new_highs": [10] * 6},
            "credit_balance": {"forced": {"value": 100, "avg20": 200}},
            "korea_vol": {"value": 30, "spark": [40, 45, 50, 48, 44, 40, 36, 33, 31, 30]},
            "futures": {"streak": 4, "d5": 3000, "d5_eok": 6000},
            "flows": {"KOSPI": {"foreign": {"d5": 500}, "inst": {"d5": 200}}},
            "thermo": {"score": 40, "history": {"week": {"score": 25}}}}


def test_entry_signals_levels():
    t = _signal_temp()
    sig = logic.entry_signals(t)
    assert sig["level"] == 2 and sig["stages"][0]["met"] == 3 and "추세 확인" in sig["next"]
    t = _signal_temp(close_above=True, rising=True)
    assert logic.entry_signals(t)["level"] == 3
    t = _signal_temp()
    t["breadth"]["new_lows"][-1] = 500
    t["credit_balance"]["forced"]["value"] = 900
    assert logic.entry_signals(t)["level"] == 0      # 1단계를 못 넘으면 뒤 단계가 맞아도 0


def test_regime_transition_distance():
    rg = logic.market_regime(_kospi(-0.002, n=80), {"thermo": {"score": 30}})
    tr = logic.regime_transition(rg)
    assert tr["to"] == "횡보·전환" and tr["items"][0]["ok"] is False and tr["gap20"] > 0


def test_flow_detail_counts_streak_and_daily():
    d = lambda f, i: {"foreign": pd.Series({"A": f}), "inst": pd.Series({"A": i})}
    daily = [("20261021", d(-1e8, 0)), ("20261022", d(2e8, 1e8)), ("20261023", d(0, 5e8))]
    five = {"foreign": pd.Series({"A": 1e8}), "inst": pd.Series({"A": 6e8})}
    fl = logic.flow_detail("A", daily, five, None)
    assert fl["streak"] == 2 and fl["days_pos"] == 2 and fl["inst_today"] == 5.0
    assert [x["sum"] for x in fl["daily"]] == [-1.0, 3.0, 5.0] and fl["pension_5d"] == 0.0


def test_value_wake_counts_attention_signals():
    col = pd.Series([100.0] * 50 + [80.0] * 9 + [86.0])
    vals = pd.DataFrame({"A": [10.0] * 55 + [20.0] * 5})
    w = logic.value_wake("A", col, 3e8, True, vals)
    assert w["total"] == 4 and w["met"] == 4
    w = logic.value_wake("A", pd.Series([100.0] * 60), -1e8, False, None)
    assert w["total"] == 3 and w["met"] == 0


def test_value_feature_stats_skip_unknown_days():
    e = lambda wake, x: {"group": "value", "x5": x, "features": {"v_flow": True, "v_news": False, "v_wake2": wake}}
    fs = logic.feature_stats([e(None, 1.0), e(True, 2.0), e(False, -1.0)], groups=("value",), features=logic.VALUE_FEATURES)
    w2 = next(f for f in fs if f["key"] == "v_wake2")
    assert w2["yes"]["n"] == 1 and w2["no"]["n"] == 1


def test_exit_level_per_tab():
    c = pd.Series([100.0 + i for i in range(30)])
    lo = c - 2
    sw = logic.exit_level("swing", c, lo)
    assert sw["stop"] == round(sum(c.iloc[-20:]) / 20) and sw["gap"] < 0 and "20일 평균" in sw["rule"]
    assert logic.exit_level("scalp", c, lo)["stop"] == 127
    assert logic.exit_level("value", c, lo)["stop"] == 118
    assert logic.exit_level("swing", c.iloc[:10], lo.iloc[:10]) is None


def test_stop_scorecard_sells_when_close_breaks_stop():
    days = [f"202610{d:02d}" for d in range(1, 31)]
    up = [100.0 + i for i in range(23)] + [123, 124, 125, 126, 127, 128, 129]
    down = [100.0 + i for i in range(23)] + [123, 90, 85, 80, 75, 70, 65]
    closes = pd.DataFrame({"A": up, "B": down}, index=days)
    lows = closes - 1
    hist = [(days[22], {"swing": [{"code": "A", "name": "a", "close": 122}, {"code": "B", "name": "b", "close": 122}]})]
    st = logic.stop_scorecard(hist, closes, lows, opens=closes, horizons=(5,), cost=0)["swing"]["5"]
    assert st["hit"] == 50 and st["hold"]["n"] == 2
    assert st["stop"]["avg"] > st["hold"]["avg"]   # B는 첫날 손절해서 덜 잃음


def test_report_season_and_status():
    s = logic.report_season("20261023")
    assert s["key"] == "2026Q3" and s["due"] == "20261114"
    assert logic.report_season("20260601") is None and logic.report_season("20270210")["key"] == "2026Y"
    items = [{"corp": "가나", "title": "분기보고서 (2026.09)", "at": "2026-10-20T15:00+09:00"},
             {"corp": "다라", "title": "결산실적공시예고", "at": "2026-10-21T15:00+09:00"},
             {"corp": "가나", "title": "[기재정정]분기보고서", "at": "2026-10-22T15:00+09:00"}]
    st = logic.update_reported({}, s, items, {"가나": "A", "다라": "B"})
    assert st["reported"]["A"]["date"] == "20261020" and "B" in st["notice"]
    assert logic.earnings_status("A", "20261023", s, st)["state"] == "done"
    assert logic.earnings_status("A", "20261019", s, st)["state"] == "pending"
    p = logic.earnings_status("B", "20261023", s, st)
    assert p["state"] == "pending" and p["days_left"] == 22 and p["notice"]


def test_morning_summary_tone():
    up = logic.morning_summary([{"key": "ewy", "change": 1.5}, {"key": "nasdaq", "change": 0.8}, {"key": "vix", "change": -6}])
    assert up["tone"] == "up" and "한국 ETF +1.5%" in up["text"] and "공포지수 급락" in up["text"]
    down = logic.morning_summary([{"key": "ewy", "change": -1.2}, {"key": "usdkrw", "change": 0.5}])
    assert down["tone"] == "down"
    assert logic.morning_summary([])["label"] == "자료 없음"


def test_report_attention_marks_fresh_coverage():
    rep = lambda i, code, d: {"id": i, "code": code, "date": d, "title": "t", "broker": "b", "reads": 1}
    st = logic.merge_reports({"since": "20260601"}, [rep(1, "A", "20261020"), rep(2, "B", "20261020"),
                                                     rep(3, "B", "20260801"), rep(4, "C", "20260101")], "20261023", "20260601")
    assert all(r["id"] != 4 for r in st["items"])              # 150일 지난 건 버림
    a = logic.report_attention("A", st, "20261023")
    assert a["n"] == 1 and a["fresh"] and a["known"]
    b = logic.report_attention("B", st, "20261023")
    assert b["n"] == 1 and b["before"] == 1 and not b["fresh"]
    assert logic.report_attention("A", st, "20261019")["n"] == 0   # 기준일 뒤 리포트는 안 봄
    short = logic.merge_reports({}, [rep(1, "A", "20261020")], "20261023", "20261010")
    assert not logic.report_attention("A", short, "20261023")["fresh"]   # 기록이 짧으면 '오랜만' 판단 보류


def test_report_wake_and_features():
    x = {"wake": {"met": 1, "total": 4, "checks": [{"key": "flow", "ok": True}]},
         "reports": {"n": 2, "before": 0, "fresh": True, "known": True, "items": []}, "search_rank": None}
    logic.add_report_wake(x)
    logic.add_report_wake(x)   # 두 번 불러도 한 번만
    assert x["wake"]["met"] == 2 and x["wake"]["total"] == 5 and x["wake"]["checks"][-1]["value"] == "오랜만에 2건"
    f = logic.features_of(x)
    assert f["report"] is True and f["report_fresh"] is True and f["search_top"] is False
    assert logic.features_of({})["search_top"] is None and logic.features_of({})["report"] is None
