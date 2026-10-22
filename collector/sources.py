"""외부 데이터 수집. 네트워크를 타는 코드는 전부 여기에 둔다.

GitHub Actions 에서만 돈다 (사무실 PC는 증권 사이트가 막혀 있음).
KRX 는 pykrx 가 환경변수 KRX_ID / KRX_PW 로 자동 로그인한다.
"""
import html
import math
import re
import time
from datetime import datetime, timedelta
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
    """시장 전체 투자자별 순매수(원). {KOSPI: DataFrame, KOSDAQ: DataFrame}"""
    from pykrx import stock
    return {m: retry(lambda: stock.get_market_trading_value_by_date(fromdate, todate, m))
            for m in ("KOSPI", "KOSDAQ")}


def fundamentals(ymd):
    """전종목 BPS·PER·PBR·EPS·DIV. 반환: DataFrame(index=티커)"""
    from pykrx import stock
    df = retry(lambda: stock.get_market_fundamental(ymd, market="ALL"))
    return df.astype(float)


def index_history(ticker, fromdate, todate):
    from pykrx import stock
    df = retry(lambda: stock.get_index_ohlcv(fromdate, todate, ticker))
    return df["종가"].astype(float)


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


def cnn_fear_greed():
    r = retry(lambda: get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                          headers={"Referer": "https://edition.cnn.com/", "Accept": "application/json"}))
    fg = r.json()["fear_and_greed"]
    return {
        "score": round(fg["score"], 1),
        "rating": fg["rating"],
        "week_ago": round(fg.get("previous_1_week") or math.nan, 1),
        "month_ago": round(fg.get("previous_1_month") or math.nan, 1),
        "timestamp": fg.get("timestamp"),
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


DART_GOOD = ["공급계약", "판매", "수주", "자기주식취득", "자기주식 취득", "무상증자", "영업(잠정)실적",
             "실적", "특허", "기술이전", "기술도입", "신규시설투자", "타법인주식", "합병", "현금ㆍ현물배당"]


def dart_today(ymd, max_pages=15):
    """DART 오늘 공시 목록(웹). 코스피·코스닥 상장사만. 정식 OpenDART 는 키가 필요하다."""
    date_dot = f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            r = retry(lambda: get("https://dart.fss.or.kr/dsac001/mainAll.do",
                                  params={"selectDate": date_dot, "currentPage": page, "maxResults": 100,
                                          "maxLinks": 10, "sort": "", "series": ""}))
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
                        "good": any(k in title for k in DART_GOOD) and "정정" not in title[:4]})
        if new == 0:
            break
        time.sleep(0.5)
    return out


def _tag(block, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    if v.startswith("<![CDATA["):
        v = v[9:-3]
    return html.unescape(v).strip()
