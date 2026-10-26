"""업종 지수 확인: KRX 업종 분류(get_market_sector_classifications) 이름과 업종 지수 이름이 맞는지,
지수 구성 종목이 그 업종 분류 종목과 같은지(겹침 비율), 지수 시세를 받을 수 있는지.

업종 대비 상대강도를 넣어도 되는지 판단용. 맞지 않는 업종은 계산하지 않는다.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import sources  # noqa: E402  (KRX 로그인 설정)

from pykrx import stock  # noqa: E402

days = sources.business_days(sys.argv[1] if len(sys.argv) > 1 else "20261023", 25)
base, start = days[-1], days[0]
print("기준일", base)


def norm(s):
    s = re.sub(r"^(코스피|코스닥)\s*", "", str(s))
    return re.sub(r"[\s·・,]", "", s)


for market in ("KOSPI", "KOSDAQ"):
    cls = stock.get_market_sector_classifications(base, market)
    names = cls["업종명"].value_counts()
    tickers = stock.get_index_ticker_list(base, market=market)
    idx = {t: stock.get_index_ticker_name(t) for t in tickers}
    by_norm = {}
    for t, n in idx.items():
        by_norm.setdefault(norm(n), []).append((t, n))
    print(f"\n=== {market}: 업종 {len(names)}개, 지수 {len(idx)}개")
    print("지수 목록:", "; ".join(f"{t} {n}" for t, n in idx.items()))
    ok = miss = 0
    for name, cnt in names.items():
        cand = by_norm.get(norm(name))
        if not cand:
            miss += 1
            print(f"  ✗ {name} ({cnt}종목): 같은 이름 지수 없음")
            continue
        t, n = cand[0]
        members = set(cls.index[cls["업종명"] == name])
        try:
            pdf = set(stock.get_index_portfolio_deposit_file(t, base))
        except Exception as e:
            pdf = set()
            print(f"    구성 종목 실패 {type(e).__name__}")
        inter = len(members & pdf)
        try:
            px = stock.get_index_ohlcv(start, base, t)
            nd = len(px)
        except Exception as e:
            nd = f"실패 {type(e).__name__}"
        cover = (inter / len(members) * 100) if members else 0
        purity = (inter / len(pdf) * 100) if pdf else 0
        ok += 1
        print(f"  ✓ {name} ({cnt}) → {t} {n} | 지수 구성 {len(pdf)}, 겹침 {inter} (업종의 {cover:.0f}%, 지수의 {purity:.0f}%) | 시세 {nd}일")
        if len(cand) > 1:
            print(f"    같은 이름 지수 여러 개: {cand}")
    print(f"  → 이름 맞음 {ok} / 없음 {miss}")
