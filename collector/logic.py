"""필터 규칙과 지표 계산. 네트워크 없이 테스트할 수 있게 순수 함수만 둔다.

화면에는 '조건에 맞는 종목'만 보여 준다. 매수·매도 판단 문구는 만들지 않는다.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta

import pandas as pd

from .util import EOK

# ---------------------------------------------------------------- 공통


def pct_word(p):
    """1년 백분위를 말로 (화면의 pctWord 와 같은 구간)."""
    if p is None:
        return ""
    return "1년 중 가장 높은 쪽" if p >= 90 else "1년 중 높은 편" if p >= 70 else "1년 중 보통" if p > 30 else \
        "1년 중 낮은 편" if p > 10 else "1년 중 가장 낮은 쪽"


def pct_rank(values, x):
    """values 안에서 x 가 몇 % 위치인지 (0~100)."""
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals or x is None or math.isnan(x):
        return None
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return round(100 * (below + 0.5 * equal) / len(vals))


def score_percentiles(items, past_scores):
    """정렬 점수를 탭끼리 같은 눈금(0~100)으로: 지금까지 그 탭 후보 점수들 중 몇 % 위치인지."""
    pool = list(past_scores) + [x["score"] for x in items if x.get("score") is not None]
    for x in items:
        if x.get("score") is not None:
            x["score_pct"] = pct_rank(pool, x["score"])
            x["score_pool"] = len(pool)
    return items


def mkt_of(market):
    """비교할 시장 지수 이름: 코스닥 글로벌 세그먼트도 코스닥 지수와 비교한다."""
    return "KOSDAQ" if (market or "").startswith("KOSDAQ") else market


def r1(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 1)


def realized_vol(closes, n=20):
    """최근 n 일 일간 로그수익률 표준편차를 연율화(%)."""
    rets = (closes / closes.shift(1)).apply(math.log).dropna()
    return rets.rolling(n).std() * math.sqrt(252) * 100


# ---------------------------------------------------------------- 시장 온도


def series_stat(rows, days_back=5, year=252):
    """[(날짜, 값)] → 최신값, n일 전 대비, 1년 내 백분위."""
    if not rows:
        return None
    vals = [v for _, v in rows]
    last_d, last = rows[-1]
    prev = vals[-1 - days_back] if len(vals) > days_back else None
    return {
        "date": last_d,
        "value": r1(last),
        "change": r1(last - prev) if prev is not None else None,
        "pct_1y": pct_rank(vals[-year:], last),
        "spark": [r1(v) for v in vals[-60:]],
    }


FUT_MULT = 250_000  # 코스피200 선물 1포인트 = 25만 원


def futures_summary(rows):
    """[(날짜, 외국인, 기관, 개인, 가격)] (계약) → 외국인 코스피200 선물 순매수 요약.

    금액(억 원) = 계약 × 그날 선물 가격 × 25만 원. 가격이 비면 가장 가까운 앞날 가격으로 채운다.
    """
    if not rows or len(rows) < 2:
        return None
    last_price, daily = None, []
    for d, fo, ins, ret, price in rows:
        last_price = price or last_price
        eok = (lambda c: round(c * last_price * FUT_MULT / EOK)) if last_price else (lambda c: None)
        daily.append({"date": d, "foreign": fo, "inst": ins, "retail": ret,
                      "foreign_eok": eok(fo), "inst_eok": eok(ins or 0), "retail_eok": eok(ret or 0)})
    recent = daily[-20:]
    total = lambda key, n: sum((x[key] or 0) for x in daily[-n:])
    cum, acc = [], 0
    for x in recent:
        acc += x["foreign"]
        cum.append(acc)
    up_days = sum(1 for x in recent if x["foreign"] > 0)
    # 지금 방향이 며칠째 이어지는지
    streak, sign = 0, (1 if daily[-1]["foreign"] > 0 else -1)
    for x in reversed(daily):
        if (x["foreign"] > 0) - (x["foreign"] < 0) != sign:
            break
        streak += 1
    return {"date": f"{rows[-1][0][:4]}-{rows[-1][0][4:6]}-{rows[-1][0][6:]}",
            "today": daily[-1]["foreign"], "d5": total("foreign", 5), "d20": total("foreign", 20),
            "today_eok": daily[-1]["foreign_eok"], "d5_eok": total("foreign_eok", 5), "d20_eok": total("foreign_eok", 20),
            "inst_today": daily[-1]["inst"], "retail_today": daily[-1]["retail"],
            "inst_today_eok": daily[-1]["inst_eok"], "retail_today_eok": daily[-1]["retail_eok"],
            "price": rows[-1][4], "cum20": cum, "up_days20": up_days, "days20": len(recent),
            "streak": streak * sign, "daily": recent}


def credit_summary(loans, funds):
    """FreeSIS 신용공여 잔고(STATSCU0100000070BO) + 증시자금(STATSCU0100000060BO) → 억 원 단위 요약.

    loans: TMPV2 신용거래융자 전체, TMPV3 코스피, TMPV4 코스닥
    funds: TMPV5 위탁매매 미수금, TMPV6 반대매매 금액, TMPV7 미수금 대비 반대매매 비중(%)
    """
    eok = lambda v: None if v is None else v / 100  # 백만 원 → 억 원
    iso = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    out = {}
    if loans:
        last = loans[-1]
        out["loan"] = {**series_stat([(iso(r["TMPV1"]), eok(r["TMPV2"])) for r in loans]),
                       "kospi": r1(eok(last.get("TMPV3"))), "kosdaq": r1(eok(last.get("TMPV4")))}
    if funds:
        last = funds[-1]
        forced = [(iso(r["TMPV1"]), eok(r["TMPV6"])) for r in funds]
        # 오늘 급증을 평균과 비교하므로 평균에는 오늘을 넣지 않는다 (앞 20일)
        recent = [v for _, v in forced[-21:-1]] or [forced[-1][1]]
        out["forced"] = {**series_stat(forced), "unpaid": r1(eok(last.get("TMPV5"))),
                         "ratio": last.get("TMPV7"), "avg20": r1(sum(recent) / len(recent))}
    return out or None


def korea_vol(kospi200):
    """VKOSPI 대체: 코스피200 20일 실제 변동성."""
    vol = realized_vol(kospi200).dropna()
    rows = [(d.strftime("%Y-%m-%d"), v) for d, v in vol.items()]
    return series_stat(rows)


def breadth(closes, window=60, recent=20, markets=None):
    """closes: DataFrame(행=영업일 오래된 순, 열=티커). markets: 티커 → 시장(KOSPI/KOSDAQ).

    상승·하락 종목 수(전체 + 시장별), window 일 신고가·신저가 종목 수(최근 recent 일 추이).
    """
    rose = closes.iloc[-1] > closes.iloc[-2]
    fell = closes.iloc[-1] < closes.iloc[-2]
    up, down = int(rose.sum()), int(fell.sum())
    by_market = {}
    for m in ("KOSPI", "KOSDAQ"):
        cols = [c for c in closes.columns if (markets or {}).get(c) == m]
        if cols:
            by_market[m] = {"up": int(rose[cols].sum()), "down": int(fell[cols].sum())}
    lows, highs = [], []
    for i in range(max(window, len(closes) - recent), len(closes)):
        win = closes.iloc[i - window + 1:i + 1]
        today = win.iloc[-1]
        valid = win.notna().sum() >= window * 0.9
        lows.append(int(((today <= win.min()) & valid).sum()))
        highs.append(int(((today >= win.max()) & valid).sum()))
    return {"up": up, "down": down, "by_market": by_market, "new_lows": lows, "new_highs": highs,
            "dates": [d for d in closes.index[-len(lows):]] if lows else []}


def flows_summary(df):
    """시장 전체 투자자별 순매수(원) DataFrame → 억 원 단위 최근/5일 합."""
    out = {}
    for col, key in (("외국인합계", "foreign"), ("기관합계", "inst"), ("개인", "retail")):
        if col in df:
            s = df[col].astype(float)
            out[key] = {"today": r1(s.iloc[-1] / EOK), "d5": r1(s.iloc[-5:].sum() / EOK),
                        "d20": r1(s.iloc[-20:].sum() / EOK)}
    if "외국인합계" in df and "기관합계" in df:
        both = (df["외국인합계"].astype(float) + df["기관합계"].astype(float)).iloc[-20:]
        out["pos_days20"] = int((both > 0).sum())
        out["days20"] = int(len(both))
    return out


THERMO_LABELS = [(25, "극단적 공포", "xfear"), (45, "공포", "fear"), (55, "중립", "neutral"),
                 (75, "탐욕", "greed"), (101, "극단적 탐욕", "xgreed")]


def thermo_label(score):
    for limit, label, tone in THERMO_LABELS:
        if score < limit:
            return label, tone
    return THERMO_LABELS[-1][1:]


THERMO_PARTS = (("korea_vol", "국내 변동성"), ("vix", "미국 VIX"), ("cnn", "미국 투자심리 (CNN)"), ("breadth", "신고가 vs 신저가"),
                ("momentum", "코스피 20일 추세"), ("flows", "큰손 수급 (코스피)"), ("credit", "자금시장 (하이일드)"),
                ("forced", "반대매매 (강제 매도)"))


def thermometer(t):
    """국내 시장 온도계: 0 = 극단적 공포, 100 = 극단적 탐욕. CNN 방식처럼 여러 지표를 같은 무게로 평균.

    각 항목은 '탐욕 쪽일수록 높은' 0~100 점수로 바꾼다.
    """
    parts = []

    def add(key, name, score, raw, weight=1.0):
        if score is None:
            return
        parts.append({"key": key, "name": name, "score": round(max(0, min(100, score))), "raw": raw, "weight": weight})

    kv = t.get("korea_vol")
    if kv and kv.get("pct_1y") is not None:
        add("korea_vol", "국내 변동성", 100 - kv["pct_1y"], f"{kv['value']}% · {pct_word(kv['pct_1y'])}")
    vix = t.get("vix")
    if vix and vix.get("pct_1y") is not None:
        add("vix", "미국 VIX", 100 - vix["pct_1y"], f"{vix['value']} · {pct_word(vix['pct_1y'])}")
    fg = t.get("fear_greed")
    if fg and fg.get("score") is not None:
        add("cnn", "미국 투자심리 (CNN)", fg["score"], f"{fg['score']}점")
    b = t.get("breadth")
    if b and b.get("new_lows"):
        hi, lo = b["new_highs"][-1], b["new_lows"][-1]
        # 코스피 20일 추세와 같은 '주가 하락'을 보는 지표라 둘 다 반 무게 (급락장에서 공포 쪽으로 겹쳐 쏠리지 않게)
        add("breadth", "신고가 vs 신저가", 50 if hi + lo == 0 else 100 * hi / (hi + lo), f"신고가 {hi} · 신저가 {lo}", 0.5)
    k = (t.get("index") or {}).get("kospi")
    if k and k.get("change_20d") is not None:
        add("momentum", "코스피 20일 추세", 50 + k["change_20d"] * 3, f"20일 {k['change_20d']:+.1f}%", 0.5)
    fl = (t.get("flows") or {}).get("KOSPI")
    if fl and fl.get("days20"):
        add("flows", "큰손 수급 (코스피)", 100 * fl["pos_days20"] / fl["days20"],
            f"20일 중 {fl['pos_days20']}일 외국인+기관 순매수")
    hy = t.get("hy_spread")
    if hy and hy.get("pct_1y") is not None:
        add("credit", "자금시장 (하이일드)", 100 - hy["pct_1y"], f"{hy['value']}%p · {pct_word(hy['pct_1y'])}")
    cb = t.get("credit_balance") or {}
    fo = cb.get("forced")
    if fo and fo.get("pct_1y") is not None:
        add("forced", "반대매매 (강제 매도)", 100 - fo["pct_1y"], f"{fo['value']:,.0f}억 · {pct_word(fo['pct_1y'])}")
    # 빚투(신용융자)는 많으면 탐욕이지만 급감도 위험 신호라 방향이 모호해 온도계에서는 뺀다 (칸으로만 보여 줌)
    if len(parts) < 3:
        return None
    score = round(sum(p["score"] * p["weight"] for p in parts) / sum(p["weight"] for p in parts))
    label, tone = thermo_label(score)
    got = {p["key"] for p in parts}
    missing = [name for key, name in THERMO_PARTS if key not in got]   # 그날 자료를 못 받아 평균에서 빠진 지표
    return {"score": score, "label": label, "tone": tone, "parts": parts, "missing": missing}


def summarize_temperature(t):
    """지표 → 세 가지 한 줄 판단 (공포 크기 / 매도 압력 / 자금시장)."""
    out = []

    # 1. 공포 크기: VIX·국내 변동성 백분위, CNN 점수
    scores = []
    if t.get("vix") and t["vix"]["pct_1y"] is not None:
        scores.append(t["vix"]["pct_1y"])
    if t.get("korea_vol") and t["korea_vol"]["pct_1y"] is not None:
        scores.append(t["korea_vol"]["pct_1y"])
    if t.get("fear_greed"):
        scores.append(100 - t["fear_greed"]["score"])
    if scores:
        s = sum(scores) / len(scores)
        level = "큼" if s >= 70 else "보통" if s >= 35 else "작음"
        tone = "fear" if s >= 70 else "neutral" if s >= 35 else "calm"
        out.append({"key": "fear", "title": "공포 크기", "level": level, "tone": tone,
                    "score": round(s),
                    "text": f"시장이 얼마나 겁먹었나를 지난 1년과 비교하면 {round(s)}점 (100점 = 1년 중 가장 무서웠던 때, 0점 = 가장 편했던 때)."})

    # 2. 매도 압력: 신저가 종목 수가 최근 5일 평균보다 늘었나
    b = t.get("breadth")
    if b and len(b["new_lows"]) >= 6:
        now, avg = b["new_lows"][-1], sum(b["new_lows"][-6:-1]) / 5
        if now > avg * 1.3 and now >= 30:
            level, tone, text = "커지는 중", "fear", f"석 달 만에 가장 싼 값까지 떨어진 종목(신저가) {now}개 — 최근 5일 평균 {avg:.0f}개보다 많음. 파는 힘이 세지는 중."
        elif now < avg * 0.7:
            level, tone, text = "줄어드는 중", "calm", f"석 달 만에 가장 싼 값까지 떨어진 종목(신저가) {now}개 — 최근 5일 평균 {avg:.0f}개보다 적음. 파는 힘이 약해지는 중."
        else:
            level, tone, text = "비슷함", "neutral", f"석 달 만에 가장 싼 값까지 떨어진 종목(신저가) {now}개 — 최근 5일 평균 {avg:.0f}개와 비슷."
        # 반대매매가 20일 평균의 2배 넘게 몰리면 신저가와 상관없이 매도 압력이 커지는 중으로 본다
        fo = (t.get("credit_balance") or {}).get("forced")
        if fo and fo.get("avg20"):
            mult = fo["value"] / fo["avg20"]
            if mult >= 2 and fo["value"] >= 300:
                level, tone = "커지는 중", "fear"
                text += f" 빚 때문에 강제로 팔린 금액(반대매매) {fo['value']:,.0f}억 — 평소의 {mult:.1f}배."
            else:
                text += f" 빚 때문에 강제로 팔린 금액(반대매매) {fo['value']:,.0f}억 (평소 {fo['avg20']:,.0f}억)."
        out.append({"key": "selling", "title": "매도 압력", "level": level, "tone": tone, "text": text})

    # 3. 자금시장: 하이일드 스프레드
    hy = t.get("hy_spread")
    if hy:
        wide = (hy["pct_1y"] or 0) >= 80 or (hy["change"] or 0) >= 0.5
        out.append({"key": "credit", "title": "자금시장", "level": "경색 신호" if wide else "안정",
                    "tone": "fear" if wide else "calm",
                    "text": f"미국에서 신용 낮은 회사가 돈 빌릴 때 더 내는 이자(하이일드 스프레드) {hy['value']}%p — {pct_word(hy['pct_1y'])}. "
                            + ("돈 빌리기가 어려워지는 쪽이라 조심." if wide else "주가가 무서워도 돈줄이 막히는 위기로 번지진 않음.")})
    return out


# ---------------------------------------------------------------- 종목 이름 매칭

# 일반 단어와 겹쳐 오탐이 많은 종목명
NAME_STOP = {"대상", "국보", "서울", "보성", "화성", "동방", "삼양", "태양", "선진", "미래", "신화", "한국",
             "대원", "부국", "삼일", "일정", "세원", "한일", "대한", "우진", "경방", "방림", "백광", "유니"}


HANGUL = re.compile(r"[가-힣]")
PARTICLES = set("은는이가을를의와과도만에로")


def is_common_stock(code, name, section=""):
    if code[-1] != "0":           # 우선주 코드는 끝자리가 0 이 아님
        return False
    if "스팩" in name or re.search(r"SPAC", name, re.I):
        return False
    if "관리" in section:
        return False
    return True


class NameMatcher:
    """뉴스 제목에서 종목명을 찾는다. 긴 이름부터 맞춰 'LG' 가 'LG전자' 를 가로채지 않게 한다."""

    def __init__(self, names):
        # names: {코드: 이름}
        items = [(n, c) for c, n in names.items() if len(n) >= 2 and n not in NAME_STOP]
        items.sort(key=lambda x: -len(x[0]))
        self.items = items
        self.by_first = {}
        for n, c in items:
            self.by_first.setdefault(n[0], []).append((n, c))

    def find(self, text):
        found, used = [], [False] * len(text)
        for i, ch in enumerate(text):
            for n, c in self.by_first.get(ch, ()):
                if text.startswith(n, i) and not any(used[i:i + len(n)]):
                    nxt = text[i + len(n)] if i + len(n) < len(text) else ""
                    prv = text[i - 1] if i > 0 else ""
                    # 영문 이름은 앞뒤가 영문이면 다른 단어 (예: 'KT' in 'KTX')
                    if re.match(r"[A-Za-z]", n[-1]) and re.match(r"[A-Za-z]", nxt):
                        continue
                    if re.match(r"[A-Za-z]", n[0]) and re.match(r"[A-Za-z]", prv):
                        continue
                    # 두 글자 한글 이름은 뒤에 조사만 허용 (예: '동서' in '동서대학교' 제외)
                    if len(n) == 2 and HANGUL.match(n) and HANGUL.match(nxt) and nxt not in PARTICLES:
                        continue
                    if HANGUL.match(n[0]) and HANGUL.match(prv) and len(n) <= 3:
                        continue
                    for k in range(i, i + len(n)):
                        used[k] = True
                    if c not in found:
                        found.append(c)
                    break
        return found


def parse_earnings(text):
    """실적 공시 본문 → 영업이익 방향. 잠정실적 표: 당해실적 | 이번 | 직전분기 | 증감 | 전환 | 전년 같은 분기 | 증감 | 전환.

    전년 같은 분기와 비교한다 (계절성 때문에 직전 분기보다 낫다). 읽지 못하면 None.
    """
    m = re.search(r"영업이익 \| 당해실적 \| (.*?) \| 누계실적", text or "")
    if not m:
        return None
    f = [x.strip() for x in m.group(1).split("|")]
    num = lambda v: float(v.replace(",", "")) if re.fullmatch(r"-?[\d,]+(\.\d+)?", v) else None
    cur = num(f[0]) if f else None
    prev = num(f[4]) if len(f) > 4 else None
    turn = f[6] if len(f) > 6 else ""
    if cur is None:
        return None
    if "적자전환" in turn or (cur < 0 and prev is not None and prev >= 0):
        return {"dir": "down", "label": "영업이익 적자 전환", "cur": cur, "prev": prev}
    if cur < 0:
        return {"dir": "down", "label": "영업이익 적자 지속", "cur": cur, "prev": prev}
    if "흑자전환" in turn or (prev is not None and prev < 0):
        return {"dir": "up", "label": "영업이익 흑자 전환", "cur": cur, "prev": prev}
    if not prev:
        return None
    ch = (cur / prev - 1) * 100
    return {"dir": "up" if ch >= 0 else "down", "label": f"영업이익 전년 같은 분기 대비 {ch:+.0f}%", "cur": cur, "prev": prev}


def attach_news(items, matcher, since, until):
    """뉴스·공시 → {코드: [기사…]} (since ≤ 시각 ≤ until)."""
    out = {}
    for it in items:
        at = datetime.fromisoformat(it["at"])
        if not (since <= at <= until):
            continue
        if it["kind"] == "공시":
            codes = matcher.find(it["corp"])[:1] if it.get("good") else []
        else:
            codes = matcher.find(it["title"])
            if len(codes) > 3:   # 종목 나열형 기사(시황 요약)는 신호가 약하다
                continue
        for c in codes:
            out.setdefault(c, []).append(it)
    for c in out:
        out[c].sort(key=lambda x: x["at"], reverse=True)
    return out


# ---------------------------------------------------------------- 종목 필터

RULES = {
    "min_cap": 500 * EOK,
    "min_value": 10 * EOK,
    "scalp_value_mult": 3.0,
    "scalp_min_change": 2.0,
    "scalp_min_net_ratio": 2.0,   # 오늘 기관+외국인 순매수 / 거래대금 (%) — 1원만 사도 통과하지 않게
    "swing_min_days": 3,
    "swing_min_ratio": 0.05,      # 5일 기관+외국인 순매수 / 시총 (%)
    "swing_max_5d_rise": 20.0,
}


def _money(series, code):
    v = series.get(code) if series is not None else None
    return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def flow_detail(code, flows_daily, flows_5d, pension_5d):
    """종목 하나의 기관·외국인 수급 요약 (억): 오늘, 5일 합, 순매수한 날 수, 연속일, 일별 합."""
    day = [_money(fl["foreign"], code) + _money(fl["inst"], code) for _, fl in flows_daily]
    streak = 0
    for v in reversed(day):
        if v <= 0:
            break
        streak += 1
    last = flows_daily[-1][1] if flows_daily else {}
    return {
        "foreign_today": r1(_money(last.get("foreign"), code) / EOK), "inst_today": r1(_money(last.get("inst"), code) / EOK),
        "foreign_5d": r1(_money(flows_5d["foreign"], code) / EOK), "inst_5d": r1(_money(flows_5d["inst"], code) / EOK),
        "pension_5d": r1(_money(pension_5d, code) / EOK),
        "days_pos": sum(1 for v in day if v > 0), "streak": streak,
        "daily": [{"date": d, "sum": r1(v / EOK)} for (d, _), v in zip(flows_daily, day)],
    }


def scalp_checks(mult, change, net_today, value, rules=None):
    """단타 조건 (거래대금 급증·상승·오늘 큰손 순매수). 종목 필터와 전체 종목 검색이 같이 쓴다."""
    rules = rules or RULES
    ratio = net_today / value * 100 if value else 0
    return [
        {"label": f"거래대금 20일 평균의 {rules['scalp_value_mult']:.0f}배 이상", "ok": mult >= rules["scalp_value_mult"],
         "value": f"{mult:.1f}배"},
        {"label": f"오늘 {rules['scalp_min_change']:.0f}% 이상 상승", "ok": change >= rules["scalp_min_change"],
         "value": f"{change:+.1f}%"},
        {"label": f"오늘 기관+외국인 순매수가 거래대금의 {rules['scalp_min_net_ratio']:.0f}% 이상",
         "ok": bool(value) and ratio >= rules["scalp_min_net_ratio"],
         "value": f"{net_today / EOK:+,.0f}억 ({ratio:.1f}%)"},
    ]


def swing_checks(close, m20, m60, m20_prev, rise5, days_pos, ratio, rules=None):
    """스윙 조건 (며칠째 큰손 순매수·오르는 흐름·과열 아님)."""
    rules = rules or RULES
    return [
        {"label": f"5일 중 {rules['swing_min_days']}일 이상 기관+외국인 순매수", "ok": days_pos >= rules["swing_min_days"],
         "value": f"{days_pos}일"},
        {"label": f"5일 순매수가 회사 덩치(시총)의 {rules['swing_min_ratio']}% 이상", "ok": ratio >= rules["swing_min_ratio"],
         "value": f"{ratio:.2f}%"},
        {"label": "주가 > 20일 평균 > 60일 평균 (오르는 흐름)", "ok": close > m20 > m60,
         "value": f"{close:,.0f} / {m20:,.0f} / {m60:,.0f}"},
        {"label": "20일 평균 가격이 오르는 중", "ok": m20 > m20_prev, "value": f"{(m20 / m20_prev - 1) * 100:+.1f}%"},
        {"label": f"5일 상승률 {rules['swing_max_5d_rise']:.0f}% 이하 (과열 아님)",
         "ok": rise5 <= rules["swing_max_5d_rise"], "value": f"{rise5:+.1f}%"},
    ]


def value_checks(drop, pbr, med, per, eps, eps0, rules=None):
    """저평가 조건 (많이 빠짐·평소보다 쌈·절대로도 쌈·이익 유지)."""
    rules = rules or VALUE_RULES
    return [
        {"label": f"60일 최고가 대비 {rules['min_drop']:.0f}% 이상 하락", "ok": drop >= rules["min_drop"],
         "value": f"-{drop:.1f}%"},
        {"label": f"PBR이 1년 중앙값의 {rules['max_pbr_ratio']:.0%} 이하", "ok": pbr / med <= rules["max_pbr_ratio"],
         "value": f"{pbr:.2f} / {med:.2f}"},
        {"label": f"흑자이고 PER {rules['max_per']:.0f} 이하", "ok": per <= rules["max_per"], "value": f"{per:.1f}"},
        {"label": f"PBR {rules['max_pbr']:.0f} 이하", "ok": pbr <= rules["max_pbr"], "value": f"{pbr:.2f}"},
        {"label": f"EPS가 1년 전의 {rules['min_eps_ratio']:.0%} 이상", "ok": eps / eps0 >= rules["min_eps_ratio"],
         "value": f"{eps:,.0f} / {eps0:,.0f}"},
    ]


def screen(universe, closes, values, flows_daily, flows_5d, pension_5d, news_today, news_3d, rules=RULES):
    """단타·스윙 후보.

    universe:   {코드: listing 정보} (보통주만)
    closes/values: DataFrame(행=영업일 오래된 순, 열=티커), 마지막 행이 기준일
    flows_daily: [(ymd, {"foreign": Series, "inst": Series})] 최근 5영업일 (오래된 순)
    flows_5d:    {"foreign": Series, "inst": Series} 5일 합
    pension_5d:  Series 연기금 5일 합
    news_today / news_3d: {코드: [기사]}
    """
    scalp, swing = [], []
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    avg_val20 = values.iloc[-21:-1].mean()

    money = _money
    for code, info in universe.items():
        if code not in closes.columns:
            continue
        if info["cap"] < rules["min_cap"] or info["value"] < rules["min_value"]:
            continue
        has_today, has_3d = code in news_today, code in news_3d
        if not has_3d:
            continue

        close = closes[code].iloc[-1]
        flow = flow_detail(code, flows_daily, flows_5d, pension_5d)
        f_today = money(flows_daily[-1][1]["foreign"], code) if flows_daily else 0.0
        i_today = money(flows_daily[-1][1]["inst"], code) if flows_daily else 0.0
        f5, i5 = money(flows_5d["foreign"], code), money(flows_5d["inst"], code)
        days_pos, streak = flow["days_pos"], flow["streak"]
        base = {
            "code": code, "name": info["name"], "market": info["market"],
            "close": close, "change": r1(info["change"]),
            "value_eok": r1(info["value"] / EOK), "cap_eok": round(info["cap"] / EOK),
            "flow": flow,
        }

        # 단타: 오늘 기사 + 거래대금 급증 + 상승 + 기관·외국인 순매수
        avg = avg_val20.get(code)
        mult = info["value"] / avg if avg and avg > 0 else None
        if has_today and mult is not None:
            checks = scalp_checks(mult, info["change"], f_today + i_today, info["value"], rules)
            if all(c["ok"] for c in checks):
                score, parts = scalp_score(mult, info["change"], max(0, f_today + i_today) / info["value"])
                scalp.append({**base, "checks": checks, "score": score, "score_parts": parts,
                              "value_mult": r1(mult), "news": news_today[code][:4]})

        # 스윙: 3일 내 기사 + 며칠째 기관·외국인 순매수 + 정배열 + 과열 아님
        if len(closes) >= 61 and not math.isnan(ma60[code].iloc[-1]):
            m20, m60 = ma20[code].iloc[-1], ma60[code].iloc[-1]
            m20_prev = ma20[code].iloc[-6]
            rise5 = (close / closes[code].iloc[-6] - 1) * 100
            ratio = (f5 + i5) / info["cap"] * 100 if info["cap"] else 0
            checks = swing_checks(close, m20, m60, m20_prev, rise5, days_pos, ratio, rules)
            if all(c["ok"] for c in checks):
                score = ratio * (1 + streak / 5)
                swing.append({**base, "checks": checks, "score": round(score, 3), "ratio": round(ratio, 4),
                              "rise_5d": r1(rise5), "news": news_3d[code][:4]})

    scalp.sort(key=lambda x: -x["score"])
    swing.sort(key=lambda x: -x["score"])
    return scalp, swing


# ---------------------------------------------------------------- 공포 속 저평가

VALUE_RULES = {
    "min_cap": 1000 * EOK,
    "min_value": 5 * EOK,
    "min_drop": 20.0,          # 60일 최고가 대비 하락률(%)
    "max_pbr_ratio": 0.8,      # 현재 PBR / 1년 PBR 중앙값
    "min_eps_ratio": 0.8,      # 현재 EPS / 1년 전 EPS (이익이 크게 줄지 않음)
    # 1년 내내 급등했던 종목은 '자기 중앙값 대비'로는 싸 보여도 비싸다 → 절대 기준도 둔다
    "max_per": 15.0,
    "max_pbr": 2.0,
}


# 저평가 점수: 네 가지를 각각 0~100으로 맞춰 가중평균 (예전 '하락률 × 중앙PBR/현재PBR'은
# 주가 하락을 두 번 곱하고 거품 꺼진 종목을 과하게 우대해서 바꿈)
VALUE_WEIGHTS = {"drop": 0.25, "cheap": 0.15, "per": 0.35, "eps": 0.25}


def value_score(drop, pbr_ratio, per, eps_ratio, fwd_ratio=None, exact=False):
    """→ (0~100 점수, 항목별 0~100). fwd_ratio = 올해 추정 EPS / 지금 EPS (있으면 이익 항목에 반반 섞음).
    exact=True 면 항목 값을 반올림하지 않는다 (어제 대비 변화를 항목별로 나눌 때)."""
    clip = lambda v: max(0.0, min(1.0, v))
    eps_part = clip((eps_ratio - 0.8) / 0.7)         # EPS 1년 전의 0.8배 → 0, 1.5배 이상 → 100
    if fwd_ratio is not None:
        eps_part = (eps_part + clip((fwd_ratio - 0.8) / 0.7)) / 2  # 추정치도 같은 눈금 (결산 EPS는 늦게 바뀌므로)
    parts = {
        "drop": clip((drop - 20) / 30),              # 낙폭 20% → 0, 50% 이상 → 100
        "cheap": clip((1 - pbr_ratio - 0.2) / 0.5),  # 1년 중앙 PBR 대비 할인 20% → 0, 70% 이상 → 100
        "per": clip((15 - per) / 10),                # PER 15 → 0, 5 이하 → 100
        "eps": eps_part,
    }
    score = sum(VALUE_WEIGHTS[k] * v for k, v in parts.items()) * 100
    return round(score, 1), {k: (v * 100 if exact else round(v * 100)) for k, v in parts.items()}


# 단타 점수: 거래량이 폭발하고 크게 튄 '주목받는 종목'은 이후 성적이 나쁜 경향(개인의 주목 매수, Barber·Odean 2008)이
# 실제 기록에서도 보여(거래가 가장 몰린 쪽이 더 나빴음), 큰손 확인을 중심에 두고 과열은 깎는다
SCALP_WEIGHTS = {"flow": 0.5, "volume": 0.25, "move": 0.25}


def scalp_score(mult, change, net_ratio, exact=False):
    """mult = 거래대금/20일 평균, change = 등락률(%), net_ratio = 기관+외국인 순매수 / 거래대금 → (0~100, 항목별 0~100)."""
    clip = lambda v: max(0.0, min(1.0, v))
    vol = clip(math.log(max(mult, 1e-9) / 3) / math.log(8 / 3))   # 3배 → 0, 8배 이상 → 100
    if mult > 15:
        vol *= clip(1 - (mult - 15) / 15)                          # 15배 넘으면 깎여 30배에 0
    if change < 5:
        move = clip((change - 2) / 3)                              # 필터 하한 2% → 0, 5% → 100
    elif change <= 15:
        move = 1.0
    else:
        move = clip((29 - change) / 14)                            # 15% → 100, 상한가 근처 29% → 0
    parts = {"flow": clip(net_ratio / 0.2), "volume": vol, "move": move}  # 순매수 비중 0% → 0, 20% 이상 → 100
    score = sum(SCALP_WEIGHTS[k] * v for k, v in parts.items()) * 100
    return round(score, 1), {k: (v * 100 if exact else round(v * 100)) for k, v in parts.items()}


def rescore(group, x):
    """기록 파일의 후보 점수. 저평가·단타는 저장된 항목으로 지금 공식에 맞춰 다시 계산한다."""
    if group == "value" and all(x.get(k) is not None for k in ("drop_60d", "pbr", "pbr_med", "per", "eps_ratio")) and x["pbr_med"]:
        return value_score(x["drop_60d"], x["pbr"] / x["pbr_med"], x["per"], x["eps_ratio"], x.get("eps_fwd_ratio"))[0]
    fl = x.get("flow") or {}
    if group == "scalp" and x.get("value_mult") and x.get("value_eok") and x.get("change") is not None:
        net = (fl.get("foreign_today") or 0) + (fl.get("inst_today") or 0)
        return scalp_score(x["value_mult"], x["change"], max(0, net) / x["value_eok"])[0]
    return x.get("score")


def apply_forward_eps(value, profiles):
    """저평가 후보에 네이버 추정 EPS를 반영해 점수를 다시 매기고 다시 정렬한다."""
    for x in value:
        p = profiles.get(x["code"]) or {}
        eps, cns = p.get("eps"), p.get("cns_eps")
        if eps and eps > 0 and cns is not None:
            x["eps_fwd_ratio"] = round(cns / eps, 2)
            x["score"], x["score_parts"] = value_score(x["drop_60d"], x["pbr"] / x["pbr_med"], x["per"],
                                                       x["eps_ratio"], x["eps_fwd_ratio"])
    value.sort(key=lambda x: -x["score"])
    return value


# 싼 종목은 시장이 안 보면 몇 년이고 싼 채로 남는다(가치 함정). 그래서 '다시 주목받기 시작했나'를 따로 본다.
WAKE_RULES = {"vol_mult": 1.3, "rebound": 5.0}


def value_wake(code, col, flow5, has_news, values=None, rules=WAKE_RULES):
    """저평가 후보의 주목 신호: 큰손 매수, 소식, 거래 증가, 바닥 반등. 점수엔 안 넣고 따로 보여 주며 성적표로 확인한다."""
    low10 = col.iloc[-10:].min()
    rebound = (col.iloc[-1] / low10 - 1) * 100 if low10 > 0 else 0.0
    checks = [
        {"key": "flow", "label": "큰손(기관+외국인)이 최근 5일 동안 삼", "ok": flow5 > 0, "value": f"{flow5 / EOK:+,.0f}억"},
        {"key": "news", "label": "최근 3일 안에 기사·공시가 나옴", "ok": bool(has_news), "value": "있음" if has_news else "없음"},
    ]
    if values is not None and code in values.columns:
        v = values[code]
        avg20, avg5 = v.iloc[-21:-1].mean(), v.iloc[-5:].mean()
        mult = avg5 / avg20 if avg20 and avg20 > 0 else 0.0
        checks.append({"key": "volume", "label": f"거래가 살아남 (5일 평균 거래대금이 평소의 {rules['vol_mult']}배 이상)",
                       "ok": mult >= rules["vol_mult"], "value": f"{mult:.1f}배"})
    checks.append({"key": "rebound", "label": f"바닥에서 고개 듦 (최근 10일 최저가보다 {rules['rebound']:.0f}% 이상 위)",
                   "ok": rebound >= rules["rebound"], "value": f"+{rebound:.1f}%"})
    return {"met": sum(1 for c in checks if c["ok"]), "total": len(checks), "checks": checks}


def add_report_wake(x):
    """저평가 주목 신호에 '증권사 리포트가 최근 2주 안에 나옴'을 더한다 (리포트 기록이 있을 때만)."""
    w, rep = x.get("wake"), x.get("reports")
    if not w or rep is None or any(c.get("key") == "report" for c in w["checks"]):
        return x
    ok = rep["n"] > 0
    value = ("오랜만에 " if rep.get("fresh") else "") + (f"{rep['n']}건" if ok else "없음")
    w["checks"].append({"key": "report", "label": "증권사 리포트가 최근 2주 안에 나옴", "ok": ok, "value": value})
    w["met"] += 1 if ok else 0
    w["total"] += 1
    return x


def value_screen(universe, closes, fund_now, fund_hist, flows_5d, news_3d, rules=VALUE_RULES, values=None):
    """많이 떨어졌는데 장부가치 대비 싸졌고, 이익은 크게 줄지 않은 종목.

    fund_now: DataFrame(index=티커, BPS/PER/PBR/EPS)
    fund_hist: [DataFrame] 1년 동안의 과거 스냅샷 (오래된 순, 첫 번째가 약 1년 전)
    values: 거래대금 DataFrame (주목 신호의 '거래가 살아남'용, 없으면 그 항목은 빠짐)
    """
    out = []
    if fund_now is None or len(fund_now) == 0 or not fund_hist:
        return out
    pbr_hist = pd.DataFrame({i: f["PBR"] for i, f in enumerate(fund_hist)})
    pbr_hist = pbr_hist.where(pbr_hist > 0)
    pbr_med = pbr_hist.median(axis=1)
    eps_year_ago = fund_hist[0]["EPS"]

    for code, info in universe.items():
        if code not in closes.columns or code not in fund_now.index:
            continue
        if info["cap"] < rules["min_cap"] or info["value"] < rules["min_value"]:
            continue
        col = closes[code].dropna()
        if len(col) < 60:
            continue
        close, high60 = col.iloc[-1], col.iloc[-60:].max()
        drop = (1 - close / high60) * 100
        f = fund_now.loc[code]
        pbr, per, eps = f.get("PBR", 0), f.get("PER", 0), f.get("EPS", 0)
        med = pbr_med.get(code)
        eps0 = eps_year_ago.get(code)
        if not (pbr > 0 and per > 0 and eps > 0) or med is None or math.isnan(med) or med <= 0:
            continue
        if eps0 is None or math.isnan(eps0) or eps0 <= 0:
            continue
        pbr_ratio, eps_ratio = pbr / med, eps / eps0
        checks = value_checks(drop, pbr, med, per, eps, eps0, rules)
        if not all(c["ok"] for c in checks):
            continue
        f5 = float(flows_5d["foreign"].get(code, 0) or 0)
        i5 = float(flows_5d["inst"].get(code, 0) or 0)
        out.append({
            "code": code, "name": info["name"], "market": info["market"],
            "close": close, "change": r1(info["change"]),
            "value_eok": r1(info["value"] / EOK), "cap_eok": round(info["cap"] / EOK),
            "drop_60d": r1(drop), "pbr": round(pbr, 2), "pbr_med": round(med, 2), "per": r1(per),
            "eps_ratio": round(eps_ratio, 2),
            "flow5_eok": r1((f5 + i5) / EOK),
            "checks": checks,
            **dict(zip(("score", "score_parts"), value_score(drop, pbr_ratio, per, eps_ratio))),
            "news": news_3d.get(code, [])[:3],
            "wake": value_wake(code, col, f5 + i5, code in news_3d, values),
        })
    out.sort(key=lambda x: -x["score"])
    return out


def mark_streaks(groups, prev_filters):
    """같은 탭에 며칠 연속 걸렸는지(오늘 포함)와 처음 걸린 날을 붙인다.

    groups: {"swing": [...], ...} 오늘 후보, prev_filters: [(기준일, filter dict)] 최근 날짜부터 (기록 파일엔 탭마다 상위 10개만)
    """
    for g, items in groups.items():
        for x in items:
            days, first = 1, None
            for d, flt in prev_filters:
                if any(y["code"] == x["code"] for y in flt.get(g) or []):
                    days, first = days + 1, d
                else:
                    break
            x["streak_days"], x["since"] = days, first
    return groups


def mark_overlap(scalp, swing, value):
    """수급·추세 관점(단타·스윙)과 저평가 관점에 동시에 걸린 종목 코드."""
    flow_codes = {x["code"] for x in scalp} | {x["code"] for x in swing}
    both = sorted(flow_codes & {x["code"] for x in value})
    for group in (scalp, swing, value):
        for x in group:
            x["overlap"] = x["code"] in both
    return both


# ---------------------------------------------------------------- 위험 종목

def risk_codes(dart_items, matcher, sections=None):
    """최근 악재성 공시(유상증자·CB 등) 또는 투자주의환기 종목 → {코드: [사유]}"""
    out = {}
    for it in dart_items:
        if not it.get("bad"):
            continue
        for c in matcher.find(it["corp"])[:1]:
            reasons = out.setdefault(c, [])
            label = f"{it['at'][5:10].replace('-', '/')} {it['title']}"
            if label not in reasons:
                reasons.append(label)
    for c, sec in (sections or {}).items():
        if "투자주의환기" in sec:
            out.setdefault(c, []).append(sec)
    return out


# ---------------------------------------------------------------- 종목 정보 보강

def _num(v):
    """'1,234.5배' '22,292원' '0.78%' → 숫자. 없거나 '-' 면 None."""
    if v is None:
        return None
    t = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def _eok(v):
    """'1,248조 1,805억' → 억 원."""
    if not v:
        return None
    jo = re.search(r"([\d,]+)조", v)
    eok = re.search(r"([\d,]+)억", v)
    if not jo and not eok:
        return _num(v)
    return (float(jo.group(1).replace(",", "")) * 10000 if jo else 0) + (float(eok.group(1).replace(",", "")) if eok else 0)


PROFILE_ROWS = ("매출액", "영업이익", "당기순이익", "영업이익률", "ROE", "부채비율")


def parse_profile(integ, fin):
    """네이버 integration + finance/annual 응답 → 화면용 기업 정보."""
    info = {i.get("code"): i.get("value") for i in (integ or {}).get("totalInfos") or []}
    out = {
        "cap_eok": _eok(info.get("marketValue")),
        "per": _num(info.get("per")), "cns_per": _num(info.get("cnsPer")), "cns_eps": _num(info.get("cnsEps")),
        "pbr": _num(info.get("pbr")), "eps": _num(info.get("eps")), "bps": _num(info.get("bps")),
        "div_yield": _num(info.get("dividendYieldRatio")),
        "high52": _num(info.get("highPriceOf52Weeks")), "low52": _num(info.get("lowPriceOf52Weeks")),
        "foreign_rate": _num(info.get("foreignRate")),
        "industry": ((integ or {}).get("industryCompareInfo") or [{}])[0].get("industryName") if isinstance((integ or {}).get("industryCompareInfo"), list) else None,
    }
    summ = (fin or {}).get("corporationSummary")
    if isinstance(summ, dict):
        summ = " ".join(str(v) for v in summ.values() if isinstance(v, str) and len(v) > 10)
    out["summary"] = re.sub(r"\s+", " ", summ).strip()[:600] if isinstance(summ, str) and summ.strip() else None
    fi = (fin or {}).get("financeInfo") or {}
    years = [(t["key"], t["title"].rstrip("."), t.get("isConsensus") == "Y") for t in fi.get("trTitleList") or []]
    rows = {r["title"]: r.get("columns") or {} for r in fi.get("rowList") or [] if r.get("title") in PROFILE_ROWS}
    if years and rows:
        out["annual"] = {
            "years": [{"label": lab + (" (추정)" if est else ""), "est": est} for _, lab, est in years],
            "rows": [{"title": t, "values": [_num((rows[t].get(k) or {}).get("value")) for k, _, _ in years]}
                     for t in PROFILE_ROWS if t in rows],
        }
    return out


def enrich(items, closes, sectors, quality):
    """후보 종목에 업종, 60일 차트, 수급의 질(연기금·투신·사모·외국인 지분율)을 붙인다.

    quality: {"pension": S, "trust": S, "private": S, "fratio_now": S, "fratio_prev": S} (값은 원·%)
    """
    def get(s, code):
        if s is None:
            return None
        v = s.get(code)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    for x in items:
        code = x["code"]
        x["sector"] = (sectors.get(code) if sectors is not None else None) or ""
        if code in closes.columns:
            col = closes[code]
            x["chart"] = {
                "dates": list(closes.index[-60:]),
                "close": [r1(v) for v in col.iloc[-60:]],
                "ma20": [r1(v) for v in col.rolling(20).mean().iloc[-60:]],
                "ma60": [r1(v) for v in col.rolling(60).mean().iloc[-60:]],
            }
        fr_now, fr_prev = get(quality.get("fratio_now"), code), get(quality.get("fratio_prev"), code)
        x["quality"] = {
            "pension_5d": r1((get(quality.get("pension"), code) or 0) / EOK),
            "trust_5d": r1((get(quality.get("trust"), code) or 0) / EOK),
            "private_5d": r1((get(quality.get("private"), code) or 0) / EOK),
            "fratio": r1(fr_now) if fr_now is not None else None,
            "fratio_chg": round(fr_now - fr_prev, 2) if fr_now is not None and fr_prev is not None else None,
        }
    return items


# ---------------------------------------------------------------- 업종별 돈 흐름

def sector_flows(universe, sectors, flows_5d, closes, top=8):
    """업종별 5일 기관+외국인 순매수 합(억)과 5일 등락률 중앙값."""
    if sectors is None or len(sectors) == 0:
        return {"inflow": [], "outflow": []}
    groups = {}
    for code, info in universe.items():
        sec = sectors.get(code)
        if not sec or code not in closes.columns:
            continue
        f = float(flows_5d["foreign"].get(code, 0) or 0) + float(flows_5d["inst"].get(code, 0) or 0)
        col = closes[code]
        rise = (col.iloc[-1] / col.iloc[-6] - 1) * 100 if len(col) >= 6 and col.iloc[-6] else None
        g = groups.setdefault(sec, {"flow": 0.0, "rises": [], "stocks": []})
        g["flow"] += f
        if rise is not None and not math.isnan(rise):
            g["rises"].append(rise)
        g["stocks"].append((f, info["name"]))
    rows = []
    for sec, g in groups.items():
        if len(g["stocks"]) < 3:
            continue
        rises = sorted(g["rises"])
        g["stocks"].sort(reverse=True)
        rows.append({"sector": sec, "flow_eok": round(g["flow"] / EOK), "n": len(g["stocks"]),
                     "rise_5d": r1(rises[len(rises) // 2]) if rises else None,
                     "top": [n for f, n in g["stocks"][:3] if f > 0],
                     "bottom": [n for f, n in g["stocks"][::-1][:3] if f < 0]})
    rows.sort(key=lambda r: -r["flow_eok"])
    return {"inflow": [r for r in rows if r["flow_eok"] > 0][:top],
            "outflow": [r for r in rows[::-1] if r["flow_eok"] < 0][:5]}


# ---------------------------------------------------------------- 장세 판정

REGIMES = {
    "up": "상승장", "range": "횡보·전환", "down": "하락장", "panic": "패닉",
}

# 시나리오 적합도의 근거. 화면의 '왜?'에 그대로 나간다
EVIDENCE = {
    "mom": ("오르는 장에서는 이미 오르고 있고 큰손이 사는 종목이 한동안 더 오르는 경향이 있습니다. '달리는 말에 올라타라'는 말과 같은 이야기입니다.",
            "모멘텀 연구 (Jegadeesh·Titman 1993 등)"),
    "mom_crash": ("크게 떨어진 뒤 출렁임이 큰 장에서는, 반등이 오면 그동안 많이 빠졌던 종목이 훨씬 크게 튑니다. "
                  "반대로 '그나마 버티던 종목'을 따라 산 전략은 이때 가장 크게 손해를 봤습니다. "
                  "그래서 이 장에서는 스윙(따라가기)은 피하고, 많이 빠진 저평가 종목이 상대적으로 낫습니다.",
                  "Daniel·Moskowitz (2016) '모멘텀 붕괴' 연구"),
    "trend": ("지수가 20일·60일 평균 가격보다 아래에 있을 때는 더 내려가는 경우가 많아서, 그때 따라 사면 같이 밀리기 쉽습니다. "
              "다만 방향 없이 오르내리는 장에서는 이런 신호가 자주 틀립니다.",
              "이동평균 추세 연구 (Faber 등)"),
    "value_trap": ("다들 오르는데 혼자 크게 떨어진 종목은 실적이 나빠지는 등 이유가 있는 경우가 많습니다. 싸 보여도 '싼 데는 이유가 있는' 함정일 수 있습니다.",
                   "가치 함정에 관한 일반적인 관찰"),
    "reversal": ("방향 없이 오르내리는 장에서는 며칠 사이 많이 빠진 종목이 제자리로 돌아오는 경우가 상대적으로 많습니다.",
                 "단기 반전 연구 (Jegadeesh 1990 등)"),
    "falling_knife": ("내리는 장에서는 싸 보이는 종목도 시장을 따라 더 빠지기 쉽습니다(떨어지는 칼날). 한 번에 사지 말고 나눠서 지켜보는 게 낫습니다.",
                      "추세 연구의 반대쪽 이야기"),
    "short_term": ("하루 이틀 움직임은 장 전체보다 그 종목의 뉴스 영향이 큽니다. 어느 장에서든 짧게 끊는 게 핵심이고, 장이 불안할수록 손실도 커집니다.",
                   "경험칙 (뚜렷한 연구 근거는 약함)"),
    "inv_trend": ("확실히 내리는 장에서는 하락이 며칠 이어지는 경향이 있어, 짧게 들고 가는 1배 인버스가 상대적으로 덜 불리합니다.",
                  "이동평균 추세 연구"),
    "inv_decay": ("인버스는 매일 다시 계산되는 구조라, 지수가 오르락내리락하며 제자리여도 손해가 쌓입니다. "
                  "개인 투자자는 '이제 떨어지겠지' 하고 거꾸로 들어갔다가 손해 본 경우가 많았습니다.",
                  "자본시장연구원 레버리지·인버스 ETF 분석"),
    "inv_panic": ("크게 떨어지고 다들 겁먹은 뒤에는 하루에 3~5%씩 확 튀는 날이 자주 옵니다. "
                  "이때 인버스(특히 2배)를 들고 있으면 반등 하루에 크게 잃습니다. 급락 뒤 따라 들어가는 게 가장 손해가 큰 패턴입니다.",
                  "급락 뒤 급반등 연구, 레버리지·인버스 ETF 분석"),
}

# 장세별 시나리오 적합도: (등급, 한 줄 이유, 근거 키)
FIT = {
    "up": {
        "swing": ("good", "오르는 흐름이 살아 있어, 큰손이 사는 종목이 더 갈 가능성이 큼", "mom"),
        "scalp": ("ok", "거래가 몰린 종목이 하루 이틀 더 가는 경우가 많음", "short_term"),
        "value": ("bad", "다 오르는데 혼자 빠진 종목은 이유가 있는 경우가 많음", "value_trap"),
        "inverse": ("bad", "오르는 장에서 내리는 쪽에 거는 것", "inv_decay"),
    },
    "range": {
        "swing": ("ok", "올랐다 내렸다 해서 속기 쉬움, 손절을 짧게", "trend"),
        "scalp": ("ok", "짧게 사고파는 쪽이 상대적으로 유리", "short_term"),
        "value": ("ok", "많이 빠진 종목이 제자리로 돌아오는 경우가 있음", "reversal"),
        "inverse": ("bad", "방향이 없어서 시간이 갈수록 손해가 쌓이기 쉬움", "inv_decay"),
    },
    "down": {
        "swing": ("bad", "내리는 장에서 따라 사면 같이 밀리기 쉬움", "trend"),
        "scalp": ("ok", "하루 이틀만, 손절은 짧게", "short_term"),
        "value": ("ok", "싸 보여도 더 빠질 수 있으니 나눠서 지켜보기", "falling_knife"),
        "inverse": ("good", "확실히 내리는 장이라 1배 인버스를 짧게 고려해 볼 만함", "inv_trend"),
    },
    "panic": {
        "swing": ("bad", "급반등이 오면 가장 크게 깨지는 전략", "mom_crash"),
        "scalp": ("ok", "출렁임이 커서 벌 기회도 잃을 위험도 큼, 아주 짧게", "short_term"),
        "value": ("good", "다들 겁먹었을 때 많이 빠진 종목이 크게 튀는 경향, 나눠서", "mom_crash"),
        "inverse": ("bad", "급반등이 잦아 인버스가 크게 당하는 구간, 따라 들어가지 않기", "inv_panic"),
    },
}


def _ma(vals, n, back=0):
    end = len(vals) - back
    return sum(vals[end - n:end]) / n if end >= n else None


def _trend_key(close, ma20, ma60, rising):
    if close < ma20 < ma60 and not rising:
        return "down"
    if close > ma20 > ma60 and rising:
        return "up"
    return "range"


def market_regime(kospi, t, dates=None):
    """코스피 종가(오래된 순, 66일 이상)와 그날 시장 온도 → 장세, 판정 근거, 시나리오별 적합도, 인버스 점검표.

    dates 를 주면 화면용 최근 120일 코스피·이동평균 차트도 넣는다.
    """
    if len(kospi) < 66:
        return None
    close, ma20, ma60 = kospi[-1], _ma(kospi, 20), _ma(kospi, 60)
    ma20_prev = _ma(kospi, 20, back=5)
    rising = ma20 > ma20_prev
    chg20 = (close / kospi[-21] - 1) * 100
    th = (t.get("thermo") or {}).get("score")
    kv = (t.get("korea_vol") or {}).get("pct_1y")
    fo = (t.get("credit_balance") or {}).get("forced") or {}
    fo_mult = fo["value"] / fo["avg20"] if fo.get("avg20") else None

    # 1단계: 패닉 조건 (하나라도 맞으면 추세와 상관없이 패닉)
    panic_checks = [
        {"label": "시장 온도 25점 미만 (극단적 공포)", "value": "–" if th is None else f"{th}점",
         "ok": th is not None and th < 25},
        {"label": "코스피가 20일 새 15% 넘게 떨어짐", "value": f"{chg20:+.1f}%", "ok": chg20 <= -15},
        {"label": "출렁임(변동성)이 1년 중 최상위 10%", "value": "–" if kv is None else f"{kv}% 위치",
         "ok": kv is not None and kv >= 90},
        {"label": "강제 매도(반대매매)가 평소의 2배 이상 (300억 이상)",
         "value": "–" if fo_mult is None else f"{fo['value']:,.0f}억, 평균의 {fo_mult:.1f}배",
         "ok": fo_mult is not None and fo["value"] >= 300 and fo_mult >= 2},
    ]
    met_panic = [f"{c['label'].split(' (')[0]}: {c['value']}" for c in panic_checks if c["ok"]]
    # 급락 하나만으로는 패닉이 아니다: 하락과 높은 변동성·공포가 겹친 상태 (Daniel·Moskowitz의 '패닉 구간')
    panic = met_panic if len(met_panic) >= 2 else []
    # 2단계: 추세 조건
    slope = (ma20 / ma20_prev - 1) * 100
    trend_checks = [
        {"label": "코스피가 20일 평균 가격 위", "value": f"{close:,.0f} / {ma20:,.0f} ({(close / ma20 - 1) * 100:+.1f}%)", "ok": close > ma20},
        {"label": "20일 평균이 60일 평균보다 높음", "value": f"{ma20:,.0f} / {ma60:,.0f}", "ok": ma20 > ma60},
        {"label": "20일 평균 가격이 오르는 중 (5일 전보다 높음)", "value": f"{slope:+.2f}%", "ok": rising},
    ]
    tkey = _trend_key(close, ma20, ma60, rising)
    key = "panic" if panic else tkey

    trend_txt = {"up": "코스피가 20일·60일 평균 가격보다 위에 있고 계속 오르는 중",
                 "down": "코스피가 20일·60일 평균 가격보다 아래에 있고 계속 내리는 중",
                 "range": "오르는 신호와 내리는 신호가 섞여 있음"}[tkey]
    if key == "panic":
        reason = f"급락·공포 신호가 {len(panic)}개나 겹쳐서 '패닉'입니다. 흐름만 보면 {REGIMES[tkey]}입니다 ({trend_txt})."
    else:
        pre = f"급락·공포 신호가 {len(met_panic)}개 있지만 패닉(2개 이상)까지는 아님" if met_panic else "급락·공포 신호는 없음"
        reason = f"{pre}. {trend_txt} → {REGIMES[key]}입니다."
    if key == "panic":
        nxt = f"급락·공포 신호가 잦아들면 흐름대로 다시 판정합니다. 지금 흐름이면 {REGIMES[tkey]}이 됩니다."
    elif key == "down":
        nxt = f"코스피가 20일 평균 가격({ma20:,.0f})을 넘어서면 '횡보·전환'으로 바뀝니다. 지금보다 {(ma20 / close - 1) * 100:.1f}% 더 올라야 합니다."
    elif key == "up":
        nxt = f"코스피가 20일 평균 가격({ma20:,.0f}) 아래로 내려가면 '횡보·전환'으로 바뀝니다. 지금보다 {(1 - ma20 / close) * 100:.1f}% 빠지면 그렇게 됩니다."
    else:
        nxt = (f"코스피가 평균 가격선들 위로 확실히 올라서면 상승장, "
               f"60일 평균 가격({ma60:,.0f}) 아래로 확실히 내려가면 하락장이 됩니다.")
    if key != "panic" and len(met_panic) == 1:
        nxt += " 급락·공포 신호가 하나 더 켜지면 '패닉'이 됩니다."

    fl = (t.get("flows") or {}).get("KOSPI") or {}
    b = t.get("breadth") or {}
    hy = (t.get("hy_spread") or {}).get("pct_1y")
    inv_checks = [
        {"label": "코스피가 20일·60일 평균 가격 아래", "value": f"{close:,.0f} / {ma20:,.0f} / {ma60:,.0f}", "ok": close < ma20 < ma60},
        {"label": "20일 평균 가격이 내려가는 중", "value": f"{slope:+.2f}%", "ok": not rising},
        {"label": "큰손(외국인+기관) 20일 중 순매수일 8일 이하",
         "value": "–" if fl.get("pos_days20") is None else f"{fl['pos_days20']}일",
         "ok": fl.get("pos_days20") is not None and fl["pos_days20"] <= 8},
        {"label": "신저가 종목이 신고가보다 많음",
         "value": "–" if not b.get("new_lows") else f"신저가 {b['new_lows'][-1]} / 신고가 {b['new_highs'][-1]}",
         "ok": bool(b.get("new_lows")) and b["new_lows"][-1] > b["new_highs"][-1]},
        {"label": "미국 하이일드 1년 중 70% 이상 (자금시장 긴장)", "value": "–" if hy is None else f"{hy}% 위치",
         "ok": hy is not None and hy >= 70},
    ]
    fu = t.get("futures")
    if fu:
        inv_checks.append({"label": "외국인 선물 20일 누적 순매도", "value": f"{fu['d20']:+,}계약", "ok": fu["d20"] < 0})
    met = sum(1 for c in inv_checks if c["ok"])
    need = 4 if len(inv_checks) <= 5 else 5  # 외국인 선물까지 6개면 5개 이상
    # 패닉까지는 아니어도 패닉 조건이 하나라도 켜져 있으면(예: 20일 −15% 급락) 추격은 위험하다
    blockers = met_panic
    if met >= need and not blockers:
        verdict = ("consider", "인버스 고려 구간", "내리는 흐름이 확인됐습니다. 한다면 1배 상품으로 짧게, 코스피가 20일 평균 가격을 되찾으면 정리.",
                   f"코스피가 20일 평균 가격({ma20:,.0f}) 위로 올라서면 정리할 때입니다.")
    elif met >= need:
        verdict = ("caution", "하락 추세지만 추격 주의",
                   "이미 많이 떨어져서 갑자기 튀어 오르는 날이 잦은 때입니다. 지금 따라 들어가기보다, 한 번 반등했다가 다시 꺾일 때가 그나마 나은 자리입니다.",
                   f"급락·공포 신호가 잦아든 뒤에도 내리는 신호가 {need}개 이상 남아 있으면 '고려 구간'이 됩니다.")
    elif met == need - 1:
        verdict = ("watch", "하락 신호 일부", "내리는 흐름이 아직 확실하지 않습니다. 지켜보기.",
                   f"하락 신호가 {need}개 이상이 되면 다시 판단합니다.")
    else:
        verdict = ("no", "인버스 불리", "내리는 흐름이라고 보기 어렵습니다.", f"하락 신호가 {need}개 이상이 되면 다시 판단합니다.")

    out = {
        "key": key, "label": REGIMES[key], "trend_key": tkey,
        "trend": {"close": r1(close), "ma20": r1(ma20), "ma60": r1(ma60), "ma20_rising": rising,
                  "ma20_slope": round(slope, 2), "chg20": r1(chg20)},
        "panic": panic, "panic_met": met_panic, "reason": reason, "next": nxt,
        "panic_checks": panic_checks, "trend_checks": trend_checks,
        "fit": {g: {"grade": v[0], "why": v[1], "evidence": EVIDENCE[v[2]][0], "source": EVIDENCE[v[2]][1]}
                for g, v in FIT[key].items()},
        "inverse": {"verdict": verdict[0], "label": verdict[1], "text": verdict[2], "next": verdict[3],
                    "checks": inv_checks, "met": met, "need": need, "blockers": blockers},
    }
    if dates is not None and len(kospi) >= 180:
        n = 120
        out["chart"] = {
            "dates": list(dates[-n:]),
            "close": [r1(v) for v in kospi[-n:]],
            "ma20": [r1(_ma(kospi, 20, back=i)) for i in range(n - 1, -1, -1)],
            "ma60": [r1(_ma(kospi, 60, back=i)) for i in range(n - 1, -1, -1)],
        }
    return out


# ---------------------------------------------------------------- 장의 전환 · 사도 되는 신호

def regime_transition(rg):
    """지금 장세에서 다음 장세로 넘어가려면 무엇이 얼마나 남았나 (코스피·이동평균 거리, 20일선 기울기)."""
    if not rg:
        return None
    tr = rg["trend"]
    close, ma20, ma60, slope = tr["close"], tr["ma20"], tr["ma60"], tr["ma20_slope"]
    gap20 = (ma20 / close - 1) * 100          # +면 20일선이 위에 있음 (회복까지 남은 %)
    gap60 = (ma60 / close - 1) * 100
    key = rg["trend_key"]
    items = []
    if key in ("down", "range"):
        target = "횡보·전환" if key == "down" else "상승장"
        items = [
            {"label": "코스피가 20일 평균 가격 위로 올라섬", "ok": close > ma20,
             "value": "회복" if close > ma20 else f"{gap20:.1f}% 남음"},
            {"label": "20일 평균 가격이 다시 오르기 시작 (5일 전보다 높음)", "ok": slope > 0, "value": f"{slope:+.2f}%"},
            {"label": "20일 평균이 60일 평균보다 높아짐 (짧은 흐름이 긴 흐름을 이김)", "ok": ma20 > ma60, "value": f"{(ma60 / ma20 - 1) * 100:.1f}% 남음" if ma20 <= ma60 else "회복"},
        ]
    else:
        target = "횡보·전환 (상승 추세 꺾임)"
        items = [
            {"label": "코스피가 20일 평균 가격 아래로 떨어짐", "ok": close < ma20,
             "value": "이탈" if close < ma20 else f"{-gap20:.1f}% 여유"},
            {"label": "20일 평균 가격이 내려가기 시작", "ok": slope < 0, "value": f"{slope:+.2f}%"},
        ]
    met = sum(1 for i in items if i["ok"])
    # 전환 임박: 20일선까지 2% 안쪽이거나, 20일선 기울기가 거의 평평해짐
    near = (key in ("down", "range") and close <= ma20 and gap20 <= 2) or (key == "up" and close >= ma20 and -gap20 <= 2) \
        or abs(slope) < 0.3
    return {"from": rg["label"], "to": target, "items": items, "met": met, "near": near,
            "gap20": round(gap20, 1), "gap60": round(gap60, 1)}


def entry_signals(t):
    """하락·급락 뒤 '사도 되는 신호'를 3단계로: 투매 진정 → 수급 전환 → 추세 확인. 단계는 앞 단계를 통과해야 올라간다."""
    rg = t.get("regime")
    if not rg:
        return None
    b = t.get("breadth") or {}
    lows = b.get("new_lows") or []
    fo = (t.get("credit_balance") or {}).get("forced") or {}
    kv = t.get("korea_vol") or {}
    kv_recent = [v for v in (kv.get("spark") or [])[-10:] if v is not None]
    fu = t.get("futures") or {}
    fl = (t.get("flows") or {}).get("KOSPI") or {}
    th = t.get("thermo") or {}
    week = (th.get("history") or {}).get("week")
    tr = rg["trend"]

    def chk(label, ok, value):
        return {"label": label, "ok": bool(ok), "value": value}

    lows_avg = sum(lows[-6:-1]) / 5 if len(lows) >= 6 else None
    big5 = ((fl.get("foreign") or {}).get("d5") or 0) + ((fl.get("inst") or {}).get("d5") or 0) if fl else None
    stages = [
        {"name": "투매 진정", "need": 2, "checks": [
            chk("바닥을 새로 뚫는 종목(신저가)이 최근 5일 평균보다 적음", lows_avg is not None and lows[-1] < lows_avg,
                f"{lows[-1]} / 평균 {lows_avg:.0f}" if lows_avg is not None else "–"),
            chk("빚 때문에 강제로 팔리는 물량(반대매매)이 평소(20일 평균) 이하", fo.get("avg20") and fo["value"] <= fo["avg20"],
                f"{fo['value']:,.0f}억 / 평균 {fo['avg20']:,.0f}억" if fo.get("avg20") else "–"),
            chk("출렁임(변동성)이 최근 10일 최고보다 10% 이상 줄어듦", kv_recent and kv["value"] <= max(kv_recent) * 0.9,
                f"{kv['value']}% / 최고 {max(kv_recent)}%" if kv_recent else "–"),
        ]},
        {"name": "수급 전환", "need": 2, "checks": [
            chk("외국인이 선물을 사는 쪽으로 돌아섬 (3일 연속 또는 5일 합계 순매수)",
                fu and ((fu.get("streak") or 0) >= 3 or (fu.get("d5") or 0) > 0),
                (f"{fu['streak']:+d}일째 · 5일 {fu.get('d5_eok') if fu.get('d5_eok') is not None else fu.get('d5'):+,}" +
                 ("억" if fu.get("d5_eok") is not None else "계약")) if fu else "–"),
            chk("외국인+기관이 최근 5일 동안 주식을 판 것보다 더 삼", big5 is not None and big5 > 0,
                f"{big5:+,.0f}억" if big5 is not None else "–"),
            chk("시장 온도가 1주 전보다 올라감", week and th.get("score") is not None and th["score"] > week["score"],
                f"{th.get('score')}점 / 1주 전 {week['score']}점" if week else "–"),
        ]},
        {"name": "추세 확인", "need": 2, "checks": [
            chk("코스피가 20일 평균 가격 위", tr["close"] > tr["ma20"], f"{tr['close']:,.0f} / {tr['ma20']:,.0f}"),
            chk("20일 평균 가격이 다시 오르는 중", tr["ma20_rising"], f"{tr['ma20_slope']:+.2f}%"),
        ]},
    ]
    level = 0
    for st in stages:
        st["met"] = sum(1 for c in st["checks"] if c["ok"])
        st["pass"] = st["met"] >= st["need"]
    for st in stages:
        if not st["pass"]:
            break
        level += 1
    # 상태만 말한다 (몇 분의 몇을 사라는 안내는 성적으로 확인한 적이 없어 뺌)
    guides = ["회복 확인 전 — 겁먹고 파는 물량(투매)이 아직 안 멈춤", "1단계 확인 — 투매가 잦아드는 중",
              "2단계까지 확인 — 큰손 돈이 다시 들어오기 시작", "3단계까지 확인 — 지수가 다시 오르는 흐름"]
    nxt = stages[level] if level < len(stages) else None
    return {"level": level, "guide": guides[level], "stages": stages,
            "next": (f"다음 단계 '{nxt['name']}'까지 {max(0, nxt['need'] - nxt['met'])}개 더" if nxt else "세 단계 모두 통과"),
            "applies": rg["trend_key"] != "up" or rg["key"] == "panic"}


# ---------------------------------------------------------------- 성적표

# ---------------------------------------------------------------- 아침 요약

def morning_summary(items):
    """미국장 마감 결과 → 오늘 한국장 출발 분위기 한 줄. EWY(미국 상장 한국 ETF)를 가장 무겁게 본다."""
    ch = {x["key"]: x["change"] for x in items}
    if not ch:
        return {"tone": "neutral", "label": "자료 없음", "text": "미국장 자료를 못 받았습니다."}
    score = 0.0
    for key, w in (("ewy", 2.0), ("nasdaq", 1.0), ("sox", 1.0), ("sp500", 0.5)):
        if key in ch:
            score += w * ch[key]
    if "vix" in ch:
        score -= ch["vix"] / 10          # VIX 10% 오름 ≈ 지수 1% 내림만큼 나쁨
    if "usdkrw" in ch:
        score -= ch["usdkrw"] * 2        # 환율 0.5% 오름 ≈ 지수 1% 내림
    if score >= 2:
        tone, label = "up", "상승 출발 가능성"
    elif score <= -2:
        tone, label = "down", "하락 출발 가능성"
    else:
        tone, label = "neutral", "큰 방향 없음"
    parts = []
    if "ewy" in ch:
        parts.append(f"한국 ETF {ch['ewy']:+.1f}%")
    if "nasdaq" in ch:
        parts.append(f"나스닥 {ch['nasdaq']:+.1f}%")
    if "sox" in ch:
        parts.append(f"미국 반도체 {ch['sox']:+.1f}%")
    if "vix" in ch and abs(ch["vix"]) >= 5:
        parts.append(f"공포지수 {'급등' if ch['vix'] > 0 else '급락'} {ch['vix']:+.0f}%")
    return {"tone": tone, "label": label, "score": round(score, 1),
            "text": " · ".join(parts) + f" → 오늘 아침 {label}"}


# ---------------------------------------------------------------- 업종·테마 ETF

THEME_RULES = {"overheat20": 20.0, "flow_days": 3, "breadth": 60,
               # '돈이 먼저 들어오는 중'은 좁게: 시총 대비 0.1% 이상, 4일 이상 순매수, 하루에 60% 넘게 몰리지 않음,
               # 가격이 5일 새 3% 넘게 더 빠지지 않고 20일 평균에서 10% 안쪽 (떨어지는 칼날을 '선취매'로 부르지 않게)
               "early_pct": 0.1, "early_days": 4, "early_one_day": 0.6, "early_r5": -3.0, "early_gap20": -10.0,
               "early_one_stock": 0.6}   # 들어온 돈의 60% 넘게가 한 종목이면 업종이 아니라 그 종목 이야기


def etf_trend(closes, bench=None):
    """ETF 종가 Series(오래된 순) → 5·20일 등락, 20일 평균 위인지, 20일 평균이 오르는지, 시장 대비(20일)."""
    c = closes.dropna()
    if len(c) < 21:
        return None
    ma20 = c.rolling(20).mean()
    r20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
    out = {"close": round(float(c.iloc[-1]), 1), "r1": r1((c.iloc[-1] / c.iloc[-2] - 1) * 100),
           "r5": r1((c.iloc[-1] / c.iloc[-6] - 1) * 100), "r20": r1(r20),
           "above20": bool(c.iloc[-1] > ma20.iloc[-1]), "ma20_up": bool(ma20.iloc[-1] > ma20.iloc[-6]),
           "gap20": r1((c.iloc[-1] / ma20.iloc[-1] - 1) * 100), "spark": [round(float(v), 1) for v in c.iloc[-20:]]}
    if bench is not None and len(bench.dropna()) >= 21:
        b = bench.dropna()
        out["rel20"] = r1(r20 - (b.iloc[-1] / b.iloc[-21] - 1) * 100)
    return out


def theme_strength(name, etfs, members, caps, closes, flows_daily, flows_5d, reports=None, base=None, regime=None,
                   rules=THEME_RULES):
    """테마 하나: 구성 종목의 큰손 돈 흐름·넓이·리포트와 ETF 흐름으로 판정.

    etfs: [{"code","name","trend"(etf_trend)}], members: {코드: 이름} (ETF 구성 상위, 중복 없이)
    caps: {코드: 시총(원)}
    """
    codes = [c for c in members if c in closes.columns and caps.get(c)]
    if not codes:
        return None
    cap = sum(caps[c] for c in codes)
    net5 = sum(_money(flows_5d["foreign"], c) + _money(flows_5d["inst"], c) for c in codes)
    daily = [sum(_money(fl["foreign"], c) + _money(fl["inst"], c) for c in codes) for _, fl in flows_daily]
    ma20 = closes[codes].iloc[-20:].mean()
    above = sum(1 for c in codes if closes[c].iloc[-1] > ma20[c])
    breadth = round(100 * above / len(codes))
    rep_n = rep_fresh = 0
    if reports:
        for c in codes:
            a = report_attention(c, reports, base)
            if a:
                rep_n += a["n"]
                rep_fresh += 1 if a["fresh"] else 0
    main = next((e for e in etfs if e.get("trend")), None)
    tr = main["trend"] if main else None
    flow_pct = net5 / cap * 100 if cap else 0
    money_in = net5 > 0 and sum(1 for v in daily if v > 0) >= rules["flow_days"]
    price_ok = bool(tr and tr["above20"] and (tr.get("rel20") or 0) > 0)
    hot = bool(tr and tr["r20"] >= rules["overheat20"])
    if hot:
        key, label = "hot", "과열 — 20일 새 많이 오름, 따라 사기 조심"
    elif money_in and not (tr and tr["above20"]):
        pos = sum(v for v in daily if v > 0)
        why = []
        if flow_pct < rules["early_pct"]:
            why.append("들어온 돈이 덩치에 비해 적음")
        if sum(1 for v in daily if v > 0) < rules["early_days"] or (pos and max(daily) / pos > rules["early_one_day"]):
            why.append("돈이 하루에 몰림")
        per = [_money(flows_5d["foreign"], c) + _money(flows_5d["inst"], c) for c in codes]
        pos_s = sum(v for v in per if v > 0)
        if pos_s and max(per) / pos_s > rules["early_one_stock"]:
            why.append(f"돈이 한 종목({members[codes[per.index(max(per))]]})에 몰림")
        if tr and (tr["r5"] < rules["early_r5"] or tr["gap20"] < rules["early_gap20"]):
            why.append("가격이 아직 계속 빠지는 중")
        if why:
            key, label = "watch", "돈은 들어왔지만 " + "·".join(why) + " — 지켜보기"
        else:
            key, label = "early", "돈이 먼저 들어오는 중 — 가격은 아직 (미리 볼 후보)"
    elif money_in and price_ok and breadth >= rules["breadth"]:
        key, label = "strong", "돈도 들어오고 흐름도 좋음"
    elif price_ok and net5 < 0:
        key, label = "fading", "가격은 강한데 큰손은 파는 중 — 주의"
    elif money_in:
        key, label = "watch", "돈은 들어오는 중 — 흐름 확인 필요"
    else:
        key, label = "weak", "약함 — 돈이 빠지거나 흐름 약함"
    warn = None
    if regime and regime.get("key") in ("down", "panic") and key in ("strong", "early", "watch"):
        warn = f"지금은 {regime.get('label')} — 업종이 강해도 같이 밀릴 수 있어 나눠서 천천히"
    return {"name": name, "key": key, "label": label, "warn": warn,
            "flow_eok": round(net5 / EOK), "flow_pct": round(flow_pct, 3), "flow_days": sum(1 for v in daily if v > 0),
            "daily": [round(v / EOK) for v in daily], "breadth": breadth, "n": len(codes),
            "reports": rep_n, "fresh": rep_fresh, "etfs": etfs,
            "top": [{"code": c, "name": members[c], "flow_eok": round((_money(flows_5d["foreign"], c) + _money(flows_5d["inst"], c)) / EOK)}
                    for c in sorted(codes, key=lambda c: -(_money(flows_5d["foreign"], c) + _money(flows_5d["inst"], c)))[:3]]}


def theme_scorecard(records, horizons=(5, 20), cost=None):
    """판정별로 대표 ETF(테마의 첫 ETF)를 그날 종가에 샀다면 N기록일 뒤 KODEX 200보다 나았나.

    records: [(기준일, 그날 파일의 themes)] 날짜순. ETF 종가는 각 날짜 파일에 저장된 값을 쓴다.
    """
    cost = COST if cost is None else cost
    acc = {}
    for i, (d, th) in enumerate(records):
        if not th:
            continue
        b0 = ((th.get("bench") or {}).get("069500") or {}).get("close")
        for x in th.get("themes", []):
            e = next((e for e in x.get("etfs", []) if e.get("trend")), None)
            if not e:
                continue
            for n in horizons:
                if i + n >= len(records) or not records[i + n][1]:
                    continue
                later = records[i + n][1]
                e2 = next((y for t in later.get("themes", []) for y in t.get("etfs", []) if y["code"] == e["code"] and y.get("trend")), None)
                if not e2:
                    continue
                r = (e2["trend"]["close"] / e["trend"]["close"] - 1) * 100 - cost
                b1 = ((later.get("bench") or {}).get("069500") or {}).get("close")
                a = acc.setdefault(x["key"], {}).setdefault(n, {"r": [], "x": []})
                a["r"].append(r)
                if b0 and b1:
                    a["x"].append(r - (b1 / b0 - 1) * 100)
    return {k: {str(n): summarize_returns(v["r"], v["x"]) for n, v in by.items()} for k, by in acc.items()}


# ---------------------------------------------------------------- 운용사 방향 (액티브 ETF 구성 변화)

MOVE_RULES = {"min_change": 10.0, "unit_shift": 5.0}
MOVE_NAME = {"new": "새로 담음", "add": "늘림", "cut": "줄임", "out": "모두 뺌"}


def holdings_diff(now, before, rules=MOVE_RULES):
    """액티브 ETF 구성 비교 → 종목별 움직임. now/before: {코드: (이름, 계약수, 비중)}.

    설정 단위가 통째로 바뀌면(모든 종목 계약수가 같은 비율로 변함) 그 비율(가운데 값)로 나눠 운용사 매매만 남긴다.
    """
    if not now or not before:
        return []
    common = [c for c in now if c in before and before[c][1] > 0]
    ratios = sorted(now[c][1] / before[c][1] for c in common)
    med = 1.0
    if ratios:
        mid = len(ratios) // 2
        m = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
        same = sum(1 for r in ratios if abs(r / m - 1) <= 0.02) / len(ratios)
        # 대부분(60% 이상)이 같은 비율로 움직였을 때만 설정 단위가 바뀐 것으로 본다
        if abs(m - 1) * 100 >= rules["unit_shift"] and same >= 0.6:
            med = m
    out = []
    for c, (name, sh, w) in now.items():
        if c not in before:
            out.append({"code": c, "name": name, "act": "new", "chg": None, "w": round(w, 2)})
            continue
        chg = (sh / before[c][1] / med - 1) * 100
        if chg >= rules["min_change"]:
            out.append({"code": c, "name": name, "act": "add", "chg": round(chg), "w": round(w, 2)})
        elif chg <= -rules["min_change"]:
            out.append({"code": c, "name": name, "act": "cut", "chg": round(chg), "w": round(w, 2)})
    for c, (name, sh, w) in before.items():
        if c not in now:
            out.append({"code": c, "name": name, "act": "out", "chg": -100, "w": round(w, 2)})
    order = {"new": 0, "add": 1, "cut": 2, "out": 3}
    return sorted(out, key=lambda m: (order[m["act"]], -m["w"]))


def manager_moves(etfs, known=None):
    """ETF별 움직임을 종목별로 모아, 몇 곳의 운용사가 같은 방향으로 움직였나.

    etfs: [{"manager","code","name","moves":[...]}], known: 상장 보통주 코드 집합(있으면 그 밖은 뺌)
    반환: buys/sells = [{code,name,managers:[...],etfs:[...],n}] (운용사 수 많은 순)
    """
    by = {}
    for e in etfs:
        for m in e.get("moves") or []:
            if known is not None and m["code"] not in known:
                continue
            side = "buy" if m["act"] in ("new", "add") else "sell"
            r = by.setdefault((side, m["code"]), {"code": m["code"], "name": m["name"], "managers": [], "etfs": []})
            if e["manager"] not in r["managers"]:
                r["managers"].append(e["manager"])
            r["etfs"].append({"name": e["name"], "act": m["act"], "chg": m["chg"], "w": m["w"]})
    buys = [r for (side, _), r in by.items() if side == "buy"]
    sells = [r for (side, _), r in by.items() if side == "sell"]
    both = {r["code"] for r in buys} & {r["code"] for r in sells}
    for r in buys + sells:
        r["n"] = len(r["managers"])
        r["mixed"] = r["code"] in both          # 어떤 운용사는 사고 어떤 곳은 팜
    key = lambda r: (-r["n"], -len(r["etfs"]), -max(e["w"] for e in r["etfs"]))
    return {"buys": sorted(buys, key=key), "sells": sorted(sells, key=key)}


def enrich_manager_moves(moves, moves5=None, closes=None, since=None, sectors=None, trust=None, skill=None):
    """운용사 매수·매도 종목에 확신도를 붙인다 (moves 를 고쳐서 돌려줌).

    - persist: 5일 동안에도 같은 방향 (계속 사는/파는 중)
    - big: 그 종목을 비중 3% 이상 담은 ETF가 있음
    - since: 비교 시작일 이후 주가 등락 (이미 많이 올랐으면 늦었을 수 있음)
    - trust5: 투신(자산운용사 전체) 5일 순매수 (억)
    - good: 요즘(60일) 시장보다 잘한 운용사 수
    - score: 정렬용 = 운용사 수×2 + 계속 + 비중 큼 + 잘하는 운용사
    """
    m5 = {}
    for side in ("buys", "sells"):
        for r in (moves5 or {}).get(side, []):
            m5[(side, r["code"])] = r
    for side in ("buys", "sells"):
        for r in moves.get(side, []):
            c = r["code"]
            r["persist"] = (side, c) in m5
            r["big"] = any(e["w"] >= 3 for e in r["etfs"])
            if closes is not None and since and c in closes.columns and since in closes.index:
                c0, c1 = closes[c].get(since), closes[c].iloc[-1]
                r["since"] = r1((c1 / c0 - 1) * 100) if c0 and c0 == c0 and c1 == c1 else None
            if sectors is not None and c in sectors:
                r["sector"] = sectors.get(c)
            if trust is not None:
                r["trust5"] = r1(_money(trust, c) / EOK)
            r["good"] = sum(1 for m in r["managers"] if (skill or {}).get(m, {}).get("x60", 0) > 0)
            r["score"] = r["n"] * 2 + (1 if r["persist"] else 0) + (1 if r["big"] else 0) + r["good"]
        moves[side].sort(key=lambda r: (-r["score"], -r["n"]))
    return moves


def manager_skill(etf_rows):
    """운용사별 실력: 그 운용사 액티브 ETF들의 20·60일 성적에서 KODEX 200 성적을 뺀 값의 평균.

    etf_rows: [{"manager", "r20", "r60", "b20", "b60"}] (b = 같은 기간 KODEX 200)
    """
    out = {}
    for e in etf_rows:
        if e.get("r60") is None or e.get("b60") is None:
            continue
        m = out.setdefault(e["manager"], {"x20": [], "x60": [], "n": 0})
        m["x20"].append(e["r20"] - e["b20"])
        m["x60"].append(e["r60"] - e["b60"])
        m["n"] += 1
    return {k: {"x20": r1(sum(v["x20"]) / len(v["x20"])), "x60": r1(sum(v["x60"]) / len(v["x60"])), "n": v["n"]}
            for k, v in out.items() if v["x60"]}


def sector_tilt(moves, sectors):
    """운용사들이 늘리는·줄이는 업종: 업종별 (사들인 종목 수 − 판 종목 수), 운용사 수 가중."""
    if sectors is None:
        return []
    acc = {}
    for side, sign in (("buys", 1), ("sells", -1)):
        for r in moves.get(side, []):
            sec = sectors.get(r["code"]) if r["code"] in sectors else None
            if not sec:
                continue
            a = acc.setdefault(sec, {"sector": sec, "net": 0, "buy": [], "sell": []})
            a["net"] += sign * r["n"]
            (a["buy"] if sign > 0 else a["sell"]).append(r["name"])
    rows = sorted(acc.values(), key=lambda a: -a["net"])
    return [{**a, "buy": a["buy"][:3], "sell": a["sell"][:3]} for a in rows if a["net"]]


# ---------------------------------------------------------------- 급등주 복기·신호 검증

SURGE = {"r5": 20.0, "d1": 15.0, "day": 8.0, "fwd": 5, "hit": 15.0, "lookback": 15}


def net_frame(flows_hist):
    """[(날짜, {"foreign": S, "inst": S})] → DataFrame(행=날짜, 열=종목) 기관+외국인 순매수(원)."""
    if not flows_hist:
        return pd.DataFrame()
    return pd.DataFrame({d: fl["foreign"].add(fl["inst"], fill_value=0) for d, fl in flows_hist}).T.astype(float)


def _signal_frames(closes, values, net, caps):
    """날짜×종목 신호 표: 큰손 3일 연속 순매수, 조용한 거래 증가(거래 2배↑인데 등락 5% 안), 5일 큰손 순매수가 시총의 0.1%↑."""
    ret = closes.pct_change() * 100
    mult = values / values.rolling(20).mean().shift(1)
    netx = net.reindex(columns=closes.columns)
    pos = netx > 0
    out = {
        "streak3": (pos & pos.shift(1, fill_value=False) & pos.shift(2, fill_value=False)).reindex(closes.index, fill_value=False),
        "quiet_vol": (mult >= 2) & (ret.abs() < 5),
    }
    cap = pd.Series(caps).reindex(closes.columns)
    out["flow_big"] = (netx.rolling(5).sum().div(cap, axis=1) * 100 >= 0.1).reindex(closes.index, fill_value=False)
    return out, ret, mult


SIGNAL_NAMES = {"streak3": "큰손이 3일 연속 삼", "quiet_vol": "가격은 조용한데 거래만 2배↑", "flow_big": "큰손 5일 순매수가 덩치의 0.1%↑",
                "streak3+quiet_vol": "큰손 3일 연속 + 조용한 거래 증가", "streak3+flow_big": "큰손 3일 연속 + 덩치 대비 많이"}


def replay_mover(code, name, closes, values, net, caps, cand_by_day, reports=None, rules=SURGE):
    """급등주 하나를 거슬러 본다: 언제 급등했고, 그 전에 어떤 신호가 몇 일 먼저 있었고, 우리가 언제 잡았나."""
    sig, ret, mult = _signal_frames(closes[[code]], values[[code]], net[[code]] if code in net.columns else pd.DataFrame(), caps)
    days = list(closes.index[-rules["lookback"]:])
    r = ret[code]
    recent = [d for d in days[-10:] if r.get(d, 0) >= rules["day"]]
    surge = recent[0] if recent else max(days[-5:], key=lambda d: r.get(d, 0))
    si = days.index(surge)
    timeline = []
    for d in days:
        timeline.append({"d": d, "chg": r1(r.get(d)), "mult": r1(mult[code].get(d)),
                         "net": round(float(net[code].get(d)) / EOK) if code in net.columns and d in net.index and net[code].get(d) == net[code].get(d) else None,
                         "cand": cand_by_day.get(d, {}).get(code, [])})
    early = []
    for key in ("streak3", "quiet_vol", "flow_big"):
        hit = [d for d in days[:si] if bool(sig[key][code].get(d, False))]
        if hit:
            early.append({"key": key, "label": SIGNAL_NAMES[key], "first": hit[0], "lead": si - days.index(hit[0])})
    if reports:
        rep = sorted({x["date"] for x in reports.get("items", []) if x["code"] == code and days[max(0, si - 10)] <= x["date"] < surge})
        if rep:
            early.append({"key": "report", "label": "증권사 리포트", "first": rep[0],
                          "lead": si - next((i for i, d in enumerate(days) if d >= rep[0]), si)})
    cand_days = [d for d in days if cand_by_day.get(d, {}).get(code)]
    before = [d for d in cand_days if d < surge]
    if before:
        verdict, lead = "early", si - days.index(before[0])
        how = f"{lead}일 먼저 " + "·".join(cand_by_day[before[0]][code]) + " 후보로 잡음"
    elif surge in cand_days:
        verdict, lead, how = "sameday", 0, "급등 당일 장 마감 뒤에야 " + "·".join(cand_by_day[surge][code]) + " 후보로 잡음 (다음 날 아침엔 이미 오른 뒤)"
    elif [d for d in cand_days if d > surge]:
        first = next(d for d in cand_days if d > surge)
        verdict, lead = "late", -(days.index(first) - si)
        how = f"급등 시작 {-lead}일 뒤에야 " + "·".join(cand_by_day[first][code]) + " 후보로 잡음"
    else:
        verdict, lead, how = "missed", None, "후보에 안 걸림"
    r5 = (closes[code].iloc[-1] / closes[code].iloc[-6] - 1) * 100
    return {"code": code, "name": name, "r5": r1(r5), "surge": surge, "surge_chg": r1(r.get(surge)),
            "verdict": verdict, "lead": lead, "how": how, "early": sorted(early, key=lambda e: -e["lead"]), "timeline": timeline}


def find_movers(closes, universe, rules=SURGE, min_cap=None, top=12):
    """최근 급등주: 5일 +20%↑ 또는 최근 5일 중 하루 +15%↑ (시총 기준 이상만)."""
    min_cap = min_cap if min_cap is not None else RULES["min_cap"]
    r5 = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100
    d1 = (closes.pct_change().iloc[-5:] * 100).max()
    codes = [c for c in closes.columns if c in universe and universe[c]["cap"] >= min_cap
             and (r5.get(c, 0) >= rules["r5"] or d1.get(c, 0) >= rules["d1"])]
    return sorted(codes, key=lambda c: -r5.get(c, 0))[:top]


def signal_validation(closes, values, net, caps, eligible, rules=SURGE):
    """신호가 뜬 날(종목) 뒤 5일 안에 +15% 넘게 오른 비율 vs 전체 평균. '이 신호가 정말 급등을 앞서나'를 본다."""
    if net is None or net.empty:
        return None
    cols = [c for c in closes.columns if c in eligible]
    cl, va = closes[cols], values[cols]
    sig, _, _ = _signal_frames(cl, va, net, caps)
    sig["streak3+quiet_vol"] = sig["streak3"] & sig["quiet_vol"]
    sig["streak3+flow_big"] = sig["streak3"] & sig["flow_big"]
    f = rules["fwd"]
    fwd_max = (cl[::-1].rolling(f, min_periods=f).max()[::-1].shift(-1) / cl - 1) * 100
    days = [d for d in net.index if d in cl.index and cl.index.get_loc(d) + f < len(cl.index)]
    if not days:
        return None
    hitf = (fwd_max.loc[days] >= rules["hit"])
    valid = fwd_max.loc[days].notna()
    base_n = int(valid.values.sum())
    base = float(hitf.values[valid.values].mean() * 100) if base_n else 0.0
    rows = []
    for k, frame in sig.items():
        m = frame.loc[days].fillna(False).astype(bool) & valid
        n = int(m.values.sum())
        rate = float(hitf.values[m.values].mean() * 100) if n else None
        rows.append({"key": k, "label": SIGNAL_NAMES[k], "n": n, "rate": r1(rate) if rate is not None else None,
                     "lift": round(rate / base, 1) if rate is not None and base else None})
    return {"base": r1(base), "base_n": base_n, "days": len(days), "from": days[0], "to": days[-1],
            "fwd": f, "hit": rules["hit"], "rows": rows}


FOLLOW = {"big": 15.0, "mid": 8.0, "again": 8.0, "rebound": 5.0}


def follow_through(closes, opens, eligible, rules=FOLLOW, cost=None, highs=None, values=None, net=None, caps=None):
    """급등한 날 뒤에 어떻게 됐나 (최근 약 80영업일, 시총·거래대금 기준 이상 종목):
    다음 날도 오름, 3일 안에 또 +8%↑ 하루, 하루 쉬고(다음 날 보합·하락) 2~3일째 +5%↑,
    그리고 실제로 살 수 있는 '다음 날 시가'에 사서 3·5영업일째 종가에 판 수익(비용 뺌)을 평소(모든 종목·날)와 비교."""
    cost = COST if cost is None else cost
    cols = [c for c in closes.columns if c in eligible and c in opens.columns]
    if not cols or len(closes.index) < 8:
        return None
    cl, op = closes[cols], opens[cols]
    ret = cl.pct_change() * 100
    r1_, r2_, r3_ = ret.shift(-1), ret.shift(-2), ret.shift(-3)
    entry = op.shift(-1)
    h3 = (cl.shift(-3) / entry - 1) * 100 - cost
    h5 = (cl.shift(-5) / entry - 1) * 100 - cost
    valid = ret.notna() & entry.notna() & h5.notna()
    again = (r1_ >= rules["again"]) | (r2_ >= rules["again"]) | (r3_ >= rules["again"])
    rest = (r1_ <= 0) & ((r2_ >= rules["rebound"]) | (r3_ >= rules["rebound"]))

    def row(key, label, mask):
        m = (mask & valid).values
        n = int(m.sum())
        if not n:
            return {"key": key, "label": label, "n": 0}
        pct = lambda f: round(float(f.values[m].mean() * 100), 1)
        a3, a5 = h3.values[m], h5.values[m]
        return {"key": key, "label": label, "n": n, "next_up": pct(r1_ > 0), "again3": pct(again), "rest_again": pct(rest),
                "h3": r1(float(pd.Series(a3).mean())), "h3_med": r1(float(pd.Series(a3).median())), "h3_win": round(float((a3 > 0).mean() * 100), 1),
                "h5": r1(float(pd.Series(a5).mean())), "h5_win": round(float((a5 > 0).mean() * 100), 1)}

    big = ret >= rules["big"]
    prev_big = big.shift(1, fill_value=False)
    rows = [row("big", f"하루 +{rules['big']:g}%↑", big),
            row("mid", f"하루 +{rules['mid']:g}~{rules['big']:g}%", (ret >= rules["mid"]) & ~big),
            row("big2", f"+{rules['big']:g}%↑ 이틀 연속", big & prev_big),
            row("base", "평소 (모든 종목·날)", ret.notna())]
    # 어떤 급등이 이어졌나: +8%↑ 급등 날을 그날 모양으로 나눠 본다 (필터 조건 후보를 고르는 근거)
    surge = ret >= rules["mid"]
    splits = [row("all", f"+{rules['mid']:g}%↑ 급등 전체", surge)]
    if highs is not None:
        hi = highs.reindex(index=cl.index, columns=cols)
        near = cl >= hi * 0.97
        splits += [row("close_high", "종가가 고가 근처 (3% 안)", surge & near), row("close_off", "고가에서 밀려 마감", surge & ~near & hi.notna())]
    splits.append(row("limit", "상한가 근처 (+29%↑)", ret >= 29))
    pre5 = (cl.shift(1) / cl.shift(6) - 1) * 100
    splits += [row("first", "첫 급등 (전 5일 +10% 안)", surge & (pre5 < 10)), row("again", "이미 오른 뒤 또 급등", surge & (pre5 >= 10))]
    if values is not None:
        va = values.reindex(index=cl.index, columns=cols)
        mult = va / va.shift(1).rolling(20, min_periods=10).mean()
        splits += [row("vol5", "거래 평소의 5배↑", surge & (mult >= 5)), row("vol_low", "거래 평소의 3배 안", surge & (mult < 3))]
    if net is not None and not net.empty:
        nt = net.reindex(index=cl.index, columns=cols)
        splits += [row("net_buy", "그날 큰손 순매수", surge & (nt > 0)), row("net_sell", "그날 큰손 순매도", surge & (nt < 0))]
    # 위 줄들 중 성적이 나았던 모양을 겹친 조합 ('급등 이어짐' 필터 후보)
    if values is not None:
        quiet_first = surge & (pre5 < 10) & (mult < 3)
        splits.append(row("first_quiet", "첫 급등 + 거래 3배 안", quiet_first))
        if net is not None and not net.empty:
            splits.append(row("first_quiet_buy", "첫 급등 + 거래 3배 안 + 큰손 순매수", quiet_first & (nt > 0)))
        if highs is not None:
            splits.append(row("first_quiet_high", "첫 급등 + 거래 3배 안 + 종가 고가 근처", quiet_first & near))
    if caps:
        small = pd.Series({c: (caps.get(c) or 0) < 3000 * EOK for c in cols})
        sm = pd.DataFrame([small.values] * len(cl.index), index=cl.index, columns=cols)
        splits += [row("small", "시총 3천억 아래", surge & sm), row("large", "시총 3천억 이상", surge & ~sm)]
    days = [d for d in cl.index if valid.loc[d].any()]
    return {"from": days[0] if days else None, "to": days[-1] if days else None, "days": len(days), "rules": rules, "cost": cost,
            "rows": rows, "splits": [x for x in splits if x["n"]]}


HOLD = {"min": 8.0, "days": 10, "trail": 7.0}


def hold_after_surge(closes, opens, lows, eligible, values=None, rules=HOLD):
    """이미 들고 있던 종목이 급등한 날(+8%↑) 종가 기준으로, 그 뒤 10영업일 동안 어떻게 됐나 — '얼마나 들고 가야 하나'.
    · after: 1·3·5·10일 뒤 종가가 급등일 종가보다 높았던 비율과 가운데 값
    · peak: 10일 안 가장 높은 종가가 나온 날 (급등 당일·1일 뒤·2일 뒤·3~5일·6~10일) 비율
    · giveback: 10일 안에 종가가 급등 전날 종가 아래로 (오른 걸 다 돌려줌) 내려간 비율
    · exits: 팔기 방법별 급등일 종가 대비 수익 — 다음 날 시가·다음 날 종가·3일·5일·10일 보유,
      '전날 저가 깨지면 그날 종가', '최고 종가에서 7% 내려오면 그날 종가' (10일째엔 무조건 팖)
    묶음: 전체, 하루 +15%↑, 거래 평소 5배↑(뜨거운 급등), 첫 급등 + 거래 3배 안(조용한 급등)."""
    import numpy as np
    cols = [c for c in closes.columns if c in eligible and c in opens.columns and c in lows.columns]
    if not cols:
        return None
    cl, op, lo = closes[cols], opens[cols].reindex(index=closes.index), lows[cols].reindex(index=closes.index)
    ret = cl.pct_change() * 100
    pre5 = (cl.shift(1) / cl.shift(6) - 1) * 100
    groups = {"all": ret >= rules["min"], "big": ret >= 15}
    if values is not None:
        va = values.reindex(index=cl.index, columns=cols)
        mult = va / va.shift(1).rolling(20, min_periods=10).mean()
        groups["hot"] = (ret >= rules["min"]) & (mult >= 5)
        groups["quiet"] = (ret >= rules["min"]) & (pre5 < 10) & (mult < 3)
    C, O, L = cl.values, op.values, lo.values
    T, K = len(cl.index), rules["days"]
    out = {}
    for g, mask in groups.items():
        ev = [(t, j) for t, j in zip(*np.nonzero(mask.values)) if 1 <= t and t + K < T]
        recs = []
        for t, j in ev:
            c0, prev = C[t, j], C[t - 1, j]
            fut = C[t + 1:t + K + 1, j]
            if not (c0 > 0) or np.isnan(fut).any() or np.isnan(O[t + 1, j]):
                continue
            r = lambda x: (x / c0 - 1) * 100
            # 전날 저가 깨지면 그날 종가에 팖
            trail_low = fut[-1]
            for k in range(1, K + 1):
                if C[t + k, j] < L[t + k - 1, j]:
                    trail_low = C[t + k, j]
                    break
            # 최고 종가에서 7% 내려오면 그날 종가에 팖
            peak, trail_pk = c0, fut[-1]
            for k in range(1, K + 1):
                peak = max(peak, C[t + k, j])
                if C[t + k, j] <= peak * (1 - rules["trail"] / 100):
                    trail_pk = C[t + k, j]
                    break
            pk = int(np.argmax(np.concatenate([[c0], fut])))
            recs.append({"a1": r(fut[0]), "a3": r(fut[2]), "a5": r(fut[4]), "a10": r(fut[-1]), "pk": pk,
                         "gb": bool(fut.min() < prev), "open1": r(O[t + 1, j]),
                         "trail_low": r(trail_low), "trail_pk": r(trail_pk)})
        if not recs:
            continue
        df = pd.DataFrame(recs)
        stat = lambda s: {"avg": r1(s.mean()), "med": r1(s.median()), "up": round(float((s > 0).mean() * 100), 1)}
        pkb = df["pk"]
        out[g] = {"n": len(df),
                  "after": {k: stat(df[k]) for k in ("a1", "a3", "a5", "a10")},
                  "peak": {"d0": round(float((pkb == 0).mean() * 100), 1), "d1": round(float((pkb == 1).mean() * 100), 1),
                           "d2": round(float((pkb == 2).mean() * 100), 1), "d3_5": round(float(((pkb >= 3) & (pkb <= 5)).mean() * 100), 1),
                           "d6_10": round(float((pkb >= 6).mean() * 100), 1)},
                  "giveback": round(float(df["gb"].mean() * 100), 1),
                  "exits": {k: stat(df[k]) for k in ("open1", "a1", "a3", "a5", "a10", "trail_low", "trail_pk")}}
    return {"rules": rules, "groups": out} if out else None


def volume_profile(high, low, close, value, bins=20):
    """매물대: 기간 동안 거래대금을 가격대별로 나눈다 (하루 거래대금을 그날 저가~고가에 고르게 펼친 근사).
    반환 {lo, hi, b: 칸별 비중(%), now: 현재가, below: 현재가 아래에서 거래된 비중(%), avg: 거래대금 가중 평균가, top: 가장 많이 거래된 칸}"""
    import numpy as np
    h, l, c, v = (np.asarray(x, dtype=float) for x in (high, low, close, value))
    ok = ~(np.isnan(h) | np.isnan(l) | np.isnan(c) | np.isnan(v)) & (v > 0) & (h >= l) & (l > 0)
    if ok.sum() < 10:
        return None
    h, l, c, v = h[ok], l[ok], c[ok], v[ok]
    lo, hi = float(l.min()), float(h.max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    acc = np.zeros(bins)
    for hh, ll, vv in zip(h, l, v):
        if hh == ll:
            acc[min(bins - 1, int((ll - lo) / (hi - lo) * bins))] += vv
            continue
        ov = np.clip(np.minimum(edges[1:], hh) - np.maximum(edges[:-1], ll), 0, None)
        acc += vv * ov / (hh - ll)
    tot = acc.sum()
    now = float(c[-1])
    below = sum(acc[i] * max(0.0, min(1.0, (now - edges[i]) / (edges[i + 1] - edges[i]))) for i in range(bins)) / tot * 100
    avg = float((v * (h + l + c) / 3).sum() / v.sum())
    return {"lo": round(lo), "hi": round(hi), "b": [round(float(x / tot * 100), 1) for x in acc], "now": round(now),
            "below": round(float(below), 1), "avg": round(avg), "top": int(acc.argmax())}


MYTH = {"fwd": (3, 5), "gap": 3.0, "gap_days": 5, "high_days": 60, "down_days": 5, "vp_days": 40, "vp_bins": 20,
        "hype_mult": 5.0, "hype_ret": 10.0, "report_n": 3}


def _trim_mean(a, cut=0.05):
    """위아래 5%를 뺀 평균 (한두 종목의 폭등·폭락이 평균을 끌고 가지 않게). 20개 안 되면 그냥 평균."""
    import numpy as np
    a = np.sort(np.asarray(a, dtype=float))
    if len(a) >= 20:
        k = int(len(a) * cut)
        a = a[k:len(a) - k]
    return float(a.mean()) if len(a) else None


def myth_check(closes, opens, highs, lows, values, eligible, reports=None, rules=MYTH, cost=None,
               markets=None, caps=None, reported=None, attention=None, index=None, dart_events=None):
    """주식 속설 검증: 흔히 믿는 심리 패턴이 최근 약 80영업일 실제로 맞았나.
    사는 쪽 속설은 '다음 날 시가에 사서 3·5영업일째 종가에 팖'(비용 뺌). 판정은 통으로 보지 않고
    · 같은 날 전 종목 평균을 뺀 초과 수익(시장 전체가 오르내린 날의 효과를 뺌), 위아래 5%를 뺀 평균
    · 몇 종목·며칠에 몰렸나(상위 5종목 몫, 하루 최대 몫), 기간 앞·뒤 절반이 같은 방향인가
    · 코스피/코스닥, 시총 크기, 시장이 오르는 중/내리는 중으로 나눠 봄
    갭은 '며칠 안에 전날 종가로 돌아왔나'를 같은 만큼 벌어진 다른 날과 비교한다."""
    import numpy as np
    cost = COST if cost is None else cost
    cols = [c for c in closes.columns if c in eligible and c in opens.columns and c in highs.columns and c in lows.columns and c in values.columns]
    if not cols or len(closes.index) < 30:
        return None
    idx = closes.index
    cl = closes[cols]
    op, hi, lo, va = (x.reindex(index=idx, columns=cols) for x in (opens, highs, lows, values))
    ret = cl.pct_change(fill_method=None) * 100
    entry = op.shift(-1)
    fwd = {n: (cl.shift(-n) / entry - 1) * 100 - cost for n in rules["fwd"]}
    n_last = max(rules["fwd"])
    valid = entry.notna() & fwd[n_last].notna() & ret.notna()
    # 같은 날 전 종목 평균 대비 (시장 전체 효과 빼기)
    exc = {n: f.sub(f.where(valid).mean(axis=1), axis=0) for n, f in fwd.items()}
    # 상승장·하락장: 코스피가 20일 평균 위면 상승장 쪽 (화면 장세 판정과 같은 기준). 지수가 없으면 전 종목 평균으로 대신
    if index is not None and len(index) >= 30:
        ix = pd.Series(index, dtype=float)
        mk_up = (ix > ix.rolling(20, min_periods=10).mean()).reindex(idx).ffill().fillna(False).astype(bool).values
        mk_basis = "kospi"
    else:
        mk = (1 + ret.mean(axis=1).fillna(0) / 100).cumprod()
        mk_up = (mk > mk.rolling(20, min_periods=10).mean()).values
        mk_basis = "equal"
    col_mkt = np.array([(markets or {}).get(c, "") for c in cols])
    cap_arr = np.array([float((caps or {}).get(c) or 0) for c in cols])
    size = np.where(cap_arr >= 10000 * EOK, "large", np.where(cap_arr >= 3000 * EOK, "mid", "small"))
    dates = np.array(idx)

    def perf(mask, segs=True):
        m = (mask & valid).values
        n = int(m.sum())
        if not n:
            return {"n": 0}
        tt, jj = np.nonzero(m)
        out = {"n": n, "stocks": int(len(set(jj))), "days": int(len(set(tt)))}
        for k in rules["fwd"]:
            a, x = fwd[k].values[m], exc[k].values[m]
            out[f"h{k}"] = r1(float(np.mean(a)))
            out[f"h{k}_med"] = r1(float(np.median(a)))
            out[f"h{k}_win"] = round(float((a > 0).mean() * 100), 1)
            out[f"x{k}"] = r1(_trim_mean(x))
            out[f"x{k}_med"] = r1(float(np.median(x)))
            out[f"beat{k}"] = round(float((x > 0).mean() * 100), 1)
        x3 = exc[3].values[m]
        # 우연인가: 같은 날 걸린 종목끼리는 함께 움직이므로 날짜별 평균으로 묶어 표준오차를 잼 (날짜 수가 실제 표본 크기)
        # 판정의 평균(위아래 5% 뺀 평균)과 맞추려고 양 끝 5%를 그 경계값으로 눌러 두고 잼
        xc = np.clip(x3, *np.percentile(x3, [5, 95])) if len(x3) >= 20 else x3
        dm = pd.Series(xc).groupby(tt).mean()
        if len(dm) >= 5 and dm.std(ddof=1) > 0:
            se = float(dm.std(ddof=1) / np.sqrt(len(dm)))
            out["se3"] = round(se, 2)
            # 판정·그림이 같은 값을 쓰도록 t는 화면에 보이는 평균(위아래 5% 뺀 x3)을 이 표준오차로 나눔
            out["t3"] = round(float(_trim_mean(x3) / se), 1)
        else:
            out["se3"], out["t3"] = None, None
        # 쏠림: 종목별 초과 수익 합 중 위 5종목 몫(이긴 쪽 기준), 가장 많이 걸린 하루의 몫
        per = pd.Series(x3).groupby(jj).sum()
        pos = per[per > 0].sort_values(ascending=False)
        out["top5"] = round(float(pos.head(5).sum() / pos.sum() * 100), 1) if len(pos) and pos.sum() > 0 else None
        out["top5_names"] = [cols[c] for c in pos.head(5).index]
        out["day_max"] = round(float(pd.Series(tt).value_counts().iloc[0] / n * 100), 1)
        # 기간 앞·뒤 절반
        mid = np.median(tt)
        first, second = x3[tt <= mid], x3[tt > mid]
        out["halves"] = [r1(_trim_mean(first)) if len(first) else None, r1(_trim_mean(second)) if len(second) else None]
        if segs:
            seg = {}
            for key, sel in (("kospi", col_mkt[jj] == "P"), ("kosdaq", col_mkt[jj] == "D"),
                             ("large", size[jj] == "large"), ("mid", size[jj] == "mid"), ("small", size[jj] == "small"),
                             ("mk_up", mk_up[tt]), ("mk_down", ~mk_up[tt])):
                if sel.sum():
                    seg[key] = {"n": int(sel.sum()), "x3": r1(_trim_mean(x3[sel])), "beat3": round(float((x3[sel] > 0).mean() * 100), 1)}
            out["seg"] = seg
        return out

    base = perf(ret.notna(), segs=False)

    def verdict(p, bullish=True):
        """판정 (사는 쪽 기준, 파는 쪽 속설은 부호를 뒤집음):
        · few: 30번·20종목·8일 미만 / none: 날짜로 묶은 t가 ±2 안 → 우연과 구분 안 됨
        · true·false: 우연이 아니고(|t|≥2) 같은 날 대비 ±1%p·이긴 비율 50±5% 넘음 (쓸 만한 크기)
        · weak_true·weak_false: 우연은 아니지만 크기가 작음 (꾸준하지만 거래 비용·출렁임에 묻힐 정도)
        · shaky_*: 위 5종목이 이긴 몫 절반 넘게, 또는 기간 앞·뒤 반대 방향"""
        if p.get("n", 0) < 30 or p.get("stocks", 0) < 20 or p.get("days", 0) < 8:
            return "few"
        x, b, t = p["x3"], p["beat3"] - 50, p.get("t3")
        if not bullish:
            x, b, t = -x, -b, (-t if t is not None else None)
        if t is None or abs(t) < 2:
            return "none"
        side = "true" if t > 0 else "false"
        strong = (x >= 1.0 and b >= 5) if t > 0 else (x <= -1.0 and b <= -5)
        v = side if strong else "weak_" + side
        h = [y for y in p.get("halves", []) if y is not None]
        flip = len(h) == 2 and h[0] * h[1] < 0 and min(abs(h[0]), abs(h[1])) >= 0.5
        if flip or (p.get("top5") or 0) > 50:
            return "shaky_" + side
        return v

    up_day = pd.DataFrame(np.repeat(mk_up[:, None], len(cols), axis=1), index=idx, columns=cols)

    def by_mk(mask, bullish=True):
        """시장이 오르는 중(상승장 쪽) / 내리는 중(하락장 쪽)으로 나눠 따로 판정"""
        out = {}
        for k, R in (("up", up_day), ("down", ~up_day)):
            q = perf(mask & R, segs=False)
            q["verdict"] = verdict(q, bullish) if q.get("n") else "few"
            out[k] = q
        return out

    items = []
    # 1) 매물대를 뚫으면 간다
    W, B = rules["vp_days"], rules["vp_bins"]
    typ = ((hi + lo + cl) / 3).values
    V, C = va.values, cl.values
    brk = np.zeros(C.shape, dtype=bool)
    for t in range(W, len(idx)):
        tp, vv = typ[t - W:t], V[t - W:t]
        for j in range(C.shape[1]):
            x, w = tp[:, j], vv[:, j]
            ok = ~(np.isnan(x) | np.isnan(w))
            if ok.sum() < W * 0.8 or np.isnan(C[t, j]) or np.isnan(C[t - 1, j]):
                continue
            x, w = x[ok], w[ok]
            lo_, hi_ = x.min(), x.max()
            if hi_ <= lo_:
                continue
            hist, edges = np.histogram(x, bins=B, range=(lo_, hi_), weights=w)
            top_hi = edges[hist.argmax() + 1]
            brk[t, j] = C[t - 1, j] <= top_hi < C[t, j]
    brk = pd.DataFrame(brk, index=idx, columns=cols)
    p = perf(brk)
    items.append({"key": "vp_break", "title": "매물대를 뚫으면 간다", "kind": "buy", **p, "verdict": verdict(p), "by_mk": by_mk(brk),
                  "event": f"종가가 최근 {W}일 가장 많이 거래된 가격대를 처음 넘은 날"})
    # 2) 신고가는 사라
    H = rules["high_days"]
    prev_high = hi.shift(1).rolling(H, min_periods=H).max()
    newhi = (cl > prev_high) & ~(cl.shift(1) > prev_high.shift(1))
    p = perf(newhi)
    items.append({"key": "new_high", "title": "신고가는 사라", "kind": "buy", **p, "verdict": verdict(p), "by_mk": by_mk(newhi),
                  "event": f"종가가 직전 {H}일 최고가를 처음 넘은 날 (52주 기록은 없어 {H}일로 확인)"})
    # 3) 갭은 메워진다: 시가가 전날 종가보다 3%↑(↓) 벌어져 시작 → 그날 안에 / 다음 4일 안에 전날 종가까지 돌아왔나.
    #    주가는 원래 출렁여 3% 떨어진 가격에 자주 다시 닿으므로, '갭 없이(시가 ±1% 안) 장중에 3%↑ 벌어진 날'의 다음 4일과 비교
    pc = cl.shift(1)
    g = rules["gap"]
    D = rules["gap_days"] - 1
    nxt_lo = pd.concat([lo.shift(-k) for k in range(1, D + 1)]).groupby(level=0).min().reindex(idx)
    nxt_hi = pd.concat([hi.shift(-k) for k in range(1, D + 1)]).groupby(level=0).max().reindex(idx)
    okd = lo.shift(-D).notna() & pc.notna() & op.notna()
    flat_open = (op / pc - 1).abs() < 0.01

    def rate(mask, hit):
        m = (mask & okd).values
        n = int(m.sum())
        return n, (round(float(hit.values[m].mean() * 100), 1) if n else None)

    def gap_stats(R):
        sides, hits = {}, [0.0, 0, 0.0, 0]
        for side, gap, same, later, far in (
                ("up", op >= pc * (1 + g / 100), lo <= pc, nxt_lo <= pc, cl >= pc * (1 + g / 100)),
                ("down", op <= pc * (1 - g / 100), hi >= pc, nxt_hi >= pc, cl <= pc * (1 - g / 100))):
            n, same_rate = rate(gap & R, same)
            # 마감 때도 3%↑ 벌어진 채 끝난 갭 vs 갭 없이 장중에 3%↑ 벌어져 끝난 날 — 같은 거리에서 다음 4일 안에 돌아왔나
            n_after, after = rate(gap & far & R, later)
            bn, base_rate = rate(flat_open & far & R, later)
            sides[side] = {"n": n, "same_day": same_rate, "after_n": n_after, "after": after, "base_n": bn, "base": base_rate}
            if after is not None and base_rate is not None:
                hits[0] += after / 100 * n_after; hits[1] += n_after; hits[2] += base_rate / 100 * bn; hits[3] += bn
        tot = sides["up"]["n"] + sides["down"]["n"]
        diff = z = None
        if hits[1] and hits[3]:
            p1, p2 = hits[0] / hits[1], hits[2] / hits[3]
            pp = (hits[0] + hits[2]) / (hits[1] + hits[3])
            diff = (p1 - p2) * 100
            se = (pp * (1 - pp) * (1 / hits[1] + 1 / hits[3])) ** 0.5
            z = (p1 - p2) / se if se > 0 else None
        if tot < 30 or diff is None or hits[1] < 30:
            v = "few"
        elif z is None or abs(z) < 2:
            v = "none"
        else:
            v = ("true" if diff >= 10 else "weak_true") if diff > 0 else ("false" if diff <= -10 else "weak_false")
        return {"n": tot, **sides, "diff": r1(diff), "z": round(z, 1) if z is not None else None, "verdict": v}

    allR = pd.DataFrame(True, index=idx, columns=cols)
    gs = gap_stats(allR)
    items.append({"key": "gap", "title": "갭은 메워진다", "kind": "gap", **gs,
                  "by_mk": {"up": gap_stats(up_day), "down": gap_stats(~up_day)},
                  "event": f"시가가 전날 종가보다 {g:g}% 넘게 위(아래)에서 시작한 날 → 그날 안에 전날 종가까지 돌아왔나, 마감 때도 벌어져 있었다면 다음 {D}일 안에 돌아왔나"})
    # 4) 5일 연속 빠지면 반등한다
    K = rules["down_days"]
    down = pd.concat([(ret.shift(k) < 0) for k in range(K)]).groupby(level=0).all().reindex(idx)
    down = down & ~(ret.shift(K) < 0)   # 딱 5일째 되는 날만 (6일·7일째 중복 빼기)
    p = perf(down)
    nxt = ret.shift(-1)
    m = (down & nxt.notna()).values
    p["next_up"] = round(float((nxt.values[m] > 0).mean() * 100), 1) if m.sum() else None
    bm = (ret.notna() & nxt.notna()).values
    base_next = round(float((nxt.values[bm] > 0).mean() * 100), 1) if bm.sum() else None
    items.append({"key": "down5", "title": f"{K}일 연속 빠지면 반등한다", "kind": "buy", **p, "base_next_up": base_next,
                  "verdict": verdict(p), "by_mk": by_mk(down), "event": f"종가가 {K}일 연속 내린 날"})
    # 5) 모두가 볼 때는 늦었다: 관심이 한꺼번에 몰린 날 다음 날 사면 (파는 쪽 속설 — 평소보다 나쁘면 맞음)
    mult = va / va.shift(1).rolling(20, min_periods=10).mean()
    hype = (mult >= rules["hype_mult"]) & (ret >= rules["hype_ret"])
    p = perf(hype)
    item = {"key": "hype", "title": "모두가 볼 때는 늦었다", "kind": "sell", **p, "verdict": verdict(p, bullish=False), "by_mk": by_mk(hype, False),
            "event": f"거래대금이 평소의 {rules['hype_mult']:g}배 넘고 그날 +{rules['hype_ret']:g}% 넘게 오른 날 (관심이 한꺼번에 몰림)"}
    subs = []
    hype2 = hype & (mult.shift(1) >= rules["hype_mult"])
    subs.append({"label": "이틀 연속 거래 폭발 + 급등", **perf(hype2, segs=False)})
    pos = {d: k for k, d in enumerate(idx)}
    rep_cnt = pd.DataFrame(0, index=idx, columns=cols)
    if reports and reports.get("items"):
        for x in reports["items"]:
            if x["code"] in rep_cnt.columns and x["date"] in pos:
                rep_cnt.iat[pos[x["date"]], rep_cnt.columns.get_loc(x["code"])] += 1
        many = rep_cnt.rolling(5, min_periods=1).sum() >= rules["report_n"]
        subs.append({"label": f"증권사 리포트 5일 안 {rules['report_n']}건↑ 쏟아짐", **perf(many & ~many.shift(1, fill_value=False), segs=False)})
    if attention:
        top = pd.DataFrame(False, index=idx, columns=cols)
        for d, codes in attention.items():
            if d in pos:
                for c in codes:
                    if c in top.columns:
                        top.iat[pos[d], top.columns.get_loc(c)] = True
        subs.append({"label": "네이버 검색 상위 100 (쌓는 중)", **perf(top & ~top.shift(1, fill_value=False), segs=False)})
    item["subs"] = subs
    items.append(item)
    # 6) 소문에 사서 뉴스에 팔아라: 뉴스(실적 발표 공시·오랜만의 증권사 리포트) 전 10일 이미 +10%↑ 올랐던(소문) 종목이
    #    뉴스 뒤에 소문 없던 종목보다 못하면 맞음. 뉴스 다음 날 시가에 산 성적으로 비교
    news_e = pd.DataFrame(False, index=idx, columns=cols)
    for c, v in ((reported or {}).items()):
        d = v.get("date") if isinstance(v, dict) else v
        if c in news_e.columns and d in pos:
            news_e.iat[pos[d], news_e.columns.get_loc(c)] = True
    news_r = (rep_cnt > 0) & (rep_cnt.shift(1).rolling(20, min_periods=1).sum().fillna(0) == 0)
    pre = (cl.shift(1) / cl.shift(11) - 1) * 100
    rumor = pre >= 10
    news = news_e | news_r

    def rumor_verdict(a, b):
        if min(a.get("n", 0), b.get("n", 0)) < 30 or a.get("stocks", 0) < 15 or a.get("days", 0) < 8:
            return "few"
        dx, db = a["x3"] - b["x3"], a["beat3"] - b["beat3"]
        se = ((a.get("se3") or 0) ** 2 + (b.get("se3") or 0) ** 2) ** 0.5
        t = dx / se if se > 0 else None
        if t is None or abs(t) < 2:
            return "none"
        if t < 0:   # 소문 쪽이 뉴스 뒤에 더 못함 → 속설 맞음
            v = "true" if dx <= -1 and db <= -5 else "weak_true"
        else:
            v = "false" if dx >= 1 and db >= 5 else "weak_false"
        return "shaky_" + v.replace("weak_", "") if (a.get("top5") or 0) > 50 else v

    rows = []
    for lab, ev in (("실적 발표 공시", news_e), ("오랜만의 증권사 리포트 (20일 없다가)", news_r), ("뉴스 전체", news)):
        rows.append({"label": lab, "rumor": perf(ev & rumor, segs=False), "quiet": perf(ev & ~rumor & pre.notna(), segs=False)})
    a, b = rows[-1]["rumor"], rows[-1]["quiet"]
    mkr = {}
    for k, R in (("up", up_day), ("down", ~up_day)):
        ra, rb = perf(news & rumor & R, segs=False), perf(news & ~rumor & pre.notna() & R, segs=False)
        mkr[k] = {"rumor": ra, "quiet": rb, "verdict": rumor_verdict(ra, rb)}
    items.append({"key": "rumor", "title": "소문에 사서 뉴스에 팔아라", "kind": "pair", "n": int(a.get("n", 0)) + int(b.get("n", 0)),
                  "rows": rows, "verdict": rumor_verdict(a, b), "by_mk": mkr,
                  "pre_run": r1(float(pre[news & rumor & valid].stack().mean())) if a.get("n") else None,
                  "event": "실적 발표·오랜만의 리포트(뉴스) 전 10일 동안 이미 +10%↑ 오른 종목(소문에 산 사람이 있음) vs 안 오른 종목 — 뉴스 다음 날 시가에 샀다면"})
    # 7~9) OpenDART '사는' 공시: 공시가 나온 날(대개 장 마감 뒤) 다음 날 아침에 샀다면
    if dart_events:
        for key, kind, title, event in (
                ("insider", "insider", "회사 사람이 자기 주식을 사면 오른다", "임원·주요주주가 자기 회사 주식을 늘렸다고 보고한 날 (OpenDART)"),
                ("buyback", "buyback", "자사주를 산다고 하면 오른다", "회사가 자기 주식을 사겠다고(자기주식 취득 결정) 공시한 날 (OpenDART)"),
                ("major", "major", "큰손이 5% 넘게 사들이면 오른다", "5% 넘게 가진 투자자가 장에서 더 샀다고 보고한 날 (OpenDART)")):
            ev = pd.DataFrame(False, index=idx, columns=cols)
            for x in dart_events:
                if x.get("kind") == kind and x.get("code") in ev.columns and x.get("date") in pos:
                    ev.iat[pos[x["date"]], ev.columns.get_loc(x["code"])] = True
            if not ev.values.any():
                continue
            p = perf(ev)
            items.append({"key": key, "title": title, "kind": "buy", **p, "verdict": verdict(p), "by_mk": by_mk(ev), "event": event})
    days = [d for d in idx if valid.loc[d].any()]
    return {"from": days[0] if days else None, "to": days[-1] if days else None, "base": base, "cost": cost, "rules": rules, "items": items,
            "mk_basis": mk_basis, "mk_days": {"up": int(mk_up[[valid.loc[d].any() for d in idx]].sum()),
                                              "down": int((~mk_up[[valid.loc[d].any() for d in idx]]).sum())}}


def mover_scorecard(movers):
    """그동안의 급등주 복기 모음: 미리 잡은·당일 잡은·놓친 비율, 신호별로 급등 전에 있었던 비율과 평균 몇 일 먼저였나."""
    if not movers:
        return None
    n = len(movers)
    cnt = {k: sum(1 for m in movers if m["verdict"] == k) for k in ("early", "sameday", "late", "missed")}
    sig = {}
    for m in movers:
        for e in m.get("early") or []:
            s = sig.setdefault(e["key"], {"label": e["label"], "n": 0, "lead": []})
            s["n"] += 1
            s["lead"].append(e["lead"])
    return {"n": n, **{k: round(100 * v / n) for k, v in cnt.items()},
            "signals": sorted(({"key": k, "label": v["label"], "pct": round(100 * v["n"] / n),
                                "lead": r1(sum(v["lead"]) / len(v["lead"]))} for k, v in sig.items()), key=lambda r: -r["pct"])}


def theme_heat(src, net, caps, days=20):
    """테마 × 날짜 히트맵: 날마다 그 테마(ETF 구성 종목)에 들어온 큰손 돈(시총 대비 %, 억)과 대표 ETF 등락(%).

    src: {테마: ([구성 종목 코드], 대표 ETF 일별 등락 Series 또는 None)}, net: 날짜×종목 큰손 순매수(원)
    """
    if net is None or net.empty or not src:
        return None
    cols = list(net.index[-days:])
    rows = []
    for name, (codes, ret) in src.items():
        cs = [c for c in codes if c in net.columns and caps.get(c)]
        if not cs:
            continue
        cap = sum(caps[c] for c in cs)
        flow = [float(net.loc[d, cs].fillna(0).sum()) for d in cols]
        rows.append({"name": name,
                     "pct": [round(v / cap * 100, 3) for v in flow],            # 시총 대비 % (테마 크기가 달라도 비교되게)
                     "eok": [round(v / EOK) for v in flow],
                     "ret": [r1(ret.get(d)) if ret is not None and d in ret.index and ret.get(d) == ret.get(d) else None for d in cols]})
    # 최근 5일 돈이 많이 들어온 테마부터
    rows.sort(key=lambda r: -sum(r["pct"][-5:]))
    return {"days": cols, "rows": rows}


# ---------------------------------------------------------------- 전체 종목 검색용 요약

def _bits(checks):
    return "".join("1" if c["ok"] else "0" for c in checks)


def stock_index(universe, excluded, closes, values, flows_daily, flows_5d, news_today, news_3d,
                fund_now=None, fund_hist=None, sectors=None, picked=None, rules=None, vrules=None):
    """전 종목 한 줄 요약 + 탭별 조건 통과 여부(1/0 문자열). 화면의 '왜 후보에 안 걸렸나'용.

    universe: 보통주 전체 {코드: listing 정보} (위험 공시로 뺀 종목 포함), excluded: {코드: [사유]}
    조건 문구는 labels 에 한 번만 담고 종목마다 통과 여부만 담아 파일을 작게 한다.
    """
    rules, vrules = rules or RULES, vrules or VALUE_RULES
    picked = picked or {}
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    avg_val20 = values.iloc[-21:-1].mean()
    pbr_med = eps0s = None
    if fund_now is not None and len(fund_now) and fund_hist:
        h = pd.DataFrame({i: f["PBR"] for i, f in enumerate(fund_hist)})
        pbr_med = h.where(h > 0).median(axis=1)
        eps0s = fund_hist[0]["EPS"]
    out = {}
    for code, info in universe.items():
        if code not in closes.columns:
            continue
        col = closes[code]
        close = col.iloc[-1]
        if close is None or math.isnan(close):
            continue
        fl = flow_detail(code, flows_daily, flows_5d, None)
        f5 = (fl["foreign_5d"] or 0) + (fl["inst_5d"] or 0)
        rec = {"n": info["name"], "m": {"KOSPI": "P", "KOSDAQ": "D"}.get(info["market"], info["market"][:1]), "c": round(float(close)), "ch": r1(info["change"]),
               "cap": round(info["cap"] / EOK), "v": round(info["value"] / EOK), "f5": round(f5),
               "pd": fl["days_pos"], "nw": 2 if code in news_today else 1 if code in news_3d else 0}
        if sectors is not None and code in sectors:
            rec["s"] = sectors.get(code)
        size = info["cap"] >= rules["min_cap"] and info["value"] >= rules["min_value"]
        vsize = info["cap"] >= vrules["min_cap"] and info["value"] >= vrules["min_value"]
        rec["sz"] = ("1" if size else "0") + ("1" if vsize else "0")
        avg = avg_val20.get(code)
        if avg and avg > 0:
            net = _money(flows_daily[-1][1]["foreign"], code) + _money(flows_daily[-1][1]["inst"], code) if flows_daily else 0.0
            rec["sc"] = _bits(scalp_checks(info["value"] / avg, info["change"], net, info["value"], rules))
            rec["mul"] = r1(info["value"] / avg)
        c = col.dropna()
        if len(closes) >= 61 and not math.isnan(ma60[code].iloc[-1]):
            m20, m60, m20p = ma20[code].iloc[-1], ma60[code].iloc[-1], ma20[code].iloc[-6]
            rise5 = (close / col.iloc[-6] - 1) * 100
            net5 = _money(flows_5d["foreign"], code) + _money(flows_5d["inst"], code)   # 반올림 전 원 단위로 (필터와 같은 값)
            ratio = net5 / info["cap"] * 100 if info["cap"] else 0
            rec["sw"] = _bits(swing_checks(close, m20, m60, m20p, rise5, fl["days_pos"], ratio, rules))
            rec["g20"], rec["g60"] = r1((close / m20 - 1) * 100), r1((close / m60 - 1) * 100)
        if pbr_med is not None and code in fund_now.index and len(c) >= 60:
            f = fund_now.loc[code]
            pbr, per, eps = f.get("PBR", 0), f.get("PER", 0), f.get("EPS", 0)
            med, eps0 = pbr_med.get(code), eps0s.get(code)
            rec["per"], rec["pbr"] = (r1(per) if per else None), (round(float(pbr), 2) if pbr else None)
            if not (pbr > 0 and per > 0 and eps > 0):
                rec["vr"] = "적자이거나 재무 자료 없음"
            elif med is None or math.isnan(med) or med <= 0 or eps0 is None or math.isnan(eps0) or eps0 <= 0:
                rec["vr"] = "1년 전 재무 자료가 없음"
            else:
                drop = (1 - close / c.iloc[-60:].max()) * 100
                rec["va"] = _bits(value_checks(drop, pbr, med, per, eps, eps0, vrules))
                rec["dr"] = r1(drop)
        if code in excluded:
            rec["x"] = excluded[code][:2]
        if picked.get(code):
            rec["p"] = picked[code]
        out[code] = rec
    dummy = dict(mult=0, change=0, net_today=0, value=1)
    labels = {"scalp": [c["label"] for c in scalp_checks(**dummy, rules=rules)],
              "swing": [c["label"] for c in swing_checks(2, 1, 0, 1, 0, 0, 0, rules)],
              "value": [c["label"] for c in value_checks(0, 1, 1, 1, 1, 1, vrules)]}
    return {"labels": labels, "rules": {"min_cap": rules["min_cap"] / EOK, "min_value": rules["min_value"] / EOK,
                                        "v_min_cap": vrules["min_cap"] / EOK, "v_min_value": vrules["min_value"] / EOK},
            "stocks": out}


# ---------------------------------------------------------------- 증권사 리포트 (주목도)

# 리포트는 '증권사가 다루기 시작했다'는 신호라 검색 순위보다 앞선다. 특히 오랫동안 없다가 새로 나온 리포트를 본다.
REPORT_RULES = {"recent_days": 14, "quiet_days": 90}


def merge_reports(store, new, base, fetched_since, keep_days=150):
    """리포트 기록(reports.json)에 새 목록(fetched_since 이후를 받은 것)을 합치고 오래된 것은 버린다. 같은 리포트(id)는 한 번만."""
    old = {r["id"]: r for r in store.get("items", [])}
    for r in new:
        old[r["id"]] = r
    cut = (datetime.strptime(base, "%Y%m%d") - timedelta(days=keep_days)).strftime("%Y%m%d")
    items = sorted((r for r in old.values() if r["date"] >= cut), key=lambda r: (r["date"], r["id"]), reverse=True)
    since = min(store.get("since") or fetched_since, fetched_since)
    return {"since": max(since, cut), "updated": max(store.get("updated") or base, base), "items": items}


def report_attention(code, store, base, rules=REPORT_RULES):
    """기준일까지 최근 2주 리포트와, 그 전 석 달 동안 리포트가 있었는지 ('오랜만에 다뤄짐' 판단)."""
    if not store or not store.get("items"):
        return None
    b = datetime.strptime(base, "%Y%m%d")
    recent_cut = (b - timedelta(days=rules["recent_days"])).strftime("%Y%m%d")
    quiet_cut = (b - timedelta(days=rules["recent_days"] + rules["quiet_days"])).strftime("%Y%m%d")
    mine = [r for r in store["items"] if r["code"] == code and r["date"] <= base]
    recent = [r for r in mine if r["date"] > recent_cut]
    before = [r for r in mine if quiet_cut < r["date"] <= recent_cut]
    # 기록이 석 달 치가 안 되면 '오랜만'인지 알 수 없다
    known = (store.get("since") or base) <= quiet_cut
    return {"n": len(recent), "before": len(before), "fresh": bool(recent) and not before and known,
            "known": known, "items": recent[:3]}


# ---------------------------------------------------------------- 실적 시즌

# 법정 마감: 분기·반기보고서는 분기 끝나고 45일, 사업보고서는 90일
REPORT_TITLES = ("분기보고서", "반기보고서", "사업보고서", "잠정)실적", "잠정실적", "손익구조")
NOTICE_TITLES = ("실적공시예고", "결산실적공시예고")   # 기업설명회(IR)는 실적 발표 날짜라고 볼 수 없어 뺌


def report_season(base):
    """기준일이 실적 발표 기간이면 그 기간 정보. 아니면 None."""
    y, md = int(base[:4]), base[4:]
    for start, due, key, label in (("0101", "0331", f"{y - 1}Y", f"{y - 1}년 결산"), ("0401", "0515", f"{y}Q1", "1분기"),
                                   ("0701", "0814", f"{y}Q2", "2분기(반기)"), ("1001", "1114", f"{y}Q3", "3분기")):
        if start <= md <= due:
            return {"key": key, "label": label, "start": f"{y}{start}", "due": f"{y}{due}"}
    return None


def update_reported(store, season, items, name_to_code):
    """DART 공시 목록에서 이번 시즌 실적 발표(보고서·잠정실적)와 발표 예고를 모은다. store 는 그대로 고쳐서 돌려준다."""
    if not season:
        return store
    if store.get("season") != season["key"] or store.get("v") != 2:   # v2: 접수일 기준으로 다시 모음
        store = {"season": season["key"], "v": 2, "checked": [], "reported": {}, "notice": {}}
    for x in items:
        code = name_to_code.get(x.get("corp"))
        t = x.get("title", "")
        if not code or "정정" in t[:8]:
            continue
        # 날짜는 접수번호 앞 8자리(실제 접수일)를 우선 쓴다
        rc = re.search(r"rcpNo=(\d{8})", x.get("url") or "")
        day = rc.group(1) if rc else x["at"][:10].replace("-", "")
        if day < season["start"]:
            continue
        kind = "reported" if any(k in t for k in REPORT_TITLES) and "예고" not in t else \
            "notice" if any(k in t for k in NOTICE_TITLES) else None
        if kind and (code not in store[kind] or store[kind][code]["date"] > day):
            store[kind][code] = {"date": day, "title": t, "url": x.get("url")}
    return store


def earnings_status(code, base, season, store):
    """후보 카드용: 이번 시즌 실적을 이미 냈는지, 아직이면 마감까지 며칠인지."""
    if not season or store.get("season") != season["key"] or not store.get("checked"):   # 한 날도 못 받았으면 판단 안 함
        return None
    rep = store["reported"].get(code)
    if rep and rep["date"] <= base:
        return {"state": "done", "label": season["label"], "date": rep["date"], "title": rep["title"], "url": rep.get("url")}
    left = (datetime.strptime(season["due"], "%Y%m%d") - datetime.strptime(base, "%Y%m%d")).days
    note = store["notice"].get(code)
    return {"state": "pending", "label": season["label"], "due": season["due"], "days_left": left,
            "notice": note if note and note["date"] <= base else None}


# ---------------------------------------------------------------- 언제 정리할지 (나가는 기준)

# 정리선은 걸린 날 값으로 고정한다 (스윙도 매일 바뀌는 20일 평균이 아니라 걸린 날의 20일 평균). 성적표도 같은 규칙:
# 마감 가격이 정리선 아래면 다음 날 아침 첫 가격에 판다
EXIT_RULES = {
    "swing": ("걸린 날의 20일 평균 가격 아래로 마감하면 다음 날 아침에 정리 (선은 걸린 날 값으로 고정)", "오르는 흐름이 깨졌다는 뜻", "보통 1~4주"),
    "scalp": ("걸린 날 저가 아래로 마감하면 다음 날 아침에 정리", "급등을 만든 힘이 빠졌다는 뜻", "보통 1~3일, 길게 끌지 않기"),
    "value": ("걸린 날까지 최근 10일 최저가 아래로 마감하면 다음 날 아침에 정리", "아직 바닥이 아니었다는 뜻", "몇 주~몇 달"),
}


def exit_level(group, close_s, low_s=None):
    """걸린 날(마지막 값) 기준 정리 가격. close_s / low_s: 그날까지의 종가·저가 Series."""
    c = close_s.dropna()
    if len(c) < 20 or group not in EXIT_RULES:
        return None
    close = float(c.iloc[-1])
    lows = low_s.dropna() if low_s is not None else None
    if group == "swing":
        stop = float(c.iloc[-20:].mean())
    elif group == "scalp":
        stop = float(lows.iloc[-1]) if lows is not None and len(lows) and lows.index[-1] == c.index[-1] else close * 0.97
    else:
        stop = float(lows.iloc[-10:].min()) if lows is not None and len(lows) >= 10 else float(c.iloc[-10:].min())
    if not (0 < stop <= close):
        return None
    rule, why, hold = EXIT_RULES[group]
    return {"stop": round(stop), "gap": r1((stop / close - 1) * 100), "rule": rule, "why": why, "hold": hold}


# ---------------------------------------------------------------- 기록 출처 · 전략 버전

# 점수 공식 번호 (공식을 바꾸면 올림). 기준값(RULES 등)까지 묶은 짧은 해시를 같이 남겨 '어떤 규칙으로 고른 기록인가'를 구분한다
STRATEGY = {"scalp": 3, "swing": 1, "value": 2}
# 지금 공식을 처음 쓴 기준일: 그 전 기록은 공식을 만들 때 본 기간(규칙을 정한 기간), 그 뒤가 검증 기간
STRATEGY_SINCE = {"scalp": "20261022", "value": "20261022", "swing": None}


def strategy_ver():
    src = json.dumps([RULES, VALUE_RULES, SCALP_WEIGHTS, VALUE_WEIGHTS, WAKE_RULES, {k: v[0] for k, v in EXIT_RULES.items()}],
                     sort_keys=True, default=str)
    return f"s{STRATEGY['scalp']}w{STRATEGY['swing']}v{STRATEGY['value']}-" + hashlib.sha1(src.encode()).hexdigest()[:6]


def _next_weekday(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def record_origin(base, made):
    """기준일 장 마감(15:30) 뒤 ~ 다음 영업일 장 시작(09:00) 전에 만든 기록만 '실제 기록'(live).
    그 뒤에 만든 건 나중에 다시 계산한 기록(recalc). 공휴일은 모르므로 다음 평일로 본다(창이 짧아지는 쪽 = 보수적).
    made: KST datetime (naive 면 KST 로 본다)."""
    b = datetime.strptime(base, "%Y%m%d")
    made = made.replace(tzinfo=None)
    start = b.replace(hour=15, minute=30)
    end = _next_weekday(b).replace(hour=9, minute=0)
    return "live" if start <= made < end else "recalc"


def classify_record(rec, base):
    """저장된 기록의 출처: live / recalc / unknown(판별 불가 — 만든 시각이 없음)."""
    meta = rec.get("record") or {}
    if meta.get("origin") in ("live", "recalc"):
        return meta["origin"]
    if rec.get("backfill"):
        return "recalc"
    made = rec.get("generated_at")
    if not made:
        return "unknown"
    try:
        return record_origin(base, datetime.fromisoformat(made))
    except ValueError:
        return "unknown"


# ---------------------------------------------------------------- 후보 변화 추적

SCORE_PART_NAMES = {
    "scalp": {"flow": "큰손 확인 (오늘 순매수 비중)", "volume": "거래 증가", "move": "오늘 상승폭"},
    "swing": {"flow": "5일 큰손 순매수 (덩치 대비)", "streak": "연속 순매수 일수"},
    "value": {"drop": "낙폭", "cheap": "평소보다 쌈 (PBR)", "per": "PER", "eps": "이익"},
}


def score_inputs(group, x):
    """점수를 다시 계산할 수 있는 최소 재료 (기록 파일의 후보·최소 기록 둘 다에서)."""
    if x.get("k") is not None:
        return x["k"]
    fl = x.get("flow") or {}
    if group == "scalp":
        if not x.get("value_mult") or not x.get("value_eok") or x.get("change") is None:
            return None
        net = (fl.get("foreign_today") or 0) + (fl.get("inst_today") or 0)
        return {"mult": x["value_mult"], "chg": x["change"], "nr": round(max(0, net) / x["value_eok"], 4)}
    if group == "swing":
        st = fl.get("streak")
        if st is None or x.get("score") is None:
            return None
        ratio = x.get("ratio") if x.get("ratio") is not None else x["score"] / (1 + st / 5)
        return {"ratio": round(ratio, 4), "streak": st, "pos": fl.get("days_pos"), "rise5": x.get("rise_5d")}
    if group == "value":
        if not all(x.get(k) is not None for k in ("drop_60d", "pbr", "pbr_med", "per", "eps_ratio")) or not x["pbr_med"]:
            return None
        return {"drop": x["drop_60d"], "pbr_r": round(x["pbr"] / x["pbr_med"], 3), "per": x["per"], "eps_r": x["eps_ratio"],
                "fwd": x.get("eps_fwd_ratio")}
    return None


def score_from_inputs(group, k, exact=False):
    """→ (지금 공식 점수, 항목별 값). 스윙 항목은 0~100이 아니라 실제 값 (순매수 비중 %, 연속일)."""
    if not k:
        return None, None
    if group == "scalp":
        return scalp_score(k["mult"], k["chg"], k["nr"], exact)
    if group == "swing":
        return round(k["ratio"] * (1 + k["streak"] / 5), 3), {"flow": k["ratio"], "streak": k["streak"]}
    if group == "value":
        return value_score(k["drop"], k["pbr_r"], k["per"], k["eps_r"], k.get("fwd"), exact)
    return None, None


def score_change(group, k_now, k_prev):
    """어제 대비 점수 변화와 그 몫 (항목별로 나눠 더하면 전체 변화와 같다). 원인은 점수 재료에서만 — 지어내지 않는다."""
    s1, p1 = score_from_inputs(group, k_now, True)
    s0, p0 = score_from_inputs(group, k_prev, True)
    if s1 is None or s0 is None:
        return None
    names = SCORE_PART_NAMES[group]
    if group == "swing":
        r0, r1_, t0, t1 = p0["flow"], p1["flow"], p0["streak"], p1["streak"]
        parts = {"flow": (r1_ - r0) * (1 + t0 / 5), "streak": r1_ * (t1 - t0) / 5}
        raw = {"flow": (f"{r0:.2f}%", f"{r1_:.2f}%"), "streak": (f"{t0}일", f"{t1}일")}
    else:
        w = SCALP_WEIGHTS if group == "scalp" else VALUE_WEIGHTS
        parts = {key: w[key] * (p1[key] - p0[key]) for key in w}
        raw = {key: (round(p0[key]), round(p1[key])) for key in w}
    fmt = {"scalp": {"flow": lambda k: f"{k['nr'] * 100:.0f}%", "volume": lambda k: f"{k['mult']:.1f}배",
                     "move": lambda k: f"{k['chg']:+.1f}%"},
           "value": {"drop": lambda k: f"-{k['drop']:.0f}%", "cheap": lambda k: f"{k['pbr_r']:.2f}배",
                     "per": lambda k: f"{k['per']:.1f}", "eps": lambda k: f"{k['eps_r']:.2f}배"}}.get(group)
    if fmt:   # 점수 항목(0~100) 말고 실제 값: 순매수 비중·거래 배수·등락률 / 낙폭·PBR 배율·PER·EPS 배율
        raw = {key: (fmt[key](k_prev), fmt[key](k_now)) for key in fmt}
    causes = sorted(({"key": key, "label": names[key], "d": round(v, 3 if group == "swing" else 1),
                      "from": raw[key][0], "to": raw[key][1]} for key, v in parts.items() if abs(v) > 1e-9),
                    key=lambda c: -abs(c["d"]))
    return {"prev": s0, "now": s1, "d": round(s1 - s0, 3 if group == "swing" else 1), "causes": causes}


def pool_record(group, items, extra=None):
    """조건을 통과한 후보 전체의 최소 기록 (화면엔 상위 10개만 내보내도 전부 남긴다)."""
    extra = extra or {}
    out = []
    for i, x in enumerate(items, 1):
        r = {"c": x["code"], "n": x["name"], "m": x.get("market"), "r": i, "s": x.get("score"),
             "p": round(float(x["close"])), "k": score_inputs(group, x), "nw": len(x.get("news") or [])}
        if group == "scalp":
            r["mul"] = x.get("value_mult")
        r.update(extra.get(x["code"]) or {})
        out.append(r)
    return out


def failed_conditions(group, rec, labels, rules=None, vrules=None):
    """전 종목 요약(stock_index)의 한 줄로 그 탭에서 깨진 조건 문구들. None = 판단할 자료 없음."""
    rules, vrules = rules or RULES, vrules or VALUE_RULES
    if rec is None:
        return None
    out = []
    if rec.get("x"):
        out.append("위험 공시·투자 주의로 제외: " + ", ".join(rec["x"]))
    sz = rec.get("sz") or "00"
    if group == "value":
        if sz[1] != "1":
            out.append(f"시총 {vrules['min_cap'] / EOK:,.0f}억·거래대금 {vrules['min_value'] / EOK:,.0f}억 이상 아님")
        if rec.get("vr"):
            out.append(rec["vr"])
        bits = rec.get("va")
    else:
        if sz[0] != "1":
            out.append(f"시총 {rules['min_cap'] / EOK:,.0f}억·거래대금 {rules['min_value'] / EOK:,.0f}억 이상 아님")
        nw = rec.get("nw") or 0
        if group == "scalp" and nw < 2:
            out.append("오늘 기사·공시 없음")
        if group == "swing" and nw < 1:
            out.append("최근 3일 기사·공시 없음")
        bits = rec.get("sc" if group == "scalp" else "sw")
    if bits is None and not out:
        return None
    out += [lb for lb, b in zip(labels.get(group) or [], bits or "") if b == "0"]
    return out


def candidate_changes(group, today, prev_top, prev_pool, today_pool, index=None, labels=None, top=10):
    """어제 상위 top 개와 비교해 오늘 화면 후보를 새로 들어옴 / 유지로 나누고, 빠진 종목은 왜 빠졌는지.

    today: 오늘 후보 전체(점수순), prev_top: 어제 기록 파일의 상위 후보, prev_pool: 어제 전체 최소 기록(없으면 None)
    today_pool: 오늘 전체 최소 기록, index: 오늘 전 종목 요약 stocks (깨진 조건용)
    오늘 상위 후보에는 x["track"] 을 붙이고, 빠진 종목 목록을 돌려준다.
    """
    prev_rank = {y["code"]: i for i, y in enumerate(prev_top, 1)}
    prev_by = {y["code"]: y for y in prev_top}
    pool_prev = {r["c"]: r for r in prev_pool} if prev_pool is not None else None
    pool_now = {r["c"]: r for r in today_pool}
    for i, x in enumerate(today[:top], 1):
        c = x["code"]
        tr = {"st": "kept" if c in prev_rank else "new", "rank": i}
        if c in prev_rank:
            tr["prev_rank"] = prev_rank[c]
            old = score_inputs(group, prev_by[c])
        elif pool_prev is not None and c in pool_prev:
            tr["prev_rank"] = pool_prev[c]["r"]     # 어제도 조건은 통과했지만 화면 밖 순위
            old = pool_prev[c].get("k")
        else:
            old = None
            if pool_prev is None:
                tr["prev_known"] = False             # 어제 전체 기록이 없어 '어제 순위 밖이었나'는 모름
        ch = score_change(group, score_inputs(group, x), old) if old else None
        if ch:
            tr["score"] = ch
        x["track"] = tr
    dropped = []
    now_codes = {x["code"] for x in today[:top]}
    for c, rk in prev_rank.items():
        if c in now_codes:
            continue
        y = prev_by[c]
        d = {"code": c, "name": y.get("name"), "prev_rank": rk, "prev_close": y.get("close")}
        rec = (index or {}).get(c)
        if c in pool_now:
            d["why"], d["rank"] = "rank", pool_now[c]["r"]
            ch = score_change(group, pool_now[c].get("k"), score_inputs(group, y))
            if ch:
                d["score"] = ch
        else:
            broken = failed_conditions(group, rec, labels or {})
            if broken:
                d["why"], d["broken"] = "filter", broken
            else:
                d["why"] = "unknown"                 # 오늘 자료가 없거나 깨진 조건을 못 찾음 → 추적 불가
        if rec:
            d["close"] = rec.get("c")
        dropped.append(d)
    return dropped


def first_seen(code, group, records):
    """우리가 가진 기록 중 이 탭에 처음 걸린 날과 그날 종가. records: [(기준일, filter dict)] 오래된 순."""
    for d, flt in records:
        for y in flt.get(group) or []:
            if y["code"] == code:
                return {"date": d, "price": y.get("close")}
        for r in (flt.get("pools") or {}).get(group) or []:
            if r["c"] == code:
                return {"date": d, "price": r.get("p")}
    return None


# ---------------------------------------------------------------- 상대강도 · 평소 변동폭(ATR)

def price_breaks(closes, chg, tol=1.0):
    """종가로 잰 등락률과 거래소 등락률(권리락·분할 등을 반영한 기준가 대비)이 tol %p 넘게 다르면 가격이 끊긴 날 (True).
    거래소 등락률이 없으면 하루 ±30.5% 넘는 움직임(가격 제한폭 밖)을 끊긴 날로 본다."""
    raw = (closes / closes.shift(1) - 1) * 100
    if chg is None:
        return raw.abs() > 30.5
    chg = chg.reindex(index=closes.index, columns=closes.columns)
    off = (raw - chg).abs() > tol
    # 하루에 5% 넘는 종목이 어긋나면 권리락이 아니라 그날 등락률 자료가 이상한 것 → 그날은 제한폭 기준만
    bad_day = off.sum(axis=1) > 0.05 * closes.notna().sum(axis=1).clip(lower=1)
    off.loc[bad_day] = False
    return off | (raw.abs() > 30.5)


def rel_strength(close_s, idx_close, breaks_s=None, n=20):
    """n영업일 종목 수익률 − 같은 날짜 사이 그 시장 지수 수익률 (%p).
    close_s: 종목 종가 Series(index=YYYYMMDD, 마지막이 기준일), idx_close: 지수 종가 Series(같은 index).
    → ({stock, mkt, diff, from}, None) 또는 (None, 이유)."""
    if close_s is None or len(close_s) < n + 1:
        return None, "기간 부족"
    w = close_s.iloc[-n - 1:]
    if w.isna().any() or (w <= 0).any():
        return None, "기간 중 거래 없는 날 (신규 상장·거래 정지)"
    if breaks_s is not None and bool(breaks_s.iloc[-n:].fillna(False).any()):
        return None, "기간 중 가격이 끊김 (권리락·분할 등)"
    d0, d1 = w.index[0], w.index[-1]
    i0, i1 = idx_close.get(d0) if idx_close is not None else None, idx_close.get(d1) if idx_close is not None else None
    if not i0 or not i1 or math.isnan(i0) or math.isnan(i1):
        return None, "지수 자료 없음"
    s = (w.iloc[-1] / w.iloc[0] - 1) * 100
    m = (i1 / i0 - 1) * 100
    return {"stock": r1(s), "mkt": r1(m), "diff": r1(s - m), "from": d0}, None


def rs_pair(close_s, idx_close, breaks_s=None):
    """20·60영업일 상대강도. 화면·기록용 (점수·필터엔 안 씀)."""
    out = {}
    for n in (20, 60):
        v, why = rel_strength(close_s, idx_close, breaks_s, n)
        out[f"r{n}"] = v if v else {"why": why}
    return out


def atr_wilder(high, low, close, n=14, breaks_s=None, min_len=None):
    """평소 하루 변동폭 ATR(n). TR = max(고가−저가, |고가−전일 종가|, |저가−전일 종가|),
    첫 값은 TR n개 평균, 그 뒤 (전 ATR×(n−1) + TR)/n (Wilder). 빈 날·가격 끊긴 날 뒤부터만 쓴다.
    → ATR 값 또는 None (이어진 기간이 min_len(기본 2n)개 TR보다 짧음)."""
    min_len = min_len or 2 * n
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1, skipna=False)
    bad = tr.isna()
    if breaks_s is not None:
        bad = bad | breaks_s.reindex(tr.index).fillna(False).astype(bool)
    idx = [i for i, b in enumerate(bad.values) if b]
    tr = tr.iloc[idx[-1] + 1:] if idx else tr
    if len(tr) < min_len:
        return None
    atr = float(tr.iloc[:n].mean())
    for v in tr.iloc[n:]:
        atr = (atr * (n - 1) + float(v)) / n
    return atr


def entry_risk(ref, stop, atr):
    """기준 가격에서 정리선까지 거리 (%와 평소 변동폭의 몇 배). 최대 손실이나 예상 손실이 아니다."""
    if not ref or ref <= 0:
        return None
    out = {"ref": round(ref)}
    if atr:
        out["atr"], out["atr_pct"] = round(atr, 1), r1(atr / ref * 100)
    if stop:
        out["stop"] = round(stop)
        out["dist"] = r1((ref - stop) / ref * 100)
        if atr:
            out["mult"] = round((ref - stop) / atr, 1)
    return out


def _drawdown(path):
    """거래 하나 동안 가장 깊이 빠진 폭(%): 지나온 최고값 대비 (사는 가격에서 시작). 포트폴리오 MDD가 아니다."""
    peak, dd = path[0], 0.0
    for v in path:
        peak = max(peak, v)
        dd = max(dd, (1 - v / peak) * 100)
    return dd


def _tail(vals, frac=0.1, worst=True):
    """나쁜 쪽 10% 평균 (10건 미만이면 None)."""
    if len(vals) < 10:
        return None
    k = max(1, math.ceil(len(vals) * frac))
    v = sorted(vals, reverse=not worst)[:k]
    return r1(sum(v) / k)


def stop_scorecard(history, closes, lows, opens=None, bench=None, horizons=(5, 20), cost=None, index_closes=None, breaks=None):
    """정리 기준을 지켰다면: 그냥 N일 들고 있기 vs 정리선 아래로 마감한 다음 날 아침 첫 가격(시가)에 팔고 현금으로 기다리기.

    - 둘 다 같은 날 사서(다음 날 시가) 같은 날(N영업일째)까지 잰다. 일찍 판 뒤엔 현금(0%)이라 시장 대비는 N일 시장 수익과 비교
    - 정리선은 걸린 날까지의 종가·저가로 정하고 그 뒤 고정 (예전 기록에도 같은 규칙)
    - 다음 날 시가가 비면(거래 정지 등) 그다음 거래되는 날 시가에 판다. N일째까지 못 팔면 N일째 종가 (실제로도 못 팔았을 것)
    - 마감 가격이 빈 날은 정리선 판단을 못 하므로 건너뛴다. 기간 중 가격이 끊긴(권리락·분할) 거래는 양쪽 모두에서 뺀다
    - 평균·이긴 비율 말고 나쁜 쪽 10% 평균, 거래 하나 동안 가장 깊이 빠진 폭(마감 가격 기준)도 같이
    """
    cost = COST if cost is None else cost
    days = list(closes.index)
    pos = {d: i for i, d in enumerate(days)}
    acc, last = {}, {}
    for date, groups in sorted(history, key=lambda h: h[0]):
        if date not in pos:
            continue
        i0 = pos[date]
        for g, items in groups.items():
            if g not in EXIT_RULES:
                continue
            for x in items:
                code = x["code"]
                if code not in closes.columns:
                    continue
                cs = closes[code]
                os_ = opens[code] if opens is not None and code in opens.columns else None
                ex = exit_level(g, cs.iloc[:i0 + 1], lows[code].iloc[:i0 + 1] if lows is not None and code in lows.columns else None)
                for n in horizons:
                    if i0 + n >= len(days):
                        continue
                    c_end = cs.iloc[i0 + n]
                    buy = os_.iloc[i0 + 1] if os_ is not None else x["close"]
                    if c_end is None or math.isnan(c_end) or buy is None or math.isnan(buy) or not buy:
                        continue
                    if breaks is not None and code in breaks.columns and bool(breaks[code].iloc[i0 + 1:i0 + n + 1].fillna(False).any()):
                        continue
                    if (g, code, n) in last and i0 - last[(g, code, n)] < n:
                        continue
                    last[(g, code, n)] = i0
                    path = [buy] + [float(v) for v in cs.iloc[i0 + 1:i0 + n + 1] if not math.isnan(v)]
                    sell, k_out, spath = float(c_end), None, path
                    if ex:
                        for k in range(1, n):
                            v = cs.iloc[i0 + k]
                            if math.isnan(v) or v >= ex["stop"]:
                                continue
                            for j in range(k + 1, n + 1):
                                o = os_.iloc[i0 + j] if os_ is not None else None
                                if o is not None and not math.isnan(o) and o > 0:
                                    sell, k_out = float(o), k
                                    spath = [buy] + [float(u) for u in cs.iloc[i0 + 1:i0 + k + 1] if not math.isnan(u)] + [sell]
                                    break
                            break
                    mk = bench.get(mkt_of(x.get("market"))) if bench else None
                    if mk is not None:
                        k0, k1 = mk[0].get(days[i0 + 1]), mk[1].get(days[i0 + n])
                    elif index_closes is not None:
                        k0, k1 = index_closes.get(days[i0]), index_closes.get(days[i0 + n])
                    else:
                        k0 = k1 = None
                    m = (k1 / k0 - 1) * 100 if k0 and k1 else None
                    a = acc.setdefault(g, {}).setdefault(n, {"hold": [], "hold_x": [], "stop": [], "stop_x": [], "hit": 0,
                                                             "hold_dd": [], "stop_dd": [], "gap": []})
                    for kind, price, pth in (("hold", float(c_end), path), ("stop", sell, spath)):
                        r = (price / buy - 1) * 100 - cost
                        a[kind].append(r)
                        if m is not None:
                            a[kind + "_x"].append(r - m)
                        a[kind + "_dd"].append(_drawdown(pth))
                    if k_out:
                        a["hit"] += 1
                        a["gap"].append((sell / ex["stop"] - 1) * 100)   # 정리선보다 얼마나 낮게 팔렸나 (갭 하락)

    def summ(a, kind):
        s = summarize_returns(a[kind], a[kind + "_x"])
        if s:
            s["low10"] = _tail(a[kind])
            s["dd"] = r1(sum(a[kind + "_dd"]) / len(a[kind + "_dd"]))
            s["dd10"] = _tail(a[kind + "_dd"], worst=False)
        return s

    return {g: {str(n): {"hold": summ(a, "hold"), "stop": summ(a, "stop"),
                         "hit": round(100 * a["hit"] / len(a["hold"])) if a["hold"] else None,
                         "fill_gap": r1(sum(a["gap"]) / len(a["gap"])) if a["gap"] else None}
                for n, a in by_n.items()} for g, by_n in acc.items()}


HORIZONS = (1, 5, 20)


COST = 0.2  # 왕복 거래 비용(%): 매도 세금 + 수수료 대략


def scorecard(history, closes, index_closes, regimes=None, opens=None, bench=None, cost=COST, breaks=None, origins=None,
              entries_out=None):
    """과거 후보가 N영업일 뒤 얼마나 움직였나.

    history: [(기준일, {"scalp": [...], "swing": [...], "value": [...]})]
    closes / opens: DataFrame(행=영업일, 열=티커) 종가·시가
    index_closes: Series(index=영업일) 코스피 종가 (bench 가 없을 때 비교 기준)
    bench: {"KOSPI": (시가 Series, 종가 Series), "KOSDAQ": (...)} — 종목이 속한 시장 지수와 비교

    실제로 할 수 있는 매매에 맞춘다:
    - 결과는 장 마감 뒤에 나오므로 다음 영업일 시가에 사서 N영업일째 종가에 판다 (opens 가 있을 때)
    - 거래 비용 cost(%)를 뺀다
    - 같은 탭에서 같은 종목이 N일 안에 다시 걸리면 그 N일 성적에는 한 번만 센다 (구간 겹침 방지)
    - 보유 기간 중 가격이 끊긴(권리락·분할 등, breaks) 거래는 뺀다
    반환: 탭별 성적, 최근 기록, 조건별 성적, 장세별 성적, 점수 위·아래 절반 성적
    entries_out(list)을 주면 거래별 기록 전체를 담아 준다 (origins: {기준일: live/recalc/unknown})
    """
    origins = origins or {}
    every = []
    days = list(closes.index)
    pos = {d: i for i, d in enumerate(days)}
    stats, recent, by_regime, halves = {}, [], {}, {}
    regimes = regimes or {}
    last_counted = {}
    for date, groups in sorted(history, key=lambda h: h[0]):
        if date not in pos:
            continue
        i0 = pos[date]
        for g, items in groups.items():
            # 그날 그 탭 후보를 점수순으로 줄 세워 위 절반·아래 절반 (2개 이상일 때만)
            ranked = sorted(items, key=lambda y: -(rescore(g, y) or 0))
            half = {y["code"]: ("hi" if k < len(ranked) // 2 else "lo") for k, y in enumerate(ranked)} if len(ranked) >= 2 else {}
            for x in items:
                code = x["code"]
                entry = {"date": date, "group": g, "code": code, "name": x["name"], "close": x["close"]}
                mid = len(ranked) % 2 and ranked.index(x) == len(ranked) // 2
                meta = {"origin": origins.get(date), "half": None if mid else half.get(code), "rank": x.get("r"),
                        "mult": x.get("value_mult", x.get("mul")),
                        "rs20": ((x.get("rs") or {}).get("r20") or {}).get("diff")}
                for n in HORIZONS:
                    if i0 + n >= len(days) or code not in closes.columns:
                        continue
                    if breaks is not None and code in breaks.columns and bool(breaks[code].iloc[i0 + 1:i0 + n + 1].fillna(False).any()):
                        continue
                    c1 = closes[code].iloc[i0 + n]
                    buy = opens[code].iloc[i0 + 1] if opens is not None and code in opens.columns else x["close"]
                    if c1 is None or math.isnan(c1) or buy is None or math.isnan(buy) or not buy:
                        continue
                    ret = (c1 / buy - 1) * 100 - cost
                    mk = bench.get(mkt_of(x.get("market"))) if bench else None
                    if mk is not None:
                        k0, k1 = mk[0].get(days[i0 + 1]), mk[1].get(days[i0 + n])
                    else:
                        k0, k1 = index_closes.get(days[i0]), index_closes.get(days[i0 + n])
                    ex = ret - ((k1 / k0 - 1) * 100) if k0 and k1 else None
                    entry[f"r{n}"] = r1(ret)
                    if ex is not None:
                        entry[f"x{n}"] = r1(ex)
                    key = (g, code, n)
                    if key in last_counted and i0 - last_counted[key] < n:
                        entry.setdefault("dup", []).append(n)
                        continue
                    last_counted[key] = i0
                    buckets = [stats.setdefault(g, {}).setdefault(n, {"rets": [], "ex": []})]
                    if regimes.get(date):
                        buckets.append(by_regime.setdefault(regimes[date], {}).setdefault(g, {}).setdefault(n, {"rets": [], "ex": []}))
                    if code in half and not (len(ranked) % 2 and ranked.index(x) == len(ranked) // 2):
                        buckets.append(halves.setdefault(g, {}).setdefault(half[code], {}).setdefault(n, {"rets": [], "ex": []}))
                    for s in buckets:
                        s["rets"].append(ret)
                        if ex is not None:
                            s["ex"].append(ex)
                recent.append(entry)
                entry["features"] = features_of(x)
                every.append({**entry, **meta})
    out = {}
    for g, by_n in stats.items():
        out[g] = {}
        for n, s in by_n.items():
            out[g][str(n)] = summarize_returns(s["rets"], s["ex"])
    regime_out = {}
    for rk, by_g in by_regime.items():
        regime_out[rk] = {"days": sum(1 for d, _ in history if regimes.get(d) == rk),
                          "groups": {g: {str(n): summarize_returns(s["rets"], s["ex"]) for n, s in by_n.items()}
                                     for g, by_n in by_g.items()}}
    score_check = {g: {h: {str(n): summarize_returns(s["rets"], s["ex"]) for n, s in by_n.items()} for h, by_n in hl.items()}
                   for g, hl in halves.items()}
    recent.sort(key=lambda e: e["date"], reverse=True)
    if entries_out is not None:
        entries_out.extend(every)
    feats = feature_stats(recent) + feature_stats(recent, groups=("value",), features=VALUE_FEATURES)
    return out, recent[:60], feats, regime_out, score_check


MULT_BUCKETS = ((3, 5, "3~5배"), (5, 8, "5~8배"), (8, 15, "8~15배"), (15, 1e9, "15배 넘음"))
MIN_N = 30   # 이보다 적으면 비교 판단을 하지 않는다 (근거 부족)


def _cmp(a, b, min_n=MIN_N):
    """두 묶음 시장 대비 초과 수익 비교 → few(어느 한쪽 30건 미만) / none(차이가 운으로도 나올 폭 안) / better / worse (a가 b보다)."""
    if len(a) < min_n or len(b) < min_n:
        return "few"
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / (len(a) - 1)
    vb = sum((v - mb) ** 2 for v in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if not se or abs(ma - mb) < 2 * se:
        return "none"
    return "better" if ma > mb else "worse"


def scorecard_extra(entries, pool_dates=(), horizon=5, since=None, min_n=MIN_N):
    """scorecard 의 거래별 기록(entries_out)으로 나눠 보기. 모두 시장 대비 초과 수익(x{horizon}), 같은 종목 겹침은 뺀다.

    - by_origin: 실제 기록(live) / 다시 계산(recalc) / 판별 불가(unknown) 따로
    - scope: 전체 기록이 있는 날만 — 화면 상위 10개 vs 조건 통과 전체 (탭 이름 뒤 '_rest' = 11위 밑)
    - rs: 같은 날 같은 탭 후보 중 20일 상대강도 위 절반 vs 아래 절반
    - mult: 단타 후보 거래 배수 구간별 (점수 공식은 자동으로 바꾸지 않음)
    - periods: 점수 위·아래 절반을 '공식을 정한 기간'(since 전)과 '검증 기간'(since 뒤)으로 나눔
    """
    key = f"x{horizon}"
    pool_dates = set(pool_dates)
    since = STRATEGY_SINCE if since is None else since
    ok = [e for e in entries if e.get(key) is not None and horizon not in e.get("dup", [])]
    base = lambda g: g[:-5] if g.endswith("_rest") else g
    out = {"horizon": horizon, "min_n": min_n}
    by_o = {}
    for e in ok:
        if e["group"].endswith("_rest"):
            continue
        by_o.setdefault(e.get("origin") or "unknown", {}).setdefault(e["group"], []).append(e[key])
    out["by_origin"] = {o: {g: summarize_returns(v, v) for g, v in gs.items()} for o, gs in by_o.items()}
    scope = {}
    for e in ok:
        if e["date"] not in pool_dates:
            continue
        s = scope.setdefault(base(e["group"]), {"top": [], "all": [], "days": set()})
        s["all"].append(e[key])
        s["days"].add(e["date"])
        if not e["group"].endswith("_rest"):
            s["top"].append(e[key])
    out["scope"] = {g: {"top": summarize_returns(s["top"], s["top"]), "all": summarize_returns(s["all"], s["all"]),
                        "days": len(s["days"]), "verdict": "few" if len(s["all"]) < min_n else "info"} for g, s in scope.items()}
    rs = {}
    days = {}
    for e in ok:
        if e.get("rs20") is not None:
            days.setdefault((e["date"], base(e["group"])), []).append(e)
    for (d, g), es in days.items():
        if len(es) < 2:
            continue
        v = sorted(x["rs20"] for x in es)
        med = (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2
        for x in es:
            if x["rs20"] != med:
                rs.setdefault(g, {"hi": [], "lo": []})["hi" if x["rs20"] > med else "lo"].append(x[key])
    out["rs"] = {g: {"hi": summarize_returns(s["hi"], s["hi"]), "lo": summarize_returns(s["lo"], s["lo"]),
                     "verdict": _cmp(s["hi"], s["lo"], min_n)} for g, s in rs.items()}
    mult = {lb: [] for _, _, lb in MULT_BUCKETS}
    for e in ok:
        if base(e["group"]) == "scalp" and e.get("mult"):
            lb = next(lb for lo, hi, lb in MULT_BUCKETS if lo <= e["mult"] < hi or (lo == 3 and e["mult"] < 3))
            mult[lb].append(e[key])
    out["mult"] = {"rows": [{"label": lb, **(summarize_returns(v, v) or {"n": 0})} for lb, v in mult.items()],
                   "verdict": "few" if min(len(v) for v in mult.values()) < min_n else "info"}
    per = {}
    for e in ok:
        g = e["group"]
        if g.endswith("_rest") or e.get("half") is None:
            continue
        cut = since.get(g)
        span = "eval" if cut and e["date"] >= cut else "set"
        per.setdefault(g, {}).setdefault(span, {"hi": [], "lo": []})[e["half"]].append(e[key])
    out["periods"] = {}
    for g, sp in per.items():
        row = {"since": since.get(g)}
        for span, s in sp.items():
            row[span] = {"hi": summarize_returns(s["hi"], s["hi"]), "lo": summarize_returns(s["lo"], s["lo"]),
                         "verdict": _cmp(s["hi"], s["lo"], min_n)}
        ev = row.get("eval")
        row["verdict"] = "unconfirmed" if not ev or ev["verdict"] == "few" else ev["verdict"]
        out["periods"][g] = row
    return out


def summarize_returns(rets, ex=None):
    if not rets:
        return None
    return {"n": len(rets), "avg": r1(sum(rets) / len(rets)),
            "win": round(100 * sum(1 for r in rets if r > 0) / len(rets)),
            "excess": r1(sum(ex) / len(ex)) if ex else None, "low10": _tail(rets)}


# 조건별 성적: 후보 안에서 이 조건이 있을 때와 없을 때 5일 뒤 수익률 비교
# 저평가 후보: 주목받는 싼 종목 vs 아무도 안 보는 싼 종목 (예전 기록에도 있는 값만 써서 바로 비교되게)
VALUE_FEATURES = {
    "v_flow": "큰손이 5일 동안 삼",
    "v_news": "최근 3일 기사·공시 있음",
    "v_wake2": "주목 신호 2개 이상",
    "report": "최근 2주 증권사 리포트",
    "report_fresh": "오랜만에 나온 리포트",
    "search_top": "네이버 검색 상위 100",
}

FEATURES = {
    "pension": "연기금 5일 순매수",
    "trust": "투신 5일 순매수",
    "streak3": "3일 이상 연속 순매수",
    "fratio_up": "외국인 지분율 20일 증가",
    "overlap": "두 관점 겹침",
    "report": "최근 2주 증권사 리포트",
    "report_fresh": "오랜만에 나온 리포트",
    "search_top": "네이버 검색 상위 100",
}


def features_of(x):
    q = x.get("quality") or {}
    fl = x.get("flow") or {}
    return {
        "pension": (q.get("pension_5d") or 0) > 0,
        "trust": (q.get("trust_5d") or 0) > 0,
        "streak3": (fl.get("streak") or 0) >= 3,
        "fratio_up": (q.get("fratio_chg") or 0) > 0,
        "overlap": bool(x.get("overlap")),
        "v_flow": (x.get("flow5_eok") or 0) > 0,
        "v_news": bool(x.get("news")),
        "v_wake2": None if x.get("wake") is None else x["wake"]["met"] >= 2,
        "report": None if x.get("reports") is None else x["reports"]["n"] > 0,
        "report_fresh": None if not (x.get("reports") or {}).get("known") else x["reports"]["fresh"],
        "search_top": None if "search_rank" not in x else x["search_rank"] is not None,   # 검색 순위를 못 받은 날은 뺌
    }


def feature_stats(entries, horizon=5, groups=("swing", "scalp"), features=FEATURES):
    """조건 있음/없음 비교 (기본: 스윙·단타 후보). 날마다 시장 움직임이 달라 시장 지수 대비 초과 수익으로 본다.
    값이 None인 기록(그 조건을 아직 저장하지 않던 날)은 양쪽 모두에서 뺀다."""
    out = []
    pool = [e for e in entries if e["group"] in groups and e.get(f"x{horizon}") is not None
            and horizon not in e.get("dup", [])]
    for key, label in features.items():
        yes = [e[f"x{horizon}"] for e in pool if e.get("features", {}).get(key) is True]
        no = [e[f"x{horizon}"] for e in pool if e.get("features", {}).get(key) is False]
        out.append({"key": key, "label": label, "horizon": horizon, "who": "value" if groups == ("value",) else "flow",
                    "yes": summarize_returns(yes), "no": summarize_returns(no)})
    return out
