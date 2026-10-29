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
    assert stats["swing"]["1"] == {"n": 1, "avg": 10.0, "win": 100, "excess": 10.0, "low10": None}
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
             {"corp": "마바", "title": "반기보고서", "at": "2026-10-01T09:00+09:00",
              "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814001590"},   # 10/1 목록에 섞인 8월 공시
             {"corp": "마바", "title": "기업설명회(IR)개최(안내공시)", "at": "2026-10-02T09:00+09:00"},
             {"corp": "다라", "title": "결산실적공시예고", "at": "2026-10-21T15:00+09:00"},
             {"corp": "가나", "title": "[기재정정]분기보고서", "at": "2026-10-22T15:00+09:00"}]
    st = logic.update_reported({}, s, items, {"가나": "A", "다라": "B", "마바": "C"})
    assert st["reported"]["A"]["date"] == "20261020" and "B" in st["notice"]
    assert "C" not in st["reported"] and "C" not in st["notice"]
    assert logic.earnings_status("A", "20261023", s, st) is None   # 한 날도 확인 못 했으면(DART 실패) 판단 안 함
    st["checked"] = ["20261020"]
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


def test_stock_index_explains_why_not_picked():
    n = 70
    days = [f"d{i:02d}" for i in range(n)]
    closes = pd.DataFrame({"A": [100.0 + i for i in range(n)], "B": [200.0 - i for i in range(n)]}, index=days)
    values = pd.DataFrame({"A": [10 * EOK] * (n - 1) + [40 * EOK], "B": [20 * EOK] * n}, index=days)
    fl = {"foreign": pd.Series({"A": 5 * EOK, "B": -1 * EOK}), "inst": pd.Series({"A": 1 * EOK, "B": 0})}
    daily = [(d, fl) for d in days[-5:]]
    five = {"foreign": pd.Series({"A": 25 * EOK, "B": -5 * EOK}), "inst": pd.Series({"A": 5 * EOK, "B": 0})}
    uni = {"A": {"name": "가", "market": "KOSDAQ", "cap": 1000 * EOK, "value": 40 * EOK, "change": 3.0},
           "B": {"name": "나", "market": "KOSPI", "cap": 300 * EOK, "value": 20 * EOK, "change": -0.5}}
    idx = logic.stock_index(uni, {"B": ["유상증자"]}, closes, values, daily, five, {"A": []}, {"A": []}, picked={"A": ["swing"]})
    a, b = idx["stocks"]["A"], idx["stocks"]["B"]
    assert a["sw"] == "11111" and a["sc"] == "111" and a["nw"] == 2 and a["p"] == ["swing"] and a["sz"] == "11"
    assert b["sw"][2] == "0" and b["sz"][0] == "0" and b["x"] == ["유상증자"] and b["nw"] == 0
    assert len(idx["labels"]["swing"]) == 5 and len(idx["labels"]["value"]) == 5


def test_theme_strength_and_scorecard():
    n = 30
    days = [f"d{i:02d}" for i in range(n)]
    closes = pd.DataFrame({"A": [100.0] * n, "B": [50.0] * (n - 1) + [55.0]}, index=days)
    fl = {"foreign": pd.Series({"A": 2 * EOK, "B": 2 * EOK}), "inst": pd.Series({"A": 0, "B": 0})}
    daily = [(d, fl) for d in days[-5:]]
    caps = {"A": 1000 * EOK, "B": 500 * EOK}
    down = pd.Series([100.0 - i * 0.5 for i in range(n)])
    tr = logic.etf_trend(down, pd.Series([100.0] * n))
    assert tr["above20"] is False and tr["rel20"] < 0
    th = logic.theme_strength("테마", [{"code": "E", "name": "E", "trend": tr}], {"A": "가", "B": "나"}, caps, closes,
                              daily, fl, regime={"key": "down", "label": "하락장"})
    assert th["key"] == "early" and th["flow_days"] == 5 and th["breadth"] == 50 and th["warn"]
    rec = lambda c, b: {"themes": [{"key": "early", "etfs": [{"code": "E", "trend": {"close": c}}]}], "bench": {"069500": {"close": b}}}
    sc = logic.theme_scorecard([("1", rec(100, 100)), ("2", rec(101, 100)), ("3", rec(102, 100)),
                                ("4", rec(103, 100)), ("5", rec(104, 100)), ("6", rec(110, 105))], horizons=(5,), cost=0)
    assert sc["early"]["5"]["n"] == 1 and sc["early"]["5"]["avg"] == 10.0 and sc["early"]["5"]["excess"] == 5.0


def test_stop_scorecard_hold_matches_scorecard():
    # 정리 가격이 없는 날(20일 평균 아래)·빈 시가·같은 종목 재등장이 섞여도 '그냥 보유'는 성적표와 같아야 한다
    import numpy as np
    rng = np.random.default_rng(3)
    n = 70
    days = [f"d{i:02d}" for i in range(n)]
    cl = pd.DataFrame({c: 100 * np.cumprod(1 + rng.normal(0, 0.02, n)) for c in "ABCDE"}, index=days)
    op = cl * (1 + rng.normal(0, 0.005, (n, 5)))
    op.iloc[40, 2] = float("nan")
    k = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=days)
    bench = {"KOSPI": (k * 1.001, k), "KOSDAQ": (k * 0.999, k * 1.02)}
    hist = [(days[i], {"swing": [{"code": c, "name": c, "close": cl[c].iloc[i], "market": "KOSPI" if c < "C" else "KOSDAQ"}
                                 for c in "ABCDE" if (i + ord(c)) % 3]}) for i in range(22, 60)]
    st, *_ = logic.scorecard(hist, cl, k, opens=op, bench=bench)
    sp = logic.stop_scorecard(hist, cl, cl * 0.98, opens=op, bench=bench, horizons=(5,), index_closes=k)
    assert {k: v for k, v in sp["swing"]["5"]["hold"].items() if k not in ("dd", "dd10")} == st["swing"]["5"]


def test_holdings_diff_and_manager_moves():
    before = {"A": ("가", 100, 10), "B": ("나", 200, 5), "C": ("다", 50, 3)}
    now = {"A": ("가", 130, 12), "B": ("나", 200, 5), "D": ("라", 10, 2)}
    mv = logic.holdings_diff(now, before)
    acts = {m["code"]: m["act"] for m in mv}
    assert acts == {"A": "add", "D": "new", "C": "out"}
    # 설정 단위가 통째로 2배 → 같은 비율은 매매 아님, A만 더 늘림
    unit = logic.holdings_diff({"A": ("가", 260, 12), "B": ("나", 400, 5), "C": ("다", 100, 3)}, before)
    assert [m["code"] for m in unit] == ["A"]
    res = logic.manager_moves([{"manager": "타임", "code": "E1", "name": "T", "moves": mv},
                               {"manager": "삼성", "code": "E2", "name": "K", "moves": [{"code": "A", "name": "가", "act": "new", "chg": None, "w": 1}]},
                               {"manager": "KB", "code": "E3", "name": "R", "moves": [{"code": "A", "name": "가", "act": "cut", "chg": -20, "w": 1}]}],
                              known={"A", "C", "D"})
    assert res["buys"][0]["code"] == "A" and res["buys"][0]["n"] == 2 and res["buys"][0]["mixed"]
    assert {r["code"] for r in res["sells"]} == {"A", "C"}


def test_theme_early_rejects_one_stock_inflow():
    n = 30
    days = [f"d{i:02d}" for i in range(n)]
    closes = pd.DataFrame({"A": [100.0] * n, "B": [50.0] * n, "C": [30.0] * n}, index=days)
    fl = {"foreign": pd.Series({"A": 9 * EOK, "B": 0.5 * EOK, "C": 0.5 * EOK}), "inst": pd.Series({"A": 0, "B": 0, "C": 0})}
    caps = {"A": 1000 * EOK, "B": 500 * EOK, "C": 300 * EOK}
    tr = logic.etf_trend(pd.Series([100.0 - i * 0.1 for i in range(n)]), pd.Series([100.0] * n))
    th = logic.theme_strength("t", [{"code": "E", "name": "E", "trend": tr}], {"A": "가", "B": "나", "C": "다"}, caps, closes,
                              [(d, fl) for d in days[-5:]], fl)
    assert th["key"] == "watch" and "한 종목(가)" in th["label"]


def test_feature_stats_marks_which_group():
    e = {"group": "value", "x5": 1.0, "features": {"report": True}}
    fs = logic.feature_stats([e], groups=("value",), features=logic.VALUE_FEATURES)
    assert all(f["who"] == "value" for f in fs)
    assert all(f["who"] == "flow" for f in logic.feature_stats([], groups=("swing", "scalp")))


def test_manager_enrich_skill_and_tilt():
    days = ["d1", "d2", "d3"]
    closes = pd.DataFrame({"A": [100.0, 105, 110], "B": [50.0, 50, 45]}, index=days)
    mv20 = {"buys": [{"code": "A", "name": "가", "managers": ["타임", "KB"], "etfs": [{"w": 4.0, "act": "new"}], "n": 2},
                     {"code": "B", "name": "나", "managers": ["KB"], "etfs": [{"w": 1.0, "act": "add"}], "n": 1}],
            "sells": [{"code": "B", "name": "나", "managers": ["삼성"], "etfs": [{"w": 1.0, "act": "cut"}], "n": 1}]}
    mv5 = {"buys": [{"code": "A"}], "sells": []}
    skill = logic.manager_skill([{"manager": "타임", "r20": 5, "r60": 10, "b20": 1, "b60": 2},
                                 {"manager": "KB", "r20": 0, "r60": -5, "b20": 1, "b60": 2}])
    assert skill["타임"]["x60"] == 8.0 and skill["KB"]["x60"] == -7.0
    trust = pd.Series({"A": 30 * EOK})
    out = logic.enrich_manager_moves(mv20, mv5, closes, "d1", pd.Series({"A": "IT", "B": "화학"}), trust, skill)
    a = out["buys"][0]
    assert a["code"] == "A" and a["persist"] and a["big"] and a["since"] == 10.0 and a["trust5"] == 30.0 and a["good"] == 1
    assert a["score"] == 2 * 2 + 1 + 1 + 1
    tilt = logic.sector_tilt(out, pd.Series({"A": "IT", "B": "화학"}))
    assert tilt[0]["sector"] == "IT" and tilt[0]["net"] == 2


def test_replay_mover_and_signal_validation():
    n = 40
    days = [f"d{i:02d}" for i in range(n)]
    base = [100.0] * 30 + [100, 101, 100, 101, 100, 125, 128, 130, 131, 132]   # d35에 하루 +25% 급등
    closes = pd.DataFrame({"A": base, "B": [50.0] * n}, index=days)
    values = pd.DataFrame({"A": [10 * EOK] * 31 + [25 * EOK] * 9, "B": [10 * EOK] * n}, index=days)
    netd = pd.DataFrame({"A": [0] * 30 + [-1, 2, 2, 2, 2, 5, 5, 1, 1, 1], "B": [1, -1] * 20}, index=days) * EOK
    caps = {"A": 1000 * EOK, "B": 1000 * EOK}
    uni = {"A": {"cap": 1000 * EOK}, "B": {"cap": 1000 * EOK}}
    assert logic.find_movers(closes, uni) == ["A"]
    # 급등 = 하루 +20%↑: 5일 동안 +25% 나눠 오른 S는 아님, 오늘 하루 +30%인 D는 급등. 하루 +30.5% 넘는 건(가격 끊김) 제외
    up = pd.DataFrame({"S": [100.0] * 34 + [100, 110, 120, 125, 125, 125], "D": [100.0] * 40, "X": [100.0] * 39 + [50.0]}, index=days)
    up.iloc[-6:, 1] = [100, 100, 95, 90, 87, 113.1]
    up.iloc[-2:, 2] = [100.0, 200.0]
    u2 = {k: {"cap": 1000 * EOK} for k in "SDX"}
    assert logic.find_movers(up, u2) == ["D"]
    cand = {"d35": {"A": ["단타"]}}
    rp = logic.replay_mover("A", "가", closes, values, netd, caps, cand)
    assert rp["surge"] == "d35" and rp["verdict"] == "sameday"
    keys = {e["key"]: e["lead"] for e in rp["early"]}
    assert keys["streak3"] == 2 and keys["quiet_vol"] == 4   # d33부터 3일 연속, d31부터 조용한 거래 증가
    v = logic.signal_validation(closes, values, netd.iloc[25:], caps, {"A", "B"})
    row = next(r for r in v["rows"] if r["key"] == "streak3")
    assert v["base_n"] > 0 and row["n"] >= 1 and row["rate"] > v["base"]


def test_replay_late_catch():
    n = 40
    days = [f"d{i:02d}" for i in range(n)]
    closes = pd.DataFrame({"A": [100.0] * 35 + [125, 128, 130, 131, 132]}, index=days)
    values = pd.DataFrame({"A": [10 * EOK] * n}, index=days)
    netd = pd.DataFrame({"A": [0.0] * n}, index=days)
    rp = logic.replay_mover("A", "가", closes, values, netd, {"A": 1000 * EOK}, {"d36": {"A": ["단타"]}})
    assert rp["surge"] == "d35" and rp["verdict"] == "late" and rp["lead"] == -1


def test_theme_heat_rows():
    days = [f"d{i}" for i in range(6)]
    net = pd.DataFrame({"A": [1, 2, 3, 4, 5, 6], "B": [-1] * 6, "C": [0] * 6}, index=days) * EOK
    ret = pd.Series([0, 1, -1, 2, 0, 3.0], index=days)
    h = logic.theme_heat({"가": (["A"], ret), "나": (["B", "C"], None)}, net, {"A": 100 * EOK, "B": 100 * EOK, "C": 100 * EOK}, days=5)
    assert h["days"] == days[1:] and h["rows"][0]["name"] == "가"
    assert h["rows"][0]["eok"] == [2, 3, 4, 5, 6] and h["rows"][0]["ret"][-1] == 3.0
    assert h["rows"][1]["pct"][0] == -0.5 and h["rows"][1]["ret"][0] is None


def test_follow_through():
    days = [f"202609{i:02d}" for i in range(1, 13)]
    # A: 3일째 +20% 급등 → 다음 날 −2%(쉼) → 그다음 +10%(쉬고 다시 오름, 급등은 아님) ; B: 평범하게 매일 +1%
    a = [100, 100, 120, 117.6, 129.36, 130, 131, 132, 133, 134, 135, 136]
    b = [100 * 1.01 ** i for i in range(12)]
    closes = pd.DataFrame({"A": a, "B": b}, index=days)
    opens = closes.shift(1).fillna(100)
    out = logic.follow_through(closes, opens, {"A", "B"}, cost=0)
    big = next(r for r in out["rows"] if r["key"] == "big")
    assert big["n"] == 1 and big["next_up"] == 0 and big["again3"] == 0 and big["rest_again"] == 100
    # 다음 날 시가(=급등 날 종가 120)에 사서 3일째 종가(130) → +8.3%
    assert big["h3"] == 8.3
    base = next(r for r in out["rows"] if r["key"] == "base")
    assert base["n"] > big["n"]


def test_follow_through_splits():
    days = [f"202609{i:02d}" for i in range(1, 21)]
    a = [100] * 10 + [120, 117.6, 129.36, 130, 131, 132, 133, 134, 135, 136]
    closes = pd.DataFrame({"A": a, "B": [100 * 1.01 ** i for i in range(20)]}, index=days)
    opens = closes.shift(1).fillna(100)
    highs = closes * 1.01
    values = pd.DataFrame(1.0, index=days, columns=["A", "B"])
    values.loc[days[10], "A"] = 6.0   # 첫 급등 날 거래 6배
    out = logic.follow_through(closes, opens, {"A", "B"}, cost=0, highs=highs, values=values,
                               caps={"A": 1000 * logic.EOK, "B": 9000 * logic.EOK})
    sp = {r["key"]: r for r in out["splits"]}
    # 급등(하루 +20%↑)은 A의 +20% 날 하나뿐 (+10% 날은 급등 아님). 종가 고가 근처·소형·첫 급등·거래 6배
    assert sp["all"]["n"] == 1 and sp["close_high"]["n"] == 1 and sp["small"]["n"] == 1
    assert sp["vol5"]["n"] == 1 and sp["first"]["n"] == 1 and "again" not in sp and "large" not in sp
    # 첫 급등은 거래 6배라 '3배 안' 조합에 없음
    assert "first_quiet" not in sp


def test_hold_after_surge():
    days = [f"2026{i:04d}" for i in range(101, 125)]
    # 5일째 +20% 급등(100→120) 뒤: 125, 130(최고), 121, 118, ... 10일째 110 → 최고는 2일 뒤
    a = [100] * 5 + [120, 125, 130, 121, 118, 116, 115, 114, 112, 111, 110] + [110] * 8
    closes = pd.DataFrame({"A": a, "B": [100.0] * 24}, index=days)
    opens = closes.shift(1).fillna(100)
    lows = closes * 0.99
    out = logic.hold_after_surge(closes, opens, lows, {"A", "B"})
    g = out["groups"]["all"]
    assert g["n"] == 1 and g["peak"]["d2"] == 100 and g["giveback"] == 0
    assert g["after"]["a1"]["med"] == round((125 / 120 - 1) * 100, 1)
    # 전날 저가(130*0.99=128.7) 깨진 첫날 = 121 에 팖 / 최고 130에서 7% 내려온 첫날(≤120.9) = 118 에 팖
    assert g["exits"]["trail_low"]["med"] == round((121 / 120 - 1) * 100, 1)
    assert g["exits"]["trail_pk"]["med"] == round((118 / 120 - 1) * 100, 1)


def test_volume_profile():
    # 100~110에서 9일, 마지막 날만 200~210 (거래 적음) → 현재가 205 아래에서 거래된 비중이 큼
    h = [110] * 9 + [210] + [110] * 0
    l = [100] * 9 + [200]
    c = [105] * 9 + [205]
    v = [10] * 9 + [1]
    vp = logic.volume_profile(h + [210], l + [200], c + [205], v + [1])
    assert vp["lo"] == 100 and vp["hi"] == 210 and vp["now"] == 205
    assert abs(sum(vp["b"]) - 100) < 1 and vp["top"] in (0, 1) and vp["below"] > 90


def test_myth_check_events():
    import numpy as np
    days = [f"2026{i:04d}" for i in range(100, 182)]
    n = len(days)
    rng = np.random.default_rng(3)
    cols = [f"{i:06d}" for i in range(40)]
    cl = pd.DataFrame(1000 * np.exp(np.cumsum(rng.normal(0, 0.01, (n, 40)), 0)), index=days, columns=cols)
    # A: 65일째 직전 60일 최고가를 크게 넘음(신고가) / B: 70~74일째 5일 연속 하락 / C: 50일째 시가 +5% 갭
    cl["A"] = 1000.0; cl.iloc[65:, cl.columns.get_loc("A")] = 1100.0
    b = [1000.0] * n
    for k in range(70, 75):
        b[k] = b[k - 1] * 0.98
    for k in range(75, n):
        b[k] = b[74]
    cl["B"] = b
    cl["C"] = 1000.0
    op = cl.shift(1).fillna(1000.0)
    op.iloc[50, op.columns.get_loc("C")] = 1050.0
    hi = np.maximum(cl, op) * 1.001
    lo = np.minimum(cl, op) * 0.999
    va = pd.DataFrame(1.0, index=days, columns=cl.columns)
    out = logic.myth_check(cl, op, hi, lo, va, set(cl.columns), cost=0)
    it = {x["key"]: x for x in out["items"]}
    assert it["new_high"]["n"] >= 1
    assert it["down5"]["n"] >= 1
    # C의 갭은 그날 종가(1000)가 전날 종가와 같으니 그날 안에 메워짐
    assert it["gap"]["up"]["n"] >= 1 and it["gap"]["up"]["same_day"] is not None
    assert {x["verdict"] for x in out["items"]} <= {"true", "false", "none", "few", "shaky_true", "shaky_false", "weak_true", "weak_false"}


def test_myth_check_excess_and_skew():
    import numpy as np
    days = [f"2026{i:04d}" for i in range(100, 182)]
    n = len(days)
    rng = np.random.default_rng(5)
    cols = [f"{i:06d}" for i in range(60)]
    base = rng.normal(0, 0.01, (n, 60))
    base[40] -= 0.05          # 시장 전체가 빠진 날 → 다음 날들 전 종목 반등
    base[41:44] += 0.02
    cl = pd.DataFrame(1000 * np.exp(np.cumsum(base, 0)), index=days, columns=cols)
    op = cl.shift(1).fillna(1000.0)
    hi, lo = np.maximum(cl, op) * 1.001, np.minimum(cl, op) * 0.999
    va = pd.DataFrame(1.0, index=days, columns=cols)
    out = logic.myth_check(cl, op, hi, lo, va, set(cols), cost=0,
                           markets={c: "P" for c in cols}, caps={c: 5000 * logic.EOK for c in cols})
    d5 = {x["key"]: x for x in out["items"]}["down5"]
    if d5["n"]:
        # 같은 날 평균을 빼니 시장 전체 반등은 초과 수익에서 빠짐 (원래 수익보다 훨씬 작음)
        assert abs(d5["x3"]) < abs(d5["h3"]) + 0.5
        assert "seg" in d5 and "kospi" in d5["seg"]
    assert logic._trim_mean([0] * 19 + [100]) == 0.0   # 20개↑면 한 종목 폭등은 빠짐
    assert logic._trim_mean([0] * 9 + [100]) == 10.0   # 20개 안 되면 그냥 평균
    assert logic._trim_mean([0] * 38 + [100, -100]) == 0.0


def test_myth_check_planted_hype_is_significant():
    """관심 과열(거래 5배 + 10%↑) 뒤 3일 동안 빠지도록 심어 두면 '모두가 볼 때는 늦었다'가 맞음으로, 상승·하락장 따로도 나옴"""
    import numpy as np
    days = [f"2026{i:04d}" for i in range(100, 182)]
    n, k = len(days), 80
    rng = np.random.default_rng(11)
    r = rng.normal(0, 0.01, (n, k))
    vol = np.ones((n, k))
    for j in range(k):
        for t in range(25 + (j % 7), n - 8, 9):   # 종목마다 9일 간격으로 과열 → 다음 4일 하락
            r[t, j] += 0.12
            vol[t, j] = 8.0
            r[t + 1:t + 5, j] -= 0.02
    cols = [f"{i:06d}" for i in range(k)]
    cl = pd.DataFrame(1000 * np.exp(np.cumsum(r, 0)), index=days, columns=cols)
    op = cl.shift(1).fillna(1000.0)
    hi, lo = np.maximum(cl, op) * 1.001, np.minimum(cl, op) * 0.999
    va = pd.DataFrame(vol, index=days, columns=cols)
    out = logic.myth_check(cl, op, hi, lo, va, set(cols), cost=0)
    hype = {x["key"]: x for x in out["items"]}["hype"]
    assert hype["verdict"] == "true" and hype["t3"] < -2
    assert set(hype["by_mk"]) == {"up", "down"}


def test_myth_check_dart_events():
    import numpy as np
    days = [f"2026{i:04d}" for i in range(100, 182)]
    rng = np.random.default_rng(2)
    cols = [f"{i:06d}" for i in range(50)]
    cl = pd.DataFrame(1000 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(days), 50)), 0)), index=days, columns=cols)
    op = cl.shift(1).fillna(1000.0)
    hi, lo = np.maximum(cl, op) * 1.001, np.minimum(cl, op) * 0.999
    va = pd.DataFrame(1.0, index=days, columns=cols)
    ev = [{"date": days[30 + i], "code": cols[i], "kind": "insider", "rcept": str(i)} for i in range(40)]
    ev.append({"date": days[40], "code": "999999", "kind": "buyback", "rcept": "x"})   # 대상 밖 종목은 무시
    out = logic.myth_check(cl, op, hi, lo, va, set(cols), cost=0, dart_events=ev)
    it = {x["key"]: x for x in out["items"]}
    assert it["insider"]["n"] == 40 and "by_mk" in it["insider"]
    assert "buyback" not in it   # 해당 종목 없음 → 항목 안 만듦


# ---------------------------------------------------------------- 후보 변화 · 상대강도 · 진입 위험 · 기록 출처

def test_record_origin_and_classify():
    from datetime import datetime as dt
    assert logic.record_origin("20261023", dt(2026, 10, 23, 18, 25)) == "live"
    assert logic.record_origin("20261023", dt(2026, 10, 26, 8, 59)) == "live"      # 금요일 기록 → 월요일 장 전까지
    assert logic.record_origin("20261023", dt(2026, 10, 26, 9, 0)) == "recalc"
    assert logic.record_origin("20261023", dt(2026, 10, 23, 15, 0)) == "recalc"     # 장중에 만든 건 실제 마감 기록이 아님
    assert logic.classify_record({"generated_at": "2026-10-22T19:32+09:00"}, "20261022") == "live"
    assert logic.classify_record({"generated_at": "2026-10-26T13:15+09:00"}, "20261023") == "recalc"
    assert logic.classify_record({"backfill": True, "generated_at": "2026-10-22T19:32+09:00"}, "20261022") == "recalc"
    assert logic.classify_record({}, "20261022") == "unknown"
    assert logic.classify_record({"record": {"origin": "live"}}, "20261022") == "live"
    assert logic.strategy_ver().startswith("s3w1v2-")


def test_atr_wilder_matches_hand_calc():
    import numpy as np
    n = 40
    rng = np.random.default_rng(1)
    c = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    h, lo = c + rng.uniform(0.5, 2, n), c - rng.uniform(0.5, 2, n)
    tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)]
    atr = sum(tr[:14]) / 14
    for v in tr[14:]:
        atr = (atr * 13 + v) / 14
    assert abs(logic.atr_wilder(h, lo, c) - atr) < 1e-9
    # 가격이 끊긴 날 뒤부터만 쓰고, 그러면 기간이 짧아 None
    br = pd.Series(False, index=c.index)
    br.iloc[20] = True
    assert logic.atr_wilder(h, lo, c, breaks_s=br) is None
    assert logic.atr_wilder(h, lo, c, breaks_s=br, min_len=14) is not None


def test_price_breaks_and_rel_strength():
    days = [f"2026{i:04d}" for i in range(101, 131)]
    cl = pd.DataFrame({"A": [100.0 * 1.01 ** i for i in range(30)], "B": [100.0] * 30}, index=days)
    cl.iloc[25:, 1] = 50.0                         # B: 25일째 1:2 분할 → 종가는 반토막, 거래소 등락률은 0
    chg = (cl / cl.shift(1) - 1) * 100
    chg.iloc[25, 1] = 0.0
    br = logic.price_breaks(cl, chg)
    assert br["B"].iloc[25] and not br["A"].any()
    junk = chg.copy()
    junk.iloc[10] = 0.0                            # 그날 등락률이 통째로 0 (자료 이상) → 끊긴 날로 보지 않음
    assert not logic.price_breaks(cl, junk)["A"].iloc[10]
    idx = pd.Series([1000.0 * 1.005 ** i for i in range(30)], index=days)
    v, why = logic.rel_strength(cl["A"], idx, br["A"], 20)
    assert v["from"] == days[-21] and abs(v["stock"] - (1.01 ** 20 - 1) * 100) < 0.06
    assert abs(v["diff"] - (v["stock"] - v["mkt"])) < 0.11 and v["diff"] > 0
    assert logic.rel_strength(cl["B"], idx, br["B"], 20) == (None, "기간 중 가격이 끊김 (권리락·분할 등)")
    assert logic.rel_strength(cl["A"], idx, br["A"], 60)[1] == "기간 부족"
    gap = cl["A"].copy()
    gap.iloc[-3] = float("nan")
    assert "거래 없는 날" in logic.rel_strength(gap, idx, None, 20)[1]
    # 날짜가 정확히 맞아야: 지수에 시작일이 없으면 계산하지 않음
    assert logic.rel_strength(cl["A"], idx.drop(days[-21]), None, 20)[1] == "지수 자료 없음"


def test_entry_risk():
    r = logic.entry_risk(10000, 9500, 250)
    assert r == {"ref": 10000, "atr": 250, "atr_pct": 2.5, "stop": 9500, "dist": 5.0, "mult": 2.0}
    assert "mult" not in logic.entry_risk(10000, 9500, None)


def test_score_change_sums_to_total():
    a = {"mult": 4.0, "chg": 3.0, "nr": 0.05}
    b = {"mult": 6.0, "chg": 8.0, "nr": 0.02}
    ch = logic.score_change("scalp", b, a)
    assert abs(sum(c["d"] for c in ch["causes"]) - ch["d"]) < 0.3
    assert {c["key"] for c in ch["causes"]} == {"flow", "volume", "move"}
    sw = logic.score_change("swing", {"ratio": 0.2, "streak": 4}, {"ratio": 0.1, "streak": 2})
    assert abs(sum(c["d"] for c in sw["causes"]) - sw["d"]) < 0.002 and sw["now"] == 0.36
    v0 = {"drop": 30, "pbr_r": 0.5, "per": 8, "eps_r": 1.0, "fwd": None}
    assert logic.score_change("value", v0, v0)["causes"] == []


def test_candidate_changes_new_kept_dropped():
    mk = lambda c, s, mult=4.0: {"code": c, "name": c, "close": 100.0, "score": s, "value_mult": mult, "value_eok": 100.0,
                                 "change": 3.0, "flow": {"foreign_today": 5, "inst_today": 5}}
    prev_top = [mk("A", 50), mk("B", 40), mk("C", 30)]
    today = [mk("A", 60, 6.0), mk("D", 55), mk("E", 20)]
    today_pool = logic.pool_record("scalp", today)
    prev_pool = logic.pool_record("scalp", prev_top + [mk("D", 10)])
    index = {"B": {"sz": "11", "nw": 2, "sc": "101", "c": 90}, "C": None}
    labels = {"scalp": ["거래", "상승", "큰손"]}
    dropped = logic.candidate_changes("scalp", today, prev_top, prev_pool, today_pool, index, labels, top=2)
    tr = {x["code"]: x["track"] for x in today[:2]}
    assert tr["A"]["st"] == "kept" and tr["A"]["prev_rank"] == 1 and tr["A"]["score"]["causes"][0]["key"] == "volume"
    assert tr["D"]["st"] == "new" and tr["D"]["prev_rank"] == 4      # 어제도 조건 통과, 화면 밖 4위
    assert "track" not in today[2]
    by = {d["code"]: d for d in dropped}
    assert by["B"]["why"] == "filter" and by["B"]["broken"] == ["상승"] and by["B"]["close"] == 90
    assert by["C"]["why"] == "unknown"                                # 오늘 자료 없음 → 추적 불가
    dropped = logic.candidate_changes("scalp", [mk("A", 60)], prev_top[:1] + [mk("E", 5)], None,
                                      logic.pool_record("scalp", [mk("A", 60), mk("E", 20)]), {}, labels, top=1)
    assert dropped == [{"code": "E", "name": "E", "prev_rank": 2, "prev_close": 100.0, "why": "rank", "rank": 2,
                        "score": dropped[0]["score"]}]
    # 어제 전체 기록이 없으면 '어제 순위 밖이었나'는 모름
    t2 = [mk("Z", 9)]
    logic.candidate_changes("scalp", t2, prev_top, None, logic.pool_record("scalp", t2), {}, labels)
    assert t2[0]["track"] == {"st": "new", "rank": 1, "prev_known": False}


def test_failed_conditions_gates():
    labels = {"swing": ["a", "b", "c", "d", "e"], "value": ["v1", "v2", "v3", "v4", "v5"]}
    assert logic.failed_conditions("swing", {"sz": "01", "nw": 0, "sw": "11011", "x": ["관리종목"]}, labels) == [
        "위험 공시·투자 주의로 제외: 관리종목", "시총 500억·거래대금 10억 이상 아님", "최근 3일 기사·공시 없음", "c"]
    assert logic.failed_conditions("value", {"sz": "11", "vr": "적자이거나 재무 자료 없음"}, labels) == ["적자이거나 재무 자료 없음"]
    assert logic.failed_conditions("value", {"sz": "11"}, labels) is None


def test_first_seen():
    recs = [("20261001", {"swing": [{"code": "A", "close": 100}]}),
            ("20261002", {"pools": {"scalp": [{"c": "B", "p": 50}]}, "scalp": []})]
    assert logic.first_seen("A", "swing", recs) == {"date": "20261001", "price": 100}
    assert logic.first_seen("B", "scalp", recs) == {"date": "20261002", "price": 50}
    assert logic.first_seen("B", "swing", recs) is None


def test_stop_scorecard_next_open_and_cash():
    days = [f"d{i:02d}" for i in range(30)]
    base = [100.0] * 30
    cl = pd.DataFrame({"A": base[:]}, index=days)
    op = cl.copy()
    # 걸린 날 d24 (스윙 정리선 = 20일 평균 100). d26 마감 95 (선 아래) → d27 시가 90에 팔고 현금, 끝(d29)엔 120
    cl.loc["d26", "A"] = 95.0
    op.loc["d27", "A"] = 90.0
    cl.loc["d27":, "A"] = 120.0
    hist = [("d24", {"swing": [{"code": "A", "name": "A", "close": 100.0}]})]
    sp = logic.stop_scorecard(hist, cl, cl, opens=op, horizons=(5,), cost=0)["swing"]["5"]
    assert sp["hold"]["avg"] == 20.0 and sp["stop"]["avg"] == -10.0 and sp["hit"] == 100
    assert sp["fill_gap"] == -10.0                     # 정리선 100보다 10% 낮게 팔림 (갭 하락)
    assert sp["stop"]["dd"] == 10.0 and sp["hold"]["dd"] == 5.0
    # 다음 날 시가가 비면(거래 정지) 그다음 거래되는 날 시가에
    op.loc["d27", "A"] = float("nan")
    op.loc["d28", "A"] = 80.0
    assert logic.stop_scorecard(hist, cl, cl, opens=op, horizons=(5,), cost=0)["swing"]["5"]["stop"]["avg"] == -20.0
    # 마지막 날에 깨지면 둘 다 그날 종가 (같은 끝 날)
    cl2 = pd.DataFrame({"A": base[:]}, index=days)
    cl2.loc["d29", "A"] = 90.0
    sp2 = logic.stop_scorecard(hist, cl2, cl2, opens=cl2, horizons=(5,), cost=0)["swing"]["5"]
    assert sp2["hold"]["avg"] == sp2["stop"]["avg"] == -10.0 and sp2["hit"] == 0
    # 보유 중 가격이 끊긴 거래는 양쪽 모두에서 뺌
    br = pd.DataFrame({"A": [False] * 30}, index=days)
    br.loc["d27", "A"] = True
    assert logic.stop_scorecard(hist, cl, cl, opens=op, horizons=(5,), cost=0, breaks=br) == {}


def test_scorecard_extra_splits():
    es = []
    for i in range(40):
        d = f"2026{1001 + i // 4:04d}"
        es.append({"date": d, "group": "scalp", "x5": 1.0 + (i % 2), "origin": "live" if i < 20 else "recalc",
                   "half": "hi" if i % 2 else "lo", "mult": 4.0, "rs20": float(i % 4)})
        es.append({"date": d, "group": "scalp_rest", "x5": -1.0, "origin": "live", "mult": 20.0})
    ex = logic.scorecard_extra(es, pool_dates={e["date"] for e in es}, since={"scalp": "20261006"})
    assert ex["by_origin"]["live"]["scalp"]["n"] == 20 and ex["by_origin"]["recalc"]["scalp"]["n"] == 20
    assert ex["scope"]["scalp"]["top"]["n"] == 40 and ex["scope"]["scalp"]["all"]["n"] == 80
    assert ex["mult"]["verdict"] == "few"
    rows = {r["label"]: r for r in ex["mult"]["rows"]}
    assert rows["3~5배"]["n"] == 40 and rows["15배 넘음"]["n"] == 40
    assert ex["rs"]["scalp"]["verdict"] == "few"                     # 위·아래 각각 30건 미만
    p = ex["periods"]["scalp"]
    assert p["since"] == "20261006" and p["set"]["hi"]["n"] == 10 and p["verdict"] == "unconfirmed"


def test_live_record_kept_apart_from_recalc(tmp_path, monkeypatch):
    import json as js
    from collector import main
    monkeypatch.setattr(main, "DATA", str(tmp_path))
    monkeypatch.setattr(main, "LIVE", str(tmp_path / "live"))
    day = {"generated_at": "2026-10-28T10:00+09:00", "filter": {"swing": [{"code": "B"}]}}
    (tmp_path / "20261026.json").write_text(js.dumps(day))
    rec, origin = main.load_record("20261026")
    assert origin == "recalc" and rec["filter"]["swing"][0]["code"] == "B"
    out = {"generated_at": "2026-10-26T18:30+09:00", "record": {"origin": "live"},
           "filter": {"swing": [{"code": "A", "profile": {}, "reports": {"n": 1, "detail": {"x": 1}}}], "scalp": [], "value": [],
                      "swing_total": 1, "scalp_total": 0, "value_total": 0, "pools": {}, "score_ver": 3}}
    main.write_live("20261026", out)
    rec, origin = main.load_record("20261026")      # 날짜 파일이 나중에 다시 계산돼도 실제 기록을 쓴다
    x = rec["filter"]["swing"][0]
    assert origin == "live" and x["code"] == "A" and "profile" not in x and x["reports"] == {"n": 1}


def test_sector_rs_only_for_members_of_large_enough_index():
    days = [f"2026{i:04d}" for i in range(101, 131)]
    c = pd.Series([100.0 * 1.01 ** i for i in range(30)], index=days)
    sec = pd.Series([1000.0] * 30, index=days)
    members = {f"{i:06d}" for i in range(12)} | {"A"}
    r = logic.sector_rs("A", c, {"name": "화학", "members": members, "close": sec})
    assert r["name"] == "화학" and r["r20"]["mkt"] == 0.0 and r["r20"]["diff"] == r["r20"]["stock"] and r["r60"]["why"] == "기간 부족"
    assert logic.sector_rs("B", c, {"name": "화학", "members": members, "close": sec})["why"] == "업종 지수에 안 들어 있음"
    assert "5개뿐" in logic.sector_rs("A", c, {"name": "통신", "members": {"A", "1", "2", "3", "4"}, "close": sec})["why"]
    assert logic.sector_rs("A", c, None) is None


def test_move_vs_atr_uses_only_pre_surge_range():
    days = [f"d{i:02d}" for i in range(40)]
    c = pd.Series([100.0] * 38 + [130.0, 110.0], index=days)
    h, lo = c + 1, c - 1                       # 급등 전 하루 변동폭 2 (2%)
    m = logic.move_vs_atr(h, lo, c, "d38")     # 다음 날 되돌린 건 섞지 않고 급등 하루만
    assert m == {"atr_pct": 2.0, "rise": 30.0, "x": 15.0}
    assert logic.move_vs_atr(h, lo, c, "d00") is None and logic.move_vs_atr(h, lo, c, "zz") is None


def test_scorecard_extra_vol_buckets():
    es = [{"date": "20261001", "group": "scalp", "x5": 1.0, "atr_pct": v} for v in (2.0, 4.0, 4.5, 6.0, 9.0)]
    es.append({"date": "20261001", "group": "scalp_rest", "x5": -2.0, "atr_pct": 9.5})
    rows = {r["label"]: r["n"] for r in logic.scorecard_extra(es)["vol"]["rows"]}
    assert rows == {"하루 3% 미만": 1, "3~5%": 2, "5~8%": 1, "8% 넘음": 2}


def test_exit_level_value_never_above_close():
    days = [f"d{i:02d}" for i in range(30)]
    c = pd.Series([100.0] * 29 + [90.0], index=days)
    lo = pd.Series([99.0] * 29 + [95.0], index=days)    # 오늘 저가가 마감보다 높게 찍힌 어긋난 자료
    ex = logic.exit_level("value", c, lo)
    assert ex["stop"] == 90 and ex["gap"] == 0.0
    assert logic.exit_level("value", c, lo.where(lo.index != "d29"))["stop"] == 90   # 오늘 저가가 빈 날도


def test_ma_breadth_counts_by_market_and_excludes_breaks():
    import numpy as np
    days = [f"d{i:03d}" for i in range(210)]
    up = pd.Series(np.linspace(100, 200, 210), index=days)      # 계속 오름 → 200일선 위
    down = pd.Series(np.linspace(200, 100, 210), index=days)    # 계속 내림 → 아래
    cl = pd.DataFrame({"A": up, "B": down, "C": up, "D": up}, index=days)
    cl.loc["d100", "D"] = float("nan")                          # 200일 안에 거래 없던 날 → 제외
    br = pd.DataFrame(False, index=days, columns=cl.columns)
    br.loc["d150", "C"] = True                                  # 200일 안에 가격 끊김 → 제외
    out = logic.ma_breadth(cl, br, {"A": "KOSPI", "B": "KOSPI", "C": "KOSDAQ", "D": "KOSDAQ"}, n=200, hist=5)
    k = out["markets"]["KOSPI"]
    assert (k["above"], k["below"], k["excluded"], k["pct"]) == (1, 1, 0, 50.0) and len(k["spark"]) == 5
    assert "KOSDAQ" not in out["markets"]                       # 두 종목 다 제외되면 그 시장은 안 보여 줌
    assert logic.ma_breadth(cl.iloc[:100], br.iloc[:100], {"A": "KOSPI"}) is None


def test_long_history_caches_and_fetches_only_missing(tmp_path, monkeypatch):
    from collector import main, sources
    days = [f"2026{i:04d}" for i in range(101, 131)]
    monkeypatch.setattr(main, "HIST", str(tmp_path / "hist"))
    monkeypatch.setattr(main, "HIST_DAYS", 20)
    monkeypatch.setattr(sources, "business_days", lambda base, n: [d for d in days if d <= base][-n:])
    asked = []

    def fake_ohlcv(ds):
        asked.extend(ds)
        return {d: pd.DataFrame({"종가": [100.0, 50.0], "등락률": [0.0, 0.0]}, index=["A", "B"]) for d in ds}
    monkeypatch.setattr(sources, "daily_ohlcv", fake_ohlcv)
    now = pd.DataFrame({"A": [101.0] * 5, "B": [51.0] * 5}, index=days[25:30])
    chg = pd.DataFrame(0.0, index=now.index, columns=now.columns)
    cl, br = main.long_history(days[29], now, chg)
    assert len(cl) == 20 and len(asked) == 15 and cl.loc[days[29], "A"] == 101.0 and not br.any().any()
    asked.clear()
    cl2, _ = main.long_history(days[29], now, chg)                  # 두 번째엔 보관본으로 → 새로 받을 게 없음
    assert asked == [] and len(cl2) == 20 and list(cl2.columns) == ["A", "B"]


def test_tv_signals_match_pine_rules():
    import numpy as np
    days = [f"d{i:03d}" for i in range(120)]
    # 내리다가(0~59) 오르는(60~) 흐름 → 오르기 시작한 뒤 BUY 한 번, 그 전엔 SELL 쪽
    c = pd.Series(np.r_[np.linspace(200, 100, 60), np.linspace(101, 180, 60)], index=days)
    s = logic.tv_signals(c)
    b = list(s.index[s["buy"]])
    assert len(b) == 1 and b[0] > "d060"
    row = s.loc[b[0]]
    assert row["ema_f"] > row["ema_s"] and row["rsi"] > 50 and row["macd"] > row["sig"]
    # EMA는 트레이딩뷰 ta.ema 와 같은 재귀식: ema = a*x + (1-a)*ema[1], a = 2/(n+1)
    e, a = c.iloc[0], 2 / 10
    for x in c.iloc[1:]:
        e = a * x + (1 - a) * e
    assert abs(s["ema_f"].iloc[-1] - e) < 1e-9
    ch = logic.tv_chart(c, days=50)
    assert ch["buy"] == b and ch["now"]["ema_up"] and ch["now"]["last"]["kind"] == "buy"


def test_tv_signal_check_trades_and_baseline():
    import numpy as np
    days = [f"d{i:03d}" for i in range(160)]
    c = np.r_[np.linspace(200, 100, 60), np.linspace(101, 180, 60), np.linspace(180, 120, 40)]
    cl = pd.DataFrame({"A": c, "B": c * 2}, index=days)
    out = logic.tv_signal_check(cl, cost=0)
    assert out["n"] == 2 and out["win"] == 100 and out["base"]["n"] > 0
    br = pd.DataFrame(False, index=days, columns=["A", "B"])
    br.iloc[70:, 1] = True                                       # B는 보유 중 가격이 끊김 → 그 거래 뺌
    assert logic.tv_signal_check(cl, br, cost=0)["n"] == 1


def test_weekly_and_4h_bars():
    from datetime import datetime as dt
    d = pd.Series([1.0, 2, 3, 4, 5, 6, 7], index=["20261026", "20261027", "20261028", "20261029", "20261030", "20261102", "20261103"])
    w = logic.to_weekly(d)
    assert list(w.index) == ["20261030", "20261103"] and list(w) == [5.0, 7.0]       # 그 주 마지막 거래일 마감
    rows = [(dt(2026, 10, 29, h), float(h)) for h in (9, 10, 11, 12, 13, 14, 15)] + [(dt(2026, 10, 29, 16), 99.0)]
    h4 = logic.to_4h(rows)
    assert dict(h4) == {"2026102909": 12.0, "2026102913": 15.0}                    # 09~13시 / 13~15:30, 장 밖 봉은 뺌
