"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const signed = (v, d = 1, unit = "") => (v == null ? "–" : (v > 0 ? "+" : "") + num(v, d) + unit);
const cls = (v) => (v == null ? "na" : v > 0 ? "up" : v < 0 ? "down" : "");
const ymd = (s) => `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}`;

let state = { data: null, tab: "swing", score: null, expAll: false, watch: {}, watchFrom: {}, prices: null, morning: null, latest: null };
try {
  state.tab = localStorage.getItem("tab") || "swing";
  state.expAll = localStorage.getItem("expAll") === "1";
  state.watch = JSON.parse(localStorage.getItem("watch") || "{}") || {};
  state.watchFrom = JSON.parse(localStorage.getItem("watchFrom") || "{}") || {};   // ★ 누른 날 가격 {코드: {price, date, kospi}}
} catch (e) { /* 저장 불가 환경 */ }

async function load(date) {
  const r = await fetch(`data/${date}.json`, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${date} 데이터를 불러오지 못했습니다`);
  state.data = await r.json();
  render();
}

async function init() {
  try {
    const r = await fetch("data/index.json", { cache: "no-cache" });
    const idx = await r.json();
    const sel = $("#date");
    sel.innerHTML = idx.dates.map((d) => `<option value="${d}">${ymd(d)}</option>`).join("");
    sel.onchange = () => load(sel.value);
    state.latest = idx.latest;
    setupSearch();
    await load(idx.latest);
    fetch("data/scorecard.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
      .then((sc) => { state.score = sc; renderScore(); renderRegime(); renderTurn(); }).catch(() => renderScore());
    fetch("data/prices.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
      .then((p) => { state.prices = p; renderWatch(); }).catch(() => {});
    fetch("data/morning.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
      .then((m) => { state.morning = m; renderMorning(); }).catch(() => {});
  } catch (e) {
    $("#cards").innerHTML = `<div class="empty">${esc(e.message || "데이터가 아직 없습니다")}</div>`;
  }
  const ea = $("#exp-all");
  ea.checked = state.expAll;
  ea.onchange = () => {
    state.expAll = ea.checked;
    try { localStorage.setItem("expAll", state.expAll ? "1" : "0"); } catch (e) { /* 무시 */ }
    applyExpAll();
  };
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => {
      state.tab = b.dataset.tab;
      try { localStorage.setItem("tab", state.tab); } catch (e) { /* 무시 */ }
      renderFilter();
    };
  });
}

function render() {
  const d = state.data;
  const gen = d.generated_at.replace("T", " ").slice(0, 16);
  $("#meta").textContent = `기준일 ${ymd(d.base_date)} · 업데이트 ${gen}` + (d.backfill ? " · 나중에 다시 계산한 기록" : "");
  renderTemp();
  renderSectors();
  renderRegime();
  renderFilter();
  renderToday();
  renderTurn();
  renderRules();
  renderMorning();
  const n = d.filter.news_count;
  $("#sources").textContent = `데이터: KRX(시세·수급), 구글 뉴스 ${n.google}건 · 네이버 ${n.naver}건 · DART 공시 ${n.dart}건, CBOE(VIX), FRED(하이일드), CNN(공포·탐욕). 네이버·CNN은 비공식 경로라 바뀌면 빠질 수 있습니다.`;
  $("#errors").textContent = d.errors && d.errors.length ? `수집 실패: ${d.errors.join(", ")}` : "";
}


/* ---------------- 지표 설명(ⓘ)과 한 줄 해석 ---------------- */

const INFO = {
  thermo: "지금 시장 분위기가 '겁먹은 쪽'인지 '들뜬 쪽'인지 0~100점으로 보여 줍니다.\n· 25점 아래: 다들 겁먹음 (극단적 공포)\n· 50점 근처: 보통\n· 75점 위: 다들 들뜸 (극단적 탐욕)\n아래 '무엇으로 계산했나'의 여러 지표를 점수로 바꿔 평균했습니다. 공포가 크다고 바로 바닥이라는 뜻은 아닙니다.",
  korea_vol: "코스피200이 최근 20일 동안 하루하루 얼마나 크게 오르내렸는지입니다.\n· 높을수록: 장이 크게 출렁임 = 불안\n· 낮을수록: 잔잔함\n원래는 'VKOSPI(한국 공포지수)'를 보는데, 구할 수 없어 대신 씁니다.",
  vix: "미국 증시의 '공포지수'입니다. 앞으로 한 달 동안 미국 주가가 얼마나 출렁일지 시장이 예상하는 값입니다.\n· 20 아래: 평온\n· 20~30: 경계\n· 30 위: 불안\n미국 장이 흔들리면 다음 날 한국 장도 영향을 받습니다.",
  cnn: "미국 CNN이 매일 발표하는 '투자 심리 점수'입니다.\n· 0에 가까울수록: 겁먹음 (공포)\n· 100에 가까울수록: 들뜸 (탐욕)\n미국 주가 흐름, 오르는 종목 수 등 7가지를 합쳐 만듭니다.",
  breadth: "최근 60일 중 가장 비싼 값(신고가) 또는 가장 싼 값(신저가)을 찍은 종목 수입니다.\n· 신저가가 많을수록: 시장 안쪽이 약함\n지수는 삼성전자 같은 큰 종목 몇 개 때문에 버틸 수 있어서, 종목 전체가 어떤지 따로 봅니다.",
  updown: "그날 오른 종목 수와 내린 종목 수입니다.\n지수는 올랐는데 내린 종목이 더 많으면, 큰 종목 몇 개만 지수를 끌어올렸다는 뜻입니다.",
  momentum: "코스피가 최근 20거래일(약 한 달) 동안 몇 % 오르거나 내렸는지입니다.\n· 많이 내렸으면: 공포 쪽\n· 많이 올랐으면: 들뜬 쪽",
  flows: "최근 20거래일 중 외국인과 기관(큰손)이 코스피 주식을 산 날이 며칠인지입니다.\n· 많을수록: 큰손이 꾸준히 사는 중 (좋은 신호)\n· 적을수록: 큰손이 계속 파는 중",
  credit: "형편이 어려운 미국 회사들이 돈을 빌릴 때 이자를 얼마나 더 내야 하는지입니다.\n· 높아질수록: 돈 빌리기가 어려워짐 = 금융시장이 긴장\n주가 하락이 '돈줄 문제'로 번지는지 보는 지표입니다.",
  kospi: "코스피 지수, 그날 몇 % 움직였는지, 최근 20거래일(약 한 달) 동안 몇 % 움직였는지입니다.",
  kosdaq: "코스닥 지수(작은 회사·성장주 위주), 그날 등락, 최근 20거래일 등락입니다.",
  usdkrw: "1달러를 사려면 원화가 얼마 필요한지입니다.\n· 환율이 빠르게 오르면(원화 가치 하락): 외국인이 한국 주식을 팔고 나가는 경우가 많음\n미국 자료라 며칠 늦게 반영됩니다.",
  us10y: "미국 정부가 10년 동안 돈을 빌릴 때 내는 이자율입니다. 전 세계 금리의 기준입니다.\n· 오르면: 주식, 특히 성장주에 불리 (돈이 안전한 채권으로 감)",
  marketflows: "시장 전체에서 외국인과 기관이 산 금액에서 판 금액을 뺀 값(순매수)입니다. 단위는 억 원입니다.\n· +면 더 많이 삼, −면 더 많이 팖",
  selling: "주가가 60일 중 최저가까지 떨어진 종목 수를 최근 5일 평균과 비교합니다.\n· 늘어나면: 버티던 종목까지 팔리는 중 (파는 힘이 셈)\n· 줄어들면: 마구 팔던 게(투매) 잦아드는 중",
  regime: "지금 장이 어떤 상태인지 넷 중 하나로 나눕니다.\n· 상승장: 코스피가 20일·60일 평균 가격 위에서 오르는 중\n· 하락장: 둘 다 아래에서 내리는 중\n· 횡보·전환: 방향이 섞임\n· 패닉: 급락, 극단적 공포, 큰 출렁임, 강제 매도 급증 중 두 가지 이상이 겹침\n장마다 잘 통하는 전략이 다르다는 연구 결과를 바탕으로 '어떤 탭이 맞는지'를 함께 보여 줍니다.",
  inverse: "지수가 떨어지면 돈을 버는 상품(ETF)입니다. 예: 코스피가 1% 내리면 약 1% 오름.\n주의: 매일 다시 계산되는 구조라, 지수가 오르락내리락하며 제자리여도 손해가 쌓입니다. 2배 상품은 더 심합니다. 그래서 '확실히 내리는 짧은 구간'에만 맞고, 이미 많이 떨어진 뒤 따라 들어가면 반등에 크게 당하기 쉽습니다.",
  score_swing: "강도: 이 종목이 조건에 얼마나 강하게 걸렸는지 0~100점입니다. 지금까지 같은 탭에 걸린 종목들 중 어디쯤인지로 매겨서, 세 탭을 같은 눈금으로 비교할 수 있습니다.\n스윙 계산: 회사 크기에 비해 최근 5일 큰손이 얼마나 많이 샀는지 + 며칠 연속 샀는지.\n강도가 높다고 오른다는 보장은 없습니다. '어떤 종목부터 볼지' 순서로만 쓰세요.",
  score_scalp: "강도: 이 종목이 조건에 얼마나 강하게 걸렸는지 0~100점입니다. 지금까지 같은 탭에 걸린 종목들 중 어디쯤인지로 매깁니다.\n단타 계산 (100점 만점을 나눠 가짐):\n· 큰손 확인 50%: 오늘 거래 중 큰손이 산 비중 (20% 이상이면 만점)\n· 거래 증가 25%: 평소보다 거래가 몇 배 늘었나 (8배면 만점, 15배 넘게 과열되면 오히려 감점)\n· 상승폭 25%: 5~15% 오르면 만점, 상한가 근처까지 튀면 감점\n너무 과열된 종목은 다음 날 꺾이는 경우가 많아서 이렇게 만들었습니다.",
  report: "증권사 애널리스트가 이 회사를 분석해 쓴 보고서입니다. 리포트가 나온다는 건 시장의 관심이 이 회사로 오기 시작했다는 신호예요.\n· '오랜만에 다뤄짐': 석 달 넘게 리포트가 없다가 최근 2주 안에 새로 나온 경우. 아직 사람들이 많이 몰리기 전이라 가장 앞선 신호로 봅니다\n· 목표가: 애널리스트가 생각하는 적정 주가. 다만 증권사 목표가는 대체로 높게 잡히는 편이라 '지금보다 +50%'를 그대로 믿진 마세요\n· 목표가를 올렸는지 내렸는지가 숫자 자체보다 더 중요합니다\n정말 도움이 되는지는 성적표 '조건별 성적'의 리포트 줄에서 확인합니다.",
  exit: "언제 팔지 미리 정해 두는 기준입니다. 처음 투자할 때 가장 어려운 게 '언제 파느냐'라서, 사기 전에 정해 두면 손실이 커지는 걸 막을 수 있습니다.\n· 스윙: 종가가 20일 평균 가격 아래로 마감하면 정리 (오르는 흐름이 깨짐)\n· 단타: 걸린 날의 저가를 깨고 마감하면 정리 (급등 힘이 빠짐)\n· 저평가: 최근 10일 최저가를 깨고 마감하면 정리 (아직 바닥이 아니었음)\n괄호 안 %는 지금 가격에서 그 기준까지 거리입니다. 이 기준을 실제로 지켰다면 성적이 어땠는지는 성적표의 '정리 기준을 지켰다면'에서 확인합니다.",
  earn_season: "회사들은 분기가 끝나면 정해진 기한 안에 실적을 발표합니다 (1분기 5/15, 반기 8/14, 3분기 11/14, 1년 결산 3/31까지).\n발표 전에는 결과를 모르니, 좋든 나쁘든 발표 날 주가가 크게 움직일 수 있습니다. 조건에 맞아 산 종목도 실적 한 번에 기준이 깨질 수 있어 미리 알려 드립니다.\n'발표 예고 공시'는 회사가 실적 발표 날짜를 미리 알린 경우입니다.",
  morning: "새벽에 끝난 미국 시장 결과로 오늘 아침 한국 시장 분위기를 미리 짐작해 봅니다.\n· 한국 ETF(EWY): 미국에 상장된 한국 주식 묶음이라 밤사이 외국인 시각이 가장 잘 보입니다 (가장 크게 반영)\n· 나스닥·미국 반도체: 삼성전자·SK하이닉스 같은 큰 종목과 같이 움직이는 경우가 많음\n· 공포지수(VIX)·환율: 오르면 부담\n짐작일 뿐이고, 장이 열리면 다르게 움직일 수 있습니다.",
  wake: "싼 종목도 시장이 관심을 안 주면 몇 년이고 싼 채로 남습니다 (가치 함정). 특히 한국 시장에서 흔합니다.\n그래서 '다시 주목받기 시작했나'를 따로 봅니다:\n· 큰손(기관+외국인)이 최근 5일 동안 샀나\n· 최근 3일 안에 기사·공시 같은 소식이 있나\n· 거래가 평소보다 늘었나\n· 최근 10일 바닥에서 5% 이상 올라왔나\n· 최근 2주 안에 증권사 리포트가 나왔나 (오랫동안 없다가 나오면 더 의미 있음)\n2개 이상이면 '움직일 계기가 생긴' 쪽입니다. 점수(강도)에는 넣지 않았고, 정말 성적이 나은지는 성적표의 '저평가 조건별 성적'에서 확인합니다.",
  score_value: "강도: 이 종목이 조건에 얼마나 강하게 걸렸는지 0~100점입니다. 지금까지 같은 탭에 걸린 종목들 중 어디쯤인지로 매깁니다.\n저평가 계산 (100점 만점을 나눠 가짐):\n· 낙폭 25%: 최근 고점보다 많이 빠졌나 (50% 빠지면 만점)\n· 싸짐 15%: 평소(1년)보다 얼마나 싸게 거래되나\n· PER 35%: 버는 돈에 비해 싼가 (PER 5 이하면 만점)\n· 이익 25%: 이익이 줄지 않고 버티거나 늘었나\n'싸면서 돈은 잘 버는 회사'가 위로 오게 했습니다.",
  futures: "외국인이 '코스피 선물'을 산 금액에서 판 금액을 뺀 값입니다.\n선물은 '앞으로 지수가 오를지 내릴지'에 거는 상품이라, 외국인의 방향 생각이 잘 드러납니다.\n· 며칠 연속 사면(+): 지수가 오를 쪽으로 보는 중\n· 며칠 연속 팔면(−): 내릴 쪽으로 보는 중\n한국 장의 방향을 먼저 알려 주는 지표로 많이 봅니다.",
  signals: "많이 떨어진 뒤 '이제 사도 될까?'를 세 단계로 확인합니다.\n1단계 투매 진정: 마구 팔던 게 잦아드는지 (신저가 종목·강제 매도 감소, 출렁임 감소)\n2단계 수급 전환: 큰손이 다시 사기 시작하는지 (외국인 선물·주식 순매수, 분위기 회복)\n3단계 추세 확인: 지수가 다시 오르는 흐름인지 (20일 평균 가격 회복)\n바닥은 아무도 정확히 못 맞힙니다. 그래서 한 번에 다 사지 말고 단계마다 나눠서(1/3 → 2/3 → 나머지) 들어가는 방법을 안내합니다.",
  transition: "지금 장(예: 하락장)이 다음 장(예: 횡보·전환)으로 바뀌려면 무엇이 필요한지, 지금 얼마나 남았는지입니다.\n핵심은 코스피가 20일 평균선(최근 20일 종가 평균) 위로 올라서느냐입니다. 평균선까지 2% 안쪽으로 가까워지면 '전환 임박'이 뜹니다.",
  loan: "개인이 증권사에서 돈을 빌려 산 주식 금액(빚투)입니다.\n· 많이 쌓여 있을수록: 주가가 빠질 때 강제로 팔리는 물량이 커질 수 있음\n하루 늦게 발표되는 자료입니다.",
  forced: "빌려서 산 주식 대금을 제때 못 갚아 증권사가 강제로 판 금액(반대매매)입니다.\n· 갑자기 늘면: 좋은 종목까지 같이 떨어지는 투매가 나오는 중\n· 줄어들면: 투매가 잦아드는 중\n하루 늦게 발표되는 자료입니다.",
};

// 1년 중 위치(백분위)로 쓰는 한 줄 해석. high = 값이 높을 때의 의미
function pctLine(p, high, low) {
  if (p == null) return "";
  if (p >= 80) return `1년 중 높은 편 → ${high}`;
  if (p <= 20) return `1년 중 낮은 편 → ${low}`;
  return "1년 기준 보통 범위";
}

function interpret(key, t) {
  switch (key) {
    case "korea_vol": return pctLine(t.korea_vol && t.korea_vol.pct_1y, "국내 시장이 크게 출렁이는 중", "국내 시장이 잠잠함");
    case "vix": {
      const v = t.vix; if (!v) return "";
      const lv = v.value >= 30 ? "불안 구간(30 이상)" : v.value >= 20 ? "경계 구간(20~30)" : "평온 구간(20 미만)";
      return `${lv}${v.change != null ? `, 5일 ${v.change > 0 ? "상승" : v.change < 0 ? "하락" : "변화 없음"}` : ""}`;
    }
    case "cnn": {
      const f = t.fear_greed; if (!f) return "";
      const d = f.week_ago != null ? f.score - f.week_ago : null;
      return `미국 투자심리 ${THERMO_KR(f.score)}${d != null ? `, 1주 전보다 ${d > 0 ? "개선" : d < 0 ? "악화" : "비슷"}` : ""}`;
    }
    case "credit": return pctLine(t.hy_spread && t.hy_spread.pct_1y, "돈줄이 조여지는 쪽", "자금시장 여유");
    case "usdkrw": return pctLine(t.usdkrw && t.usdkrw.pct_1y, "원화 약세, 외국인 매도 압력 주의", "원화 강세");
    case "us10y": return pctLine(t.us10y && t.us10y.pct_1y, "금리 부담 큼 (성장주에 불리)", "금리 부담 작음");
    case "breadth": {
      const b = t.breadth; if (!b) return "";
      const lo = b.new_lows.at(-1), hi = b.new_highs.at(-1);
      return lo > hi * 2 ? "신저가가 훨씬 많음 → 시장 속이 약함" : hi > lo * 2 ? "신고가가 훨씬 많음 → 시장 속이 강함" : "신고가·신저가 비슷";
    }
    case "updown": {
      const b = t.breadth; if (!b) return "";
      const k = (t.index || {}).kospi;
      const r = b.up / Math.max(1, b.up + b.down);
      const base = r >= 0.6 ? "오른 종목이 많음" : r <= 0.4 ? "내린 종목이 많음" : "오른 종목·내린 종목 비슷";
      if (k && k.change > 0.5 && r < 0.45) return base + " (지수는 올랐는데 다수는 내림 → 대형주 쏠림)";
      if (k && k.change < -0.5 && r > 0.55) return base + " (지수는 내렸는데 다수는 오름)";
      return base;
    }
    case "futures": {
      const f = t.futures; if (!f) return "";
      const dir = f.d20 < 0 ? "20일 누적 순매도 → 지수 하락 쪽 베팅" : "20일 누적 순매수 → 지수 상승 쪽 베팅";
      let out = f.d5 * f.d20 < 0 ? `${dir}, 다만 최근 5일은 반대로 ${f.d5 < 0 ? "팔고" : "사고"} 있음` : dir;
      if (f.streak && Math.abs(f.streak) >= 3) out += ` · ${Math.abs(f.streak)}일 연속 ${f.streak > 0 ? "순매수" : "순매도"}`;
      return out;
    }
    case "loan": {
      const l = (t.credit_balance || {}).loan; if (!l) return "";
      const p = pctLine(l.pct_1y, "빚투 많이 쌓임", "빚투 부담 작음");
      return l.change != null && Math.abs(l.change) >= 5000 ? `${p} · 5일 새 ${eokText(Math.abs(l.change))} ${l.change > 0 ? "늘어남" : "줄어듦 (빚 정리 중)"}` : p;
    }
    case "forced": return pctLine(((t.credit_balance || {}).forced || {}).pct_1y, "강제 매도가 많은 편 → 투매 압력", "강제 매도 적음");
    case "kospi": case "kosdaq": {
      const x = (t.index || {})[key]; if (!x) return "";
      return x.change_20d <= -10 ? "20일 새 10% 넘게 하락 — 조정 폭이 큼" : x.change_20d >= 10 ? "20일 새 10% 넘게 상승 — 단기 과열 주의" : "20일 흐름은 보통 범위";
    }
    default: return "";
  }
}

const THERMO_KR = (s) => (s < 25 ? "극단적 공포" : s < 45 ? "공포" : s < 55 ? "중립" : s < 75 ? "탐욕" : "극단적 탐욕");
const THERMO_COLOR = (s) => (s < 25 ? "var(--g0)" : s < 45 ? "var(--g1)" : s < 55 ? "var(--g2)" : s < 75 ? "var(--g3)" : "var(--g4)");

const infoBtn = (key) => (INFO[key] ? `<button class="info" type="button" data-info="${key}" aria-label="설명 보기">ⓘ</button>` : "");
const infoBox = (key) => (INFO[key] ? `<p class="exp" data-exp="${key}"${state.expAll ? "" : " hidden"}>${esc(INFO[key])}</p>` : "");

// '설명 항상 펼치기': 모든 ⓘ 설명과 온도계 지표 목록을 펼치거나 접는다
function applyExpAll() {
  document.querySelectorAll("[data-exp]").forEach((b) => { b.hidden = !state.expAll; });
  const d = $("#thermo details");
  if (d) d.open = state.expAll;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button.info");
  if (!b) return;
  const box = b.closest(".tile, .thermo, .part, .sum, .fit > li, .scorerow").querySelector(`[data-exp="${b.dataset.info}"]`);
  if (box) box.hidden = !box.hidden;
});

/* ---------------- 시장 온도계(게이지) ---------------- */

function gauge(score) {
  const cx = 120, cy = 120, r = 88, sw = 18;
  const pt = (deg, rad) => [cx + rad * Math.cos((Math.PI * deg) / 180), cy - rad * Math.sin((Math.PI * deg) / 180)];
  const seg = (from, to, color) => {
    const [x1, y1] = pt(180 - from * 1.8, r), [x2, y2] = pt(180 - to * 1.8, r);
    return `<path d="M${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 0 1 ${x2.toFixed(1)},${y2.toFixed(1)}" stroke="${color}" stroke-width="${sw}" fill="none"/>`;
  };
  const arcs = [[0, 25, "var(--g0)"], [25.6, 45, "var(--g1)"], [45.6, 55, "var(--g2)"], [55.6, 75, "var(--g3)"], [75.6, 100, "var(--g4)"]]
    .map(([a, b, c]) => seg(a, b, c)).join("");
  const [nx, ny] = pt(180 - score * 1.8, r - 22);
  const ticks = `<text x="${cx}" y="${cy - r - 14}" text-anchor="middle">50</text>` +
    `<text x="${cx - r}" y="${cy + 15}" text-anchor="middle">0</text><text x="${cx + r}" y="${cy + 15}" text-anchor="middle">100</text>`;
  return `<svg viewBox="0 0 240 150" class="gauge" role="img" aria-label="시장 온도 ${score}점">
    ${arcs}${ticks}
    <line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="var(--text)" stroke-width="4" stroke-linecap="round"/>
    <circle cx="${cx}" cy="${cy}" r="8" fill="var(--card)" stroke="var(--text)" stroke-width="3"/>
    <text x="${cx - r}" y="${cy + 27}" text-anchor="middle" class="end">극단적 공포</text><text x="${cx + r}" y="${cy + 27}" text-anchor="middle" class="end">극단적 탐욕</text>
  </svg>`;
}

// 점수와 원래 숫자가 왜 다른지 VIX로 예시
function vixEx(th) {
  const v = state.data.temperature.vix, p = th.parts.find((x) => x.key === "vix");
  return v && p && v.pct_1y != null ? ` 예: VIX ${num(v.value, 1)}은 1년 중 ${v.pct_1y}% 위치라, 높을수록 공포이므로 100 − ${v.pct_1y} = ${p.score}점.` : "";
}

function renderThermo() {
  const th = state.data.temperature.thermo;
  if (!th) { $("#thermo").innerHTML = ""; return; }
  const badge = (s) => `<span class="badge" style="background:${THERMO_COLOR(s)}">${s}</span>`;
  const h = th.history || {};
  const cmp = [["prev", "전일"], ["week", "1주 전"], ["month", "1달 전"]].map(([k, name]) => {
    const x = h[k];
    return `<li><div><small>${name}</small><b style="color:${x ? THERMO_COLOR(x.score) : "var(--faint)"}">${x ? esc(x.label) : "기록 없음"}</b></div>${x ? badge(x.score) : ""}</li>`;
  }).join("");
  const parts = th.parts.map((p) => `<div class="part">
      <div class="part-hd"><span>${esc(p.name)} ${infoBtn(p.key)}${p.weight && p.weight < 1 ? ` <small class="half">반 무게</small>` : ""}</span><b style="color:${THERMO_COLOR(p.score)}">${p.score}점 <small>${THERMO_KR(p.score)}</small></b></div>
      <div class="part-bar"><i style="left:${p.score}%"></i></div>
      <small>${esc(p.raw)}</small>${infoBox(p.key)}
    </div>`).join("");
  $("#thermo").innerHTML = `<div class="thermo">
    <div class="th-top">
      <div class="th-now"><small>지금 ${infoBtn("thermo")}</small><b style="color:${THERMO_COLOR(th.score)}">${esc(th.label)}</b>${gauge(th.score)}
        <div class="th-score" style="color:${THERMO_COLOR(th.score)}">${th.score}<small>/100</small></div></div>
      <ul class="th-cmp">${cmp}</ul>
    </div>
    ${infoBox("thermo")}
    <details class="rules"${state.expAll ? " open" : ""}><summary>무엇으로 계산했나 (${th.parts.length}개 지표)</summary><p class="hint">오른쪽은 각 지표를 0(공포)~100(탐욕) 점수로 바꾼 값이고, 막대 아래 작은 글씨가 지표의 원래 숫자입니다. 온도는 이 점수들의 평균인데, '코스피 20일 추세'와 '신고가 vs 신저가'는 둘 다 주가 하락을 보는 지표라 반 무게로 넣었습니다(급락장에서 공포 쪽으로 겹쳐 쏠리지 않게).${vixEx(th)}</p>${parts}</details>
  </div>`;
}

/* ---------------- 시장 온도 ---------------- */

function spark(vals, color = "currentColor") {
  const v = (vals || []).filter((x) => x != null);
  if (v.length < 2) return "";
  const min = Math.min(...v), max = Math.max(...v), w = 100, h = 28;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${h - 2 - ((x - min) / (max - min || 1)) * (h - 4)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}

// 지표 구간 막대: 지금 값이 어느 구간(평온·보통·위험 등)에 있는지 색으로 보여 준다
// zones = [[끝값(0~100 눈금), 색, 이름], ...], pos = 0~100 눈금 위 현재 위치
function zoneBar(zones, pos) {
  let from = 0;
  const segs = zones.map(([to, color, name]) => {
    const w = to - from; from = to;
    return { w, color, name };
  });
  const bar = segs.map((z) => `<span style="flex:${z.w};background:${z.color}"></span>`).join("");
  // 이름은 칸이 좁아 구간 너비에 맞추지 않고 양 끝·가운데에 나눠 둔다
  const labs = segs.filter((z) => z.name).map((z) => `<span>${esc(z.name)}</span>`).join("");
  const at = Math.max(1, Math.min(99, pos));
  return `<div class="zb"><div class="zb-bar">${bar}<i style="left:${at.toFixed(1)}%"></i></div><div class="zb-lab">${labs}</div></div>`;
}

const PCT_ZONES = (low, high) => [[20, "var(--g4)", low], [80, "var(--g2)", "보통"], [100, "var(--g0)", high]];

function zoneFor(key, t) {
  const pct = (x, low, high) => (x && x.pct_1y != null ? zoneBar(PCT_ZONES(low, high), x.pct_1y) : "");
  switch (key) {
    case "vix": return t.vix ? zoneBar([[33.3, "var(--g4)", "평온"], [66.7, "var(--g1)", "경계"], [100, "var(--g0)", "불안"]], ((t.vix.value - 10) / 30) * 100) : "";
    case "korea_vol": return pct(t.korea_vol, "잠잠", "출렁임");
    case "credit": return pct(t.hy_spread, "여유", "돈줄 조임");
    case "usdkrw": return pct(t.usdkrw, "원화 강세", "원화 약세");
    case "loan": return pct((t.credit_balance || {}).loan, "적음", "빚투 과열");
    case "forced": return pct((t.credit_balance || {}).forced, "적음", "많음");
    case "us10y": return pct(t.us10y, "부담 작음", "부담 큼");
    case "cnn": return t.fear_greed ? zoneBar([[25, "var(--g0)", "극단 공포"], [45, "var(--g1)", ""], [55, "var(--g2)", "중립"], [75, "var(--g3)", ""], [100, "var(--g4)", "탐욕"]], t.fear_greed.score) : "";
    case "kospi": case "kosdaq": {
      const x = (t.index || {})[key];
      return x && x.change_20d != null ? zoneBar([[16.7, "var(--g0)", "급락"], [83.3, "var(--g2)", "보통"], [100, "var(--g1)", "과열"]], ((x.change_20d + 15) / 30) * 100) : "";
    }
    case "updown": {
      const b = t.breadth; if (!b || !(b.up + b.down)) return "";
      const r = (100 * b.up) / (b.up + b.down);
      return `<div class="zb"><div class="zb-bar"><span style="flex:${r.toFixed(1)};background:var(--up)"></span><span style="flex:${(100 - r).toFixed(1)};background:var(--down)"></span><i style="left:50%" class="mid"></i></div>` +
        `<div class="zb-lab"><span>상승 ${Math.round(r)}%</span><span>하락 ${Math.round(100 - r)}%</span></div></div>`;
    }
    default: return "";
  }
}

function tile(label, value, sub, sparkVals, opts = {}) {
  const key = opts.key;
  const line = key ? interpret(key, state.data.temperature) : "";
  const zb = key ? zoneFor(key, state.data.temperature) : "";
  // 해외·협회 자료는 하루 이상 늦게 나오므로, 기준일과 다른 날짜면 언제 값인지 붙인다
  const b = state.data.base_date, baseIso = `${b.slice(0, 4)}-${b.slice(4, 6)}-${b.slice(6)}`;
  if (opts.date && opts.date !== baseIso) sub += ` · <span class="asof">${opts.date.slice(5)} 기준</span>`;
  return `<div class="tile${opts.wide ? " wide" : ""}"><div class="l">${label} ${key ? infoBtn(key) : ""}</div><div class="v ${opts.vcls || ""}">${value}</div>` +
    `<div class="s">${sub}</div>${sparkVals ? `<div style="color:var(--faint)">${spark(sparkVals)}</div>` : ""}${opts.extra || ""}${zb}` +
    `${line ? `<div class="why">${esc(line)}</div>` : ""}${key ? infoBox(key) : ""}</div>`;
}

// 요약 카드 한 줄 해석 (key → level → 문장)
const SUM_LINE = {
  selling: {
    "커지는 중": "버티던 종목까지 팔리는 중 → 하락이 넓게 퍼지고 있음",
    "줄어드는 중": "새로 무너지는 종목이 줄어듦 → 투매가 잦아드는 중",
    "비슷함": "파는 힘이 더 세지지도 약해지지도 않음 → 흐름 유지",
  },
};

// 최근 20일 신고가(위)·신저가(아래) 막대, 점선 = 신저가 직전 5일 평균 (매도 압력 판정 기준)
function highLowChart(b) {
  const lo = b.new_lows, hi = b.new_highs, n = lo.length;
  if (n < 2) return "";
  const w = 200, h = 64, mid = h / 2, gap = 1.2, bw = w / n - gap;
  const max = Math.max(1, ...lo, ...hi);
  const y = (v) => (v / max) * (mid - 2);
  const rects = lo.map((l, i) => {
    const x = (i * w) / n, op = i === n - 1 ? 1 : 0.4;
    return `<rect x="${x.toFixed(1)}" y="${(mid - y(hi[i])).toFixed(1)}" width="${bw.toFixed(1)}" height="${y(hi[i]).toFixed(1)}" fill="var(--up)" opacity="${op}"/>` +
      `<rect x="${x.toFixed(1)}" y="${mid}" width="${bw.toFixed(1)}" height="${y(l).toFixed(1)}" fill="var(--down)" opacity="${op}"/>`;
  }).join("");
  const avg = n >= 6 ? lo.slice(-6, -1).reduce((a, v) => a + v, 0) / 5 : null;
  const avgLine = avg == null ? "" : `<line x1="${((n - 6) * w) / n}" x2="${w}" y1="${(mid + y(avg)).toFixed(1)}" y2="${(mid + y(avg)).toFixed(1)}" stroke="var(--text)" stroke-width="1" stroke-dasharray="3 2" vector-effect="non-scaling-stroke"/>`;
  const d0 = b.dates && b.dates[0], d1 = b.dates && b.dates[n - 1];
  return `<svg class="hilo" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="최근 ${n}일 신고가·신저가 종목 수">` +
    `${rects}<line x1="0" x2="${w}" y1="${mid}" y2="${mid}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>${avgLine}</svg>` +
    `<div class="legend"><span><i style="background:var(--up)"></i>신고가 ${num(hi[n - 1])}</span><span><i style="background:var(--down)"></i>신저가 ${num(lo[n - 1])}</span>` +
    `${avg == null ? "" : `<span><i class="dash"></i>신저가 5일 평균</span>`}</div>` +
    (d0 && d1 ? `<div class="barlab"><span style="text-align:left">${ymd(d0)}</span><span style="text-align:right">${ymd(d1)}</span></div>` : "");
}

// 외국인 코스피200 선물: 금액(억 원)이 먼저, 계약 수는 보조. 최근 20일 일별 막대 + 누적선
const sEok = (v) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${eokText(Math.abs(v))}`);
const sCt = (v) => (v == null ? "" : `${v > 0 ? "+" : ""}${num(v)}계약`);

function futuresTile(f) {
  const b = state.data.base_date, baseIso = `${b.slice(0, 4)}-${b.slice(4, 6)}-${b.slice(6)}`;
  const asof = f.date && f.date !== baseIso ? ` <span class="asof">${f.date.slice(5)} 기준</span>` : "";
  if (!f.daily) {  // 예전 기록: 계약 수만 있음
    return tile("외국인 선물 순매수", `<span class="${cls(f.today)}">${sCt(f.today)}</span>`, `5일 ${sCt(f.d5)} · 20일 ${sCt(f.d20)}`, f.cum20, { key: "futures", date: f.date });
  }
  const d = f.daily, n = d.length, w = 300, h = 90, mid = h / 2;
  const maxBar = Math.max(1, ...d.map((x) => Math.abs(x.foreign_eok || 0)));
  const bw = w / n - 2;
  const bars = d.map((x, i) => {
    const v = x.foreign_eok || 0, bh = (Math.abs(v) / maxBar) * (mid - 4);
    return `<rect x="${(i * w / n + 1).toFixed(1)}" y="${(v >= 0 ? mid - bh : mid).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, bh).toFixed(1)}" fill="var(--${v >= 0 ? "up" : "down"})" opacity="${i === n - 1 ? 1 : 0.55}"><title>${ymd(x.date)} ${sEok(v)} (${sCt(x.foreign)})</title></rect>`;
  }).join("");
  let acc = 0;
  const cum = d.map((x) => (acc += x.foreign_eok || 0));
  const cmin = Math.min(0, ...cum), cmax = Math.max(0, ...cum), cy = (v) => h - 4 - ((v - cmin) / (cmax - cmin || 1)) * (h - 8);
  const line = `<polyline points="${cum.map((v, i) => `${((i + 0.5) * w / n).toFixed(1)},${cy(v).toFixed(1)}`).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  const chart = `<svg class="futchart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="최근 ${n}일 외국인 선물 순매수">` +
    `<line x1="0" x2="${w}" y1="${mid}" y2="${mid}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>${bars}${line}</svg>` +
    `<div class="legend"><span><i style="background:var(--up)"></i>순매수</span><span><i style="background:var(--down)"></i>순매도</span><span><i style="background:var(--text)"></i>20일 누적</span><span class="rng">누적 ${sEok(f.d20_eok)}</span></div>` +
    `<div class="barlab"><span style="text-align:left">${ymd(d[0].date)}</span><span style="text-align:right">${ymd(d[n - 1].date)}</span></div>`;
  const streak = f.streak ? `${f.streak > 0 ? "순매수" : "순매도"} ${Math.abs(f.streak)}일째` : "";
  const who = [["외국인", f.today_eok, f.today], ["기관", f.inst_today_eok, f.inst_today], ["개인", f.retail_today_eok, f.retail_today]]
    .map(([k, e, c]) => `<td class="${cls(e)}">${sEok(e)}<small>${sCt(c)}</small></td>`).join("");
  const line1 = interpret("futures", state.data.temperature);
  return `<div class="tile wide fut"><div class="l">외국인 코스피200 선물 순매수 ${infoBtn("futures")}${asof}</div>
    <div class="futhead"><div class="v ${cls(f.today_eok)}">${sEok(f.today_eok)}</div><div class="s">${sCt(f.today)}${streak ? ` · ${streak}` : ""}</div></div>
    <div class="nums">
      <div><small>최근 5일</small><b class="${cls(f.d5_eok)}">${sEok(f.d5_eok)}</b><small>${sCt(f.d5)}</small></div>
      <div><small>최근 20일</small><b class="${cls(f.d20_eok)}">${sEok(f.d20_eok)}</b><small>${sCt(f.d20)}</small></div>
      <div><small>20일 중 순매수일</small><b>${f.up_days20}일</b><small>/ ${f.days20}일</small></div>
    </div>
    ${chart}
    <table class="flowtbl futwho"><thead><tr><th>오늘</th><th>외국인</th><th>기관</th><th>개인</th></tr></thead><tbody><tr><td>순매수</td>${who}</tr></tbody></table>
    ${line1 ? `<div class="why">${esc(line1)}</div>` : ""}${infoBox("futures")}</div>`;
}

// 억 원 → "1.23조" / "456억"
const eokText = (v) => (v == null ? "–" : Math.abs(v) >= 10000 ? `${num(v / 10000, 2)}조` : `${num(v)}억`);

const pctText = (p) => (p == null ? "" : ` · 1년 중 ${p}% 위치`);

function renderTemp() {
  const t = state.data.temperature;
  renderThermo();
  // 온도계가 있으면 공포 크기·자금시장은 온도계 지표와 겹치므로 뺀다
  $("#summary").innerHTML = (t.summary || []).filter((s) => !(t.thermo && (s.key === "fear" || s.key === "credit"))).map((s) => {
    const line = SUM_LINE[s.key] && SUM_LINE[s.key][s.level];
    const viz = s.key === "selling" && t.breadth ? highLowChart(t.breadth) : "";
    return `<div class="sum ${s.tone}"><div class="row"><span class="t">${esc(s.title)} ${infoBtn(s.key)}</span><span class="lv">${esc(s.level)}</span></div><p>${esc(s.text)}</p>${viz}` +
      `${line ? `<div class="why">${esc(line)}</div>` : ""}${infoBox(s.key)}</div>`;
  }).join("") || `<div class="empty">시장 온도 데이터를 받지 못했습니다</div>`;

  const tiles = [];
  const ix = t.index || {};
  for (const [k, name] of [["kospi", "코스피"], ["kosdaq", "코스닥"]]) {
    const x = ix[k];
    if (x) tiles.push(tile(name, num(x.close, 2), `<span class="${cls(x.change)}">${signed(x.change, 2, "%")}</span> · 20일 ${signed(x.change_20d, 1, "%")}`, x.spark, { key: k }));
  }
  if (t.futures) tiles.push(futuresTile(t.futures));  // 한국 장세를 먼저 보여 주는 지표라 지수 바로 다음에

  if (t.vix) tiles.push(tile("미국 VIX (공포지수)", num(t.vix.value, 1), `5일 ${signed(t.vix.change, 1)}${pctText(t.vix.pct_1y)}`, t.vix.spark, { key: "vix", date: t.vix.date }));
  if (t.korea_vol) tiles.push(tile("국내 변동성 <small>(VKOSPI 대체)</small>", num(t.korea_vol.value, 1) + "%", `코스피200 20일 실제 변동성${pctText(t.korea_vol.pct_1y)}`, t.korea_vol.spark, { key: "korea_vol", date: t.korea_vol.date }));
  if (t.fear_greed) {
    const fg = t.fear_greed;
    const kr = { "extreme fear": "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", "extreme greed": "극단적 탐욕" }[fg.rating] || fg.rating;
    tiles.push(tile("CNN 공포·탐욕", `${num(fg.score)} <small style="font-size:13px;font-weight:500">${esc(kr)}</small>`,
      `전일 ${num(fg.prev)} · 1주 전 ${num(fg.week_ago)} · 1달 전 ${num(fg.month_ago)}${fg.year_ago != null ? ` · 1년 전 ${num(fg.year_ago)}` : ""}`, fg.spark, { key: "cnn", date: fg.date }));
  }
  if (t.hy_spread) tiles.push(tile("미국 하이일드 스프레드", num(t.hy_spread.value, 2) + "%p", `20일 ${signed(t.hy_spread.change, 2, "%p")}${pctText(t.hy_spread.pct_1y)}`, t.hy_spread.spark, { key: "credit", date: t.hy_spread.date }));
  if (t.usdkrw) tiles.push(tile("원/달러 환율", num(t.usdkrw.value, 1) + "원", `5일 ${signed(t.usdkrw.change, 1)}${pctText(t.usdkrw.pct_1y)}`, t.usdkrw.spark, { key: "usdkrw", date: t.usdkrw.date }));
  if (t.us10y) tiles.push(tile("미국 10년 국채금리", num(t.us10y.value, 2) + "%", `5일 ${signed(t.us10y.change, 2, "%p")}${pctText(t.us10y.pct_1y)}`, t.us10y.spark, { key: "us10y", date: t.us10y.date }));
  if (t.breadth) {
    const b = t.breadth;
    const bm = b.by_market || {};
    const ud = [["KOSPI", "코스피"], ["KOSDAQ", "코스닥"]].filter(([m]) => bm[m]).map(([m, name]) => [name, bm[m]]);
    if (ud.length) {
      ud.push(["합계", b]);
      const rows = ud.map(([name, x]) => `<tr><td>${name}</td><td class="up">${num(x.up)}</td><td class="down">${num(x.down)}</td></tr>`).join("");
      tiles.push(tile("오르고 내린 종목", `<table class="flowtbl udtbl"><thead><tr><th></th><th>상승</th><th>하락</th></tr></thead><tbody>${rows}</tbody></table>`, "보통주 기준", null, { key: "updown" }));
    } else {
      tiles.push(tile("오르고 내린 종목", `<span class="up">${num(b.up)}</span> / <span class="down">${num(b.down)}</span>`, "상승 / 하락 (보통주)", null, { key: "updown" }));
    }
    if (!(t.summary || []).some((s) => s.key === "selling")) tiles.push(tile("60일 신저가 종목", num(b.new_lows.at(-1)), `신고가 ${num(b.new_highs.at(-1))} · 최근 20일 추이`, b.new_lows, { key: "breadth" }));
  }
  if (t.flows) {
    const rows = [];
    for (const [m, name] of [["KOSPI", "코스피"], ["KOSDAQ", "코스닥"]]) {
      const f = t.flows[m];
      if (!f) continue;
      for (const [k, who] of [["foreign", "외국인"], ["inst", "기관"]]) {
        const x = f[k];
        if (x) rows.push(`<tr><td>${name} ${who}</td><td class="${cls(x.today)}">${signed(x.today, 0)}</td><td class="${cls(x.d5)}">${signed(x.d5, 0)}</td><td class="${cls(x.d20)}">${signed(x.d20, 0)}</td></tr>`);
      }
    }
    tiles.push(`<div class="tile wide"><div class="l">시장 전체 순매수 (억 원) ${infoBtn("marketflows")}</div>${infoBox("marketflows")}<table class="flowtbl"><thead><tr><th></th><th>오늘</th><th>5일</th><th>20일</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`);
  }
  const cb = t.credit_balance || {};
  if (cb.loan) {
    const l = cb.loan;
    tiles.push(tile("신용융자 잔고 (빚투)", eokText(l.value), `코스피 ${eokText(l.kospi)} · 코스닥 ${eokText(l.kosdaq)}<br>5일 ${l.change == null ? "–" : (l.change > 0 ? "+" : "−") + eokText(Math.abs(l.change))}`, l.spark, { key: "loan", date: l.date }));
  }
  if (cb.forced) {
    const f = cb.forced;
    tiles.push(tile("반대매매 (강제 매도)", eokText(f.value), `미수금 ${eokText(f.unpaid)} 중 ${num(f.ratio, 1)}% · 20일 평균 ${eokText(f.avg20)}`, f.spark, { key: "forced", date: f.date }));
  }
  if (!cb.loan && !cb.forced) tiles.push(tile("신용잔고·반대매매", `<span class="na">–</span>`, "이번 기준일엔 받지 못함", null));
  $("#tiles").innerHTML = tiles.join("");
}

/* ---------------- 종목 필터 ---------------- */

const TAB_HINT = {
  swing: "며칠~몇 주 들고 가는 후보. 최근 3일 안에 소식(기사·공시)이 있고, 큰손(기관·외국인)이 며칠째 사고 있으며, 주가가 오르는 흐름인 종목.",
  scalp: "하루~이틀 짧게 보는 후보. 오늘 소식이 나왔고, 평소보다 몇 배 많이 거래됐으며, 오늘 큰손(기관·외국인)도 산 종목.",
  value: "많이 떨어져서 1년 중 싼 편인데, 회사가 버는 돈(이익)은 크게 줄지 않은 종목. 회사 문제가 아니라 시장이 겁먹어서 싸진 건지 살펴볼 후보. 싼 종목은 관심을 못 받으면 오래 싼 채로 남으니, 카드 아래 '주목 신호'가 2개 이상인지 함께 보세요.",
};
const TAB_NAME = { swing: "스윙", scalp: "단타", value: "저평가" };

function renderFilter() {
  const f = state.data.filter;
  $("#n-swing").textContent = f.swing_total;
  $("#n-scalp").textContent = f.scalp_total;
  $("#n-value").textContent = f.value_total ?? "–";
  renderWatch();
  renderOverlap();
  renderExcluded();
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
  $("#tab-hint").textContent = TAB_HINT[state.tab];
  const list = f[state.tab] || [];
  const total = f[state.tab + "_total"] || 0;
  if (!list.length) {
    const why = state.tab === "value"
      ? (f.value_total == null ? "이 날짜는 저평가 필터가 생기기 전 기록입니다." : "많이 떨어지면서도 이익이 버틴 종목이 없었습니다.")
      : `기사 걸린 종목 ${f.news_count.matched_3d}개 중 0개`;
    $("#cards").innerHTML = `<div class="empty">이 날은 조건을 모두 통과한 종목이 없습니다.<br><small>${why}</small></div>`;
    return;
  }
  const more = total > list.length ? `<p class="hint">통과 ${total}개 중 점수 상위 ${list.length}개</p>` : "";
  $("#cards").innerHTML = list.map(card).join("") + more;
}

function bars(daily) {
  const max = Math.max(1, ...daily.map((d) => Math.abs(d.sum || 0)));
  const hasNeg = daily.some((d) => (d.sum || 0) < 0);
  // 음수가 있으면 가운데 기준선 위아래로, 없으면 전체 높이를 쓴다
  const cols = daily.map((d) => {
    const v = d.sum || 0, h = Math.max(3, (Math.abs(v) / max) * 100);
    const bar = `<i class="${v < 0 ? "neg" : ""}" style="height:${h}%;background:var(--${v >= 0 ? "up" : "down"})"></i>`;
    const inner = hasNeg
      ? `<div style="height:50%;display:flex;align-items:flex-end;width:100%">${v >= 0 ? bar : ""}</div><div style="height:50%;width:100%">${v < 0 ? bar : ""}</div>`
      : bar;
    return `<div title="${ymd(d.date)} ${signed(v, 0, "억")}">${inner}</div>`;
  }).join("");
  const labs = daily.map((d) => `<span>${d.date.slice(4, 6)}/${d.date.slice(6)}</span>`).join("");
  return `<div class="bars">${cols}</div><div class="barlab">${labs}</div>`;
}

/* ---------------- 오늘 한눈에 (페이지 맨 위 요약) ---------------- */

function renderToday() {
  const d = state.data, t = d.temperature, rg = t.regime, th = t.thermo, f = d.filter;
  const rows = [];
  if (th) rows.push(["시장 온도", `<b style="color:${THERMO_COLOR(th.score)}">${th.score}점 ${esc(th.label)}</b>`]);
  if (rg) {
    const by = (grade) => ["swing", "scalp", "value"].filter((g) => rg.fit[g].grade === grade).map((g) => TAB_NAME[g]);
    const good = by("good"), bad = by("bad");
    rows.push(["장세", `<b>${esc(rg.label)}</b> <small>코스피 20일 ${signed(rg.trend.chg20, 1, "%")}${rg.trend_key && rg.key === "panic" ? ` · 추세만 보면 ${REGIME_NAME[rg.trend_key]}` : ""}</small>`]);
    rows.push(["맞는 시나리오", good.length ? `<b class="good">${good.join("·")}</b>` : "<span class=\"na\">없음</span>"]);
    if (bad.length) rows.push(["피할 시나리오", `<b class="bad">${bad.join("·")}</b>`]);
    rows.push(["인버스", esc(rg.inverse.label)]);
    const sg = d.temperature.signals, tn = d.temperature.transition;
    if (sg) rows.push(["사도 되는 신호", `<b style="color:${SIG_COLOR[sg.level]}">${sg.level}단계</b> <small>${esc(sg.guide)}</small>`]);
    if (tn) rows.push(["장의 전환", `20일 평균선까지 ${tn.gap20 > 0 ? `${num(tn.gap20, 1)}% 남음` : "회복"}${tn.near ? ` <span class="tag new">전환 임박</span>` : ""}`]);
  }
  const tops = ["swing", "scalp", "value"].filter((g) => (f[g] || []).length).map((g) => {
    const x = f[g][0];
    return `<button class="more pick" type="button" data-code="${x.code}">${esc(x.name)}</button><small>${TAB_NAME[g]}${x.score_pct != null ? ` ${x.score_pct}점` : ""}</small>`;
  });
  const fresh = ["swing", "scalp", "value"].map((g) => [g, (f[g] || []).filter((x) => x.streak_days === 1).length]).filter(([, n]) => n);
  if (["swing", "scalp", "value"].some((g) => (f[g] || []).some((x) => x.streak_days != null)))
    rows.push(["새로 걸림", fresh.length ? fresh.map(([g, n]) => `${TAB_NAME[g]} ${n}개`).join(" · ") : `<span class="na">없음 (모두 어제도 있던 종목)</span>`]);
  const vals = state.data.filter.value || [];
  if (vals.length && vals[0].wake) {
    const woke = vals.filter((x) => x.wake.met >= 2);
    rows.push(["주목받는 저평가", woke.length ? `<b>${woke.length}개</b> <small>${woke.slice(0, 3).map((x) => esc(x.name)).join(", ")}</small>` : `<span class="na">없음 (싼데 아무도 안 보는 중)</span>`]);
  }
  if (tops.length) rows.push(["탭별 1위", `<span class="picks">${tops.map((p) => `<span>${p}</span>`).join("")}</span>`]);
  const hits = Object.keys(state.watch).filter((c) => ["swing", "scalp", "value"].some((g) => (f[g] || []).some((y) => y.code === c)));
  if (Object.keys(state.watch).length) rows.push(["관심 종목", hits.length ? `<b>${hits.map((c) => esc(state.watch[c])).join(", ")}</b> 오늘 걸림` : `<span class="na">오늘 걸린 종목 없음</span>`]);
  $("#today-body").innerHTML = rows.length ? `<div class="today">${rows.map(([k, v]) => `<div class="tr"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}</div>` +
    `<p class="hint">아래 칸들의 결론만 모은 것입니다. 매수·매도 추천이 아닙니다.</p>` : "";
}

/* ---------------- 장의 전환 · 사도 되는 신호 ---------------- */

const SIG_COLOR = ["var(--faint)", "var(--g1)", "var(--g3)", "var(--g4)"];

function renderTurn() {
  const t = state.data.temperature, sig = t.signals, tr = t.transition, box = $("#turn-body");
  if (!sig || !tr) { box.innerHTML = `<div class="empty">이 날짜는 전환 신호가 생기기 전 기록입니다.</div>`; return; }
  // 신호등: 1·2·3단계, 통과한 단계까지 불이 켜짐
  const lights = sig.stages.map((st, i) => `<div class="lamp ${i < sig.level ? "on" : ""}" style="--c:${SIG_COLOR[i + 1]}"><i></i><b>${i + 1}단계</b><small>${esc(st.name)}</small></div>`).join("");
  // 최근 날짜별 단계 띠
  const lv = (state.score || {}).signal_levels || {};
  const ds = Object.keys(lv).sort().filter((d) => d <= state.data.base_date).slice(-20);
  const strip = ds.length > 1 ? `<div class="rgstrip"><div class="cells">${ds.map((d) => `<i style="background:${SIG_COLOR[lv[d]]}" title="${ymd(d)} ${lv[d]}단계"></i>`).join("")}</div><small>최근 ${ds.length}일 신호 단계 (회색 0 → 초록 3)</small></div>` : "";
  const stageCards = sig.stages.map((st, i) => `<li class="${st.pass ? "fit-good" : i === sig.level ? "fit-ok" : ""}"><div class="row"><b>${i + 1}단계 · ${esc(st.name)}</b><span>${st.met}/${st.checks.length} ${st.pass ? "통과" : `(${st.need}개 필요)`}</span></div>${checkList(st.checks)}</li>`).join("");
  const sigBox = `<div class="sum ${sig.level >= 2 ? "calm" : sig.level === 1 ? "neutral" : "fear"}">
      <div class="row"><span class="t">사도 되는 신호 ${infoBtn("signals")}</span><span class="lv">${sig.level}단계</span></div>
      <div class="lamps">${lights}</div>
      <p class="guide">${esc(sig.guide)}</p><p>${esc(sig.next)}</p>${sig.applies ? "" : `<p><small>지금은 이미 상승 추세라 이 신호보다 아래 '장의 전환'(꺾임 경고)을 보세요.</small></p>`}
      ${strip}${infoBox("signals")}</div>`;
  // 장의 전환: 다음 장세까지 남은 조건
  const gap = Math.max(-10, Math.min(10, tr.gap20));
  const gauge = `<div class="gapbar"><span class="mid"></span><i style="left:${50 - gap * 5}%"></i></div><div class="barlab"><span style="text-align:left">평균선 아래</span><span>20일 평균선</span><span style="text-align:right">위</span></div>`;
  const turnBox = `<div class="sum neutral turnbox"><div class="row"><span class="t">장의 전환 ${infoBtn("transition")}</span><span class="lv">${esc(tr.from)} → ${esc(tr.to)}</span>${tr.near ? `<span class="tag new">전환 임박</span>` : ""}</div>
      <p>코스피가 20일 평균선보다 ${tr.gap20 > 0 ? `${num(tr.gap20, 1)}% 아래` : `${num(-tr.gap20, 1)}% 위`}, 60일 평균선보다 ${tr.gap60 > 0 ? `${num(tr.gap60, 1)}% 아래` : `${num(-tr.gap60, 1)}% 위`}</p>
      ${gauge}${checkList(tr.items)}<p><small>전환 조건 ${tr.met}/${tr.items.length}</small></p>${infoBox("transition")}</div>`;
  box.innerHTML = sigBox + `<ul class="fit sigstages">${stageCards}</ul>` + turnBox +
    `<p class="hint">급락 뒤 바닥은 보통 '투매 진정 → 수급 전환 → 추세 확인' 순서로 확인됩니다. 단계마다 나눠서 들어가는 가이드이고, 매수 추천이 아닙니다.</p>`;
}

/* ---------------- 지금 장세와 시나리오 ---------------- */

const FIT_TXT = { good: ["◎", "잘 맞음"], ok: ["△", "조심해서"], bad: ["✕", "안 맞음"] };
const REGIME_TONE = { up: "calm", range: "neutral", down: "fear", panic: "fear" };
const REGIME_NAME = { up: "상승장", range: "횡보·전환", down: "하락장", panic: "패닉" };
const INV_PRODUCTS = "예: KODEX 인버스(114800, 코스피200 1배) · KODEX 200선물인버스2X(252670, 2배, 하루~며칠만) · KODEX 코스닥150선물인버스(251340)";

// 성적표의 장세별 기록 (5일 뒤)
function regimeStat(key, g) {
  const r = ((state.score || {}).by_regime || {})[key];
  const x = r && r.groups[g] && r.groups[g]["5"];
  return x ? { ...x, days: r.days } : null;
}

function regimeRecord(key, g) {
  const x = regimeStat(key, g);
  if (!x) return "이 장세 기록 아직 없음";
  return `이 장세 기록: 5일 뒤 코스피 대비 ${signed(x.excess, 1, "%")} · 오른 비율 ${x.win}% (${x.n}건, ${x.days}일)${x.days < 10 ? " — 아직 참고만" : ""}`;
}

// 규칙과 실제 기록이 반대로 나오면 알려 준다 (표본 10건 이상일 때만)
function mismatch(grade, x) {
  if (!x || x.n < 10 || x.excess == null) return "";
  if (grade === "good" && x.excess < 0) return "기록은 규칙과 반대: 이 장세에서 코스피보다 못했습니다.";
  if (grade === "bad" && x.excess > 2) return "기록은 규칙과 반대: 이 장세에서 코스피보다 나았습니다.";
  return "";
}

const checkList = (items) => `<ul class="chk">${items.map((c) =>
  `<li class="${c.ok ? "ok" : "no"}"><span>${c.ok ? "✓" : "✗"} ${esc(c.label)}</span><b>${esc(c.value)}</b></li>`).join("")}</ul>`;

// 최근 장세 흐름 띠와 '몇 일째'
function regimeStrip(key) {
  const rs = (state.score || {}).regimes || {};
  const ds = Object.keys(rs).sort().filter((d) => d <= state.data.base_date).slice(-20);
  if (!ds.length) return "";
  let run = 0;
  for (let i = ds.length - 1; i >= 0 && rs[ds[i]] === key; i--) run++;
  const cells = ds.map((d) => `<i class="rg-${rs[d]}" title="${ymd(d)} ${REGIME_NAME[rs[d]]}"></i>`).join("");
  return `<div class="rgstrip"><div class="cells">${cells}</div><small>최근 ${ds.length}일 장세 흐름${run ? ` · ${REGIME_NAME[key]} ${run}일째` : ""}</small>` +
    `<div class="legend">${Object.entries(REGIME_NAME).map(([k, n]) => `<span><i class="rg-${k}"></i>${n}</span>`).join("")}</div></div>`;
}

function regimeTable(cur) {
  const br = (state.score || {}).by_regime || {};
  const keys = ["up", "range", "down", "panic"].filter((k) => br[k]);
  if (!keys.length) return "";
  const cell = (k, g) => {
    const x = regimeStat(k, g);
    return x ? `<td class="${cls(x.excess)}">${signed(x.excess, 1, "%")}<small>${x.n}건</small></td>` : `<td class="na">–</td>`;
  };
  const rows = keys.map((k) => `<tr class="${k === cur ? "cur" : ""}"><th>${REGIME_NAME[k]} <small>${br[k].days}일</small></th>${["swing", "scalp", "value"].map((g) => cell(k, g)).join("")}</tr>`).join("");
  return `<details class="rules"><summary>장세별 성적 전체 보기</summary><table class="score rgtbl"><thead><tr><th></th><th>스윙</th><th>단타</th><th>저평가</th></tr></thead><tbody>${rows}</tbody></table>` +
    `<p class="hint">필터에 걸린 날의 장세별로, 5일 뒤 코스피보다 얼마나 더 올랐는지(코스피보다 더 번 정도) 평균입니다. 날짜가 적으면 우연이 크게 섞입니다.</p></details>`;
}

function renderRegime() {
  const rg = state.data.temperature.regime;
  const box = $("#regime-body");
  document.querySelectorAll(".tabs button").forEach((b) => { if (rg) b.dataset.fit = rg.fit[b.dataset.tab].grade; else delete b.dataset.fit; });
  if (!rg) { box.innerHTML = `<div class="empty">이 날짜는 장세 판정이 생기기 전 기록입니다.</div>`; return; }
  const tr = rg.trend;
  const head = `<div class="sum ${REGIME_TONE[rg.key]}"><div class="row"><span class="t">지금 장세 ${infoBtn("regime")}</span><span class="lv">${esc(rg.label)}</span></div>` +
    `<p>${esc(rg.reason || `코스피 ${num(tr.close, 2)} · 20일 평균 ${num(tr.ma20, 0)} · 60일 평균 ${num(tr.ma60, 0)}`)}</p>${infoBox("regime")}${regimeStrip(rg.key)}</div>`;

  // 왜 이렇게 판단했나: 1단계 패닉 조건 → 2단계 추세 조건 → 바뀌려면
  const why = rg.panic_checks ? `<details class="rules why-box" open><summary>왜 이렇게 판단했나</summary>
      ${rg.chart ? lineChart(rg.chart) : ""}
      <h3>① 급락·공포 신호 <small>두 개 이상 켜지면 패닉. 하나만 켜져도 인버스를 뒤쫓아 사는 건 조심</small></h3>${checkList(rg.panic_checks)}
      <h3>② 오르는 흐름인가 <small>셋 다 ✓면 상승장, 셋 다 ✗면 하락장, 섞여 있으면 횡보·전환</small></h3>${checkList(rg.trend_checks)}
      <h3>판정이 바뀌려면</h3><p class="next">${esc(rg.next)}</p></details>` : "";

  const rows = ["swing", "scalp", "value"].map((g) => {
    const f = rg.fit[g], [ic, lab] = FIT_TXT[f.grade], mm = mismatch(f.grade, regimeStat(rg.key, g));
    return `<li class="fit-${f.grade}"><div class="row"><b>${ic} ${TAB_NAME[g]}</b><span>${lab}</span></div><p>${esc(f.why)}</p>` +
      `<small>${regimeRecord(rg.key, g)}</small>${mm ? `<p class="mm">⚠ ${mm}</p>` : ""}` +
      (f.evidence ? `<details class="ev"><summary>왜?</summary><p>${esc(f.evidence)}</p><small>근거: ${esc(f.source)}</small></details>` : "") + `</li>`;
  }).join("");

  const iv = rg.inverse, fi = rg.fit.inverse;
  const ivTone = { consider: "fit-good", caution: "fit-bad", watch: "fit-ok", no: "fit-bad" }[iv.verdict];
  const ivChecks = iv.checks.map((c) => ({ value: "", ...c }));
  const inv = `<li class="${ivTone} inv"><div class="row"><b>${FIT_TXT[fi.grade][0]} 인버스 ${infoBtn("inverse")}</b><span>${esc(iv.label)}</span></div>` +
    `<p>${esc(iv.text)}</p>${checkList(ivChecks)}` +
    `<small>하락 신호 ${iv.met}/${iv.checks.length}${iv.blockers.length ? ` · 추격 주의: ${iv.blockers.map(esc).join(", ")}` : ""}</small>` +
    (iv.next ? `<p class="next">${esc(iv.next)}</p>` : "") +
    (fi.evidence ? `<details class="ev"><summary>왜?</summary><p>${esc(fi.evidence)}</p><small>근거: ${esc(fi.source)}</small></details>` : "") +
    `<p class="prod">${INV_PRODUCTS}. 2배 상품은 출렁이는 장에서 지수가 제자리여도 손실이 쌓입니다.</p>${infoBox("inverse")}</li>`;

  box.innerHTML = head + why + `<h3 class="fit-h">이 장세에서 시나리오별 적합도</h3><ul class="fit">${rows}${inv}</ul>` + regimeTable(rg.key) +
    `<p class="hint">연구 결과를 바탕으로 한 경향이고 매수·매도 추천이 아닙니다. '이 장세 기록'이 쌓이면 실제로 맞는지 확인할 수 있습니다.</p>`;
}

/* ---------------- 관심 종목 (☆ 눌러 저장, 이 브라우저에만) ---------------- */

const starBtn = (x) => {
  const on = !!state.watch[x.code];
  return `<button class="star${on ? " on" : ""}" type="button" data-code="${x.code}" data-name="${esc(x.name)}" data-price="${x.close ?? ""}" aria-pressed="${on}" aria-label="관심 종목${on ? " 해제" : "에 추가"}">${on ? "★" : "☆"}</button>`;
};

// ★ 누른 날 가격 대비 지금(가장 최근 수집일 종가) 몇 %인지, 같은 기간 코스피와 함께
function watchPerf(c) {
  const from = state.watchFrom[c], p = state.prices;
  const now = p && p.close ? p.close[c] : null;
  if (!from || !from.price || now == null || p.date <= from.date) return from ? ` <small class="perf">${ymd(from.date)}부터</small>` : "";
  const r = (now / from.price - 1) * 100;
  const k = from.kospi && p.index && p.index.kospi ? (p.index.kospi / from.kospi - 1) * 100 : null;
  return ` <small class="perf">${ymd(from.date).slice(5)}부터 <b class="${cls(r)}">${signed(r, 1, "%")}</b>${k != null ? ` (코스피 ${signed(k, 1, "%")})` : ""}</small>`;
}

function renderWatch() {
  const codes = Object.keys(state.watch);
  if (!codes.length) { $("#watch").innerHTML = ""; return; }
  const f = state.data.filter;
  const rows = codes.map((c) => {
    const groups = ["swing", "scalp", "value"].filter((g) => (f[g] || []).some((y) => y.code === c));
    const name = esc(state.watch[c]);
    const perf = watchPerf(c);
    return groups.length
      ? `<li class="hit"><button class="more" type="button" data-code="${c}">${name}</button> <span class="tag both">${groups.map((g) => TAB_NAME[g]).join("·")}</span>${perf}</li>`
      : `<li class="miss">${name} <small>조건 밖</small>${perf}</li>`;
  });
  const hits = rows.filter((r) => r.startsWith('<li class="hit"')).length;
  $("#watch").innerHTML = `<div class="watch"><div class="l">관심 종목 <small>${hits ? `오늘 ${hits}개 걸림` : "오늘 걸린 종목 없음"}</small></div><ul>${rows.join("")}</ul></div>`;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button.star");
  if (!b) return;
  const c = b.dataset.code;
  if (state.watch[c]) {
    delete state.watch[c];
    delete state.watchFrom[c];
  } else {
    state.watch[c] = b.dataset.name;
    const k = ((state.data.temperature || {}).index || {}).kospi;
    if (b.dataset.price) state.watchFrom[c] = { price: Number(b.dataset.price), date: state.data.base_date, kospi: k ? k.close : null };
  }
  try {
    localStorage.setItem("watch", JSON.stringify(state.watch));
    localStorage.setItem("watchFrom", JSON.stringify(state.watchFrom));
  } catch (err) { /* 무시 */ }
  const on = !!state.watch[c];
  document.querySelectorAll(`button.star[data-code="${c}"]`).forEach((s) => {
    s.classList.toggle("on", on); s.textContent = on ? "★" : "☆"; s.setAttribute("aria-pressed", on);
  });
  renderWatch();
  renderToday();
});

function renderOverlap() {
  const f = state.data.filter;
  const codes = f.overlap || [];
  if (!codes.length) { $("#overlap").innerHTML = ""; return; }
  const all = [...(f.swing || []), ...(f.scalp || []), ...(f.value || [])];
  const names = codes.map((c) => {
    const x = all.find((y) => y.code === c);
    const groups = ["swing", "scalp", "value"].filter((g) => (f[g] || []).some((y) => y.code === c)).map((g) => TAB_NAME[g]);
    return `<b>${esc(x ? x.name : c)}</b> <small>(${groups.join("·")})</small>`;
  });
  $("#overlap").innerHTML = `<div class="overlap">두 관점에 함께 걸린 종목: ${names.join(", ")}<br><small>돈이 들어오는 흐름(스윙·단타)과 공포 속 저평가에 동시에 해당</small></div>`;
}

function valueCard(x) {
  const news = newsList(x.news);
  return `<article class="card">
    ${cardHead(x)}
    ${scoreRow(x)}
    <div class="nums">
      <div><small>60일 고점 대비</small><b class="down">-${num(x.drop_60d, 1)}%</b></div>
      <div><small>PBR (1년 중앙)</small><b>${num(x.pbr, 2)} <small style="display:inline;font-size:12px;color:var(--sub)">(${num(x.pbr_med, 2)})</small></b></div>
      <div><small>5일 기관+외국인</small><b class="${cls(x.flow5_eok)}">${signed(x.flow5_eok, 0, "억")}</b></div>
    </div>
    <ul class="checks">${x.checks.map((c) => `<li><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul>
    ${wakeBlock(x.wake)}
    ${exitBlock(x)}
    ${reportBlock(x)}
    ${qualityRow(x)}
    ${news ? `<ul class="news">${news}</ul>` : `<p class="hint" style="margin:8px 0 0">최근 3일 관련 기사 없음</p>`}
    ${detailBtn(x)}
  </article>`;
}

// 증권사 리포트: 최근 2주에 나온 것, 오랜만에 다뤄지기 시작했는지, 가장 최근 리포트의 의견·목표가
const OPINION = { buy: "매수", "strong buy": "강력 매수", outperform: "시장보다 좋음", hold: "보유", neutral: "중립", marketperform: "시장 수준", sell: "매도", underperform: "시장보다 나쁨", "trading buy": "단기 매수" };
function reportBlock(x) {
  const rp = x.reports;
  if (!rp) return "";
  if (!rp.n) return `<div class="rep"><small class="faint">최근 2주 증권사 리포트 없음${rp.known && !rp.before ? " (석 달 넘게 아무도 안 다룸)" : ""}</small></div>`;
  const top = rp.items[0], d = rp.detail || {};
  const op = d.opinion ? OPINION[d.opinion.toLowerCase()] || d.opinion : null;
  const goal = d.goal ? `목표가 <b>${num(d.goal)}원</b>${d.prev_goal && d.prev_goal !== d.goal ? ` <small class="${cls(d.goal - d.prev_goal)}">(${d.goal > d.prev_goal ? "올림" : "내림"}, 전 ${num(d.prev_goal)})</small>` : ""}${x.close ? ` · 지금보다 <b class="${cls(d.goal - x.close)}">${signed((d.goal / x.close - 1) * 100, 0, "%")}</b>` : ""}` : "";
  const more = rp.items.slice(1).map((r) => `<li><a href="https://m.stock.naver.com/research/company/${r.id}" target="_blank" rel="noopener">${esc(r.title)}</a><small>${esc(r.broker)} ${ymd(r.date).slice(5)}</small></li>`).join("");
  return `<div class="rep"><div class="row"><b>증권사 리포트 ${rp.n}건 <small>(2주)</small>${infoBtn("report")}</b>${rp.fresh ? `<span class="tag fresh">오랜만에 다뤄짐</span>` : ""}</div>${infoBox("report")}
    <a href="https://m.stock.naver.com/research/company/${top.id}" target="_blank" rel="noopener">${esc(top.title)}</a>
    <small>${esc(top.broker)} ${ymd(top.date).slice(5)}${op ? ` · 의견 ${esc(op)}` : ""}</small>
    ${goal ? `<div class="goal">${goal}</div>` : ""}
    ${d.summary ? `<details class="rsum"><summary>리포트 요약 보기</summary><p>${esc(d.summary)}</p></details>` : ""}
    ${more ? `<ul class="news">${more}</ul>` : ""}</div>`;
}

// 언제 정리할지 + 이번 실적 시즌 발표 여부
function exitBlock(x) {
  const e = x.exit, es = x.earn_season;
  let earn = "";
  if (es && es.state === "pending") {
    earn = `<div class="earnwarn">⚠ ${esc(es.label)} 실적 아직 안 나옴 — ${ymd(es.due).slice(5)}까지 발표${es.days_left >= 0 ? ` (${es.days_left}일 남음)` : ""}.${es.notice ? ` <a href="${esc(es.notice.url || "#")}" target="_blank" rel="noopener">발표 예고 공시 ${ymd(es.notice.date).slice(5)}</a>` : ""} 발표 전후로 크게 출렁일 수 있음${infoBtn("earn_season")}</div>${infoBox("earn_season")}`;
  } else if (es && es.state === "done") {
    earn = `<div class="earnok">${esc(es.label)} 실적 발표함 (${ymd(es.date).slice(5)})</div>`;
  }
  if (!e) return earn ? `<div class="exit">${earn}</div>` : "";
  return `<div class="exit"><div class="row"><b>정리 기준${infoBtn("exit")}</b><span><b>${num(e.stop)}원</b> 아래 마감 시 <small class="down">(${signed(e.gap, 1, "%")})</small></span></div>${infoBox("exit")}
    <small>${esc(e.rule)} — ${esc(e.why)}. 들고 가는 기간 ${esc(e.hold)}.</small>${earn}</div>`;
}

// 싼 종목이 다시 주목받기 시작했나 (가치 함정 거르기용, 점수엔 안 들어감)
function wakeBlock(w) {
  if (!w) return "";
  const tone = w.met >= 2 ? "good" : w.met === 1 ? "" : "bad";
  const msg = w.met >= 2 ? "시장이 다시 보기 시작한 신호가 있음" : w.met === 1 ? "신호가 하나뿐 — 좀 더 지켜보기" : "아직 아무도 안 보는 중 — 싼 채로 오래 갈 수 있음";
  return `<div class="wake"><div class="row"><b>주목 신호 <span class="${tone}">${w.met}/${w.total}</span>${infoBtn("wake")}</b><small>${msg}</small></div>${infoBox("wake")}
    <ul class="checks">${w.checks.map((c) => `<li class="${c.ok ? "" : "no"}"><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul></div>`;
}

function cardHead(x) {
  return `<div class="hd">
      <a class="nm" data-code="${x.code}" href="https://m.stock.naver.com/domestic/stock/${x.code}" target="_blank" rel="noopener">${esc(x.name)}<span class="mk">${esc(x.market)} ${x.code}${x.sector ? " · " + esc(x.sector) : ""}</span></a>
      <div class="px">${starBtn(x)}<b>${num(x.close)}</b><span class="${cls(x.change)}">${signed(x.change, 1, "%")}</span></div>
    </div>${streakTag(x)}${x.search_rank ? `<span class="tag known" title="네이버 증권에서 많이 검색한 상위 100개 안. 이미 많이 알려져 미리 사 두는 기회는 지났을 수 있음">검색 ${x.search_rank}위 · 이미 알려짐</span>` : ""}${x.overlap ? `<span class="tag both">두 관점 겹침</span>` : ""}`;
}

// 같은 탭에 며칠 연속 걸렸는지: 오늘 처음이면 '새로', 아니면 'N일째'
function streakTag(x) {
  if (x.streak_days == null) return "";
  return x.streak_days <= 1 ? `<span class="tag new">새로</span>`
    : `<span class="tag stay" title="${x.since ? ymd(x.since) + "부터" : ""}">${x.streak_days}일째</span>`;
}

function newsList(items) {
  return (items || []).map((n) => {
    const at = n.at.slice(5, 16).replace("T", " ").replace("-", "/");
    const tag = (n.kind === "공시" ? `<span class="tag dart">공시</span>` : "") +
      (n.earn ? `<span class="tag earn-${n.earn}" title="${esc(n.earn_text || "")}">실적${n.earn === "up" ? "↑" : "↓"}</span>` : "");
    return `<li>${tag}<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a><small>${esc(n.source)} ${at}</small></li>`;
  }).join("");
}

// 정렬용 점수: 탭마다 공식·단위가 달라 숫자 자체보다 탭 안 순위와 1위 대비 크기로 보여 준다
function scoreFormula(x) {
  if (state.tab === "swing") {
    const ratio = parseFloat((x.checks[1] || {}).value);
    return `시총 대비 5일 순매수 ${num(ratio, 2)}% × (1 + 연속 ${x.flow.streak}일 ÷ 5)`;
  }
  if (state.tab === "scalp" && x.score_parts) {
    const p = x.score_parts;
    return `큰손 확인 ${p.flow} · 거래 증가 ${p.volume} · 상승폭 ${p.move}점의 가중평균 (50·25·25%)`;
  }
  if (state.tab === "scalp") {
    const extra = x.value_mult ? Math.max(0, (x.score / x.value_mult - 1) * 100) : 0;
    return `거래대금 평소의 ${num(x.value_mult, 1)}배 × (1 + 오늘 순매수/거래대금 ${num(extra, 1)}%)`;
  }
  const p = x.score_parts;
  if (p) return `낙폭 ${p.drop} · 싸짐 ${p.cheap} · PER ${p.per} · 이익 ${p.eps}점의 가중평균 (25·15·35·25%)` +
    (x.eps_fwd_ratio != null ? ` · 이익엔 올해 추정 EPS(지금의 ${num(x.eps_fwd_ratio, 2)}배)를 반 섞음` : "");
  return `60일 고점 대비 ${num(x.drop_60d, 1)}% 하락 × (1년 중앙 PBR ${num(x.pbr_med, 2)} ÷ 지금 ${num(x.pbr, 2)}) (예전 공식)`;
}

function scoreRow(x) {
  const list = state.data.filter[state.tab] || [];
  const i = list.findIndex((y) => y.code === x.code);
  if (i < 0 || x.score == null) return "";
  const total = state.data.filter[state.tab + "_total"] || list.length;
  const raw = `계산값 ${num(x.score, x.score >= 10 ? 1 : 2)} = ${scoreFormula(x)}`;
  // 강도: 지금까지 이 탭 후보들 중 몇 % 위치인지 (탭끼리 같은 0~100 눈금). 예전 기록엔 없어서 1위 대비로 대신
  if (x.score_pct != null) {
    return `<div class="scorerow"><div class="row"><span>강도 <b>${x.score_pct}점</b> · 오늘 ${i + 1}위/${total} ${infoBtn("score_" + state.tab)}</span>` +
      `<small>역대 상위 ${Math.max(1, 100 - x.score_pct)}%</small></div>` +
      `<div class="sbar"><i style="width:${Math.max(3, x.score_pct)}%"></i></div>` +
      `<small class="f">역대 ${TAB_NAME[state.tab]} 후보 ${num(x.score_pool)}개 기준 · ${raw}</small>${infoBox("score_" + state.tab)}</div>`;
  }
  const top = list[0].score || 1, rel = Math.round((x.score / top) * 100);
  return `<div class="scorerow"><div class="row"><span>오늘 ${i + 1}위/${total} ${infoBtn("score_" + state.tab)}</span><small>1위 대비 ${rel}%</small></div>` +
    `<div class="sbar"><i style="width:${Math.max(3, Math.min(100, rel))}%"></i></div><small class="f">${raw}</small>${infoBox("score_" + state.tab)}</div>`;
}

function card(x) {
  if (state.tab === "value") return valueCard(x);
  const fl = x.flow;
  const both5 = (fl.foreign_5d || 0) + (fl.inst_5d || 0);
  const bothToday = (fl.foreign_today || 0) + (fl.inst_today || 0);
  const nums = state.tab === "swing"
    ? [["5일 기관+외국인", `<span class="${cls(both5)}">${signed(both5, 0, "억")}</span>`],
       ["연속 순매수", `${fl.streak}일`],
       ["외국인 지분율", x.quality && x.quality.fratio != null ? `${num(x.quality.fratio, 1)}% <small style="display:inline;font-size:11px" class="${cls(x.quality.fratio_chg)}">${signed(x.quality.fratio_chg, 2, "p")}</small>` : "–"]]
    : [["거래대금", `${num(x.value_eok, 0)}억`],
       ["평소 대비", `${num(x.value_mult, 1)}배`],
       ["오늘 기관+외국인", `<span class="${cls(bothToday)}">${signed(bothToday, 0, "억")}</span>`]];
  const news = newsList(x.news);
  return `<article class="card">
    ${cardHead(x)}
    ${scoreRow(x)}
    <div class="nums">${nums.map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join("")}</div>
    <div class="l" style="font-size:12px;color:var(--sub)">최근 5일 기관+외국인 순매수 (억)</div>
    ${bars(fl.daily)}
    ${qualityRow(x)}
    <ul class="checks">${x.checks.map((c) => `<li><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul>
    ${exitBlock(x)}
    ${reportBlock(x)}
    <ul class="news">${news}</ul>
    ${detailBtn(x)}
  </article>`;
}

// 점수가 쓸모 있는지: 같은 날·같은 탭 후보를 점수순으로 반 나눠, 위 절반과 아래 절반의 5일 뒤 성적 비교
function scoreCheckTable(chk) {
  if (!chk) return "";
  const rows = ["swing", "scalp", "value"].filter((g) => chk[g]).map((g) => {
    const c = (h) => {
      const x = (chk[g][h] || {})["5"];
      return x ? `<td><b class="${cls(x.excess)}">${signed(x.excess, 1, "%p")}</b><br><small>상승 ${x.win}% · ${x.n}건</small></td>` : `<td class="na">–</td>`;
    };
    const hi = (chk[g].hi || {})["5"], lo = (chk[g].lo || {})["5"];
    const gap = hi && lo && hi.excess != null && lo.excess != null ? hi.excess - lo.excess : null;
    const verdict = gap == null ? "–" : Math.min(hi.n, lo.n) < 30 ? "기록 부족" : gap > 1 ? "점수 높을수록 나음" : gap < -1 ? "오히려 반대" : "차이 없음";
    return `<tr><th>${TAB_NAME[g]}</th>${c("hi")}${c("lo")}<td><small>${verdict}</small></td></tr>`;
  }).join("");
  return `<h3 class="fit-h">점수가 쓸모 있나</h3><div class="tile wide"><table class="score">
    <thead><tr><th></th><th>점수 위 절반</th><th>아래 절반</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">같은 날 같은 탭 후보를 점수순으로 반 나눠 5일 뒤 코스피 대비 성적(초과)을 비교합니다. 위 절반이 꾸준히 나아야 점수를 믿을 수 있습니다. 탭마다 30건 이상 쌓이기 전에는 우연이 큽니다.</p>`;
}

function renderScore() {
  const sc = state.score;
  if (!sc || !sc.stats) {
    $("#scorecard").innerHTML = `<div class="empty">아직 기록이 없습니다.</div>`;
    return;
  }
  const hs = sc.horizons || [1, 5, 20];
  const rows = ["swing", "scalp", "value"].map((g) => {
    const st = sc.stats[g] || {};
    const cells = hs.map((h) => {
      const s = st[String(h)];
      if (!s) return `<td class="na">–</td>`;
      return `<td><b class="${cls(s.avg)}">${signed(s.avg, 1, "%")}</b><br><small>상승 ${s.win}% · 초과 <span class="${cls(s.excess)}">${signed(s.excess, 1, "%p")}</span> · ${s.n}건</small></td>`;
    }).join("");
    return `<tr><th>${TAB_NAME[g]}</th>${cells}</tr>`;
  }).join("");
  const minN = Math.min(...["swing", "scalp", "value"].map((g) => ((sc.stats[g] || {})["5"] || { n: 0 }).n));
  const warn = minN < 30 ? `<p class="hint">기록이 ${sc.days}일치뿐이라 아직 기록이 적습니다 (30건이 안 되는 칸이 있음). 지금 숫자는 우연일 수 있으니 참고만 하세요.</p>` : "";
  const recent = (sc.recent || []).filter((e) => e.r1 != null).slice(0, 15).map((e) =>
    `<tr><td>${e.date.slice(4, 6)}/${e.date.slice(6)}</td><td>${TAB_NAME[e.group]}</td><td style="text-align:left">${esc(e.name)}</td>` +
    hs.map((h) => `<td class="${cls(e["r" + h])}">${e["r" + h] == null ? "–" : signed(e["r" + h], 1, "%")}</td>`).join("") + "</tr>").join("");
  const m = sc.method;
  const how = m ? `<p class="hint">걸린 다음 날 아침 첫 가격(시가)에 사서 N일 뒤 마감 가격(종가)에 팔았다면 어땠는지입니다. 수수료·세금 ${m.cost}%는 뺐습니다. '초과'는 같은 기간 그냥 지수(코스피·코스닥)를 샀을 때보다 얼마나 더 벌었나입니다. 같은 종목이 며칠 연속 걸리면 한 번만 셉니다.</p>`
    : `<p class="hint">기준일 종가 기준, 코스피 대비 성적입니다 (예전 계산 방식).</p>`;
  const bf = (sc.backfill_dates || []).length
    ? `<p class="hint">${sc.backfill_dates.length}일(${ymd(sc.backfill_dates[0])}~${ymd(sc.backfill_dates.at(-1))})은 나중에 다시 계산한 기록이라, 그날 실제로 보이던 뉴스와 달라 스윙·단타 성적은 조금 할인해서 보세요.</p>` : "";
  $("#scorecard").innerHTML = how + bf + `<div class="tile wide"><table class="score">
      <thead><tr><th></th>${hs.map((h) => `<th>${h}일 뒤</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>${warn}
    ${scoreCheckTable(sc.score_check)}
    ${stopTable(sc.stops)}
    ${featureTable(sc.features)}
    ${recent ? `<details class="rules"><summary>최근 기록 보기</summary><table class="score recent"><thead><tr><th>날짜</th><th>구분</th><th style="text-align:left">종목</th>${hs.map((h) => `<th>${h}일</th>`).join("")}</tr></thead><tbody>${recent}</tbody></table></details>` : ""}`;
}

// 정리 기준을 지켰다면: 그냥 들고 있기 vs 기준 아래로 마감한 날 팔기 (5일, 시장 지수 대비)
function stopTable(st) {
  if (!st) return "";
  const rows = ["swing", "scalp", "value"].filter((g) => st[g] && st[g]["5"] && st[g]["5"].hold).map((g) => {
    const a = st[g]["5"];
    const c = (x) => `<td><b class="${cls(x.excess)}">${signed(x.excess, 1, "%p")}</b><br><small>상승 ${x.win}% · ${x.n}건</small></td>`;
    const gap = a.stop.excess != null && a.hold.excess != null ? a.stop.excess - a.hold.excess : null;
    const verdict = gap == null ? "–" : a.hold.n < 30 ? "기록 부족" : gap > 0.5 ? "지키는 게 나음" : gap < -0.5 ? "들고 있는 게 나음" : "차이 없음";
    return `<tr><th>${TAB_NAME[g]}</th>${c(a.hold)}${c(a.stop)}<td><small>${a.hit}% 정리<br>${verdict}</small></td></tr>`;
  }).join("");
  if (!rows) return "";
  return `<h3 class="sub">정리 기준을 지켰다면 <small>5일 기준, 시장 지수 대비</small></h3>
    <div class="tile wide"><table class="score"><thead><tr><th></th><th>그냥 5일 들고 있기</th><th>기준 깨지면 팔기</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">카드의 '정리 기준'(종가가 그 가격 아래로 마감하면 그날 종가에 팔기)을 지켰을 때와 그냥 들고 있었을 때 비교입니다. '정리' %는 5일 안에 기준이 깨진 비율입니다.</p>`;
}

/* ---------------- 아침 요약 ---------------- */

function renderMorning() {
  const m = state.morning, el = $("#morning");
  if (!el) return;
  // 가장 최근 수집일을 보고 있고, 그 뒤 아침에 만든 요약일 때만 (저녁 수집 뒤엔 숨김)
  if (!m || !state.data || state.data.base_date !== state.latest || m.date <= state.data.base_date || !m.items.length) {
    el.hidden = true;
    return;
  }
  const sm = { ...(m.summary || {}) };
  // 주말에 만든 요약이면 '오늘 아침' 대신 '다음 장'으로
  const wd = new Date(`${m.date.slice(0, 4)}-${m.date.slice(4, 6)}-${m.date.slice(6, 8)}T12:00:00+09:00`).getDay();
  if (wd === 0 || wd === 6) sm.text = (sm.text || "").replace("오늘 아침", "다음 장(월요일) 시작");
  const color = sm.tone === "up" ? "var(--up)" : sm.tone === "down" ? "var(--down)" : "var(--text)";
  const items = m.items.map((x) => `<div><small>${esc(x.name)}</small><b class="${x.key === "vix" || x.key === "usdkrw" ? cls(-x.change) : cls(x.change)}">${signed(x.change, 2, "%")}</b><small class="faint">${num(x.value, x.value < 100 ? 2 : 0)}</small></div>`).join("");
  el.hidden = false;
  el.innerHTML = `<h2>아침 요약 <small>${m.at.slice(5, 10).replace("-", "/")} ${m.at.slice(11, 16)} · 새벽 미국장 마감 기준</small>${infoBtn("morning")}</h2>${infoBox("morning")}
    <div class="tile wide"><p class="msum" style="color:${color}"><b>${esc(sm.label || "")}</b></p><p class="hint" style="margin:2px 0 8px">${esc(sm.text || "")}</p>
    <div class="nums mnums">${items}</div>
    <p class="hint">VIX·환율은 내리면 좋은 쪽이라 색을 반대로 칠했습니다. 짐작일 뿐, 장이 열리면 다를 수 있습니다.</p></div>`;
}

function featureTable(all) {
  return featureTableOf((all || []).filter((f) => !f.key.startsWith("v_")), "스윙·단타 후보") +
    featureTableOf((all || []).filter((f) => f.key.startsWith("v_")), "저평가 후보",
      "싼 종목 중 '주목받는 쪽'이 '아무도 안 보는 쪽'보다 실제로 나았는지 봅니다. '주목 신호 2개 이상'은 10/23 기록부터 셉니다.");
}

function featureTableOf(fs, who, note) {
  if (!fs || !fs.length) return "";
  // 값은 시장 지수 대비 초과 수익, '앞섬'은 지수보다 더 오른 비율
  const cell = (s) => (s ? `<b class="${cls(s.avg)}">${signed(s.avg, 1, "%p")}</b><br><small>앞섬 ${s.win}% · ${s.n}건</small>` : `<span class="na">–</span>`);
  // 한쪽이라도 비어 있는 조건(예: 아직 한 건도 없는 '두 관점 겹침')은 빼고 보여 준다
  const rows = fs.filter((f) => f.yes && f.no).map((f) => `<tr><th>${esc(f.label)}</th><td>${cell(f.yes)}</td><td>${cell(f.no)}</td></tr>`).join("");
  if (!rows) return "";
  return `<h3 class="sub">조건별 성적 <small>${who}, ${fs[0].horizon}일 뒤 시장 지수 대비 초과</small></h3>
    <div class="tile wide"><table class="score"><thead><tr><th></th><th>조건 있음</th><th>없음</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">${note ? note + " " : ""}기록이 쌓이면 어떤 조건이 실제로 도움이 됐는지 보입니다. 건수가 적을 때는 참고만 하세요.</p>`;
}

/* ---------------- 업종·제외·상세 ---------------- */

function renderSectors() {
  const s = state.data.sectors;
  if (!s || (!s.inflow.length && !s.outflow.length)) {
    $("#sector-list").innerHTML = `<div class="empty">이 날짜는 업종 데이터가 없습니다.</div>`;
    return;
  }
  // 들어온 쪽·빠져나간 쪽을 각자 가장 큰 값에 맞춘다
  const scale = (list) => Math.max(1, ...list.map((r) => Math.abs(r.flow_eok)));
  const row = (r, max) => {
    const w = Math.max(2, (Math.abs(r.flow_eok) / max) * 100);
    const names = r.flow_eok > 0 ? r.top : r.bottom;
    return `<div class="sec"><div class="sec-hd"><b>${esc(r.sector)}</b><span class="${cls(r.flow_eok)}">${signed(r.flow_eok, 0, "억")}</span></div>
      <div class="sec-bar"><i style="width:${w}%;background:var(--${r.flow_eok > 0 ? "up" : "down"})"></i></div>
      <small>${r.n}종목 · 5일 등락(가운데 값) <span class="${cls(r.rise_5d)}">${signed(r.rise_5d, 1, "%")}</span>${names.length ? " · " + names.map(esc).join(", ") : ""}</small></div>`;
  };
  $("#sector-list").innerHTML = `<div class="tile wide">${s.inflow.map((r) => row(r, scale(s.inflow))).join("")}</div>` +
    (s.outflow.length ? `<details class="rules"><summary>빠져나간 업종 보기</summary><div class="tile wide">${s.outflow.map((r) => row(r, scale(s.outflow))).join("")}</div></details>` : "");
}

function renderExcluded() {
  const ex = state.data.filter.excluded || [];
  if (!ex.length) { $("#excluded").innerHTML = ""; return; }
  $("#excluded").innerHTML = `<details class="rules"><summary>위험 공시로 뺀 종목 ${ex.length}개</summary><ul class="exlist">` +
    ex.map((x) => `<li><b>${esc(x.name)}</b> <small>${x.reasons.map(esc).join(" / ")}</small></li>`).join("") + `</ul></details>`;
}

function qualityRow(x) {
  const q = x.quality;
  if (!q) return "";
  const it = (k, v) => `<span>${k} <b class="${cls(v)}">${signed(v, 0, "억")}</b></span>`;
  return `<div class="qrow"><small>5일 기관 세부</small>${it("연기금", q.pension_5d)}${it("투신", q.trust_5d)}${it("사모", q.private_5d)}</div>`;
}

function detailBtn(x) {
  return x.chart ? `<button class="more" type="button" data-code="${x.code}">차트·상세 보기</button>` : "";
}

function lineChart(ch) {
  const series = [["close", "var(--text)", 1.8], ["ma20", "var(--up)", 1.2], ["ma60", "var(--down)", 1.2]];
  const all = series.flatMap(([k]) => ch[k]).filter((v) => v != null);
  const min = Math.min(...all), max = Math.max(...all), w = 320, h = 150, n = ch.close.length;
  const y = (v) => h - 8 - ((v - min) / (max - min || 1)) * (h - 16);
  const lines = series.map(([k, c, sw]) => {
    const pts = ch[k].map((v, i) => (v == null ? null : `${(i / (n - 1)) * w},${y(v)}`)).filter(Boolean).join(" ");
    return `<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="${sw}" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="60일 주가와 이동평균">${lines}</svg>
    <div class="legend"><span><i style="background:var(--text)"></i>종가</span><span><i style="background:var(--up)"></i>20일 평균선</span><span><i style="background:var(--down)"></i>60일 평균선</span>
    <span class="rng">${num(min)} ~ ${num(max)}</span></div>
    <div class="barlab"><span style="text-align:left">${ymd(ch.dates[0])}</span><span style="text-align:right">${ymd(ch.dates[n - 1])}</span></div>`;
}

// 재무 지표 뜻 (상세의 '지표 설명 보기')
const FIN_INFO = {
  "시가총액": "주가 × 주식 수. 시장이 매기는 회사 전체 값입니다.",
  "PER": "주가 ÷ 주당순이익. 지금 이익으로 회사 값을 몇 년이면 버는지입니다. 낮을수록 싸지만, 이익이 줄 회사는 낮아 보여도 비쌀 수 있습니다. 적자면 계산되지 않습니다.",
  "PBR": "주가 ÷ 주당순자산. 1보다 낮으면 회사가 가진 순자산보다 싸게 거래된다는 뜻입니다.",
  "배당수익률": "1년 배당금 ÷ 주가. 은행 이자처럼 받는 몫입니다.",
  "52주 중 위치": "최근 1년 최저가를 0%, 최고가를 100%로 놓았을 때 지금 주가가 어디쯤인지입니다.",
  "매출액": "물건·서비스를 팔아 들어온 돈 전체입니다. 회사 덩치와 성장을 봅니다.",
  "영업이익": "매출에서 원가·인건비·판매비를 뺀 본업 이익입니다. 마이너스면 본업에서 적자입니다.",
  "당기순이익": "영업이익에 이자·투자 손익·세금 등을 모두 반영한 최종 이익입니다. 일회성 손익이 섞일 수 있습니다.",
  "영업이익률": "영업이익 ÷ 매출. 100원 팔아 몇 원 남기는지입니다. 업종마다 보통 수준이 다릅니다.",
  "ROE": "순이익 ÷ 자기자본. 주주 돈으로 1년에 몇 % 벌었는지입니다. 10% 넘으면 좋은 편으로 봅니다.",
  "부채비율": "부채 ÷ 자기자본. 100%면 빚과 자기 돈이 같다는 뜻입니다. 200% 넘으면 높은 편으로 봅니다(금융·건설 등은 원래 높음).",
};

// 연간 실적 흐름을 문장으로: 실제 값의 방향 + 올해 추정
function finTrend(title, years, vals) {
  const act = years.map((y, i) => [y, vals[i]]).filter(([y, v]) => !y.est && v != null);
  const est = years.map((y, i) => [y, vals[i]]).find(([y, v]) => y.est && v != null);
  if (act.length < 2) return "";
  const yr = (y) => y.label.slice(0, 4);
  const [y0, v0] = act[0], [y1, v1] = act[act.length - 1];
  const prevV = act[act.length - 2][1];
  const pct = /률|ROE|비율/.test(title);
  // 전체 방향과 지난해 방향이 다르면 덧붙인다 (예: 2년 새 늘었지만 지난해엔 줄어듦)
  const lastTurn = () => (act.length > 2 && prevV > 0 && v1 > 0 && Math.sign(v1 - v0) !== Math.sign(v1 - prevV)
    ? `, 다만 ${yr(y1)}년엔 ${Math.abs(Math.round((v1 / prevV - 1) * 100))}% ${v1 >= prevV ? "늘어남" : "줄어듦"}` : "");
  const f = (v) => (pct ? `${num(v, 1)}%` : eokText(v));
  let line;
  if (title === "영업이익" || title === "당기순이익") {
    const neg = act.filter(([, v]) => v < 0).length;
    const prev = act[act.length - 2][1];
    if (neg === act.length) line = `${act.length}년 연속 적자 (${f(v1)})`;
    else if (v1 < 0 && prev >= 0) line = `${yr(y1)}년 적자 전환 (${f(prev)} → ${f(v1)})`;
    else if (v1 >= 0 && prev < 0) line = `${yr(y1)}년 흑자 전환 (${f(prev)} → ${f(v1)})`;
    else if (v1 < 0) line = `적자 지속 (${f(v1)})`;
    else line = `흑자 유지, ${yr(y0)}년 대비 ${v1 >= v0 ? "늘어남" : "줄어듦"} (${f(v0)} → ${f(v1)})${lastTurn()}`;
    if (est) line += est[1] < 0 ? (v1 < 0 ? `. 올해도 적자 전망 (${f(est[1])})` : `. 올해 적자 전환 전망 (${f(est[1])})`)
      : v1 < 0 ? `. 올해 흑자 전환 전망 (${f(est[1])})` : `. 올해 ${est[1] >= v1 ? "늘어날" : "줄어들"} 전망 (${f(est[1])})`;
    return line;
  }
  if (pct) {
    const lvl = title === "부채비율" ? (v1 < 100 ? "낮은 편(안정)" : v1 <= 200 ? "보통" : "높은 편(주의)")
      : title === "ROE" ? (v1 < 0 ? "적자" : v1 < 5 ? "낮은 편" : v1 < 10 ? "보통" : v1 < 20 ? "좋은 편" : "매우 높음")
      : v1 < 0 ? "적자" : v1 < 5 ? "낮은 편" : v1 < 15 ? "보통" : "높은 편";
    const dir = Math.abs(v1 - v0) < 1 ? "비슷" : v1 > v0 ? "오름" : "내림";
    line = `${lvl} (${f(v1)}), ${yr(y0)}년 ${f(v0)}에서 ${dir}`;
    if (est && title !== "부채비율") line += `. 올해 추정 ${f(est[1])}`;
    return line;
  }
  const ch = v0 ? Math.round((v1 / v0 - 1) * 100) : null;
  let streak = 1;
  for (let i = act.length - 1; i > 0 && Math.sign(act[i][1] - act[i - 1][1]) === Math.sign(v1 - act[act.length - 2][1]); i--) streak++;
  line = `${act.length - 1}년 새 ${ch == null ? "" : `${Math.abs(ch)}% `}${v1 >= v0 ? "늘어남" : "줄어듦"} (${f(v0)} → ${f(v1)})` +
    (streak >= 3 ? `, ${streak - 1}년 연속 ${v1 >= act[act.length - 2][1] ? "증가" : "감소"}` : lastTurn());
  if (est) line += `. 올해 추정 ${f(est[1])}로 ${est[1] >= v1 ? (v1 >= prevV ? "더 늘어날" : "다시 늘어날") : (v1 < prevV ? "더 줄어들" : "줄어들")} 전망`;
  return line;
}

// 실적 한 줄 요약: 매출 방향 + 영업이익 상태
function finVerdict(a) {
  const row = (t) => (a.rows.find((r) => r.title === t) || {}).values;
  const lastAct = (vals) => { const v = vals ? a.years.map((y, i) => (!y.est ? vals[i] : null)).filter((x) => x != null) : []; return v; };
  const rev = lastAct(row("매출액")), op = lastAct(row("영업이익"));
  if (rev.length < 2 || op.length < 2) return "";
  const opRow = row("영업이익"), ei = a.years.findIndex((y) => y.est);
  const opEst = ei >= 0 && opRow ? opRow[ei] : null;
  const [tone, text] = finVerdictBase(rev, op);
  // 올해 추정 영업이익이 지난해와 반대 방향이면 덧붙인다
  if (opEst == null) return [tone, text];
  if (tone === "bad" && opEst > op.at(-1)) return ["mid", `${text} (다만 올해는 영업이익 회복 전망)`];
  if (tone === "good" && opEst < op.at(-1)) return ["mid", `${text} (다만 올해는 영업이익 감소 전망)`];
  return [tone, text];
}

function finVerdictBase(rev, op) {
  const revUp = rev.at(-1) >= rev.at(-2), opNow = op.at(-1), opPrev = op.at(-2);
  if (opNow < 0 && !revUp) return ["bad", "매출이 줄고 본업이 적자인 상태 — 실적이 나빠지는 중"];
  if (opNow < 0) return ["mid", "매출은 늘었지만 본업은 아직 적자"];
  if (opPrev < 0) return ["good", "본업이 흑자로 돌아섬 — 실적 회복 중"];
  if (revUp && opNow >= opPrev) return ["good", "매출과 영업이익이 함께 늘고 있음 — 실적 좋아지는 중"];
  if (!revUp && opNow < opPrev) return ["bad", "매출과 영업이익이 함께 줄고 있음 — 실적 둔화"];
  return ["mid", "매출과 이익 방향이 엇갈림"];
}

// 종목 상세의 기업 정보 (네이버 증권, 수집 시점 값)
function companyBlock(x) {
  const p = x.profile;
  if (!p) return "";
  const pos52 = p.high52 && p.low52 && p.high52 > p.low52 ? Math.max(0, Math.min(100, Math.round(((x.close - p.low52) / (p.high52 - p.low52)) * 100))) : null;
  const m = (label, v) => `<div><small>${label}</small><b>${v}</b></div>`;
  const metrics = [
    m("시가총액", eokText(p.cap_eok ?? x.cap_eok)),
    m("PER", (p.per == null ? (p.eps != null && p.eps < 0 ? "적자" : "–") : `${num(p.per, 1)}배`) +
      (p.cns_per != null ? ` <small style="display:inline;font-size:11px">추정 ${p.cns_per < 0 ? "적자" : num(p.cns_per, 1)}</small>` : "")),
    m("PBR", p.pbr == null ? "–" : `${num(p.pbr, 2)}배`),
    m("배당수익률", p.div_yield == null ? "–" : `${num(p.div_yield, 2)}%`),
    m("52주 범위", p.low52 && p.high52 ? `<span style="font-size:12px">${num(p.low52)}~${num(p.high52)}</span>` : "–"),
    m("52주 중 위치", pos52 == null ? "–" : `${pos52}%`),
  ].join("");
  const a = p.annual;
  const fmt = (t, v) => (v == null ? "–" : /률|ROE|비율/.test(t) ? `${num(v, 1)}%` : eokText(v));
  const table = a ? `<table class="score fin"><thead><tr><th></th>${a.years.map((y) => `<th class="${y.est ? "est" : ""}">${esc(y.label.replace(" (추정)", ""))}${y.est ? "<br><small>추정</small>" : ""}</th>`).join("")}</tr></thead>` +
    `<tbody>${a.rows.map((r) => `<tr><th>${esc(r.title)}</th>${r.values.map((v, i) => `<td class="${a.years[i].est ? "est" : ""}">${fmt(r.title, v)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : "";
  const vd = a ? finVerdict(a) : "";
  const trends = a ? a.rows.map((r) => [r.title, finTrend(r.title, a.years, r.values)]).filter(([, t]) => t) : [];
  const flow = trends.length ? `<div class="finflow">${vd ? `<p class="verdict v-${vd[0]}">${esc(vd[1])}</p>` : ""}` +
    `<ul>${trends.map(([t, l]) => `<li><b>${esc(t)}</b> ${esc(l)}</li>`).join("")}</ul></div>` : "";
  const help = `<details class="rules"><summary>지표 설명 보기</summary><ul class="finhelp">${Object.entries(FIN_INFO).map(([k, v]) => `<li><b>${k}</b> ${esc(v)}</li>`).join("")}</ul></details>`;
  return `<div class="company"><h3>기업 정보 <small>네이버 증권 · ${esc((p.as_of || "").slice(5))} 수집 (시총·PER 등은 수집 시점 값)</small></h3>` +
    (p.summary ? `<p class="summary">${esc(p.summary)}</p>` : "") +
    `<div class="nums">${metrics}</div>${table}${flow}${help}</div>`;
}

/* ---------------- 전체 종목 찾기 ---------------- */

let stocksLoad = null;
const loadStocks = () => (stocksLoad ||= fetch("data/stocks.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null));

function setupSearch() {
  const q = $("#q"), list = $("#q-list");
  if (!q) return;
  q.addEventListener("focus", loadStocks, { once: true });
  q.addEventListener("input", async () => {
    const t = q.value.trim().toLowerCase();
    const db = await loadStocks();
    if (!t || !db) { list.hidden = true; return; }
    const hits = Object.entries(db.stocks)
      .filter(([c, s]) => c.startsWith(t) || s.n.toLowerCase().includes(t))
      .sort((a, b) => (a[1].n.toLowerCase().startsWith(t) ? 0 : 1) - (b[1].n.toLowerCase().startsWith(t) ? 0 : 1) || b[1].cap - a[1].cap)
      .slice(0, 8);
    list.innerHTML = hits.length
      ? hits.map(([c, s]) => `<li><button type="button" data-stock="${c}"><b>${esc(s.n)}</b> <small>${c} · ${s.m === "P" ? "코스피" : "코스닥"}${s.p ? " · 오늘 후보" : ""}</small><span class="${cls(s.ch)}">${signed(s.ch, 1, "%")}</span></button></li>`).join("")
      : `<li class="none">찾는 종목이 없어요 (보통주만, 스팩·우선주 제외)</li>`;
    list.hidden = false;
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-stock]");
    if (b) { list.hidden = true; q.value = ""; openStock(b.dataset.stock); return; }
    if (!e.target.closest(".search")) list.hidden = true;
  });
}

// 종목 하나의 오늘 상태와 탭별 조건 ✓/✗ — 왜 후보에 안 걸렸는지
async function openStock(code) {
  const db = await loadStocks();
  const s = db && db.stocks[code];
  if (!s) return;
  const L = db.labels, R = db.rules;
  const tabs = (s.p || []).map((g) => TAB_NAME[g]);
  const gate = (ok, label) => `<li class="${ok ? "" : "no"}"><span>${esc(label)}</span><span></span></li>`;
  const list = (bits, labels) => labels.map((l, i) => `<li class="${bits[i] === "1" ? "" : "no"}"><span>${esc(l)}</span><span></span></li>`).join("");
  const miss = (bits, labels) => labels.filter((l, i) => bits[i] !== "1");
  const verdict = (g, pre, bits, labels, why) => {
    if (s.p && s.p.includes(g)) return `<b class="good">오늘 후보 ✓</b>`;
    if (!bits) return `<small>${esc(why || "자료 부족으로 판단 못 함")}</small>`;
    const m = [...pre.filter(([ok]) => !ok).map(([, l]) => l), ...miss(bits, labels)];
    const all = pre.length + labels.length;
    return m.length ? `<small>${all - m.length}/${all} 맞음 · ${m.length === 1 ? "하나만 더 맞으면 후보" : `${m.length}개 안 맞음`}</small>` : `<small>조건은 맞지만 ${s.x ? "위험 공시로 제외" : "기준일 이후 바뀜"}</small>`;
  };
  const sw = [[s.sz[0] === "1", `시총 ${num(R.min_cap)}억·거래대금 ${num(R.min_value)}억 이상`], [s.nw >= 1, "최근 3일 안에 기사·공시"]];
  const sc = [[s.sz[0] === "1", `시총 ${num(R.min_cap)}억·거래대금 ${num(R.min_value)}억 이상`], [s.nw === 2, "오늘 기사·공시"]];
  const va = [[s.sz[1] === "1", `시총 ${num(R.v_min_cap)}억·거래대금 ${num(R.v_min_value)}억 이상`]];
  const block = (g, pre, bits, labels, why) => `<li><div class="row"><b>${TAB_NAME[g]}</b>${verdict(g, pre, bits, labels, why)}</div>
    <ul class="checks">${pre.map(([ok, l]) => gate(ok, l)).join("")}${bits ? list(bits, labels) : ""}</ul></li>`;
  const trend = s.g20 == null ? "–" : s.g20 > 0 && s.g60 > 0 ? "오르는 흐름 (20·60일 평균 위)" : s.g20 < 0 && s.g60 < 0 ? "내리는 흐름 (20·60일 평균 아래)" : "섞여 있음";
  $("#detail-body").innerHTML = `<div class="hd"><a class="nm" href="https://m.stock.naver.com/domestic/stock/${code}" target="_blank" rel="noopener">${esc(s.n)}<span class="mk">${s.m === "P" ? "KOSPI" : "KOSDAQ"} ${code}${s.s ? " · " + esc(s.s) : ""}</span></a>
      <div class="px"><b>${num(s.c)}</b><span class="${cls(s.ch)}">${signed(s.ch, 1, "%")}</span></div></div>
    <p class="hint">${ymd(db.date)} 기준 · 시총 ${eokText(s.cap)} · 거래대금 ${num(s.v)}억${tabs.length ? ` · <b>오늘 ${tabs.join("·")} 후보</b>` : ""}</p>
    ${s.x ? `<div class="earnwarn">⚠ 위험 공시·투자주의로 후보에서 뺀 종목: ${s.x.map(esc).join(", ")}</div>` : ""}
    <div class="nums">
      <div><small>5일 기관+외국인</small><b class="${cls(s.f5)}">${signed(s.f5, 0, "억")}</b><small>${s.pd}일 순매수</small></div>
      <div><small>20일 평균 대비</small><b class="${cls(s.g20)}">${signed(s.g20, 1, "%")}</b><small>60일 ${signed(s.g60, 1, "%")}</small></div>
      <div><small>PER · PBR</small><b>${s.per == null ? "–" : num(s.per, 1)} · ${s.pbr == null ? "–" : num(s.pbr, 2)}</b><small>${s.mul != null ? `거래 평소의 ${num(s.mul, 1)}배` : ""}</small></div>
    </div>
    <p class="hint">흐름: ${trend}${s.dr != null ? ` · 60일 고점보다 −${num(s.dr, 1)}%` : ""}</p>
    <h3 class="sub">탭별 조건 <small>✓ 맞음 · ✗ 안 맞음</small></h3>
    <ul class="fit why">${block("swing", sw, s.sw, L.swing)}${block("scalp", sc, s.sc, L.scalp)}${block("value", va, s.va, L.value, s.vr)}</ul>
    ${tabs.length ? `<button class="more" type="button" data-code="${code}">후보 카드 자세히 보기</button>` : ""}
    <p class="hint">조건에 다 맞아도 매수 추천은 아닙니다. 네이버에서 더 보려면 이름을 누르세요.</p>`;
  if (!$("#detail").open) $("#detail").showModal();
  $("#detail .dlg-in").scrollTop = 0;
}

function openDetail(code) {
  const f = state.data.filter;
  const x = [...(f.swing || []), ...(f.scalp || []), ...(f.value || [])].find((y) => y.code === code);
  if (!x) return;
  const groups = ["swing", "scalp", "value"].filter((g) => (f[g] || []).some((y) => y.code === code)).map((g) => TAB_NAME[g]);
  const q = x.quality || {};
  const fl = x.flow;
  $("#detail-body").innerHTML = `${cardHead(x)}
    <p class="hint">${groups.join(" · ")} 후보${x.sector ? " · " + esc(x.sector) : ""} · 기준일 시총 ${eokText(x.cap_eok)} · 거래대금 ${num(x.value_eok)}억</p>
    ${companyBlock(x)}
    ${x.chart ? lineChart(x.chart) : ""}
    <div class="nums">
      <div><small>외국인 지분율</small><b>${q.fratio == null ? "–" : num(q.fratio, 1) + "%"}</b></div>
      <div><small>지분율 20일 변화</small><b class="${cls(q.fratio_chg)}">${signed(q.fratio_chg, 2, "%p")}</b></div>
      <div><small>연기금 5일</small><b class="${cls(q.pension_5d)}">${signed(q.pension_5d, 0, "억")}</b></div>
      <div><small>투신 5일</small><b class="${cls(q.trust_5d)}">${signed(q.trust_5d, 0, "억")}</b></div>
      <div><small>사모 5일</small><b class="${cls(q.private_5d)}">${signed(q.private_5d, 0, "억")}</b></div>
      <div><small>외국인 5일</small><b class="${cls(fl ? fl.foreign_5d : x.flow5_eok)}">${fl ? signed(fl.foreign_5d, 0, "억") : "–"}</b></div>
    </div>
    ${fl ? `<div class="l" style="font-size:12px;color:var(--sub)">최근 5일 기관+외국인 순매수 (억)</div>${bars(fl.daily)}` : ""}
    <ul class="checks">${x.checks.map((c) => `<li><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul>
    <ul class="news">${newsList(x.news)}</ul>
    <p class="links"><a href="https://m.stock.naver.com/domestic/stock/${x.code}" target="_blank" rel="noopener">네이버 증권</a>
      <a href="https://dart.fss.or.kr/dsab007/main.do?option=corp&textCrpNm=${encodeURIComponent(x.name)}" target="_blank" rel="noopener">DART 공시</a></p>`;
  if (!$("#detail").open) $("#detail").showModal();
  $("#detail .dlg-in").scrollTop = 0;
}

document.addEventListener("click", (e) => {
  // 카드의 종목명을 누르면 네이버 대신 상세(기업 정보 포함)를 연다. 상세 안의 종목명은 그대로 네이버로
  const nm = e.target.closest(".card a.nm");
  if (nm && nm.dataset.code) { e.preventDefault(); openDetail(nm.dataset.code); return; }
  const b = e.target.closest("button.more");
  if (b) openDetail(b.dataset.code);
  if (e.target.closest("#detail .close") || e.target.id === "detail") $("#detail").close();
});

function renderRules() {
  const r = state.data.filter.rules;
  const eok = (v) => num(v / 1e8) + "억";
  $("#rules").innerHTML = `
    <h3>공통</h3>
    <ul><li>보통주만 (우선주·스팩·관리종목·투자주의환기종목 제외)</li>
    <li>최근 5영업일 안에 유상증자·전환사채·최대주주 변경·횡령·거래정지 같은 악재성 공시가 있으면 제외</li><li>시가총액 ${eok(r.min_cap)} 이상, 거래대금 ${eok(r.min_value)} 이상</li>
    <li>기사 제목에 종목명이 나온 종목만. 종목을 4개 이상 나열한 시황 기사는 제외</li>
    <li>공시는 계약·수주·자사주 취득·실적 발표·특허·합병 등 재료가 되는 제목만. 실적 공시는 본문에서 영업이익을 전년 같은 분기와 비교해 늘었거나 흑자 전환한 것만 넣음 (줄었거나 적자면 제외, 본문을 못 읽으면 그대로 포함). 합병·타법인주식 공시는 제목만으로는 좋고 나쁨을 알 수 없어 그대로 포함</li></ul>
    <h3>단타</h3>
    <ul><li>전 영업일 장 마감 뒤 ~ 기준일 사이 기사·공시</li><li>거래대금이 20일 평균의 ${r.scalp_value_mult}배 이상</li>
    <li>${r.scalp_min_change}% 이상 상승</li><li>기준일 기관+외국인 순매수가 그날 거래대금의 ${r.scalp_min_net_ratio ?? 0}% 이상</li></ul>
    <h3>스윙</h3>
    <ul><li>최근 3영업일 안에 기사·공시</li><li>최근 5일 중 ${r.swing_min_days}일 이상 기관+외국인 순매수</li>
    <li>5일 순매수 합이 시가총액의 ${r.swing_min_ratio}% 이상</li><li>주가 &gt; 20일 이동평균 &gt; 60일 이동평균, 20일 이동평균 상승 중</li>
    <li>5일 상승률 ${r.swing_max_5d_rise}% 이하 (이미 급등한 종목 제외)</li></ul>
    ${state.data.filter.value_rules ? valueRules(state.data.filter.value_rules, eok) : ""}
    <h3>점수 (정렬용)</h3>
    <ul><li>스윙: 시총 대비 5일 기관+외국인 순매수 비율(%) × (1 + 연속 순매수일 ÷ 5)</li>
    <li>단타: 0~100점 가중평균 — 큰손 확인(거래대금 중 기관+외국인 순매수 비중 0%→0, 20%→100) 50%, 거래 증가(평소의 3배→0, 8배→100, 15배 넘으면 깎여 30배에 0) 25%, 상승폭(2%→0, 5~15%→100, 29%→0) 25%</li>
    <li>저평가: 0~100점 가중평균 — 낙폭(20%→0, 50%→100) 25%, 1년 중앙 PBR 대비 할인(20%→0, 70%→100) 15%, PER(15→0, 5→100) 35%, 이익(EPS 1년 전의 0.8배→0, 1.5배→100, 추정 EPS 있으면 반반) 25%</li>
    <li>카드의 강도는 이 계산값이 지금까지 같은 탭 후보들 중 몇 % 위치인지 (세 탭 공통 0~100)</li></ul>
    <h3>정리 기준 (카드 아래)</h3>
    <ul><li>스윙: 종가가 20일 평균 가격 아래로 마감 · 단타: 걸린 날 저가 아래로 마감 · 저평가: 최근 10일 최저가 아래로 마감</li>
    <li>성적표 '정리 기준을 지켰다면'은 다음 날 시가에 사서, 5일 안에 종가가 기준 아래로 마감한 날 그 종가에 판 것으로 계산</li>
    <li>증권사 리포트: 네이버 리서치 종목분석 목록(150일 보관). 최근 2주 리포트 수, 그 전 석 달 동안 없었으면 '오랜만에 다뤄짐'. 가장 최근 리포트의 투자의견·목표가</li>
    <li>'검색 N위 · 이미 알려짐': 네이버 증권 검색 상위 100. 좋은 신호가 아니라 '이미 사람들이 몰림' 표시</li>
    <li>실적 시즌(1분기 4/1~5/15, 반기 7/1~8/14, 3분기 10/1~11/14, 결산 1/1~3/31)에는 DART에서 분기·반기·사업보고서나 잠정실적을 아직 안 낸 후보에 경고 표시</li></ul>`;
}

function valueRules(v, eok) {
  return `<h3>저평가</h3>
    <ul><li>시가총액 ${eok(v.min_cap)} 이상, 거래대금 ${eok(v.min_value)} 이상 (기사 없어도 됨)</li>
    <li>60일 최고가 대비 ${v.min_drop}% 이상 하락</li><li>PBR이 최근 1년(분기마다 1번, 4번) 중앙값의 ${Math.round(v.max_pbr_ratio * 100)}% 이하</li>
    <li>절대 수준도 싼 편: PER ${v.max_per ?? "–"} 이하, PBR ${v.max_pbr ?? "–"} 이하 (1년 내내 급등했던 종목 제외)</li>
    <li>흑자이고, EPS가 1년 전의 ${Math.round(v.min_eps_ratio * 100)}% 이상 (KRX EPS는 결산 실적이라 늦게 바뀜)</li></ul>
    <p>주목 신호 (걸러내는 조건 아님, 카드에 따로 표시): 큰손 5일 순매수, 최근 3일 소식, 5일 평균 거래대금이 20일 평균의 1.3배 이상, 최근 10일 최저가보다 5% 이상 위. 2개 이상이면 '다시 주목받는 중'.</p>`;
}

init();
