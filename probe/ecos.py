"""한국은행 ECOS 시험: 기준금리·국고채 3년·소비자물가 통계 코드 확인.
credit-probe 워크플로에서 script=ecos (Secrets ECOS_API_KEY)."""
import os
import requests

KEY = os.environ.get("ECOS_API_KEY", "")
print("키 있음:", bool(KEY), len(KEY))
B = f"https://ecos.bok.or.kr/api"


def search(code, cycle, start, end, item=""):
    url = f"{B}/StatisticSearch/{KEY}/json/kr/1/10000/{code}/{cycle}/{start}/{end}/{item}"
    r = requests.get(url, timeout=30)
    j = r.json()
    rows = (j.get("StatisticSearch") or {}).get("row") or []
    print(f"\n=== {code} {cycle} {item} → {r.status_code} {len(rows)}행", j.get("RESULT", ""))
    for x in rows[:2] + rows[-3:]:
        print("  ", x.get("TIME"), x.get("ITEM_NAME1"), x.get("ITEM_CODE1"), x.get("DATA_VALUE"), x.get("UNIT_NAME"))
    return rows


def items(code):
    url = f"{B}/StatisticItemList/{KEY}/json/kr/1/200/{code}"
    j = requests.get(url, timeout=30).json()
    rows = (j.get("StatisticItemList") or {}).get("row") or []
    print(f"\n--- 항목 {code}: {len(rows)}개", j.get("RESULT", ""))
    for x in rows[:40]:
        print("  ", x.get("ITEM_CODE"), x.get("ITEM_NAME"), x.get("CYCLE"), x.get("START_TIME"), x.get("END_TIME"))


search("722Y001", "D", "20250101", "20261125", "0101000")      # 한국은행 기준금리
search("722Y001", "M", "202301", "202611", "0101000")
search("817Y002", "D", "20261001", "20261125", "010200000")    # 국고채 3년
search("817Y002", "D", "20261001", "20261125", "010210000")    # 국고채 10년
search("901Y009", "M", "202201", "202611", "0")                # 소비자물가지수 총지수
items("817Y002")
items("901Y009")
items("901Y010")
