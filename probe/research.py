"""증권사 리포트·주목도 출처 시험.

- 네이버 리서치 종목분석 목록(HTML)과 본문 요약, 모바일 API 후보
- 네이버 검색 상위 종목(주목도)
"""
import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def show(url, n=1500, enc=None):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if enc:
            r.encoding = enc
        print("\n==", url, r.status_code, len(r.text), r.headers.get("content-type"))
        try:
            print(json.dumps(r.json(), ensure_ascii=False)[:n])
        except ValueError:
            return r.text
    except Exception as e:
        print("\n== 실패", url, type(e).__name__, e)
    return None


html = show("https://finance.naver.com/research/company_list.naver", enc="euc-kr")
if html:
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
    print("행", len(rows))
    shown = 0
    for row in rows:
        if "company_read" not in row:
            continue
        txt = re.sub(r"<[^>]+>", "|", row)
        print(re.sub(r"[\s|]+", " | ", txt)[:300])
        print("  링크:", re.findall(r'href="([^"]+)"', row)[:3])
        shown += 1
        if shown >= 5:
            break
    m = re.search(r'company_read\.naver\?nid=(\d+)[^"]*', html)
    if m:
        body = show("https://finance.naver.com/research/" + m.group(0).replace("&amp;", "&"), enc="euc-kr")
        if body:
            for key in ("view_cnt", "목표가", "투자의견", "class=\"view_sbj\"", "td class=\"view_cnt\""):
                i = body.find(key)
                print(key, "→", re.sub(r"<[^>]+>|\s+", " ", body[i:i + 600])[:400] if i >= 0 else "없음")
for url in ("https://m.stock.naver.com/api/research/company?page=1&pageSize=5",
            "https://m.stock.naver.com/api/stock/005930/research?page=1&pageSize=5",
            "https://m.stock.naver.com/front-api/research/list?category=company&page=1&pageSize=5",
            "https://m.stock.naver.com/api/stocks/searchTop?page=1&pageSize=10",
            "https://m.stock.naver.com/api/stocks/popular?page=1&pageSize=10",
            "https://m.stock.naver.com/front-api/stocks/searchTop?page=1&pageSize=10"):
    show(url, 1200)
top = show("https://finance.naver.com/sise/lastsearch2.naver", enc="euc-kr")
if top:
    names = re.findall(r'class="tltle">([^<]+)</a>', top)
    print("검색 상위:", names[:30])
