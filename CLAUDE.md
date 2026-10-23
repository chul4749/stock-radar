# 주식 레이더 (stock-radar)

국내 주식 시장 온도와 조건에 맞는 종목(스윙·단타·저평가)을 매일 모아 정적 웹페이지로 보여 주는 개인 프로젝트.
매수·매도 추천이 아니라 "조건에 맞는 종목을 보여 주기"가 목적이다.

## 구조

- `collector/` — 수집기 (Python 3.12)
  - `main.py` — 하루치 결과를 `site/data/{기준일}.json`으로 쓰고 `index.json`, `scorecard.json`을 갱신한다.
    실행: `python -m collector.main [YYYYMMDD]` (기준일을 안 주면 18시 이후는 오늘, 그 전은 전 영업일)
  - `sources.py` — 외부 데이터 접속 (KRX/pykrx, 네이버, 구글 뉴스, DART, CBOE, FRED, CNN, 금융투자협회 FreeSIS)
  - `logic.py` — 시장 온도계, 종목 필터, 위험 공시 제외, 저평가, 성적표 계산,
    장세 판정(`market_regime`: 상승장·횡보·하락장·패닉, 시나리오 적합도 `FIT`, 인버스 점검표).
    `FIT`는 연구 결과(패닉 구간 추세 추종 붕괴, 이동평균 추세 추종, 인버스 매일 재조정 손실)에 기댄 규칙이고,
    성적표의 장세별 기록(`by_regime`)으로 맞는지 계속 확인한다
  - `util.py` — KST, EOK(억), log
- `site/` — 빌드 없는 정적 페이지 (`index.html`, `app.js`, `style.css`), 데이터는 `site/data/`
- `probe/` — 데이터 출처 접속 시험 스크립트. 결과는 `docs/data-sources.md`에 정리
- `tests/test_logic.py` — `python -m pytest -q tests`

## 운영

- 사무실 PC에서는 증권 사이트가 대부분 막혀 있어 **수집은 GitHub Actions에서만** 한다.
- `.github/workflows/collect.yml`: 평일 18:23 KST에 실행(20:47 예비 실행은 그날 데이터가 있으면 건너뜀) → 테스트 → 수집 → `data: YYYY-MM-DD` 커밋 → (설정 시) Pages 배포.
  시작할 때 최신 main을 받고, 저장할 때도 최신 main 위에 새로 만든 파일만 덮어쓴다(다른 실행이 올린 날짜 파일을 지우지 않음).
- `.github/workflows/pages.yml`: `site/` 코드(데이터 제외)가 main에 올라오면 수집 없이 바로 배포.
  수동 실행 때 `dates`에 과거 기준일을 넣으면 다시 계산한다(성적표 채우기용).
- KRX는 로그인 필요: Secrets `KRX_ID`/`KRX_PW`.
- VKOSPI는 경로를 못 찾아 코스피200 20일 실현 변동성으로 대신한다.
- 신용잔고·반대매매는 금융투자협회 FreeSIS(`sources.kofia`)에서 받는다. T+1 공표라 기준일 전날 값이 최신.
- 네이버·CNN은 비공식 경로라 바뀌면 빠질 수 있다. 수집 실패는 `errors`에 남기고 나머지는 계속 진행한다.

## 화면 규칙

- 사용자는 한국어 사용자. 화면 문구는 쉬운 한국어로, 전문 용어에는 ⓘ 설명(`INFO`)과 한 줄 해석(`interpret`)을 붙인다.
- 사용자 설정(탭, '설명 항상 펼치기', 관심 종목)은 `localStorage`에 저장하고, 접근은 항상 try/catch로 감싼다.
- 모바일(390px 폭)에서 먼저 확인한다. 라이트·다크 모두 CSS 변수(`--text`, `--sub`, `--line` 등)를 쓴다.

## 작업 습관

- 커밋 메시지는 한국어로 짧게 (예: "업종 막대 크기를 유입·유출 따로 맞춤").
- 화면을 바꾸면 `cd site && python3 -m http.server`로 띄워 실제로 확인한다.
