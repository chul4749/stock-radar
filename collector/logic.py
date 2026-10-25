"""필터 규칙과 지표 계산. 네트워크 없이 테스트할 수 있게 순수 함수만 둔다.

화면에는 '조건에 맞는 종목'만 보여 준다. 매수·매도 판단 문구는 만들지 않는다.
"""
import math
import re
from datetime import datetime, timedelta

import pandas as pd

from .util import EOK

# ---------------------------------------------------------------- 공통


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
        add("korea_vol", "국내 변동성", 100 - kv["pct_1y"], f"{kv['value']}% · 1년 중 {kv['pct_1y']}% 위치")
    vix = t.get("vix")
    if vix and vix.get("pct_1y") is not None:
        add("vix", "미국 VIX", 100 - vix["pct_1y"], f"{vix['value']} · 1년 중 {vix['pct_1y']}% 위치")
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
        add("credit", "자금시장 (하이일드)", 100 - hy["pct_1y"], f"{hy['value']}%p · 1년 중 {hy['pct_1y']}% 위치")
    cb = t.get("credit_balance") or {}
    fo = cb.get("forced")
    if fo and fo.get("pct_1y") is not None:
        add("forced", "반대매매 (강제 매도)", 100 - fo["pct_1y"], f"{fo['value']:,.0f}억 · 1년 중 {fo['pct_1y']}% 위치")
    # 빚투(신용융자)는 많으면 탐욕이지만 급감도 위험 신호라 방향이 모호해 온도계에서는 뺀다 (칸으로만 보여 줌)
    if len(parts) < 3:
        return None
    score = round(sum(p["score"] * p["weight"] for p in parts) / sum(p["weight"] for p in parts))
    label, tone = thermo_label(score)
    return {"score": score, "label": label, "tone": tone, "parts": parts}


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
                    "text": f"미국에서 신용 낮은 회사가 돈 빌릴 때 더 내는 이자(하이일드 스프레드) {hy['value']}%p — 1년 중 {hy['pct_1y']}% 위치. "
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
            checks = [
                {"label": f"거래대금 20일 평균의 {rules['scalp_value_mult']:.0f}배 이상", "ok": mult >= rules["scalp_value_mult"],
                 "value": f"{mult:.1f}배"},
                {"label": f"오늘 {rules['scalp_min_change']:.0f}% 이상 상승", "ok": info["change"] >= rules["scalp_min_change"],
                 "value": f"{info['change']:+.1f}%"},
                {"label": f"오늘 기관+외국인 순매수가 거래대금의 {rules['scalp_min_net_ratio']:.0f}% 이상",
                 "ok": (f_today + i_today) / info["value"] * 100 >= rules["scalp_min_net_ratio"] if info["value"] else False,
                 "value": f"{(f_today + i_today) / EOK:+,.0f}억 ({(f_today + i_today) / info['value'] * 100 if info['value'] else 0:.1f}%)"},
            ]
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
            checks = [
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
            if all(c["ok"] for c in checks):
                score = ratio * (1 + streak / 5)
                swing.append({**base, "checks": checks, "score": round(score, 3),
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


def value_score(drop, pbr_ratio, per, eps_ratio, fwd_ratio=None):
    """→ (0~100 점수, 항목별 0~100). fwd_ratio = 올해 추정 EPS / 지금 EPS (있으면 이익 항목에 반반 섞음)."""
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
    return round(score, 1), {k: round(v * 100) for k, v in parts.items()}


# 단타 점수: 거래량이 폭발하고 크게 튄 '주목받는 종목'은 이후 성적이 나쁜 경향(개인의 주목 매수, Barber·Odean 2008)이
# 실제 기록에서도 보여(거래가 가장 몰린 쪽이 더 나빴음), 큰손 확인을 중심에 두고 과열은 깎는다
SCALP_WEIGHTS = {"flow": 0.5, "volume": 0.25, "move": 0.25}


def scalp_score(mult, change, net_ratio):
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
    return round(score, 1), {k: round(v * 100) for k, v in parts.items()}


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
        checks = [
            {"label": f"60일 최고가 대비 {rules['min_drop']:.0f}% 이상 하락", "ok": drop >= rules["min_drop"],
             "value": f"-{drop:.1f}%"},
            {"label": f"PBR이 1년 중앙값의 {rules['max_pbr_ratio']:.0%} 이하", "ok": pbr_ratio <= rules["max_pbr_ratio"],
             "value": f"{pbr:.2f} / {med:.2f}"},
            {"label": f"흑자이고 PER {rules['max_per']:.0f} 이하", "ok": per <= rules["max_per"], "value": f"{per:.1f}"},
            {"label": f"PBR {rules['max_pbr']:.0f} 이하", "ok": pbr <= rules["max_pbr"], "value": f"{pbr:.2f}"},
            {"label": f"EPS가 1년 전의 {rules['min_eps_ratio']:.0%} 이상", "ok": eps_ratio >= rules["min_eps_ratio"],
             "value": f"{eps:,.0f} / {eps0:,.0f}"},
        ]
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
        {"name": "투매 진정", "need": 2, "guide": "1/3만 나눠서", "checks": [
            chk("바닥을 새로 뚫는 종목(신저가)이 최근 5일 평균보다 적음", lows_avg is not None and lows[-1] < lows_avg,
                f"{lows[-1]} / 평균 {lows_avg:.0f}" if lows_avg is not None else "–"),
            chk("빚 때문에 강제로 팔리는 물량(반대매매)이 평소(20일 평균) 이하", fo.get("avg20") and fo["value"] <= fo["avg20"],
                f"{fo['value']:,.0f}억 / 평균 {fo['avg20']:,.0f}억" if fo.get("avg20") else "–"),
            chk("출렁임(변동성)이 최근 10일 최고보다 10% 이상 줄어듦", kv_recent and kv["value"] <= max(kv_recent) * 0.9,
                f"{kv['value']}% / 최고 {max(kv_recent)}%" if kv_recent else "–"),
        ]},
        {"name": "수급 전환", "need": 2, "guide": "2/3까지", "checks": [
            chk("외국인이 선물을 사는 쪽으로 돌아섬 (3일 연속 또는 5일 합계 순매수)",
                fu and ((fu.get("streak") or 0) >= 3 or (fu.get("d5") or 0) > 0),
                (f"{fu['streak']:+d}일째 · 5일 {fu.get('d5_eok') if fu.get('d5_eok') is not None else fu.get('d5'):+,}" +
                 ("억" if fu.get("d5_eok") is not None else "계약")) if fu else "–"),
            chk("외국인+기관이 최근 5일 동안 주식을 판 것보다 더 삼", big5 is not None and big5 > 0,
                f"{big5:+,.0f}억" if big5 is not None else "–"),
            chk("시장 온도가 1주 전보다 올라감", week and th.get("score") is not None and th["score"] > week["score"],
                f"{th.get('score')}점 / 1주 전 {week['score']}점" if week else "–"),
        ]},
        {"name": "추세 확인", "need": 2, "guide": "상승장 루틴으로 (스윙 위주)", "checks": [
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
    guides = ["아직 기다리기 — 겁먹고 파는 물량(투매)이 안 멈춤", "쓸 돈의 1/3만 나눠서 — 투매가 잦아드는 중",
              "쓸 돈의 2/3까지 — 큰손 돈이 다시 들어오기 시작", "평소처럼 — 오르는 장 방식(스윙 위주), 저평가는 줄이기"]
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
NOTICE_TITLES = ("실적공시예고", "결산실적공시예고", "기업설명회")


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
    if store.get("season") != season["key"]:
        store = {"season": season["key"], "checked": [], "reported": {}, "notice": {}}
    for x in items:
        code = name_to_code.get(x.get("corp"))
        t = x.get("title", "")
        if not code or "정정" in t[:8]:
            continue
        day = x["at"][:10].replace("-", "")
        if day < season["start"]:
            continue
        kind = "reported" if any(k in t for k in REPORT_TITLES) and "예고" not in t else \
            "notice" if any(k in t for k in NOTICE_TITLES) else None
        if kind and (code not in store[kind] or store[kind][code]["date"] > day):
            store[kind][code] = {"date": day, "title": t, "url": x.get("url")}
    return store


def earnings_status(code, base, season, store):
    """후보 카드용: 이번 시즌 실적을 이미 냈는지, 아직이면 마감까지 며칠인지."""
    if not season or store.get("season") != season["key"]:
        return None
    rep = store["reported"].get(code)
    if rep and rep["date"] <= base:
        return {"state": "done", "label": season["label"], "date": rep["date"], "title": rep["title"], "url": rep.get("url")}
    left = (datetime.strptime(season["due"], "%Y%m%d") - datetime.strptime(base, "%Y%m%d")).days
    note = store["notice"].get(code)
    return {"state": "pending", "label": season["label"], "due": season["due"], "days_left": left,
            "notice": note if note and note["date"] <= base else None}


# ---------------------------------------------------------------- 언제 정리할지 (나가는 기준)

EXIT_RULES = {
    "swing": ("종가가 20일 평균 가격 아래로 마감하면 정리", "오르는 흐름이 깨졌다는 뜻", "보통 1~4주"),
    "scalp": ("걸린 날 저가를 깨고 마감하면 정리", "급등을 만든 힘이 빠졌다는 뜻", "보통 1~3일, 길게 끌지 않기"),
    "value": ("최근 10일 최저가를 깨고 마감하면 정리", "아직 바닥이 아니었다는 뜻", "몇 주~몇 달"),
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


def stop_scorecard(history, closes, lows, opens=None, bench=None, horizons=(5, 20), cost=None):
    """정리 기준을 지켰다면: 그냥 N일 들고 있기 vs 기준 아래로 마감한 날 종가에 팔기 (둘 다 시장 지수 대비).

    정리 가격은 그날까지의 종가·저가로 다시 계산한다 (예전 기록에도 같은 규칙 적용).
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
            for x in items:
                code = x["code"]
                if code not in closes.columns:
                    continue
                ex = exit_level(g, closes[code].iloc[:i0 + 1], lows[code].iloc[:i0 + 1] if lows is not None and code in lows.columns else None)
                if not ex:
                    continue
                buy = opens[code].iloc[i0 + 1] if opens is not None and code in opens.columns and i0 + 1 < len(days) else None
                for n in horizons:
                    if i0 + n >= len(days) or buy is None or math.isnan(buy) or not buy:
                        continue
                    if (g, code, n) in last and i0 - last[(g, code, n)] < n:
                        continue
                    last[(g, code, n)] = i0
                    path = closes[code].iloc[i0 + 1:i0 + n + 1]
                    if path.isna().any():
                        continue
                    k_out = next((k for k, v in enumerate(path, 1) if v < ex["stop"]), None)
                    mk = bench.get(x.get("market")) if bench else None

                    def excess(k):
                        r = (path.iloc[k - 1] / buy - 1) * 100 - cost
                        if mk is None:
                            return r, None
                        k0, k1 = mk[0].get(days[i0 + 1]), mk[1].get(days[i0 + k])
                        return r, (r - (k1 / k0 - 1) * 100) if k0 and k1 else None

                    a = acc.setdefault(g, {}).setdefault(n, {"hold": [], "hold_x": [], "stop": [], "stop_x": [], "hit": 0})
                    for kind, k in (("hold", n), ("stop", k_out or n)):
                        r, e = excess(k)
                        a[kind].append(r)
                        if e is not None:
                            a[kind + "_x"].append(e)
                    a["hit"] += 1 if k_out else 0
    return {g: {str(n): {"hold": summarize_returns(a["hold"], a["hold_x"]), "stop": summarize_returns(a["stop"], a["stop_x"]),
                         "hit": round(100 * a["hit"] / len(a["hold"])) if a["hold"] else None}
                for n, a in by_n.items()} for g, by_n in acc.items()}


HORIZONS = (1, 5, 20)


COST = 0.2  # 왕복 거래 비용(%): 매도 세금 + 수수료 대략


def scorecard(history, closes, index_closes, regimes=None, opens=None, bench=None, cost=COST):
    """과거 후보가 N영업일 뒤 얼마나 움직였나.

    history: [(기준일, {"scalp": [...], "swing": [...], "value": [...]})]
    closes / opens: DataFrame(행=영업일, 열=티커) 종가·시가
    index_closes: Series(index=영업일) 코스피 종가 (bench 가 없을 때 비교 기준)
    bench: {"KOSPI": (시가 Series, 종가 Series), "KOSDAQ": (...)} — 종목이 속한 시장 지수와 비교

    실제로 할 수 있는 매매에 맞춘다:
    - 결과는 장 마감 뒤에 나오므로 다음 영업일 시가에 사서 N영업일째 종가에 판다 (opens 가 있을 때)
    - 거래 비용 cost(%)를 뺀다
    - 같은 탭에서 같은 종목이 N일 안에 다시 걸리면 그 N일 성적에는 한 번만 센다 (구간 겹침 방지)
    반환: 탭별 성적, 최근 기록, 조건별 성적, 장세별 성적, 점수 위·아래 절반 성적
    """
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
                for n in HORIZONS:
                    if i0 + n >= len(days) or code not in closes.columns:
                        continue
                    c1 = closes[code].iloc[i0 + n]
                    buy = opens[code].iloc[i0 + 1] if opens is not None and code in opens.columns else x["close"]
                    if c1 is None or math.isnan(c1) or buy is None or math.isnan(buy) or not buy:
                        continue
                    ret = (c1 / buy - 1) * 100 - cost
                    mk = bench.get(x.get("market")) if bench else None
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
    feats = feature_stats(recent) + feature_stats(recent, groups=("value",), features=VALUE_FEATURES)
    return out, recent[:60], feats, regime_out, score_check


def summarize_returns(rets, ex=None):
    if not rets:
        return None
    return {"n": len(rets), "avg": r1(sum(rets) / len(rets)),
            "win": round(100 * sum(1 for r in rets if r > 0) / len(rets)),
            "excess": r1(sum(ex) / len(ex)) if ex else None}


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
        out.append({"key": key, "label": label, "horizon": horizon,
                    "yes": summarize_returns(yes), "no": summarize_returns(no)})
    return out
