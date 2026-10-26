"""외부 데이터 수집. 네트워크를 타는 코드는 전부 여기에 둔다.

GitHub Actions 에서만 돈다 (사무실 PC는 증권 사이트가 막혀 있음).
KRX 는 pykrx 가 환경변수 KRX_ID / KRX_PW 로 자동 로그인한다.
"""
import html
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from .util import KST, log

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def retry(fn, tries=3, wait=3):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            log(f"  재시도 {i + 1}: {type(e).__name__}: {str(e)[:100]}")
            time.sleep(wait * (i + 1))


def get(url, ua=True, timeout=25, **kw):
    headers = {**(UA if ua else {}), **kw.pop("headers", {})}
    r = requests.get(url, headers=headers, timeout=timeout, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- KRX

def business_days(end_ymd, count):
    """end_ymd 를 포함해 그 이전 영업일 count 개 (오래된 순)."""
    from pykrx import stock
    start = (datetime.strptime(end_ymd, "%Y%m%d") - timedelta(days=int(count * 1.6) + 20)).strftime("%Y%m%d")
    days = retry(lambda: stock.get_previous_business_days(fromdate=start, todate=end_ymd))
    days = [d.strftime("%Y%m%d") for d in days]
    return days[-count:]


def listing(ymd):
    """기준일 전종목 시세 + 종목명·시장·소속부. 코드 → dict"""
    from pykrx.website.krx.market.core import 전종목시세
    df = retry(lambda: 전종목시세().fetch(ymd, "ALL"))
    out = {}
    for r in df.to_dict("records"):
        num = lambda k: float(str(r[k]).replace(",", "") or 0)
        out[r["ISU_SRT_CD"]] = {
            "name": r["ISU_ABBRV"].strip(),
            "market": r["MKT_NM"],
            "section": (r.get("SECT_TP_NM") or "").strip(),
            "close": num("TDD_CLSPRC"),
            "change": num("FLUC_RT"),
            "value": num("ACC_TRDVAL"),
            "cap": num("MKTCAP"),
        }
    return out


def daily_ohlcv(days):
    """영업일 목록 각각의 전종목 시세. 반환: {ymd: DataFrame(index=티커)}"""
    from pykrx import stock
    out = {}
    for d in days:
        df = retry(lambda: stock.get_market_ohlcv(d, market="ALL"))
        if df["거래량"].sum() == 0:
            log(f"  {d} 거래 없음(휴장으로 보임) — 건너뜀")
            continue
        out[d] = df[["시가", "고가", "저가", "종가", "거래대금", "등락률"]]
    return out


def net_purchase(fromdate, todate, investor):
    """기간 순매수거래대금(원). 반환: Series(index=티커)"""
    from pykrx import stock
    df = retry(lambda: stock.get_market_net_purchases_of_equities(fromdate, todate, "ALL", investor))
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    return df["순매수거래대금"].astype(float)


def market_flows(fromdate, todate):
    """시장 전체 투자자별 순매수(원). {KOSPI: DataFrame, KOSDAQ: DataFrame}

    KRX가 가끔 빈 표를 돌려줘서(2026-10-23 코스피), 비어 있으면 실패로 보고 다시 받는다.
    """
    from pykrx import stock

    def fetch(m):
        df = stock.get_market_trading_value_by_date(fromdate, todate, m)
        if df is None or len(df) == 0 or "외국인합계" not in df.columns:
            raise ValueError(f"{m} 투자자별 순매수가 비어 있음")
        return df

    return {m: retry(lambda: fetch(m), tries=4, wait=5) for m in ("KOSPI", "KOSDAQ")}


def fundamentals(ymd):
    """전종목 BPS·PER·PBR·EPS·DIV. 반환: DataFrame(index=티커)"""
    from pykrx import stock
    df = retry(lambda: stock.get_market_fundamental(ymd, market="ALL"))
    return df.astype(float)


def foreign_ratio(ymd):
    """전종목 외국인 지분율(%). 반환: Series(index=티커)"""
    from pykrx import stock
    df = retry(lambda: stock.get_exhaustion_rates_of_foreign_investment_by_ticker(ymd, "ALL"))
    return df["지분율"].astype(float)


def sectors(ymd):
    """업종명. 반환: Series(index=티커)"""
    from pykrx import stock
    parts = [retry(lambda: stock.get_market_sector_classifications(ymd, m))["업종명"] for m in ("KOSPI", "KOSDAQ")]
    return pd.concat(parts)


def sector_indices(ymd):
    """KRX 업종 지수와 업종 분류 짝: {(시장, 업종명): (지수 코드, 지수 이름, 구성 종목 set)}.
    업종 분류(get_market_sector_classifications) 이름과 같은 이름의 지수만 (가운뎃점·띄어쓰기 무시). 기준일 시점 구성 종목."""
    from pykrx import stock
    norm = lambda s: re.sub(r"[\s·・,]", "", re.sub(r"^(코스피|코스닥)\s*", "", str(s)))
    out = {}
    for market in ("KOSPI", "KOSDAQ"):
        names = set(retry(lambda: stock.get_market_sector_classifications(ymd, market))["업종명"])
        idx = {norm(stock.get_index_ticker_name(t)): (t, stock.get_index_ticker_name(t))
               for t in retry(lambda: stock.get_index_ticker_list(ymd, market=market))}
        for name in names:
            hit = idx.get(norm(name))
            if hit:
                members = set(retry(lambda: stock.get_index_portfolio_deposit_file(hit[0], ymd)))
                out[(market, name)] = (hit[0], hit[1], members)
    return out


def index_history(ticker, fromdate, todate):
    from pykrx import stock
    df = retry(lambda: stock.get_index_ohlcv(fromdate, todate, ticker))
    return df["종가"].astype(float)


def index_open_close(ticker, fromdate, todate):
    """지수 시가·종가 (index=YYYYMMDD 문자열)."""
    from pykrx import stock
    df = retry(lambda: stock.get_index_ohlcv(fromdate, todate, ticker))
    df.index = [d.strftime("%Y%m%d") for d in df.index]
    return df["시가"].astype(float), df["종가"].astype(float)


# ---------------------------------------------------------------- 해외·심리 지표

def vix():
    r = retry(lambda: get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"))
    rows = []
    for line in r.text.strip().splitlines()[1:]:
        p = line.split(",")
        try:
            rows.append((datetime.strptime(p[0], "%m/%d/%Y").strftime("%Y-%m-%d"), float(p[4])))
        except (ValueError, IndexError):
            continue
    return rows


def naver_profile(code):
    """네이버 모바일 증권: 종목 요약 지표(integration) + 연간 실적·회사 설명(finance/annual). 지금 시점 값."""
    base = f"https://m.stock.naver.com/api/stock/{code}"
    integ = retry(lambda: get(f"{base}/integration", timeout=15).json(), tries=2)
    try:
        fin = get(f"{base}/finance/annual", timeout=15).json()
    except Exception:
        fin = {}
    return integ, fin


def _isin_check(body):
    """ISIN 검증 숫자 (문자 → 10~35, 룬 알고리즘)."""
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def daum_futures(base_ymd, days=60):
    """다음 금융: 코스피200 선물 투자자별 순매수(계약, 전 월물 합계) → 날짜 오름차순 [(YYYYMMDD, 외국인, 기관, 개인, 선물가격)].

    선물 종목(ISIN)이 필요해 기준일 뒤 첫 분기 만기물부터 차례로 시도한다 (2026-10 시험, probe/futures.py).
    """
    y, m = int(base_ymd[:4]), int(base_ymd[4:6])
    tries = []
    for k in range(5):
        q = ((m - 1) // 3 + k) % 4
        yy = y + ((m - 1) // 3 + k) // 4
        code = "369C"[q]
        body = f"KR4A01{yy % 10}{code}000" if yy >= 2026 else f"KR4101{'VWX'[yy - 2024]}{code}000"
        tries.append(body + _isin_check(body))
    headers = {**UA, "Referer": "https://finance.daum.net/domestic/futures"}
    for isin in tries:
        r = get(f"https://finance.daum.net/api/future/{isin}/days", headers=headers, timeout=20,
                params={"page": 1, "perPage": days})
        rows = r.json().get("data") or []
        out = [(x["date"][:10].replace("-", ""), x.get("foreignSettlement"), x.get("institutionSettlement"),
                x.get("privateSettlement"), x.get("tradePrice")) for x in rows]
        out = sorted(o for o in out if o[0] <= base_ymd and o[1] is not None)
        if out and out[-1][0] == base_ymd:
            return out
    return []


def kofia(obj, start_ymd, end_ymd):
    """금융투자협회 FreeSIS 일별 통계 → 날짜 오름차순 [{TMPV1: 날짜, TMPV2...}]. 금액 단위 백만 원, T+1 영업일 공표.

    화면에 먼저 접속해 세션 쿠키를 받아야 JSON을 준다 (2026-10 시험, probe/credit.py).
    """
    base = "https://freesis.kofia.or.kr"
    page = f"{base}/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId={obj[:-2]}"
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                         "tmpV45": start_ymd, "tmpV46": end_ymd, "OBJ_NM": obj}}

    def once():
        s = requests.Session()
        s.headers.update(UA)
        s.get(page, timeout=30).raise_for_status()
        r = s.post(f"{base}/meta/getMetaDataList.do", json=body, timeout=60, headers={"Referer": page})
        r.raise_for_status()
        return r.json()["ds1"]

    return sorted(retry(once), key=lambda x: x["TMPV1"])


def fred(series_id):
    # 브라우저 UA 를 붙이면 FRED 가 응답하지 않는다 (2026-10 시험)
    r = retry(lambda: get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                          ua=False, timeout=60, params={"id": series_id}))
    rows = []
    for line in r.text.strip().splitlines()[1:]:
        d, _, v = line.partition(",")
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def cnn_fear_greed(base_iso):
    """CNN 공포·탐욕지수. 1년치 일별 기록에서 기준일 전 미국 장 마감값을 고른다.

    CNN 기록의 날짜(UTC 0시)는 전날 미국 장 마감값이다. 한국 기준일 저녁에 알 수 있는 건
    그 날짜(UTC) ≤ 기준일인 값까지.
    """
    r = retry(lambda: get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                          headers={"Referer": "https://edition.cnn.com/", "Accept": "application/json"}))
    hist = r.json()["fear_and_greed_historical"]["data"]
    rows = [(datetime.utcfromtimestamp(p["x"] / 1000).strftime("%Y-%m-%d"), p["y"], p.get("rating"))
            for p in hist]
    rows = [x for x in rows if x[0] <= base_iso]
    if not rows:
        raise RuntimeError("기준일 이전 CNN 기록 없음")
    pick = lambda back: round(rows[-1 - back][1], 1) if len(rows) > back else None
    return {
        "date": rows[-1][0],
        "score": pick(0),
        "rating": rows[-1][2],
        "prev": pick(1),
        "week_ago": pick(5),
        "month_ago": pick(21),
        "year_ago": round(rows[0][1], 1) if len(rows) >= 240 else None,
        "spark": [round(x[1], 1) for x in rows[-60:]],
    }


# ---------------------------------------------------------------- 뉴스·공시

NEWS_QUERIES = [
    "특징주", "공급계약 체결", "수주", "MOU 체결", "실적 호조", "최대 실적", "흑자전환",
    "기술수출", "임상 승인", "정부 지원 수혜", "상한가", "급등 주가", "단독 계약",
    "투자 유치 주가", "국책과제 선정", "인수 합병 주가",
]


def google_news(start, end):
    """구글 뉴스 RSS. 검색어별 최대 100건, start ≤ 날짜 < end (YYYY-MM-DD).

    날짜 범위를 직접 주므로 과거 기준일도 그날 기준으로 다시 계산할 수 있다.
    """
    items = {}
    for q in NEWS_QUERIES:
        try:
            r = retry(lambda: get("https://news.google.com/rss/search",
                                  params={"q": f"{q} after:{start} before:{end}", "hl": "ko", "gl": "KR",
                                          "ceid": "KR:ko"}))
        except Exception as e:
            log(f"  구글 뉴스 '{q}' 실패: {e}")
            continue
        for block in re.findall(r"<item>(.*?)</item>", r.text, re.S):
            title = _tag(block, "title")
            link = _tag(block, "link")
            pub = _tag(block, "pubDate")
            if not title or not link:
                continue
            try:
                at = parsedate_to_datetime(pub).astimezone(KST)
            except (TypeError, ValueError):
                continue
            # 제목 끝의 " - 언론사" 분리
            source = ""
            m = re.match(r"(.*) - ([^-]{1,30})$", title)
            if m:
                title, source = m.group(1).strip(), m.group(2).strip()
            items[link] = {"title": title, "url": link, "source": source or "구글 뉴스",
                           "at": at.isoformat(timespec="minutes"), "kind": "뉴스"}
        time.sleep(0.5)
    return list(items.values())


def naver_main_news(pages=5):
    """네이버 증권 주요뉴스 (비공식 JSON)."""
    out = []
    for page in range(1, pages + 1):
        try:
            r = retry(lambda: get("https://m.stock.naver.com/api/news/list",
                                  params={"category": "mainnews", "pageSize": 20, "page": page}))
            data = r.json()
        except Exception as e:
            log(f"  네이버 주요뉴스 {page}쪽 실패: {e}")
            break
        rows = data if isinstance(data, list) else data.get("list") or data.get("items") or []
        if page == 1 and rows:
            log(f"  네이버 주요뉴스 필드: {sorted(rows[0].keys())}")
        for row in rows:
            title = html.unescape(str(row.get("title") or row.get("tit") or "")).strip()
            oid, aid = row.get("officeId") or row.get("oid"), row.get("articleId") or row.get("aid")
            url = row.get("linkUrl") or row.get("url") or (
                f"https://n.news.naver.com/mnews/article/{oid}/{aid}" if oid and aid else "")
            at = _parse_naver_time(row.get("datetime") or row.get("dt") or row.get("writeDateTime"))
            if title and url and at:
                out.append({"title": title, "url": url, "source": row.get("ohnm") or row.get("officeName") or "네이버",
                            "at": at.isoformat(timespec="minutes"), "kind": "뉴스"})
        if not rows:
            break
    return out


def _parse_naver_time(v):
    if not v:
        return None
    s = re.sub(r"\D", "", str(v))
    try:
        return datetime.strptime(s[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        return None


# 주가에 부담이 되는 공시 — 이런 공시가 최근 있으면 후보에서 뺀다
DART_BAD = ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "최대주주변경", "최대주주 변경", "감자결정",
            "불성실공시", "상장적격성", "횡령", "배임", "회생절차", "파산", "매매거래정지", "거래정지", "감사의견",
            "관리종목", "투자경고", "투자위험", "상장폐지"]

DART_GOOD = ["공급계약", "판매", "수주", "자기주식취득", "자기주식 취득", "무상증자", "영업(잠정)실적",
             "잠정실적", "손익구조", "특허", "기술이전", "기술도입", "신규시설투자", "타법인주식", "합병", "현금ㆍ현물배당"]
# '실적'이 들어가도 재료가 아닌 공시 (회사채 발행 보고, 실적 발표 일정 안내)
DART_NOT_GOOD = ["증권발행실적", "실적공시예고", "결산실적공시예고"]
# 실적 공시: 제목만으로는 좋고 나쁨을 모른다 → 본문에서 영업이익 증감을 읽어 판단 (earnings_direction)
DART_EARNINGS = ["잠정)실적", "잠정실적", "손익구조"]


DART_STATE = {"down": False}   # 한 번 첫 쪽부터 접속이 안 되면 이번 실행에선 더 부르지 않는다 (날마다 수 분씩 기다리지 않게)


# ---------------------------------------------------------------- OpenDART (정식 API, 무료 키 DART_API_KEY)
OPENDART = "https://opendart.fss.or.kr/api"


def opendart_key():
    return os.environ.get("DART_API_KEY", "").strip()


def opendart(path, **params):
    """OpenDART 호출. status 000(정상)·013(자료 없음)만 받아들이고 나머지는 예외 (키 오류·한도 초과 등)."""
    d = retry(lambda: get(f"{OPENDART}/{path}", params={"crtfc_key": opendart_key(), **params}, timeout=30).json(), tries=3, wait=3)
    if d.get("status") not in ("000", "013"):
        raise RuntimeError(f"OpenDART {path} {d.get('status')} {d.get('message')}")
    return d


def opendart_list(bgn, end=None, kind=None, markets=("Y", "K"), max_pages=40):
    """기간 공시 목록 (corp_code 없이는 3개월까지). 반환: list.json의 줄 그대로 (코스피 Y·코스닥 K만)."""
    out = []
    for page in range(1, max_pages + 1):
        params = {"bgn_de": bgn, "end_de": end or bgn, "page_count": 100, "page_no": page}
        if kind:
            params["pblntf_ty"] = kind
        d = opendart("list.json", **params)
        rows = d.get("list") or []
        out += [r for r in rows if r.get("corp_cls") in markets]
        if page >= int(d.get("total_page") or 1):
            break
        time.sleep(0.15)
    return out


def _dart_item(ymd, corp, title, rcp, code=None):
    """웹·API 공시를 같은 모양으로 (재료성·위험·실적 판정은 제목 글자로)."""
    title = re.sub(r"\s+", " ", title).strip()
    return {"corp": corp, "code": code, "title": title,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}",
            # API에는 접수 시각이 없어 장 마감 뒤(16:00)로 둔다 (그날 소식으로 셈)
            "at": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T16:00+09:00",
            "kind": "공시", "source": "DART",
            "good": any(k in title for k in DART_GOOD) and not any(k in title for k in DART_BAD)
                    and not any(k in title for k in DART_NOT_GOOD) and "정정" not in title[:4],
            "earnings": any(k in title for k in DART_EARNINGS) and not any(k in title for k in DART_NOT_GOOD),
            "bad": any(k in title for k in DART_BAD)
                   and not any(k in title for k in ("미지정", "철회", "해제", "취소", "지정유예"))}


def dart_today(ymd, max_pages=15):
    """그날 공시 목록. 키가 있으면 OpenDART(빠르고 안정적), 없거나 실패하면 DART 웹."""
    if opendart_key():
        try:
            rows = opendart_list(ymd)
            return [_dart_item(ymd, r["corp_name"], r["report_nm"], r["rcept_no"], r.get("stock_code")) for r in rows
                    if r["rcept_no"][:8] == ymd]
        except Exception as e:
            log(f"  OpenDART 목록 실패 → 웹으로: {type(e).__name__}: {e}")
    return dart_web_today(ymd, max_pages)


def _int(x):
    try:
        return int(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def opendart_signals(bgn, end, cache=None, max_detail=400):
    """내부자·큰손·회사의 '사는' 공시 (기간 bgn~end, 3개월 안):
    · insider: 임원·주요주주 특정증권 소유 보고에서 주식 수가 늘어난 것 (elestock, 회사마다 한 번 불러 접수번호로 맞춤)
    · major:   5% 대량보유 보고 중 보고 사유에 '장내매수'가 있고 주식 수가 늘어난 것 (majorstock)
    · buyback: 주요사항보고서(자기주식취득결정·자기주식취득신탁계약체결결정) — 제목만으로
    반환 [{date, code, name, kind, rcept, qty, rate, who}], 회사별 상세 호출은 max_detail 개까지 (cache: corp_code → 응답 줄)."""
    cache = cache if cache is not None else {}
    out, calls = [], 0
    for r in opendart_list(bgn, end, kind="B"):
        t = r["report_nm"].replace(" ", "")
        if "자기주식취득" in t and "정정" not in t[:6] and "처분" not in t:
            out.append({"date": r["rcept_no"][:8], "code": r.get("stock_code"), "name": r["corp_name"], "kind": "buyback", "rcept": r["rcept_no"]})
    rows = opendart_list(bgn, end, kind="D")
    want = [r for r in rows if not r["report_nm"].startswith("[") and
            ("임원ㆍ주요주주특정증권등소유상황보고서" in r["report_nm"] or "주식등의대량보유상황보고서" in r["report_nm"])]
    want.sort(key=lambda r: r["rcept_no"], reverse=True)   # 최근 것부터 (상세 호출 한도 안에서)
    stopped = None   # 한도에 걸려 이 날짜(포함) 이전은 다 못 봄
    for r in want:
        api = "elestock.json" if "임원" in r["report_nm"] else "majorstock.json"
        key = (api, r["corp_code"])
        if key not in cache:
            if calls >= max_detail:
                stopped = stopped or r["rcept_no"][:8]
                continue
            try:
                cache[key] = opendart(api, corp_code=r["corp_code"]).get("list") or []
            except Exception as e:
                log(f"  OpenDART {api} {r['corp_name']} 실패: {type(e).__name__}")
                cache[key] = []
            calls += 1
            time.sleep(0.1)
        hit = next((x for x in cache[key] if x.get("rcept_no") == r["rcept_no"]), None)
        if not hit:
            continue
        if api == "elestock.json":
            q = _int(hit.get("sp_stock_lmp_irds_cnt"))
            if q and q > 0:
                out.append({"date": r["rcept_no"][:8], "code": r.get("stock_code"), "name": r["corp_name"], "kind": "insider",
                            "rcept": r["rcept_no"], "qty": q, "rate": hit.get("sp_stock_lmp_irds_rate"),
                            "who": hit.get("isu_exctv_ofcps") if hit.get("isu_exctv_ofcps") not in (None, "-") else hit.get("isu_main_shrholdr")})
        else:
            q = _int(hit.get("stkqy_irds"))
            if q and q > 0 and "장내매수" in (hit.get("report_resn") or ""):
                out.append({"date": r["rcept_no"][:8], "code": r.get("stock_code"), "name": r["corp_name"], "kind": "major",
                            "rcept": r["rcept_no"], "qty": q, "rate": hit.get("stkrt_irds"), "who": hit.get("repror")})
    return out, calls, stopped


def dart_web_today(ymd, max_pages=15):
    """DART 오늘 공시 목록(웹, OpenDART 키가 없거나 실패할 때). 코스피·코스닥 상장사만.

    첫 쪽부터 실패하면 예외를 낸다 (빈 목록과 '받지 못함'을 구분해야 위험 공시 제외가 조용히 약해지지 않는다).
    """
    if DART_STATE["down"]:
        raise ConnectionError("DART 접속 안 됨 (이번 실행 앞에서 실패)")
    date_dot = f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    out, seen, stale = [], set(), 0
    for page in range(1, max_pages + 1):
        try:
            r = retry(lambda: get("https://dart.fss.or.kr/dsac001/mainAll.do", timeout=40,
                                  params={"selectDate": date_dot, "currentPage": page, "maxResults": 100,
                                          "maxLinks": 10, "sort": "", "series": ""}), tries=4, wait=5)
        except Exception as e:
            if page == 1:
                DART_STATE["down"] = True
                raise
            log(f"  DART {page}쪽 실패 (앞 쪽까지만 씀): {e}")
            break
        rows = re.findall(r"<tr>(.*?)</tr>", r.text, re.S)
        new = 0
        for row in rows:
            m = re.search(r"rcpNo=(\d+)", row)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
            # 접수번호 앞 8자리 = 실제 접수일. 그날 목록에 옛 공시(예: 8월 반기보고서)가 다시 섞여 나오는 경우가 있어 뺀다
            if m.group(1)[:8] != ymd:
                stale += 1
                continue
            market = re.search(r'class="tagCom_(\w+)"', row)
            if not market or market.group(1) not in ("kospi", "kosdaq"):
                continue
            corp = re.search(r'title="([^"]+) 기업개황', row)
            report = re.search(r'title="([^"]+) 공시뷰어', row)
            hhmm = re.search(r"(\d{2}:\d{2})", row)
            if not corp or not report:
                continue
            title = re.sub(r"\s+", " ", report.group(1)).strip()
            out.append({"corp": corp.group(1).strip(), "title": title,
                        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={m.group(1)}",
                        "at": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}T{hhmm.group(1) if hhmm else '00:00'}+09:00",
                        "kind": "공시", "source": "DART",
                        "good": any(k in title for k in DART_GOOD) and not any(k in title for k in DART_BAD)
                                and not any(k in title for k in DART_NOT_GOOD) and "정정" not in title[:4],
                        "earnings": any(k in title for k in DART_EARNINGS) and not any(k in title for k in DART_NOT_GOOD),
                        "bad": any(k in title for k in DART_BAD)
                               and not any(k in title for k in ("미지정", "철회", "해제", "취소", "지정유예"))})
        if new == 0:
            break
        time.sleep(0.5)
    if stale:
        log(f"  DART {ymd}: 다른 날 접수된 공시 {stale}건 제외")
    return out


def dart_document_text(rcp_no):
    """DART 공시 본문(공시뷰어)을 글자만 ' | '로 이어 붙여 돌려준다. 접속이 자주 끊겨 넉넉히 재시도한다."""
    main = retry(lambda: get("https://dart.fss.or.kr/dsaf001/main.do", params={"rcpNo": rcp_no}, timeout=30).text,
                 tries=4, wait=3)
    v = re.search(r"viewDoc\([\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],"
                  r"\s*[\"'](\d+)[\"'],\s*[\"']([^\"']*)[\"']", main)
    if not v:
        return ""
    keys = ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd")
    doc = retry(lambda: get("https://dart.fss.or.kr/report/viewer.do", timeout=30, params=dict(zip(keys, v.groups()))).text,
                tries=4, wait=3)
    text = re.sub(r"<[^>]+>", " | ", doc).replace("&nbsp;", " ")
    return re.sub(r"(\s*\|\s*)+", " | ", text)


def _tag(block, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    if v.startswith("<![CDATA["):
        v = v[9:-3]
    return html.unescape(v).strip()


# ---------------------------------------------------------------- 아침 요약 (미국장 마감)

def yahoo_daily(symbol, rng="1mo"):
    """야후 차트 API 일별 종가. 반환: [(YYYY-MM-DD(현지), 종가)] 오래된 순."""
    r = retry(lambda: get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
                          params={"range": rng, "interval": "1d"}), tries=3, wait=5)
    res = r.json()["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for ts, c in zip(res["timestamp"], closes):
        if c is not None:
            out.append((datetime.fromtimestamp(ts + off, tz=timezone.utc).strftime("%Y-%m-%d"), float(c)))
    return out


# ---------------------------------------------------------------- 증권사 리포트·검색 상위 (네이버 모바일, 비공식)

def naver_research(since_ymd, max_pages=40):
    """네이버 리서치 '종목분석' 리포트 목록, since_ymd 이후 것만. 최신순 목록을 거슬러 내려간다."""
    out = []
    since = f"{since_ymd[:4]}-{since_ymd[4:6]}-{since_ymd[6:]}"
    for page in range(1, max_pages + 1):
        rows = retry(lambda: get("https://m.stock.naver.com/api/research/company",
                                 params={"page": page, "pageSize": 100}).json(), tries=3, wait=5)
        if not rows:
            break
        for x in rows:
            if x.get("writeDate", "") >= since and x.get("itemCode"):
                out.append({"id": x["researchId"], "code": x["itemCode"], "date": x["writeDate"].replace("-", ""),
                            "title": x.get("title", ""), "broker": x.get("brokerName", ""),
                            "reads": int(str(x.get("readCount") or 0).replace(",", "") or 0)})
        if rows[-1].get("writeDate", "") < since:
            break
        time.sleep(0.3)
    return out


def naver_search_top(size=100):
    """네이버 증권 검색 상위 종목 코드 (1위부터)."""
    d = retry(lambda: get("https://m.stock.naver.com/api/stocks/searchTop", params={"page": 1, "pageSize": size}).json())
    return [s["itemCode"] for s in d.get("stocks", []) if s.get("itemCode")]


def naver_research_detail(rid):
    """리포트 하나의 투자의견·목표가·직전 목표가·작성일 주가·요약 본문."""
    d = retry(lambda: get(f"https://m.stock.naver.com/api/research/company/{rid}").json())
    c = d.get("researchContent") or {}
    num = lambda v: float(str(v).replace(",", "")) if v not in (None, "", "0", 0) else None
    text = re.sub(r"<br\s*/?>|</p>", "\n", c.get("content") or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return {"opinion": c.get("opinion") or None, "goal": num(c.get("goalPrice")), "prev_goal": num(c.get("prevGoalPrice")),
            "price_then": num(c.get("priceAtWriteDate")), "summary": text[:400], "pdf": c.get("attachUrl")}


# ---------------------------------------------------------------- ETF (KRX, 실패하면 네이버)

def etf_history(code, start_ymd, end_ymd):
    """ETF 일별 종가·거래대금. 반환: DataFrame(index=YYYYMMDD, 종가/거래대금)"""
    from pykrx import stock
    df = retry(lambda: stock.get_etf_ohlcv_by_date(start_ymd, end_ymd, code))
    if df is None or len(df) == 0:
        raise ValueError("빈 응답")
    df.index = [d.strftime("%Y%m%d") for d in df.index]
    return df[["종가", "거래대금"]].astype(float)


def etf_pdf(code, ymd):
    """ETF 구성 종목 전체 (KRX, 현금·채권 제외). 반환: {코드: (이름, 계약수, 비중%)} — 계약수는 설정 단위(CU)당 수량"""
    from pykrx import stock
    df = retry(lambda: stock.get_etf_portfolio_deposit_file(code, ymd))
    out = {}
    for t, r in df.iterrows():
        t, name = str(t), str(r["구성종목명"])
        if len(t) == 6 and t[:5].isalnum() and "현금" not in name and float(r["계약수"] or 0) > 0:
            out[t] = (name, float(r["계약수"]), float(r["비중"] or 0))
    return out


def etf_holdings(code, ymd, top=10):
    """ETF 구성 종목 상위 (현금 제외). 반환: [(코드, 이름, 비중%)]"""
    try:
        pdf = etf_pdf(code, ymd)
        rows = [(t, n, w) for t, (n, _, w) in pdf.items()]
        if rows:
            return sorted(rows, key=lambda x: -x[2])[:top]
    except Exception as e:
        log(f"  ETF {code} 구성(KRX) 실패: {type(e).__name__} — 네이버로")
    d = retry(lambda: get(f"https://m.stock.naver.com/api/stock/{code}/etfAnalysis").json())
    return [(x["itemCode"], x["itemName"], float(str(x.get("etfWeight", "0")).rstrip("%") or 0))
            for x in d.get("etfTop10MajorConstituentAssets") or [] if len(x.get("itemCode", "")) == 6][:top]
