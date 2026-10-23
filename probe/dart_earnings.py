"""실적 공시 본문에서 영업이익이 늘었는지 줄었는지 읽을 수 있는지 시험 (DART 웹 공시뷰어, 키 없이).

1) 공시 목록에서 실적 관련 제목을 찾고
2) main.do?rcpNo= 화면에서 본문 문서 번호(dcmNo 등)를 뽑아
3) viewer.do 본문 HTML에서 '영업이익' 줄의 숫자를 본다.
"""
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
KEYS = ("잠정)실적", "손익구조")

def get(url, **kw):
    for i in range(4):
        try:
            return requests.get(url, headers=UA, timeout=40, **kw)
        except Exception as e:
            print("  재시도", i + 1, type(e).__name__)
            time.sleep(3 * (i + 1))
    raise RuntimeError("실패")


for ymd in ("2026.10.22", "2026.10.21", "2026.10.20", "2026.10.16"):
    rows = []
    for page in range(1, 8):
        r = get("https://dart.fss.or.kr/dsac001/mainAll.do",
                params={"selectDate": ymd, "currentPage": page, "maxResults": 100, "maxLinks": 10})
        got = re.findall(r"<tr>(.*?)</tr>", r.text, re.S)
        if not got:
            break
        rows += got
        time.sleep(0.5)
    hits = []
    for row in rows:
        m = re.search(r"rcpNo=(\d+)", row)
        rep = re.search(r'title="([^"]+) 공시뷰어', row)
        corp = re.search(r'title="([^"]+) 기업개황', row)
        if m and rep and any(k in rep.group(1) for k in KEYS):
            hits.append((m.group(1), corp.group(1) if corp else "?", re.sub(r"\s+", " ", rep.group(1))))
    print(f"\n######## {ymd}: 실적 관련 {len(hits)}건")
    for rcp, corp, title in hits[:4]:
        print(f"\n== {corp} | {title} | {rcp}")
        try:
            main = get("https://dart.fss.or.kr/dsaf001/main.do", params={"rcpNo": rcp}).text
            v = re.search(r"viewDoc\([\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"'](\d+)[\"'],\s*[\"']([^\"']*)[\"']", main)
            if not v:
                print("viewDoc 못 찾음:", re.findall(r"viewDoc\([^)]*\)", main)[:3])
                continue
            rcpNo, dcmNo, eleId, offset, length, dtd = v.groups()
            doc = get("https://dart.fss.or.kr/report/viewer.do",
                      params={"rcpNo": rcpNo, "dcmNo": dcmNo, "eleId": eleId, "offset": offset,
                                       "length": length, "dtd": dtd}).text
            text = re.sub(r"<[^>]+>", " | ", doc)
            text = re.sub(r"(\s*\|\s*)+", " | ", re.sub(r"&nbsp;", " ", text))
            for kw in ("영업이익", "매출액", "당기순이익"):
                i = text.find(kw)
                print(f"  [{kw}]", text[i:i + 400] if i >= 0 else "없음")
        except Exception as e:
            print("실패", type(e).__name__, e)
        time.sleep(0.5)
