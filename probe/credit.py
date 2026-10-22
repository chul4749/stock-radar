"""신용잔고·반대매매 출처(금융투자협회 FreeSIS) 응답 모양 확인.

화면 주소로 먼저 접속해 세션 쿠키를 받은 뒤, 통계 ID로 JSON을 POST 한다.
- STATSCU0100000070BO: 신용공여 잔고 추이 (신용거래융자 코스피·코스닥 등)
- STATSCU0100000060BO: 증시자금 추이 (예탁금, 미수금, 반대매매 등)
열 이름(TMPVn)이 무엇인지 알 수 없어서, 화면 HTML의 표 머리글과 최근 행을 그대로 찍어 본다.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
BASE = "https://freesis.kofia.or.kr"
TABLES = {
    "STATSCU0100000070BO": "신용공여 잔고 추이",
    "STATSCU0100000060BO": "증시자금 추이",
}

end = datetime.now(KST)
start = end - timedelta(days=20)
s = requests.Session()
s.headers.update(UA)

for obj, name in TABLES.items():
    sid = obj[:-2]
    page = f"{BASE}/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId={sid}"
    print(f"\n===== {name} ({obj})")
    try:
        r = s.get(page, timeout=30)
        print("화면", r.status_code, len(r.text), "bytes, 쿠키", list(s.cookies.keys()))
        # 화면 소스에 들어 있는 머리글 후보(한글 문구)
        heads = sorted(set(re.findall(r"[가-힣][가-힣()·/ ]{1,25}", r.text)))
        print("한글 문구:", heads[:200])
        for m in re.finditer(r"(?i)(getMetaDataList|OBJ_NM|tmpV\d+|\.do['\"])", r.text):
            print("  단서:", r.text[max(0, m.start() - 120):m.end() + 120].replace("\n", " "))
    except Exception as e:
        print("화면 실패", type(e).__name__, e)

    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                         "tmpV45": start.strftime("%Y%m%d"), "tmpV46": end.strftime("%Y%m%d"),
                         "OBJ_NM": obj}}
    try:
        r = s.post(f"{BASE}/meta/getMetaDataList.do", json=body, timeout=30,
                   headers={"Referer": page, "Content-Type": "application/json; charset=UTF-8",
                            "X-Requested-With": "XMLHttpRequest"})
        print("데이터", r.status_code, r.headers.get("content-type"), len(r.text), "bytes")
        try:
            d = r.json()
        except ValueError:
            print("JSON 아님:", r.text[:800])
            continue
        print("키:", list(d.keys()))
        rows = d.get("ds1") or []
        print("행 수:", len(rows))
        for row in rows[:5]:
            print(json.dumps(row, ensure_ascii=False))
        for k, v in d.items():
            if k != "ds1":
                print(k, "=", json.dumps(v, ensure_ascii=False)[:1500])
    except Exception as e:
        print("데이터 실패", type(e).__name__, e)
