"""데이터 출처 접속 시험.

각 출처에 한 번씩 접속해서 받아지는지, 몇 건인지, 걸린 시간을 기록한다.
결과는 probe/result.json 과 (GitHub Actions 에서) 작업 요약 화면에 표로 남긴다.
"""
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20


def last_weekday(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


TODAY = datetime.now(KST)
# 장 마감 전이면 전 영업일 기준 (공휴일은 고려하지 않음)
BASE = last_weekday(TODAY - timedelta(days=1) if TODAY.hour < 16 else TODAY)
YMD = BASE.strftime("%Y%m%d")

checks = []


def check(name, group):
    def deco(fn):
        checks.append((name, group, fn))
        return fn
    return deco


DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dump")
os.makedirs(DUMP, exist_ok=True)


def get(url, **kw):
    ua = kw.pop("ua", True)
    r = requests.get(url, headers={**(UA if ua else {}), **kw.pop("headers", {})},
                     timeout=kw.pop("timeout", TIMEOUT), **kw)
    name = re.sub(r"[^A-Za-z0-9]+", "_", url.split("//", 1)[-1])[:80]
    with open(os.path.join(DUMP, name + ".txt"), "wb") as f:
        f.write(r.content[:200000])
    r.raise_for_status()
    return r


# ---------- 뉴스 ----------

@check("네이버 금융 뉴스 목록", "뉴스")
def naver_news():
    r = get("https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258")
    r.encoding = "euc-kr"
    titles = re.findall(r'<dd class="articleSubject">\s*<a [^>]*title="([^"]+)"', r.text)
    if not titles:
        titles = re.findall(r'class="articleSubject">\s*<a[^>]*>([^<]+)</a>', r.text)
    return len(titles), titles[:2]


@check("구글 뉴스 RSS", "뉴스")
def google_news():
    r = get("https://news.google.com/rss/search",
            params={"q": "공급계약 체결 주가", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    titles = re.findall(r"<item><title>(.*?)</title>", r.text)
    return len(titles), titles[:2]


@check("DART 공시 목록(키 없이 웹)", "뉴스")
def dart_web():
    r = get("https://dart.fss.or.kr/dsac001/mainAll.do")
    rows = re.findall(r'openReportViewer\(', r.text)
    return len(rows), [f"{len(r.text)} bytes"]


# ---------- 시세·수급 ----------

@check("네이버 일별 시세 JSON", "시세·수급")
def naver_sise():
    start = (BASE - timedelta(days=40)).strftime("%Y%m%d")
    r = get("https://api.finance.naver.com/siseJson.naver",
            params={"symbol": "005930", "requestType": 1, "startTime": start,
                    "endTime": YMD, "timeframe": "day"})
    rows = re.findall(r'\["(\d{8})"', r.text)
    return len(rows), rows[-2:]


@check("네이버 종목별 기관·외국인", "시세·수급")
def naver_frgn():
    r = get("https://finance.naver.com/item/frgn.naver?code=005930")
    r.encoding = "euc-kr"
    dates = re.findall(r'<span class="tah p10 gray03">(\d{4}\.\d{2}\.\d{2})</span>', r.text)
    return len(dates), dates[:2]


@check("네이버 모바일 수급 API", "시세·수급")
def naver_mobile_trend():
    r = get("https://m.stock.naver.com/api/stock/005930/trend", params={"pageSize": 5})
    data = r.json()
    sample = [{k: d.get(k) for k in ("bizdate", "foreignerPureBuyQuant", "organPureBuyQuant")}
              for d in data[:2]]
    return len(data), sample


@check("네이버 시가총액 순위(전종목 대체용)", "시세·수급")
def naver_market_sum():
    r = get("https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1")
    r.encoding = "euc-kr"
    names = re.findall(r'<a href="/item/main.naver\?code=\d{6}" class="tltle">([^<]+)</a>', r.text)
    return len(names), names[:2]


@check("KRX 전종목 시세 (pykrx)", "시세·수급")
def krx_ohlcv():
    from pykrx import stock
    df = stock.get_market_ohlcv(YMD, market="ALL")
    return len(df), [str(df.columns.tolist()[:5])]


@check("KRX 투자자별 순매수 상위 (pykrx)", "시세·수급")
def krx_net_purchase():
    from pykrx import stock
    df = stock.get_market_net_purchases_of_equities(YMD, YMD, "ALL", "외국인")
    return len(df), df.head(2).index.tolist()


@check("KRX PER·PBR (pykrx)", "시세·수급")
def krx_fundamental():
    from pykrx import stock
    df = stock.get_market_fundamental(YMD, market="ALL")
    return len(df), [str(df.columns.tolist())]


@check("KRX 데이터포털 직접 호출", "시세·수급")
def krx_direct():
    r = requests.post("http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                      headers={**UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"},
                      data={"bld": "dbms/MDC/STAT/standard/MDCSTAT01501", "mktId": "ALL",
                            "trdDd": YMD, "share": "1", "money": "1"},
                      timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    rows = data.get("OutBlock_1", [])
    return len(rows), [rows[0].get("ISU_ABBRV")] if rows else [str(data)[:120]]


@check("네이버 모바일 시가총액 목록", "시세·수급")
def naver_mobile_market():
    r = get("https://m.stock.naver.com/api/stocks/marketValue/KOSPI", params={"page": 1, "pageSize": 100})
    d = r.json()
    items = d.get("stocks", d if isinstance(d, list) else [])
    return len(items), [str(items[0])[:150]] if items else [str(d)[:150]]


@check("네이버 기관 순매수 상위", "시세·수급")
def naver_deal_rank():
    r = get("https://finance.naver.com/sise/sise_deal_rank_iframe.naver",
            params={"sosok": "01", "investor_gubun": "1000", "type": "buy"})
    r.encoding = "euc-kr"
    names = re.findall(r'class="company"[^>]*>([^<]+)<', r.text) or re.findall(r'title="([^"]+)"', r.text)
    return len(names), names[:3]


@check("네이버 코스닥 시가총액 목록", "시세·수급")
def naver_mobile_kosdaq():
    r = get("https://m.stock.naver.com/api/stocks/marketValue/KOSDAQ", params={"page": 1, "pageSize": 100})
    d = r.json()
    return len(d.get("stocks", [])), [d.get("totalCount")]


@check("네이버 종목 종합(PER·PBR·52주)", "시세·수급")
def naver_integration():
    r = get("https://m.stock.naver.com/api/stock/005930/integration")
    d = r.json()
    infos = {i.get("code"): i.get("value") for i in d.get("totalInfos", [])}
    return len(infos), [str(infos)[:300]]


@check("네이버 종목 뉴스", "뉴스")
def naver_stock_news():
    r = get("https://m.stock.naver.com/api/news/stock/005930", params={"pageSize": 10, "page": 1})
    d = r.json()
    items = [i for g in d for i in g.get("items", [])] if isinstance(d, list) else []
    return len(items), [items[0].get("title")] if items else [str(d)[:150]]


@check("네이버 주요뉴스 목록", "뉴스")
def naver_main_news():
    r = get("https://m.stock.naver.com/api/news/list", params={"category": "mainnews", "pageSize": 20, "page": 1})
    d = r.json()
    items = d if isinstance(d, list) else d.get("list", d.get("items", []))
    return len(items), [str(items[0])[:150]] if items else [str(d)[:150]]


# ---------- 시장 온도 ----------

@check("CBOE VIX CSV", "시장 온도")
def cboe_vix():
    r = get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
    lines = r.text.strip().splitlines()
    return len(lines) - 1, lines[-2:]


@check("FRED 하이일드 (UA 없이, 60초)", "시장 온도")
def fred_hy2():
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "BAMLH0A0HYM2"},
            ua=False, timeout=60)
    lines = r.text.strip().splitlines()
    return len(lines) - 1, lines[-2:]


@check("네이버 지수 API (코스피200)", "시장 온도")
def naver_kpi200():
    r = get("https://m.stock.naver.com/api/index/KPI200/basic")
    d = r.json()
    return 1, [d.get("closePrice"), d.get("localTradedAt")]


@check("FRED 미국 VIX", "시장 온도")
def fred_vix():
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "VIXCLS"})
    lines = r.text.strip().splitlines()
    return len(lines) - 1, lines[-2:]


@check("FRED 미국 하이일드 스프레드", "시장 온도")
def fred_hy():
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "BAMLH0A0HYM2"})
    lines = r.text.strip().splitlines()
    return len(lines) - 1, lines[-2:]


@check("CNN 공포·탐욕지수", "시장 온도")
def cnn_fng():
    r = get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"Referer": "https://edition.cnn.com/", "Accept": "application/json"})
    fg = r.json().get("fear_and_greed", {})
    return 1, [fg.get("score"), fg.get("rating"), fg.get("timestamp")]


@check("VKOSPI (pykrx 지수)", "시장 온도")
def vkospi():
    from pykrx import stock
    start = (BASE - timedelta(days=14)).strftime("%Y%m%d")
    # 파생상품 지수 중 '변동성' 이름을 가진 지수를 찾는다
    for market in ("KOSPI", "KRX", "테마"):
        try:
            for t in stock.get_index_ticker_list(YMD, market=market):
                if "변동성" in stock.get_index_ticker_name(t):
                    df = stock.get_index_ohlcv(start, YMD, t)
                    return len(df), [t, stock.get_index_ticker_name(t), float(df["종가"].iloc[-1])]
        except Exception:
            continue
    raise RuntimeError("변동성 지수를 목록에서 찾지 못함")


@check("VKOSPI (KRX 전체지수 시세)", "시장 온도")
def vkospi_all():
    from pykrx.website.krx.market.core import 전체지수시세
    seen = []
    for code in ("01", "02", "03", "04", "05", "06", "07", "08"):
        try:
            df = 전체지수시세().fetch(YMD, code)
        except Exception as e:
            seen.append(f"{code}:{type(e).__name__}")
            continue
        seen.append(f"{code}:{len(df)}")
        if "IDX_NM" in df:
            hit = df[df["IDX_NM"].str.contains("변동성|VKOSPI", regex=True)]
            if len(hit):
                row = hit.iloc[0]
                return 1, [code, row["IDX_NM"], row.get("CLSPRC_IDX")]
    raise RuntimeError("못 찾음 " + " ".join(seen))


@check("VKOSPI (KRX 파생상품지수 후보 경로)", "시장 온도")
def vkospi_deriv():
    from pykrx.website.krx.krxio import KrxWebIo
    tried = []
    for bld in ("MDCSTAT01201", "MDCSTAT01101", "MDCSTAT01001", "MDCSTAT00901", "MDCSTAT01301"):
        class Io(KrxWebIo):
            @property
            def bld(self, b=bld):
                return f"dbms/MDC/STAT/standard/{b}"

            def fetch(self):
                pass
        for params in ({"trdDd": YMD}, {"trdDd": YMD, "idxIndMidclssCd": "01"}):
            try:
                res = Io().read(**params)
            except Exception as e:
                tried.append(f"{bld}:{type(e).__name__}")
                continue
            rows = next((v for v in res.values() if isinstance(v, list)), []) if isinstance(res, dict) else []
            tried.append(f"{bld}:{len(rows)}")
            for row in rows:
                if any("변동성" in str(v) or "VKOSPI" in str(v) for v in row.values()):
                    return 1, [bld, str(row)[:200]]
    raise RuntimeError("못 찾음 " + " ".join(tried))


@check("네이버 VKOSPI 페이지", "시장 온도")
def naver_vkospi():
    r = get("https://m.stock.naver.com/api/index/VKOSPI/basic")
    d = r.json()
    return 1, [d.get("closePrice"), d.get("localTradedAt")]


@check("금융투자협회 통계(신용잔고) 접속", "시장 온도")
def kofia():
    r = get("https://freesis.kofia.or.kr/")
    return 1, [f"{len(r.text)} bytes"]


def main():
    results = []
    for name, group, fn in checks:
        t0 = time.time()
        try:
            count, sample = fn()
            ok = count > 0
            err = "" if ok else "0건"
        except Exception as e:
            ok, count, sample = False, 0, []
            err = f"{type(e).__name__}: {str(e)[:150]}"
            if os.environ.get("PROBE_DEBUG"):
                traceback.print_exc()
        results.append({"group": group, "name": name, "ok": ok, "count": count,
                        "sample": [str(s)[:80] for s in sample], "error": err,
                        "sec": round(time.time() - t0, 1)})
        print(("OK  " if ok else "FAIL"), name, count, err or sample)

    where = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "로컬 PC"
    out = {"where": where, "run_at": TODAY.strftime("%Y-%m-%d %H:%M KST"), "base_date": YMD,
           "results": results}
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## 데이터 출처 시험 ({where}, {out['run_at']}, 기준일 {YMD})\n\n")
            f.write("| 분류 | 출처 | 결과 | 건수 | 초 | 샘플/오류 |\n|---|---|---|---|---|---|\n")
            for r in results:
                note = r["error"] or " / ".join(r["sample"])
                note = note.replace("|", "/")
                f.write(f"| {r['group']} | {r['name']} | {'✅' if r['ok'] else '❌'} | "
                        f"{r['count']} | {r['sec']} | {note} |\n")


if __name__ == "__main__":
    main()
