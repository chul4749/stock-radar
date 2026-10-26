"""OpenDART 시험: 키가 되는지, 공시 목록·임원 매매·대량보유·자기주식·재무 응답 모양 확인.

키는 Secrets DART_API_KEY (출력에 키를 찍지 않는다).
"""
import json
import os
import time

import requests

KEY = os.environ.get("DART_API_KEY", "").strip()
BASE = "https://opendart.fss.or.kr/api"
print("키 길이", len(KEY))


def call(path, **params):
    t = time.time()
    r = requests.get(f"{BASE}/{path}", params={"crtfc_key": KEY, **params}, timeout=30)
    try:
        d = r.json()
    except ValueError:
        print(f"\n== {path} {r.status_code} JSON 아님 {r.text[:200]}")
        return {}
    print(f"\n== {path} {params} → {r.status_code} status={d.get('status')} {d.get('message')} "
          f"total={d.get('total_count')} pages={d.get('total_page')} ({time.time() - t:.1f}초)")
    return d


def show(d, n=3, keys=None):
    for x in (d.get("list") or [])[:n]:
        print("  ", json.dumps({k: x.get(k) for k in keys} if keys else x, ensure_ascii=False)[:600])


# 1) 하루 공시 목록 (전체, 코스피 Y / 코스닥 K)
d = call("list.json", bgn_de="20261023", end_de="20261023", page_count=100, page_no=1)
show(d, 5)
from collections import Counter
print("  시장", Counter(x.get("corp_cls") for x in d.get("list") or []))
print("  제목 예", [x["report_nm"] for x in (d.get("list") or [])[:25]])
# 2) 종류별: B 주요사항, D 지분공시, A 정기, I 거래소공시
for ty in ("A", "B", "D", "I"):
    dd = call("list.json", bgn_de="20261023", end_de="20261023", pblntf_ty=ty, page_count=100)
    print("  ", ty, Counter(x["report_nm"].split("(")[0].strip() for x in dd.get("list") or []).most_common(12))
# 3) 기간 한꺼번에 (3개월 제한 확인)
d3 = call("list.json", bgn_de="20260701", end_de="20261023", pblntf_ty="D", page_count=100, page_no=1)
# 4) 임원·주요주주 소유 보고 / 대량보유 (회사 하나)
ins = [x for x in (dd.get("list") or [])]
dd = call("list.json", bgn_de="20261019", end_de="20261023", pblntf_ty="D", page_count=100)
ele = next((x for x in dd.get("list") or [] if "임원" in x["report_nm"]), None)
big = next((x for x in dd.get("list") or [] if "대량보유" in x["report_nm"]), None)
if ele:
    print("\n임원 보고 예", ele["corp_name"], ele["corp_code"], ele["rcept_no"], ele["report_nm"])
    e = call("elestock.json", corp_code=ele["corp_code"])
    rows = [x for x in e.get("list") or [] if x.get("rcept_no") == ele["rcept_no"]]
    print("  같은 접수번호 줄", len(rows)); show({"list": rows or e.get("list")}, 3)
if big:
    print("\n대량보유 예", big["corp_name"], big["corp_code"], big["rcept_no"], big["report_nm"])
    m = call("majorstock.json", corp_code=big["corp_code"])
    show(m, 3)
# 5) 자기주식 취득 결정
b = call("list.json", bgn_de="20260901", end_de="20261023", pblntf_ty="B", page_count=100)
tsk = [x for x in b.get("list") or [] if "자기주식취득결정" in x["report_nm"].replace(" ", "")]
print("  자기주식 취득 결정", len(tsk), [x["corp_name"] for x in tsk[:5]])
if tsk:
    t = call("tsstkAqDecsn.json", corp_code=tsk[0]["corp_code"], bgn_de="20260901", end_de="20261023")
    show(t, 2)
# 6) 재무 (삼성전자 00126380) 단일회사 주요계정
f = call("fnlttSinglAcnt.json", corp_code="00126380", bsns_year="2026", reprt_code="11012")
show(f, 6, ["account_nm", "fs_div", "sj_div", "thstrm_amount", "frmtrm_amount", "thstrm_add_amount"])
f = call("fnlttSinglAcnt.json", corp_code="00126380", bsns_year="2025", reprt_code="11014")
print("  2025 3분기 줄", len(f.get("list") or []))
# 7) 여러 회사 한 번에
f = call("fnlttMultiAcnt.json", corp_code="00126380,00164779", bsns_year="2026", reprt_code="11012")
print("  여러 회사 줄", len(f.get("list") or []))
