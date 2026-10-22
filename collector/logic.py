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


def korea_vol(kospi200):
    """VKOSPI 대체: 코스피200 20일 실제 변동성."""
    vol = realized_vol(kospi200).dropna()
    rows = [(d.strftime("%Y-%m-%d"), v) for d, v in vol.items()]
    return series_stat(rows)


def breadth(closes, window=60, recent=20):
    """closes: DataFrame(행=영업일 오래된 순, 열=티커).

    상승·하락 종목 수, window 일 신고가·신저가 종목 수(최근 recent 일 추이).
    """
    up = int((closes.iloc[-1] > closes.iloc[-2]).sum())
    down = int((closes.iloc[-1] < closes.iloc[-2]).sum())
    lows, highs = [], []
    for i in range(max(window, len(closes) - recent), len(closes)):
        win = closes.iloc[i - window + 1:i + 1]
        today = win.iloc[-1]
        valid = win.notna().sum() >= window * 0.9
        lows.append(int(((today <= win.min()) & valid).sum()))
        highs.append(int(((today >= win.max()) & valid).sum()))
    return {"up": up, "down": down, "new_lows": lows, "new_highs": highs,
            "dates": [d for d in closes.index[-len(lows):]] if lows else []}


def flows_summary(df):
    """시장 전체 투자자별 순매수(원) DataFrame → 억 원 단위 최근/5일 합."""
    out = {}
    for col, key in (("외국인합계", "foreign"), ("기관합계", "inst"), ("개인", "retail")):
        if col in df:
            s = df[col].astype(float)
            out[key] = {"today": r1(s.iloc[-1] / EOK), "d5": r1(s.iloc[-5:].sum() / EOK),
                        "d20": r1(s.iloc[-20:].sum() / EOK)}
    return out


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
                    for k in range(i, i + len(n)):
                        used[k] = True
                    if c not in found:
                        found.append(c)
                    break
        return found


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
                {"label": "오늘 기관+외국인 순매수", "ok": f_today + i_today > 0,
                 "value": f"{(f_today + i_today) / EOK:+,.0f}억"},
            ]
            if all(c["ok"] for c in checks):
                score = mult * (1 + max(0, f_today + i_today) / info["value"])
                scalp.append({**base, "checks": checks, "score": round(score, 2),
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
