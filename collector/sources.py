"""외부 데이터 수집. 네트워크를 타는 코드는 전부 여기에 둔다.

GitHub Actions 에서만 돈다 (사무실 PC는 증권 사이트가 막혀 있음).
KRX 는 pykrx 가 환경변수 KRX_ID / KRX_PW 로 자동 로그인한다.
"""
import html
import math
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


def dart_today(ymd, max_pages=15):
    """DART 오늘 공시 목록(웹). 코스피·코스닥 상장사만. 정식 OpenDART 는 키가 필요하다."""
    date_dot = f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            r = retry(lambda: get("https://dart.fss.or.kr/dsac001/mainAll.do", timeout=40,
                                  params={"selectDate": date_dot, "currentPage": page, "maxResults": 100,
                                          "maxLinks": 10, "sort": "", "series": ""}), tries=4, wait=5)
        except Exception as e:
            log(f"  DART {page}쪽 실패: {e}")
            break
        rows = re.findall(r"<tr>(.*?)</tr>", r.text, re.S)
        new = 0
        for row in rows:
            m = re.search(r"rcpNo=(\d+)", row)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
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
