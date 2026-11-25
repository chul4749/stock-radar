"""미국 경제지표 발표 일정 출처 시험: 연준 FOMC 달력, BLS CPI·고용 일정, FRED 발표 달력.
credit-probe 워크플로에서 script=macro_calendar 로 돌린다 (이 컨테이너에선 막힘)."""
import re
import requests

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def show(name, url, ua=True, n=1500):
    try:
        r = requests.get(url, headers=BROWSER if ua else {}, timeout=30)
        print(f"\n=== {name} {r.status_code} {len(r.text)}B ua={ua}")
        return r.text if r.ok else ""
    except Exception as e:
        print(f"\n=== {name} 실패 {e}")
        return ""


# 1. 연준 FOMC 달력
t = show("FOMC", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
for y in re.findall(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>', t)[:3]:
    print("연도:", y)
for m, d in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', t, re.S)[:24]:
    print("  ", m.strip(), "|", d.strip())

# 2. BLS 일정 (봇 차단이 잦음)
for name, url in [("BLS CPI", "https://www.bls.gov/schedule/news_release/cpi.htm"),
                  ("BLS 고용", "https://www.bls.gov/schedule/news_release/empsit.htm"),
                  ("BLS ics", "https://www.bls.gov/schedule/news_release/bls.ics")]:
    t = show(name, url)
    rows = re.findall(r"<td>([A-Z][a-z]+ \d{4})</td>\s*<td>([A-Z][a-z]{2}\.? \d{1,2}, \d{4})</td>\s*<td>([^<]+)</td>", t)
    for r in rows[:30]:
        print("  ", r)
    if "BEGIN:VEVENT" in t:
        ev = re.findall(r"DTSTART[^:]*:(\d{8}T?\d*)\s.*?SUMMARY:([^\r\n]+)", t, re.S)
        for d, s in ev:
            if "Consumer Price Index" in s or "Employment Situation" in s:
                print("  ", d, s)

# 3. FRED 발표 달력 (CPI rid=10, 고용 rid=50) — HTML
for rid, name in [(10, "FRED CPI"), (50, "FRED 고용")]:
    for y in (2026, 2027):
        t = show(f"{name} {y}", f"https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={y}", ua=False)
        ds = sorted(set(re.findall(r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}", t)))
        print("  ", ds[:30])

# 4. FRED 시계열 (키 없이)
for sid in ["DFEDTARU", "DGS2", "DGS10", "T10Y2Y", "CPIAUCSL", "CPILFESL", "PCEPILFE", "UNRATE"]:
    t = show(sid, f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", ua=False)
    lines = t.strip().splitlines()
    print("  ", lines[:1], lines[-3:], len(lines))
