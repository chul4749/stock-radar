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


def test_pct_rank_and_summary():
    assert logic.pct_rank([1, 2, 3, 4], 4) == 88
    t = {"vix": {"pct_1y": 90}, "korea_vol": {"pct_1y": 80}, "fear_greed": {"score": 20},
         "breadth": {"new_lows": [10, 10, 10, 10, 10, 50]},
         "hy_spread": {"value": 3.3, "pct_1y": 40, "change": 0.1}}
    s = {x["key"]: x for x in logic.summarize_temperature(t)}
    assert s["fear"]["level"] == "큼"
    assert s["selling"]["level"] == "커지는 중"
    assert s["credit"]["level"] == "안정"


def test_realized_vol_flat_is_zero():
    s = pd.Series([100.0] * 30)
    assert logic.realized_vol(s).dropna().iloc[-1] == 0
    assert not math.isnan(logic.realized_vol(pd.Series([100.0 * (1.01 ** i) for i in range(30)])).iloc[-1])
