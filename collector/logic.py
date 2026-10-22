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

    def add(key, name, score, raw):
        if score is None:
            return
        parts.append({"key": key, "name": name, "score": round(max(0, min(100, score))), "raw": raw})

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
        add("breadth", "신고가 vs 신저가", 50 if hi + lo == 0 else 100 * hi / (hi + lo), f"신고가 {hi} · 신저가 {lo}")
    k = (t.get("index") or {}).get("kospi")
    if k and k.get("change_20d") is not None:
        add("momentum", "코스피 20일 추세", 50 + k["change_20d"] * 3, f"20일 {k['change_20d']:+.1f}%")
    fl = (t.get("flows") or {}).get("KOSPI")
    if fl and fl.get("days20"):
        add("flows", "큰손 수급 (코스피)", 100 * fl["pos_days20"] / fl["days20"],
            f"20일 중 {fl['pos_days20']}일 외국인+기관 순매수")
    hy = t.get("hy_spread")
    if hy and hy.get("pct_1y") is not None:
        add("credit", "자금시장 (하이일드)", 100 - hy["pct_1y"], f"{hy['value']}%p · 1년 중 {hy['pct_1y']}% 위치")
    if len(parts) < 3:
        return None
    score = round(sum(p["score"] for p in parts) / len(parts))
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
            "score": round(drop * (med / pbr), 1),
            "news": news_3d.get(code, [])[:3],
        })
    out.sort(key=lambda x: -x["score"])
    return out


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


# ---------------------------------------------------------------- 성적표

HORIZONS = (1, 5, 20)


def scorecard(history, closes, index_closes):
    """과거 후보가 N영업일 뒤 얼마나 움직였나.

    history: [(기준일, {"scalp": [...], "swing": [...], "value": [...]})]
    closes:  DataFrame(행=영업일, 열=티커)
    index_closes: Series(index=영업일) 코스피
    반환: {그룹: {N: {n, avg, win, excess}}}, 최근 기록 목록
    """
    days = list(closes.index)
    pos = {d: i for i, d in enumerate(days)}
    stats, recent = {}, []
    for date, groups in history:
        if date not in pos:
            continue
        i0 = pos[date]
        for g, items in groups.items():
            for x in items:
                code = x["code"]
                entry = {"date": date, "group": g, "code": code, "name": x["name"], "close": x["close"]}
                for n in HORIZONS:
                    if i0 + n >= len(days) or code not in closes.columns:
                        continue
                    c1 = closes[code].iloc[i0 + n]
                    if c1 is None or math.isnan(c1) or not x["close"]:
                        continue
                    ret = (c1 / x["close"] - 1) * 100
                    k0, k1 = index_closes.get(days[i0]), index_closes.get(days[i0 + n])
                    ex = ret - ((k1 / k0 - 1) * 100) if k0 and k1 else None
                    entry[f"r{n}"] = r1(ret)
                    s = stats.setdefault(g, {}).setdefault(n, {"rets": [], "ex": []})
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
    recent.sort(key=lambda e: e["date"], reverse=True)
    return out, recent[:60], feature_stats(recent)


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
    """스윙·단타 후보(수급 관점)만 대상으로 조건 있음/없음 비교."""
    out = []
    pool = [e for e in entries if e["group"] in ("swing", "scalp") and e.get(f"r{horizon}") is not None]
    for key, label in FEATURES.items():
        yes = [e[f"r{horizon}"] for e in pool if e.get("features", {}).get(key)]
        no = [e[f"r{horizon}"] for e in pool if not e.get("features", {}).get(key)]
        out.append({"key": key, "label": label, "horizon": horizon,
                    "yes": summarize_returns(yes), "no": summarize_returns(no)})
    return out
