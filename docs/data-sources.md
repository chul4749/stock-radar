# 데이터 출처 시험 결과 (2026-10-22, GitHub Actions)

사무실 PC에서는 증권 사이트 대부분이 막혀 있어 수집은 GitHub Actions에서만 한다.
시험 스크립트: `probe/probe.py`, 워크플로 `probe` (수동 실행).

| 용도 | 출처 | 결과 | 비고 |
|---|---|---|---|
| 뉴스 | 구글 뉴스 RSS | ✅ | 키 없음, 검색어별 100건 |
| 뉴스 | 네이버 주요뉴스 `m.stock.naver.com/api/news/list` | ✅ | 비공식 |
| 뉴스 | 네이버 종목 뉴스 `m.stock.naver.com/api/news/stock/{code}` | ✅ | 비공식 |
| 공시 | DART 웹 목록 | ✅ | 정식은 OpenDART 키(무료) 권장 |
| 전종목 시세 | 네이버 `api/stocks/marketValue/{KOSPI,KOSDAQ}` | ✅ | 코스피 2,494·코스닥 1,823 (ETF 포함), 100건/쪽 |
| 일봉 | 네이버 `api.finance.naver.com/siseJson.naver` | ✅ | |
| 기관·외국인 | 네이버 `api/stock/{code}/trend` | ✅ | 종목별, 일자별 순매수 수량 |
| PER·PBR·52주 | 네이버 `api/stock/{code}/integration` | ✅ | |
| 미국 VIX | CBOE CSV | ✅ | FRED VIX는 시간초과 |
| 하이일드 스프레드 | FRED csv (UA 없이, 60초) | ✅ | 브라우저 UA를 붙이면 시간초과 |
| 공포·탐욕 | CNN graphdata | ✅ | 비공식 |
| 코스피200 지수 | 네이버 `api/index/KPI200/basic` | ✅ | |
| VKOSPI | 네이버·pykrx·KRX 추정 경로 | ❌ | 로그인 후에도 경로 못 찾음 (주가지수 목록에 없음, 파생상품지수 메뉴 경로 미확인) |
| KRX 전체 (pykrx) | data.krx.co.kr | ✅ | 회원 로그인 필요 → Secrets `KRX_ID`/`KRX_PW` 등록 후 성공 (전종목 2,867·투자자별 순매수 2,598·PER/PBR 2,714) |
| 옛 네이버 금융 페이지 | finance.naver.com/item, sise, news | ❌ | Npay 증권 신형 화면으로 바뀌어 표를 못 읽음 |
| 신용잔고·반대매매 | 금융투자협회 FreeSIS `meta/getMetaDataList.do` | ✅ | 화면 GET으로 세션 쿠키 받은 뒤 JSON POST. `STATSCU0100000070BO` 신용공여 잔고(TMPV2 융자 전체·3 코스피·4 코스닥), `STATSCU0100000060BO` 증시자금(TMPV5 미수금·6 반대매매·7 비중%). 백만 원, T+1 공표 (2026-10-23 시험, `probe/credit.py`) |
