# 주식 레이더 (stock-radar)

국내 주식 시장 온도와 조건에 맞는 종목(스윙·단타·저평가)을 매일 모아 정적 웹페이지로 보여 주는 개인 프로젝트.
매수·매도 추천이 아니라 "조건에 맞는 종목을 보여 주기"가 목적이다.

## 구조

- `collector/` — 수집기 (Python 3.12)
  - `main.py` — 하루치 결과를 `site/data/{기준일}.json`으로 쓰고 `index.json`, `scorecard.json`을 갱신한다.
    실행: `python -m collector.main [YYYYMMDD]` (기준일을 안 주면 18시 이후는 오늘, 그 전은 전 영업일)
  - `sources.py` — 외부 데이터 접속 (KRX/pykrx, 네이버, 구글 뉴스, DART, CBOE, FRED, CNN, 금융투자협회 FreeSIS)
    공시는 OpenDART(Secrets `DART_API_KEY`, 무료·하루 2만 건)가 있으면 `opendart_list`로, 없거나 실패하면 DART 웹(`dart_web_today`)으로.
    `opendart_signals`: '사는' 공시 — 임원·주요주주 지분 증가(`elestock`), 5% 큰손 장내매수(`majorstock`), 자기주식 취득 결정(주요사항보고서 제목).
    main `update_dart_signals` → `site/data/dart_signals.json`(150일, 처음엔 3개월 거슬러, 회사별 상세 호출 하루 400번까지·남으면 다음 실행).
    속설 검증에 '회사 사람이 사면 오른다'·'자사주를 산다고 하면 오른다'·'큰손이 5% 넘게 사들이면 오른다', 카드 `dsTag`·검색 팝업 `dsLines`(최근 10영업일).
  - `logic.py` — 시장 온도계, 종목 필터, 위험 공시 제외, 저평가, 성적표 계산,
    장세 판정(`market_regime`: 상승장·횡보·하락장·패닉, 시나리오 적합도 `FIT`, 인버스 점검표).
    `FIT`는 연구 결과(패닉 구간 추세 추종 붕괴, 이동평균 추세 추종, 인버스 매일 재조정 손실)에 기댄 규칙이고,
    성적표의 장세별 기록(`by_regime`)으로 맞는지 계속 확인한다.
    정렬 점수: 스윙 `시총 대비 5일 순매수 × (1+연속일/5)`, 단타 `scalp_score`(큰손 확인·거래 증가·상승폭 0~100 가중평균, 과열 감점, `score_ver` 3),
    저평가 `value_score`(낙폭·싸짐·PER·이익 0~100 가중평균, `score_ver` 2). 화면의 강도는 역대 같은 탭 후보 중 백분위,
    성적표 `score_check`(같은 날 점수 위 절반 vs 아래 절반)로 점수가 쓸모 있는지 확인한다.
    저평가 이익 항목은 네이버 추정 EPS가 있으면 반반 섞는다(`apply_forward_eps`).
    저평가는 가치 함정(관심 없으면 몇 년이고 싼 채로 남음) 때문에 '주목 신호'(`value_wake`: 큰손 5일 매수·3일 소식·거래 증가·10일 바닥 반등)를
    점수와 따로 보여 주고, 성적표 `VALUE_FEATURES`(저평가 조건별 성적)로 정말 나은지 확인한다.
    후보마다 정리 가격(`exit_level`: 스윙 20일 평균, 단타 걸린 날 저가, 저평가 10일 최저가)을 붙이고,
    성적표 `stops`(`stop_scorecard`)로 그 기준을 지켰을 때와 그냥 들고 있을 때를 비교한다.
    실적 시즌(`report_season`)에는 DART 목록으로 이번 분기 실적을 낸 회사를 `site/data/reported.json`에 모아 아직 안 낸 후보에 경고(`earn_season`).
    증권사 리포트(네이버 리서치, `sources.naver_research`)는 `site/data/reports.json`에 150일 보관. `report_attention`: 최근 2주 건수, 그 전 석 달 없었으면 '오랜만에 다뤄짐'(참고 정보 — 오르는 신호로 검증 안 됨),
    최신 리포트 상세(의견·목표가·요약). 네이버 검색 상위 100(`search_rank`)은 '관심 집중' 표시일 뿐, 순위만으로 늦었다고 결론 내지 않는다.
    리포트는 저평가 주목 신호 5번째(`add_report_wake`)·조건별 성적(`report`, `report_fresh`, `search_top`)에도 들어간다.
    `site/data/stocks.json`은 전체 종목 찾기용(`stock_index`): 종목마다 탭별 조건 통과 여부를 1/0 문자열로(문구는 `labels`에 한 번).
    검색 팝업용 전 종목 60일 종가·20일 큰손 순매수는 `site/data/charts/{코드 글자 합 % 16}.json`(`_shard` = app.js `shardOf`)(`write_chart_shards`, 최신 기준일만),
    재무는 `site/data/fin/{같은 번호}.json`(`fetch_profiles`: 없는 종목은 전부, 있으면 오래된 것부터 `FIN_BATCH`개, `FIN_WORKERS`개 동시·`FIN_BUDGET`초 한도).
    둘 다 main에 안 넣고(.gitignore) `data-cache` 브랜치에 커밋 하나로 덮어써 보관 — collect·morning·pages 워크플로가 받아서 배포에 넣는다.
    조건 목록은 `scalp_checks`·`swing_checks`·`value_checks` 하나로 필터와 검색이 같이 쓴다.
    급등주 복기(`find_movers`·`replay_mover`, `SURGE`): 급등 = **하루 +20% 이상**(사용자 정의, 5일 누적은 급등 아님). 최근 5일 중 그런 날이 있는 종목을 그날 상승률 순으로 30개까지,
    15영업일 거슬러 큰손 순매수·거래 배수·리포트·우리 후보 여부와 '미리 잡음/당일/놓침'. 하루 +30.5% 넘는 값(가격 끊김)은 제외.
    신호 검증(`signal_validation`)도 같은 정의: 신호 뒤 5일 안에 하루 +20% 난 날이 있었나. `move_vs_atr`: 급등한 하루 상승률이 급등 전 평소 하루 변동폭(ATR14 %)의 몇 배인지(ⓘ `move_x`).
    급등 뒤 흐름(`follow_through`, `FOLLOW`): 하루 +20%↑(급등)·+8~20%(급등 아님, 비교용)·이틀 연속 급등 뒤 다음 날도 오름·3일 안 또 +20%·하루 쉬고 다시 +5% 비율과
    다음 날 시가 진입 3·5일 수익(비용 뺌)을 평소(모든 종목·날)와 비교. 최근 약 80영업일, 매일 다시 계산.
    `splits`: 하루 +20%↑ 급등을 그날 모양(종가 고가 근처·상한가·첫 급등·거래 배수·큰손·시총)으로 나눠 어떤 급등이 더 이어졌나 — '급등 이어짐' 필터 조건을 고를 근거
    급등 뒤 보유(`hold_after_surge`, `HOLD`): 가진 종목이 하루 +20%↑ 급등한 날 종가 기준 10일 — 최고 종가가 나온 날, 다 돌려준 비율, 팔기 방법 비교
    (다음 날 시가·종가·3/5/10일·전날 저가 깨지면·최고가 −7%). 묶음 전체·상한가 근처(+29%↑)·거래 폭발·조용한 첫 급등 (예전 기록은 +8%/+15% 기준 — `holdDesc`). `follow.hold`, 화면 '가진 종목이 급등했다면'·검색 팝업 `surgeNote`.
    매물대(`volume_profile`): 60일 거래대금을 저가~고가에 펼친 가격대별 비중·현재가 아래 비중·평균 매수가 → 차트 shard `vp`, 검색 팝업 `vpBlock`(공부 자료, ⓘ `vp`).
    속설 검증(`myth_check`, `MYTH`): 매물대 돌파·60일 신고가·갭 메움·5일 연속 하락·관심 과열(거래 폭발·리포트 쏟아짐·검색 상위)·소문에 사서 뉴스에 팔아라(실적 공시·오랜만의 리포트 전 10일 +10%↑ vs 아님).
    판정은 통으로 보지 않음: 다음 날 시가 진입 3일 수익에서 같은 날 전 종목 평균을 뺀 초과(`exc`), 위아래 5% 뺀 평균(`_trim_mean`), 이긴 비율,
    쏠림(위 5종목 몫 `top5`·하루 최대 몫 `day_max`)·기간 앞뒤(`halves`) → `shaky_*`, 나눠 보기(`seg`: 코스피/코스닥·대중소형).
    우연인가: 날짜별 평균으로 묶은 t(`t3`, |t|≥2) — 넘으면 크기에 따라 true/false 또는 `weak_*`(작지만 꾸준), 못 넘으면 none(근거 부족).
    상승장 때·하락장 때(`by_mk`: 전 종목 평균 흐름이 20일 평균 위/아래인 날) 따로 판정, 화면 맨 위 요약표.
    갭은 '마감 때도 벌어진 갭 vs 갭 없이 같은 만큼 벌어진 날'의 다음 4일 복귀율로 비교. 검색 상위는 `attention.json`에 쌓는 중(`update_attention`). 화면 '속설'(`renderMyths`)·검색 팝업 `mythNow`.
    신호 검증(`signal_validation`): 큰손 3일 연속·조용한 거래 증가·5일 순매수 덩치 0.1%↑가 뜬 뒤 5일 안 하루 +20% 비율 vs 평소. 일별 수급은 25영업일 받음(`flows_hist`). 성적표 `movers`(`mover_scorecard`)로 누적.
    `site/data/prices.json`은 가장 최근 기준일 전 종목 종가(관심 종목 ★ 이후 수익률용).
    성적표는 다음 날 시가 진입·N영업일째 종가 청산, 거래 비용 `COST` 차감, 종목이 속한 시장 지수 대비,
    같은 탭·같은 종목이 N일 안에 다시 걸리면 한 번만 센다. 조건별 성적도 지수 대비 초과로 비교한다.
    코스닥 글로벌도 코스닥 지수와 비교(`mkt_of`). 보유 중 가격이 끊긴(권리락·분할, `price_breaks`: 종가 변화 ≠ KRX 등락률) 거래는 뺀다.
    기록 출처: 기준일 15:30~다음 평일 09:00에 만든 기록만 실제 기록(`record_origin` live), 그 밖은 recalc, 만든 시각 없으면 unknown(`classify_record`).
    날짜 파일에 `record`(origin·`strategy_ver`·`data_asof`)를 남기고, live면 `site/data/live/{기준일}.json`(`write_live`)에 따로 보관 — 재계산은 여기에 절대 안 씀.
    성적표는 live 파일이 있으면 그 후보를 쓴다(`load_record`). 재계산(실제 기록이 아닌 실행)엔 '지금' 값인 추정 EPS·기업 정보·검색 순위를 안 쓴다(`live_now`, 미래 정보 차단).
    `filter.pools`: 조건 통과 후보 전체 최소 기록(`pool_record`: 순위·점수 재료 `k`·종가·상대강도·ATR) — 화면은 상위 10개.
    후보 변화(`build_changes`·`candidate_changes`): 어제 상위 10개 대비 새로/유지, 점수 변화를 점수 재료별로 나눔(`score_change`, 원인 지어내지 않음),
    빠진 종목은 순위 밖(`rank`) / 조건 깨짐(`filter`, `failed_conditions`) / 추적 불가(`unknown`), 우리 기록상 처음 걸린 날(`first_seen`)과 그 뒤 주가(가격 끊기면 추적 불가).
    상대강도(`rs_pair`·`rel_strength`): 20·60영업일 종목 − 그 시장 지수, 같은 날짜, 빈 날·가격 끊김이면 안 함. 점수·필터엔 안 넣는다.
    업종 대비(`sector_rs`, `sources.sector_indices`): 업종 분류와 이름이 같은 KRX 업종 지수(probe/sector_index.py로 확인: 지수 구성은 100% 그 업종), 구성 종목이고 지수 종목 10개 이상일 때만.
    진입 위험(`atr_wilder` 와일더 ATR14, `entry_risk`): 기준일 종가 → 정리선 거리 %와 ATR 배수. 최대·예상 손실이라고 쓰지 않는다.
    정리선은 걸린 날 값으로 고정(스윙도 걸린 날 20일 평균). `stop_scorecard`: 정리선 아래 마감 → 다음 거래되는 날 시가에 팔고 끝날까지 현금,
    끝날까지 못 팔면 끝날 종가. 나쁜 10% 평균(`low10`)·거래별 최대 하락(`dd`, 포트폴리오 MDD 아님)·정리선 대비 체결(`fill_gap`).
    `scorecard_extra`: 실제/재계산 따로(`by_origin`), 상위 10 vs 전체(`scope`), 상대강도 위/아래(`rs`), 단타 거래 배수 구간(`mult`), 단타 평소 변동폭 구간(`vol`, `VOL_BUCKETS` — 하한선 필터는 근거 쌓인 뒤 따로),
    점수 공식 정한 기간 vs 검증 기간(`periods`, `STRATEGY_SINCE`) — 30건 미만이면 '근거 부족'/'개선 효과 미확인', 공식 자동 교체 없음.
    공식이나 기준을 바꾸면 화면의 '필터 기준 보기'(`renderRules`)와 ⓘ 설명(`INFO`)도 같이 고친다
  - `etf.py` — 테마 → ETF 표(`THEMES`, 레버리지·인버스 제외). 수집 때 ETF 시세(KRX `etf_history`)와 구성 상위 10종목(`etf_holdings`, KRX→네이버)을 받아
    `logic.theme_strength`로 판정(돈이 먼저 들어오는 중·좋음·과열·큰손 매도·약함), 성적표 `themes`(`theme_scorecard`, KODEX 200 대비)로 확인
    `ACTIVE`(국내 주식형 액티브 ETF 30개): 오늘 구성과 5·20영업일 전 구성(KRX `etf_pdf`, 계약수=설정 단위당 수량이라 매니저 매매만 반영)을 비교 →
    `holdings_diff`(새로 담음·늘림·줄임·모두 뺌, 10% 이상) → `manager_moves`(종목별로 몇 곳이 같은 방향). 20일 2곳 이상 매수는 성적표 그룹 `mgr`
    `enrich_manager_moves`(계속 사는 중·비중 3% 이상·그 뒤 주가·투신 5일·잘하는 운용사 → 정렬 점수), `manager_skill`(운용사별 액티브 ETF 60일 성적 − KODEX 200), `sector_tilt`(늘리는·줄이는 업종)
    히트맵(`theme_heat`): 테마 × 최근 20일, 날마다 구성 종목 큰손 순매수(시총 대비 %)와 대표 ETF 등락. 화면은 '큰손 돈/ETF 가격' 전환, 빨강·파랑 + 회색 0(시총 히트맵은 대형주에 묻혀 안 씀)
  - `morning.py` — 아침 요약: 야후 차트로 미국장 마감(EWY·나스닥·S&P·반도체·VIX·환율) → `site/data/morning.json` (`logic.morning_summary`)
  - `util.py` — KST, EOK(억), log
- `site/` — 빌드 없는 정적 페이지 (`index.html`, `app.js`, `style.css`), 데이터는 `site/data/`
- `probe/` — 데이터 출처 접속 시험 스크립트. 결과는 `docs/data-sources.md`에 정리
- `tests/test_logic.py` — `python -m pytest -q tests`

## 운영

- 사무실 PC에서는 증권 사이트가 대부분 막혀 있어 **수집은 GitHub Actions에서만** 한다.
- `.github/workflows/collect.yml`: 평일 18:23 KST에 실행(20:47 예비 실행) → 테스트 → 수집 → `data: YYYY-MM-DD` 커밋 → (설정 시) Pages 배포.
  시작할 때 최신 main을 받고, 저장할 때도 최신 main 위에 새로 만든 파일만 덮어쓴다(다른 실행이 올린 날짜 파일을 지우지 않음).
- `.github/workflows/morning.yml`: 평일 07:41 KST 아침 요약만 갱신·배포. 화면은 가장 최근 수집일을 볼 때, 그 뒤 아침에 만든 요약만 보여 준다.
- 예약 실행은 모두 `IF_MISSING`(그날 데이터 있으면 건너뜀). 외부 예약(cron-job.org)은 `if_missing=1`로 부른다 — `docs/external-schedule.md`.
- `.github/workflows/pages.yml`: `site/` 코드(데이터 제외)가 main에 올라오면 수집 없이 바로 배포.
  수동 실행 때 `dates`에 과거 기준일을 넣으면 다시 계산한다(성적표 채우기용).
- KRX는 로그인 필요: Secrets `KRX_ID`/`KRX_PW`. OpenDART는 `DART_API_KEY` (collect·credit-probe에 전달).
- VKOSPI는 경로를 못 찾아 코스피200 20일 실현 변동성으로 대신한다.
- 신용잔고·반대매매는 금융투자협회 FreeSIS(`sources.kofia`)에서 받는다. T+1 공표라 기준일 전날 값이 최신.
- 외국인 코스피200 선물 순매수는 다음 금융(`sources.daum_futures`), 종목 기업 정보는 네이버(`sources.naver_profile`).
- `probe/` 시험은 `credit-probe` 워크플로에 스크립트 이름(`script`)을 넣어 돌린다 (이 컨테이너에선 증권 사이트 접속이 막힘).
- `main()`은 `_limit_requests_timeout(60)`(+`socket.setdefaulttimeout`): requests는 timeout=None이면 무한정 기다려 pykrx·DART가 끊긴 연결에서 16분씩 멈췄음 → 60초 실패→재시도.
- 네이버·CNN은 비공식 경로라 바뀌면 빠질 수 있다. 수집 실패는 `errors`에 남기고 나머지는 계속 진행한다.

## 화면 규칙

- 맨 위 '오늘의 결론'(`verdictBox`)과 카드 첫 줄(`whyLine`)은 숫자 없이 '그래서 뭘 하라는지'를 쉬운 말로. 1년 백분위는 '1년 중 높은 편'처럼 말로(`pctWord`).
- '지금 상태'와 '설명'을 섞지 않는다: 상태 줄(`.tile .why`, `.sum .why`, `.interp`)엔 '지금' 머리표, 카드 첫 줄은 '걸린 이유', ⓘ 설명(`.exp`)은 점선 상자에 '📖 설명 — 원래 뜻',
  색 막대는 현재 위치에 '지금' 표시(아래 글자는 눈금), 일반 원리는 '원리'/'원칙' 표시, 실제 성적은 '실제 기록'.
- 한두 글자 차이로 줄바꿈되는 짧은 글(라벨·표 머리·체크 항목 등 `FIT_SEL`)은 `fitAll`이 글자를 최대 85%까지 줄여 한 줄로 맞춘다. 더 길면 원래대로 줄바꿈.
- 팝업(`#detail`)은 `openDialog`로 연다: 높이는 `100dvh` 기준(휴대폰 주소창 때문에 위가 잘리지 않게), 열려 있는 동안 `html.modal-open`으로 뒤 화면 고정.
  격자 목록(`.fit`)은 `minmax(0,1fr)`+`overflow-wrap:anywhere`로 긴 이름이 화면 밖으로 밀지 않게. 화면 확인 땐 접힌 칸(details)도 다 열어 가로 넘침을 본다.
- 사용자는 한국어 사용자. 화면 문구는 쉬운 한국어로, 전문 용어에는 ⓘ 설명(`INFO`)과 한 줄 해석(`interpret`)을 붙인다.
- 사용자 설정(탭, '설명 항상 펼치기', 관심 종목)은 `localStorage`에 저장하고, 접근은 항상 try/catch로 감싼다.
- 배치: 오늘 한눈에(시장 상태·회복 단계 → 어제와 달라진 후보 → 먼저 볼 후보, 나머지는 '더 보기') → 종목 필터 → 시장(전환·온도·장세) → 돈 흐름(업종·테마·운용사) → 급등 복기(카드만) → 성적표 → 연구 자료(급등 뒤 흐름·신호 검증·속설 검증).
  카드 핵심 순서: 걸린 이유 → 어제와 비교(`trackLine`) → 시장 대비 힘(`rsLine`) → 큰손 흐름 → 정리선 상자(`exitRisk`: 정리선 가격·뜻 → 지금 가격보다 몇 % 아래·평소 하루 움직임의 몇 배 → "내가 산(살) 가격 넣어 보기" 즉시 다시 계산, 주문 기능 없음).
  '사도 되는 신호'는 '시장 회복 확인 단계'(`SIG_STATE`, 상태만 — 몇 분의 몇 사라는 안내 없음). 시장 온도는 보조 요약, 빠진 지표(`thermo.missing`)를 보여 준다. 위에 붙은 줄(`.bar`)에 찾기 칸과 바로 가기(`updateJump`).
  긴 목록은 앞 몇 개만 보이고 나머지는 '더 보기'(테마 4·운용사 5·급등주 4).
- 몇 개 안 되는 것끼리 비교(평소 vs 경우, 탭별, 위/아래 절반, 들고 있기/정리, 속설 전체/상승장/하락장)는 세로 막대 나란히 `colBars`(묶음·범례·0선), 이름이 긴 여러 줄 목록만 가로 막대.
- 성적·검증 숫자는 표 대신 그림 부품으로: `divBars`(0 가운데 좌우 막대, 오른쪽 빨강=나음), `rateDots`(100번 중 몇 번 막대, 맨 위 회색 '평소' 줄과 길이 비교), `forest`(속설 막대, 운으로 보기 어려운 차이만 색·나머지 회색 '근거 부족'),
  `flowPath`(급등일 종가 0 기준 흐름선). 원래 표는 `asTable`로 '숫자 표로 보기'에 접어 둔다. 30건 미만은 흐리게 하지 말고 '⚠ 아직 N건뿐 — 운일 수 있음'을 글로, 비교 기준 줄은 '(비교 기준)'. 속설은 속설대로인 방향을 오른쪽으로 맞춤(`mythPoint`).
  그림 위엔 데이터로 만든 '그래서?' 한 줄(`take`, 👉). 문구는 시가→'다음 날 아침 첫 가격', 종가→'마감 가격', 가운데 값→'보통', 우연 범위→'운으로도 나올 수 있는 폭',
  판정 none→'근거 부족'처럼 풀어 쓴다. **%는 주가가 오르고 내린 정도에만** 쓰고, 비율(몇 번 중 몇 번)은 '100번 중 34번'처럼 횟수로 쓴다(`rateDots` 값은 '번').
  비교(초과)는 화면에선 '%p' 대신 '~보다 몇 % 더'로. 그림 제목 앞엔 `kindTag`([주가 변화]/[비교]), 값 아래엔 무엇과 비교했는지(`divBars`·`forest`의 `vs`: 주가·지수보다·평균보다 등). 고르는 버튼(급등 묶음 등)은 바로 아래에 고른 것의 뜻(`HOLD_DESC`)을 한 줄로 보여 준다.
- 모바일(390px 폭)에서 먼저 확인한다. 라이트·다크 모두 CSS 변수(`--text`, `--sub`, `--line` 등)를 쓴다.

## 작업 습관

- 커밋 메시지는 한국어로 짧게 (예: "업종 막대 크기를 유입·유출 따로 맞춤").
- 화면을 바꾸면 `cd site && python3 -m http.server`로 띄워 실제로 확인한다.
