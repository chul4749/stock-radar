"""필터 규칙과 지표 계산. 네트워크 없이 테스트할 수 있게 순수 함수만 둔다.

화면에는 '조건에 맞는 종목'만 보여 준다. 매수·매도 판단 문구는 만들지 않는다.
"""
import math
import re
from datetime import datetime

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
                    "text": f"변동성·심리 지표를 1년 기준으로 놓으면 공포 쪽 {round(s)}점 (100점 = 1년 중 가장 무서운 수준)."})

    # 2. 매도 압력: 신저가 종목 수가 최근 5일 평균보다 늘었나
    b = t.get("breadth")
    if b and len(b["new_lows"]) >= 6:
        now, avg = b["new_lows"][-1], sum(b["new_lows"][-6:-1]) / 5
        if now > avg * 1.3 and now >= 30:
            level, tone, text = "커지는 중", "fear", f"60일 신저가 종목 {now}개, 최근 5일 평균 {avg:.0f}개보다 많음."
        elif now < avg * 0.7:
            level, tone, text = "줄어드는 중", "calm", f"60일 신저가 종목 {now}개, 최근 5일 평균 {avg:.0f}개보다 적음."
        else:
            level, tone, text = "비슷함", "neutral", f"60일 신저가 종목 {now}개, 최근 5일 평균 {avg:.0f}개와 비슷."
        # 반대매매가 20일 평균의 2배 넘게 몰리면 신저가와 상관없이 매도 압력이 커지는 중으로 본다
        fo = (t.get("credit_balance") or {}).get("forced")
        if fo and fo.get("avg20"):
            mult = fo["value"] / fo["avg20"]
            if mult >= 2 and fo["value"] >= 300:
                level, tone = "커지는 중", "fear"
                text += f" 반대매매 {fo['value']:,.0f}억, 20일 평균의 {mult:.1f}배."
            else:
                text += f" 반대매매 {fo['value']:,.0f}억 (20일 평균 {fo['avg20']:,.0f}억)."
        out.append({"key": "selling", "title": "매도 압력", "level": level, "tone": tone, "text": text})

    # 3. 자금시장: 하이일드 스프레드
    hy = t.get("hy_spread")
    if hy:
        wide = (hy["pct_1y"] or 0) >= 80 or (hy["change"] or 0) >= 0.5
        out.append({"key": "credit", "title": "자금시장", "level": "경색 신호" if wide else "안정",
                    "tone": "fear" if wide else "calm",
                    "text": f"미국 하이일드 스프레드 {hy['value']}%p (1년 중 {hy['pct_1y']}% 위치). "
                            + ("돈줄이 조여지는 쪽." if wide else "주가 공포가 자금 위기로 번진 신호는 아직 없음.")})
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

    def money(series, code):
        v = series.get(code) if series is not None else None
        return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    for code, info in universe.items():
        if code not in closes.columns:
            continue
        if info["cap"] < rules["min_cap"] or info["value"] < rules["min_value"]:
            continue
        has_today, has_3d = code in news_today, code in news_3d
        if not has_3d:
            continue

        close = closes[code].iloc[-1]
        f_today = money(flows_daily[-1][1]["foreign"], code) if flows_daily else 0.0
        i_today = money(flows_daily[-1][1]["inst"], code) if flows_daily else 0.0
        f5, i5 = money(flows_5d["foreign"], code), money(flows_5d["inst"], code)
        p5 = money(pension_5d, code)
        days_pos = sum(1 for _, fl in flows_daily
                       if money(fl["foreign"], code) + money(fl["inst"], code) > 0)
        streak = 0
        for _, fl in reversed(flows_daily):
            if money(fl["foreign"], code) + money(fl["inst"], code) > 0:
                streak += 1
            else:
                break
        base = {
            "code": code, "name": info["name"], "market": info["market"],
            "close": close, "change": r1(info["change"]),
            "value_eok": r1(info["value"] / EOK), "cap_eok": round(info["cap"] / EOK),
            "flow": {
                "foreign_today": r1(f_today / EOK), "inst_today": r1(i_today / EOK),
                "foreign_5d": r1(f5 / EOK), "inst_5d": r1(i5 / EOK), "pension_5d": r1(p5 / EOK),
                "days_pos": days_pos, "streak": streak,
                "daily": [{"date": d, "sum": r1((money(fl["foreign"], code) + money(fl["inst"], code)) / EOK)}
                          for d, fl in flows_daily],
            },
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
                {"label": f"5일 순매수가 시총의 {rules['swing_min_ratio']}% 이상", "ok": ratio >= rules["swing_min_ratio"],
                 "value": f"{ratio:.2f}%"},
                {"label": "주가 > 20일선 > 60일선", "ok": close > m20 > m60,
                 "value": f"{close:,.0f} / {m20:,.0f} / {m60:,.0f}"},
                {"label": "20일선 상승 중", "ok": m20 > m20_prev, "value": f"{(m20 / m20_prev - 1) * 100:+.1f}%"},
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


def value_screen(universe, closes, fund_now, fund_hist, flows_5d, news_3d, rules=VALUE_RULES):
    """많이 떨어졌는데 장부가치 대비 싸졌고, 이익은 크게 줄지 않은 종목.

    fund_now: DataFrame(index=티커, BPS/PER/PBR/EPS)
    fund_hist: [DataFrame] 1년 동안의 과거 스냅샷 (오래된 순, 첫 번째가 약 1년 전)
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
    "mom": ("오르는 장에서는 최근 잘 오르고 돈이 들어오는 종목이 한동안 더 오르는 경향(모멘텀)이 가장 잘 나타납니다.",
            "Jegadeesh·Titman (1993) 등 모멘텀 연구"),
    "mom_crash": ("급락 뒤 변동성이 큰 구간에서는, 반등이 올 때 그동안 많이 빠진 종목이 덜 빠진 종목보다 훨씬 크게 오릅니다. "
                  "그래서 오르던·돈 들어오던 종목을 따라가는 전략이 이 구간에서 가장 크게 손실을 냈고, 반대로 낙폭 큰 종목은 크게 튀었습니다.",
                  "Daniel·Moskowitz (2016) 'Momentum Crashes', 미국·해외 주식"),
    "trend": ("지수가 이동평균 아래에 있을 때는 하락이 이어지는 경향이 있어, 추세를 따르면 큰 하락을 상당 부분 피했습니다. "
              "대신 방향 없는 장에서는 신호가 자주 틀립니다.",
              "Faber 등 이동평균 추세 추종 연구"),
    "value_trap": ("오르는 장에서 혼자 크게 빠진 종목은 실적 악화 같은 이유가 있는 경우가 많아, 싸 보이는 함정(가치 함정)에 빠지기 쉽습니다.",
                   "가치 함정에 관한 일반적 관찰"),
    "reversal": ("방향 없는 장에서는 단기간 많이 빠진 종목이 제자리로 돌아오는 단기 반전이 상대적으로 잘 나타납니다.",
                 "Jegadeesh (1990), Lehmann (1990) 단기 반전 연구"),
    "falling_knife": ("하락 추세가 이어지는 동안에는 싸 보이는 종목도 시장과 같이 더 빠지기 쉬워, 한 번에 사기보다 나눠서 지켜보는 편이 낫습니다.",
                      "추세 추종 연구의 반대 측면"),
    "short_term": ("거래가 몰린 종목의 하루 이틀 움직임은 장세보다 개별 재료의 영향이 커서, 어느 장에서든 짧게 끊는 게 핵심입니다. "
                   "다만 장이 흔들릴수록 손실 폭도 커집니다.",
                   "경험칙 (뚜렷한 연구 근거는 약함)"),
    "inv_trend": ("하락 추세가 확인된 구간에서는 지수 하락이 이어지는 경향이 있어, 짧게 들고 가는 1배 인버스가 상대적으로 덜 불리합니다.",
                  "이동평균 추세 추종 연구"),
    "inv_decay": ("인버스·곱버스는 매일 수익률을 다시 맞추기 때문에, 방향 없이 출렁이면 지수가 제자리여도 손실이 쌓입니다(음의 복리). "
                  "개인 투자자는 바닥을 예측하고 거꾸로 들어갔다가 손실을 본 경우가 많았습니다.",
                  "자본시장연구원 레버리지·인버스 ETF 성과 분석, 운용사 안내"),
    "inv_panic": ("공포가 극에 달한 뒤에는 하루 3~5%씩 급반등하는 날이 잦습니다. 이때 곱버스는 반등 하루에 크게 잃고, "
                  "출렁임이 커서 음의 복리도 커집니다. 급락 뒤 추격 진입이 가장 손실이 큰 패턴입니다.",
                  "Daniel·Moskowitz (2016) 패닉 구간 급반등, 레버리지·인버스 ETF 분석"),
}

# 장세별 시나리오 적합도: (등급, 한 줄 이유, 근거 키)
FIT = {
    "up": {
        "swing": ("good", "추세가 살아 있어 돈이 들어오는 종목이 더 가는 경향", "mom"),
        "scalp": ("ok", "거래 몰린 종목이 하루 이틀 더 가는 경우가 많음", "short_term"),
        "value": ("bad", "오르는 장에서 혼자 떨어진 종목은 이유가 있는 경우가 많음", "value_trap"),
        "inverse": ("bad", "추세를 거스르는 베팅", "inv_decay"),
    },
    "range": {
        "swing": ("ok", "가짜 돌파가 잦아 손절 기준을 짧게", "trend"),
        "scalp": ("ok", "짧게 치고 빠지는 쪽이 상대적으로 유리", "short_term"),
        "value": ("ok", "많이 빠진 종목이 제자리로 돌아오는 경향", "reversal"),
        "inverse": ("bad", "방향이 없어 매일 재조정 손실만 쌓이기 쉬움", "inv_decay"),
    },
    "down": {
        "swing": ("bad", "꺾인 장에서 수급만 보고 따라가면 같이 밀리기 쉬움", "trend"),
        "scalp": ("ok", "하루 이틀만, 손절 짧게", "short_term"),
        "value": ("ok", "싸 보여도 더 빠질 수 있어 나눠서 관찰", "falling_knife"),
        "inverse": ("good", "추세가 확인된 구간이라 1배 인버스를 짧게 고려할 만함", "inv_trend"),
    },
    "panic": {
        "swing": ("bad", "급반등 때 가장 크게 깨지는 구간 (추세 추종 붕괴)", "mom_crash"),
        "scalp": ("ok", "변동성이 커서 기회도 손실도 큼, 아주 짧게", "short_term"),
        "value": ("good", "공포가 극에 달하면 많이 빠진 종목이 크게 튀는 경향, 나눠서", "mom_crash"),
        "inverse": ("bad", "급반등이 잦고 변동성이 커 곱버스 손실이 커지는 구간, 추격 금지", "inv_panic"),
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
        {"label": "코스피 20일 등락 −15% 이하", "value": f"{chg20:+.1f}%", "ok": chg20 <= -15},
        {"label": "국내 변동성 1년 중 90% 이상", "value": "–" if kv is None else f"{kv}% 위치",
         "ok": kv is not None and kv >= 90},
        {"label": "반대매매 20일 평균의 2배 이상 (300억 이상)",
         "value": "–" if fo_mult is None else f"{fo['value']:,.0f}억, 평균의 {fo_mult:.1f}배",
         "ok": fo_mult is not None and fo["value"] >= 300 and fo_mult >= 2},
    ]
    met_panic = [f"{c['label'].split(' (')[0]}: {c['value']}" for c in panic_checks if c["ok"]]
    # 급락 하나만으로는 패닉이 아니다: 하락과 높은 변동성·공포가 겹친 상태 (Daniel·Moskowitz의 '패닉 구간')
    panic = met_panic if len(met_panic) >= 2 else []
    # 2단계: 추세 조건
    slope = (ma20 / ma20_prev - 1) * 100
    trend_checks = [
        {"label": "코스피가 20일선 위", "value": f"{close:,.0f} / {ma20:,.0f} ({(close / ma20 - 1) * 100:+.1f}%)", "ok": close > ma20},
        {"label": "20일선이 60일선 위", "value": f"{ma20:,.0f} / {ma60:,.0f}", "ok": ma20 > ma60},
        {"label": "20일선이 오르는 중 (5일 전보다 높음)", "value": f"{slope:+.2f}%", "ok": rising},
    ]
    tkey = _trend_key(close, ma20, ma60, rising)
    key = "panic" if panic else tkey

    trend_txt = {"up": "코스피가 20일선·60일선 위이고 20일선이 오르는 중",
                 "down": "코스피가 20일선·60일선 아래이고 20일선이 내려가는 중",
                 "range": "추세 조건이 엇갈림 (위·아래가 섞여 있음)"}[tkey]
    if key == "panic":
        reason = f"패닉 조건 {len(panic)}개 해당 → 추세와 상관없이 패닉으로 판정. 추세만 보면 {REGIMES[tkey]} ({trend_txt})."
    else:
        pre = f"패닉 조건 {len(met_panic)}개뿐 (2개 이상이어야 패닉)" if met_panic else "패닉 조건 없음"
        reason = f"{pre} → 추세로 판정: {trend_txt} → {REGIMES[key]}."
    if key == "panic":
        nxt = f"패닉 조건이 모두 풀리면 추세로 다시 판정합니다. 지금 추세 그대로면 {REGIMES[tkey]}입니다."
    elif key == "down":
        nxt = f"코스피가 20일선({ma20:,.0f}) 위로 올라서면 횡보·전환으로 바뀝니다. 지금보다 {(ma20 / close - 1) * 100:.1f}% 위입니다."
    elif key == "up":
        nxt = f"코스피가 20일선({ma20:,.0f}) 아래로 내려가면 횡보·전환으로 바뀝니다. 지금보다 {(1 - ma20 / close) * 100:.1f}% 아래입니다."
    else:
        nxt = (f"코스피·20일선이 60일선({ma60:,.0f}) 위에서 20일선이 오르면 상승장, "
               f"모두 아래에서 20일선이 내려가면 하락장이 됩니다.")
    if key != "panic" and len(met_panic) == 1:
        nxt += " 패닉 조건이 하나 더 맞으면 패닉으로 바뀝니다."

    fl = (t.get("flows") or {}).get("KOSPI") or {}
    b = t.get("breadth") or {}
    hy = (t.get("hy_spread") or {}).get("pct_1y")
    inv_checks = [
        {"label": "코스피가 20일선·60일선 아래", "value": f"{close:,.0f} / {ma20:,.0f} / {ma60:,.0f}", "ok": close < ma20 < ma60},
        {"label": "20일선이 내려가는 중", "value": f"{slope:+.2f}%", "ok": not rising},
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
        verdict = ("consider", "인버스 고려 구간", "하락 추세가 확인됨. 1배 인버스를 짧게, 코스피가 20일선을 되찾으면 정리.",
                   f"코스피가 20일선({ma20:,.0f}) 위로 올라서면 정리 신호입니다.")
    elif met >= need:
        verdict = ("caution", "하락 추세지만 추격 주의",
                   "이미 급락했거나 공포가 커서 급반등이 잦은 구간. 지금 따라 들어가기보다 반등이 20일선 아래서 꺾일 때가 상대적으로 나은 자리.",
                   f"패닉 조건(급락·극단적 공포 등)이 모두 풀린 뒤에도 하락 신호가 {need}개 이상 남아 있으면 '고려 구간'이 됩니다.")
    elif met == need - 1:
        verdict = ("watch", "하락 신호 일부", "아직 추세가 확인되지 않음. 지켜보기.",
                   f"하락 신호가 {need}개 이상이 되면 다시 판단합니다.")
    else:
        verdict = ("no", "인버스 불리", "하락 추세 신호가 부족함.", f"하락 신호가 {need}개 이상이 되면 다시 판단합니다.")

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
            {"label": "코스피가 20일선 위로", "ok": close > ma20,
             "value": "회복" if close > ma20 else f"{gap20:.1f}% 남음"},
            {"label": "20일선이 다시 오름 (5일 전보다 높음)", "ok": slope > 0, "value": f"{slope:+.2f}%"},
            {"label": "20일선이 60일선 위로", "ok": ma20 > ma60, "value": f"{(ma60 / ma20 - 1) * 100:.1f}% 남음" if ma20 <= ma60 else "회복"},
        ]
    else:
        target = "횡보·전환 (상승 추세 꺾임)"
        items = [
            {"label": "코스피가 20일선 아래로", "ok": close < ma20,
             "value": "이탈" if close < ma20 else f"{-gap20:.1f}% 여유"},
            {"label": "20일선이 내려가기 시작", "ok": slope < 0, "value": f"{slope:+.2f}%"},
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
            chk("신저가 종목이 최근 5일 평균보다 적음", lows_avg is not None and lows[-1] < lows_avg,
                f"{lows[-1]} / 평균 {lows_avg:.0f}" if lows_avg is not None else "–"),
            chk("반대매매가 20일 평균 이하", fo.get("avg20") and fo["value"] <= fo["avg20"],
                f"{fo['value']:,.0f}억 / 평균 {fo['avg20']:,.0f}억" if fo.get("avg20") else "–"),
            chk("국내 변동성이 최근 10일 최고보다 10% 이상 내려옴", kv_recent and kv["value"] <= max(kv_recent) * 0.9,
                f"{kv['value']}% / 최고 {max(kv_recent)}%" if kv_recent else "–"),
        ]},
        {"name": "수급 전환", "need": 2, "guide": "2/3까지", "checks": [
            chk("외국인 선물 3일 이상 연속 순매수 또는 5일 합 순매수",
                fu and ((fu.get("streak") or 0) >= 3 or (fu.get("d5") or 0) > 0),
                (f"{fu['streak']:+d}일째 · 5일 {fu.get('d5_eok') if fu.get('d5_eok') is not None else fu.get('d5'):+,}" +
                 ("억" if fu.get("d5_eok") is not None else "계약")) if fu else "–"),
            chk("코스피 외국인+기관 현물 5일 순매수", big5 is not None and big5 > 0,
                f"{big5:+,.0f}억" if big5 is not None else "–"),
            chk("시장 온도가 1주 전보다 오름", week and th.get("score") is not None and th["score"] > week["score"],
                f"{th.get('score')}점 / 1주 전 {week['score']}점" if week else "–"),
        ]},
        {"name": "추세 확인", "need": 2, "guide": "상승장 루틴으로 (스윙 위주)", "checks": [
            chk("코스피가 20일선 위", tr["close"] > tr["ma20"], f"{tr['close']:,.0f} / {tr['ma20']:,.0f}"),
            chk("20일선이 다시 오름", tr["ma20_rising"], f"{tr['ma20_slope']:+.2f}%"),
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
    guides = ["기다림 — 투매가 아직 진정되지 않음", "1/3만 나눠서 (투매가 진정되는 중)",
              "2/3까지 (돈이 다시 들어오기 시작)", "상승장 루틴으로 — 스윙 위주, 저평가는 줄이기"]
    nxt = stages[level] if level < len(stages) else None
    return {"level": level, "guide": guides[level], "stages": stages,
            "next": (f"다음 단계 '{nxt['name']}'까지 {max(0, nxt['need'] - nxt['met'])}개 더" if nxt else "세 단계 모두 통과"),
            "applies": rg["trend_key"] != "up" or rg["key"] == "panic"}


# ---------------------------------------------------------------- 성적표

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
    return out, recent[:60], feature_stats(recent), regime_out, score_check


def summarize_returns(rets, ex=None):
    if not rets:
        return None
    return {"n": len(rets), "avg": r1(sum(rets) / len(rets)),
            "win": round(100 * sum(1 for r in rets if r > 0) / len(rets)),
            "excess": r1(sum(ex) / len(ex)) if ex else None}


# 조건별 성적: 후보 안에서 이 조건이 있을 때와 없을 때 5일 뒤 수익률 비교
FEATURES = {
    "pension": "연기금 5일 순매수",
    "trust": "투신 5일 순매수",
    "streak3": "3일 이상 연속 순매수",
    "fratio_up": "외국인 지분율 20일 증가",
    "overlap": "두 관점 겹침",
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
    }


def feature_stats(entries, horizon=5):
    """스윙·단타 후보(수급 관점)만 대상으로 조건 있음/없음 비교. 날마다 시장 움직임이 달라 시장 지수 대비 초과 수익으로 본다."""
    out = []
    pool = [e for e in entries if e["group"] in ("swing", "scalp") and e.get(f"x{horizon}") is not None
            and horizon not in e.get("dup", [])]
    for key, label in FEATURES.items():
        yes = [e[f"x{horizon}"] for e in pool if e.get("features", {}).get(key)]
        no = [e[f"x{horizon}"] for e in pool if not e.get("features", {}).get(key)]
        out.append({"key": key, "label": label, "horizon": horizon,
                    "yes": summarize_returns(yes), "no": summarize_returns(no)})
    return out
