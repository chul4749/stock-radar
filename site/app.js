"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const signed = (v, d = 1, unit = "") => (v == null ? "–" : (v > 0 ? "+" : "") + num(v, d) + unit);
const cls = (v) => (v == null ? "na" : v > 0 ? "up" : v < 0 ? "down" : "");
const ymd = (s) => `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}`;

let state = { data: null, tab: "swing", score: null, expAll: false, watch: {} };
try {
  state.tab = localStorage.getItem("tab") || "swing";
  state.expAll = localStorage.getItem("expAll") === "1";
  state.watch = JSON.parse(localStorage.getItem("watch") || "{}") || {};
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
    await load(idx.latest);
    fetch("data/scorecard.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null))
      .then((sc) => { state.score = sc; renderScore(); renderRegime(); }).catch(() => renderScore());
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
  renderRules();
  const n = d.filter.news_count;
  $("#sources").textContent = `데이터: KRX(시세·수급), 구글 뉴스 ${n.google}건 · 네이버 ${n.naver}건 · DART 공시 ${n.dart}건, CBOE(VIX), FRED(하이일드), CNN(공포·탐욕). 네이버·CNN은 비공식 경로라 바뀌면 빠질 수 있습니다.`;
  $("#errors").textContent = d.errors && d.errors.length ? `수집 실패: ${d.errors.join(", ")}` : "";
}


/* ---------------- 지표 설명(ⓘ)과 한 줄 해석 ---------------- */

const INFO = {
  thermo: "여러 지표를 0(극단적 공포)~100(극단적 탐욕) 점수로 바꿔 같은 무게로 평균한 국내 시장 온도계입니다. CNN 공포·탐욕지수와 같은 방식이지만 한국 시장 지표를 섞었습니다. 공포가 크다고 바닥이라는 뜻은 아니고, 지금 시장 분위기가 어느 쪽에 쏠려 있는지 보는 용도입니다.",
  korea_vol: "코스피200이 최근 20일 동안 실제로 얼마나 출렁였는지를 연율(%)로 나타낸 값입니다. VKOSPI(앞으로의 예상 흔들림)를 구하지 못해 대신 쓰는 지표입니다. 높을수록 시장이 불안하다는 뜻입니다.",
  vix: "미국 S&P500 옵션 가격으로 계산한 '앞으로 30일 예상 흔들림'입니다. 보통 20 아래면 평온, 30 위면 불안한 편으로 봅니다. 미국 장이 흔들리면 다음 날 한국 장에도 영향을 줍니다.",
  cnn: "CNN이 미국 시장의 주가 추세·신고가 종목 수·옵션·정크본드 수요 등 7가지를 합쳐 만든 투자심리 점수입니다. 0에 가까울수록 공포, 100에 가까울수록 탐욕입니다.",
  breadth: "최근 60일 중 가장 높은 가격(신고가) 또는 가장 낮은 가격(신저가)을 찍은 종목 수입니다. 지수는 대형주 몇 개 때문에 버텨도, 신저가 종목이 늘면 시장 속은 약해지고 있다는 뜻입니다.",
  updown: "기준일에 오른 종목 수와 내린 종목 수입니다(보통주, 코스피·코스닥 따로와 합계). 지수와 반대로 움직이면 소수 대형주가 지수를 끌고 있다는 신호입니다.",
  momentum: "코스피가 최근 20영업일 동안 몇 % 움직였는지입니다. 크게 빠졌으면 공포 쪽, 크게 올랐으면 탐욕 쪽으로 계산합니다.",
  flows: "최근 20영업일 중 외국인과 기관이 합쳐서 코스피 주식을 순매수한 날이 며칠인지입니다. 큰손이 꾸준히 사면 탐욕 쪽, 꾸준히 팔면 공포 쪽입니다.",
  credit: "재무가 약한 미국 기업이 돈을 빌릴 때 국채보다 얼마나 더 높은 이자를 내야 하는지입니다. 이 차이가 벌어지면 주가 공포가 기업 자금줄 문제로 번지고 있다는 신호입니다.",
  kospi: "코스피 지수와 기준일 등락률, 최근 20영업일 등락률입니다.",
  kosdaq: "코스닥 지수와 기준일 등락률, 최근 20영업일 등락률입니다. 중소형·성장주 분위기를 봅니다.",
  usdkrw: "1달러를 사는 데 드는 원화입니다. 환율이 빠르게 오르면(원화 약세) 외국인이 한국 주식을 팔 유인이 커지는 경우가 많습니다. FRED 자료라 며칠 늦게 반영됩니다.",
  us10y: "미국 10년 만기 국채 금리입니다. 금리가 오르면 성장주·기술주 가치가 낮게 평가되기 쉽고, 위험자산에서 돈이 빠지는 경우가 많습니다.",
  marketflows: "시장 전체에서 외국인·기관이 순매수(산 금액 − 판 금액)한 규모입니다. 단위는 억 원입니다.",
  selling: "60일 중 가장 낮은 가격(신저가)까지 떨어진 종목 수를 최근 5일 평균과 비교합니다. 신저가 종목이 늘면 버티던 종목까지 팔리고 있다는 뜻이고, 줄면 투매가 잦아들고 있다는 뜻입니다.",
  regime: "코스피가 20일·60일 이동평균 위인지 아래인지(추세)와 급락·극단적 공포·높은 변동성·반대매매 급증(패닉 조건, 두 개 이상 겹치면 패닉)으로 지금 장을 상승장·횡보·하락장·패닉 넷 중 하나로 나눕니다. 시나리오별 적합도는 '급락 뒤 변동성이 큰 패닉 구간에선 추세 추종이 가장 크게 깨지고 낙폭 큰 종목이 크게 튄다', '하락 추세에선 추세를 따르는 쪽이 낙폭을 줄인다' 같은 연구 결과를 바탕으로 한 경향입니다.",
  inverse: "지수가 떨어지면 오르는 ETF입니다. 하루 수익률을 매일 다시 맞추기 때문에, 지수가 오르내리며 제자리여도 손실이 쌓입니다(2배 상품은 더 심함). 그래서 방향이 뚜렷한 짧은 구간에만 맞고, 공포가 극에 달한 뒤 따라 들어가면 급반등에 크게 당하기 쉽습니다.",
  score_swing: "강도는 0~100점으로, 지금까지 이 탭에 걸린 후보들(날짜마다 상위 10개)의 계산값 중 몇 % 위치인지입니다. 100점에 가까울수록 역대 후보 중에서도 강한 편이고, 세 탭 모두 같은 눈금이라 비교할 수 있습니다. 계산값은 통과한 종목끼리 순서를 매기는 식입니다. 시가총액에 비해 최근 5일 기관·외국인이 얼마나 많이 샀는지(%)에, 며칠 연속 샀는지를 더 얹었습니다. 덩치에 비해 큰손이 꾸준히 사는 종목이 위로 옵니다. 계산값은 탭마다 공식이 달라 그 자체로는 다른 탭과 비교할 수 없습니다.",
  score_scalp: "강도는 0~100점으로, 지금까지 이 탭에 걸린 후보들의 계산값 중 몇 % 위치인지입니다. 세 탭 모두 같은 눈금이라 비교할 수 있습니다. 계산값은 세 가지를 0~100점으로 맞춰 가중평균합니다: 큰손 확인(오늘 거래대금 중 기관·외국인 순매수 비중 0%→0, 20%→100) 50%, 거래 증가(평소의 3배→0, 8배→100, 15배를 넘으면 오히려 깎여 30배에 0) 25%, 상승폭(5~15%가 100, 필터 하한 2%나 상한가 근처 29%로 갈수록 0) 25%. 거래가 폭발하고 크게 튄 '주목받는 종목'은 이후 성적이 나쁜 경향이 있어(Barber·Odean 2008, 이 사이트 기록에서도 확인), 큰손이 확인해 준 적당한 급등을 위로 올립니다.",
  score_value: "강도는 0~100점으로, 지금까지 이 탭에 걸린 후보들의 계산값 중 몇 % 위치인지입니다. 세 탭 모두 같은 눈금이라 비교할 수 있습니다. 계산값은 네 가지를 0~100점으로 맞춰 가중평균합니다: 낙폭(60일 고점 대비 20%→0점, 50%→100점) 25%, 싸짐(1년 중앙 PBR 대비 할인 20%→0, 70%→100) 15%, PER(15→0, 5→100) 35%, 이익(EPS 1년 전의 0.8배→0, 1.5배→100, 올해 추정 EPS가 있으면 '지금 대비 추정'도 같은 눈금으로 반반 섞음) 25%. 싸면서 이익이 버티는 종목이 위로 오도록 했습니다.",
  futures: "외국인이 코스피200 선물을 산 금액에서 판 금액을 뺀 값입니다. 금액은 계약 수 × 그날 선물 가격 × 25만 원으로 계산했고, 계약 수는 작게 같이 적었습니다. 외국인은 선물로 지수 방향에 베팅하거나 현물을 헤지하는 경우가 많아, 며칠씩 한 방향으로 쌓이면 지수가 그쪽으로 움직이는 경향이 있습니다. 한국 장세를 먼저 보여 주는 지표로 많이 봅니다.",
  loan: "개인이 증권사에서 돈을 빌려 산 주식 금액(신용거래융자 잔고)입니다. 많이 쌓일수록 주가가 빠질 때 담보 부족으로 강제로 팔리는 물량(반대매매)이 커질 수 있습니다. 금융투자협회 자료라 하루 늦게 나옵니다.",
  forced: "외상으로 산 주식 대금(미수금)을 기한 안에 못 갚아 증권사가 강제로 판 금액입니다. 급락장에서 반대매매가 몰리면 좋은 종목도 같이 떨어집니다. 금융투자협회 자료라 하루 늦게 나옵니다.",
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
  swing: "최근 3일 안에 기사·공시가 있고, 기관·외국인 돈이 며칠째 들어오며, 차트가 올라가는 흐름인 종목.",
  scalp: "오늘 기사·공시가 나왔고, 거래대금이 평소의 몇 배로 터지며, 오늘 기관·외국인도 산 종목.",
  value: "최근 많이 떨어져 장부가치 대비 1년 중 싼 편인데, 이익은 크게 줄지 않은 종목. 시장 공포로 싸진 건지 볼 후보.",
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
  }
  const tops = ["swing", "scalp", "value"].filter((g) => (f[g] || []).length).map((g) => {
    const x = f[g][0];
    return `<button class="more pick" type="button" data-code="${x.code}">${esc(x.name)}</button><small>${TAB_NAME[g]}${x.score_pct != null ? ` ${x.score_pct}점` : ""}</small>`;
  });
  if (tops.length) rows.push(["탭별 1위", `<span class="picks">${tops.map((p) => `<span>${p}</span>`).join("")}</span>`]);
  const hits = Object.keys(state.watch).filter((c) => ["swing", "scalp", "value"].some((g) => (f[g] || []).some((y) => y.code === c)));
  if (Object.keys(state.watch).length) rows.push(["관심 종목", hits.length ? `<b>${hits.map((c) => esc(state.watch[c])).join(", ")}</b> 오늘 걸림` : `<span class="na">오늘 걸린 종목 없음</span>`]);
  $("#today-body").innerHTML = rows.length ? `<div class="today">${rows.map(([k, v]) => `<div class="tr"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}</div>` +
    `<p class="hint">아래 칸들의 결론만 모은 것입니다. 매수·매도 추천이 아닙니다.</p>` : "";
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
    `<p class="hint">필터에 걸린 날의 장세별로, 5일 뒤 코스피보다 얼마나 더 올랐는지(초과 수익률) 평균입니다. 날짜가 적으면 우연이 크게 섞입니다.</p></details>`;
}

function renderRegime() {
  const rg = state.data.temperature.regime;
  const box = $("#regime-body");
  document.querySelectorAll(".tabs button").forEach((b) => { if (rg) b.dataset.fit = rg.fit[b.dataset.tab].grade; else delete b.dataset.fit; });
  if (!rg) { box.innerHTML = `<div class="empty">이 날짜는 장세 판정이 생기기 전 기록입니다.</div>`; return; }
  const tr = rg.trend;
  const head = `<div class="sum ${REGIME_TONE[rg.key]}"><div class="row"><span class="t">지금 장세 ${infoBtn("regime")}</span><span class="lv">${esc(rg.label)}</span></div>` +
    `<p>${esc(rg.reason || `코스피 ${num(tr.close, 2)} · 20일선 ${num(tr.ma20, 0)} · 60일선 ${num(tr.ma60, 0)}`)}</p>${infoBox("regime")}${regimeStrip(rg.key)}</div>`;

  // 왜 이렇게 판단했나: 1단계 패닉 조건 → 2단계 추세 조건 → 바뀌려면
  const why = rg.panic_checks ? `<details class="rules why-box" open><summary>왜 이렇게 판단했나</summary>
      ${rg.chart ? lineChart(rg.chart) : ""}
      <h3>1단계 · 패닉 조건 <small>두 개 이상 맞으면 패닉 (하나만 맞으면 인버스 추격 주의)</small></h3>${checkList(rg.panic_checks)}
      <h3>2단계 · 추세 조건 <small>셋 다 ✓면 상승장, 셋 다 ✗면 하락장, 섞이면 횡보·전환</small></h3>${checkList(rg.trend_checks)}
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
  return `<button class="star${on ? " on" : ""}" type="button" data-code="${x.code}" data-name="${esc(x.name)}" aria-pressed="${on}" aria-label="관심 종목${on ? " 해제" : "에 추가"}">${on ? "★" : "☆"}</button>`;
};

function renderWatch() {
  const codes = Object.keys(state.watch);
  if (!codes.length) { $("#watch").innerHTML = ""; return; }
  const f = state.data.filter;
  const rows = codes.map((c) => {
    const groups = ["swing", "scalp", "value"].filter((g) => (f[g] || []).some((y) => y.code === c));
    const name = esc(state.watch[c]);
    return groups.length
      ? `<li class="hit"><button class="more" type="button" data-code="${c}">${name}</button> <span class="tag both">${groups.map((g) => TAB_NAME[g]).join("·")}</span></li>`
      : `<li class="miss">${name} <small>조건 밖</small></li>`;
  });
  const hits = rows.filter((r) => r.startsWith('<li class="hit"')).length;
  $("#watch").innerHTML = `<div class="watch"><div class="l">관심 종목 <small>${hits ? `오늘 ${hits}개 걸림` : "오늘 걸린 종목 없음"}</small></div><ul>${rows.join("")}</ul></div>`;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button.star");
  if (!b) return;
  const c = b.dataset.code;
  if (state.watch[c]) delete state.watch[c]; else state.watch[c] = b.dataset.name;
  try { localStorage.setItem("watch", JSON.stringify(state.watch)); } catch (err) { /* 무시 */ }
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
    ${qualityRow(x)}
    ${news ? `<ul class="news">${news}</ul>` : `<p class="hint" style="margin:8px 0 0">최근 3일 관련 기사 없음</p>`}
    ${detailBtn(x)}
  </article>`;
}

function cardHead(x) {
  return `<div class="hd">
      <a class="nm" data-code="${x.code}" href="https://m.stock.naver.com/domestic/stock/${x.code}" target="_blank" rel="noopener">${esc(x.name)}<span class="mk">${esc(x.market)} ${x.code}${x.sector ? " · " + esc(x.sector) : ""}</span></a>
      <div class="px">${starBtn(x)}<b>${num(x.close)}</b><span class="${cls(x.change)}">${signed(x.change, 1, "%")}</span></div>
    </div>${x.overlap ? `<span class="tag both">두 관점 겹침</span>` : ""}`;
}

function newsList(items) {
  return (items || []).map((n) => {
    const at = n.at.slice(5, 16).replace("T", " ").replace("-", "/");
    const tag = n.kind === "공시" ? `<span class="tag dart">공시</span>` : "";
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
    const verdict = gap == null ? "–" : Math.min(hi.n, lo.n) < 30 ? "표본 적음" : gap > 1 ? "점수 높을수록 나음" : gap < -1 ? "오히려 반대" : "차이 없음";
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
  const warn = minN < 30 ? `<p class="hint">기록이 ${sc.days}일치뿐이라 표본이 적습니다 (5일 기준 30건 미만인 구분이 있음). 지금 숫자는 우연일 수 있습니다.</p>` : "";
  const recent = (sc.recent || []).filter((e) => e.r1 != null).slice(0, 15).map((e) =>
    `<tr><td>${e.date.slice(4, 6)}/${e.date.slice(6)}</td><td>${TAB_NAME[e.group]}</td><td style="text-align:left">${esc(e.name)}</td>` +
    hs.map((h) => `<td class="${cls(e["r" + h])}">${e["r" + h] == null ? "–" : signed(e["r" + h], 1, "%")}</td>`).join("") + "</tr>").join("");
  const m = sc.method;
  const how = m ? `<p class="hint">다음 날 시가에 사서 N영업일째 종가에 판 성적입니다(거래 비용 ${m.cost}% 뺌). '초과'는 같은 기간 종목이 속한 시장 지수(코스피·코스닥)보다 얼마나 더 올랐는지이고, 같은 종목이 며칠 연속 걸리면 겹치는 구간은 한 번만 셉니다.</p>`
    : `<p class="hint">기준일 종가 기준, 코스피 대비 성적입니다 (예전 계산 방식).</p>`;
  const bf = (sc.backfill_dates || []).length
    ? `<p class="hint">${sc.backfill_dates.length}일(${ymd(sc.backfill_dates[0])}~${ymd(sc.backfill_dates.at(-1))})은 나중에 다시 계산한 기록이라, 그날 실제로 보이던 뉴스와 달라 스윙·단타 성적은 조금 할인해서 보세요.</p>` : "";
  $("#scorecard").innerHTML = how + bf + `<div class="tile wide"><table class="score">
      <thead><tr><th></th>${hs.map((h) => `<th>${h}일 뒤</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>${warn}
    ${scoreCheckTable(sc.score_check)}
    ${featureTable(sc.features)}
    ${recent ? `<details class="rules"><summary>최근 기록 보기</summary><table class="score recent"><thead><tr><th>날짜</th><th>구분</th><th style="text-align:left">종목</th>${hs.map((h) => `<th>${h}일</th>`).join("")}</tr></thead><tbody>${recent}</tbody></table></details>` : ""}`;
}

function featureTable(fs) {
  if (!fs || !fs.length) return "";
  // 값은 시장 지수 대비 초과 수익, '앞섬'은 지수보다 더 오른 비율
  const cell = (s) => (s ? `<b class="${cls(s.avg)}">${signed(s.avg, 1, "%p")}</b><br><small>앞섬 ${s.win}% · ${s.n}건</small>` : `<span class="na">–</span>`);
  // 한쪽이라도 비어 있는 조건(예: 아직 한 건도 없는 '두 관점 겹침')은 빼고 보여 준다
  const rows = fs.filter((f) => f.yes && f.no).map((f) => `<tr><th>${esc(f.label)}</th><td>${cell(f.yes)}</td><td>${cell(f.no)}</td></tr>`).join("");
  return `<h3 class="sub">조건별 성적 <small>스윙·단타 후보, ${fs[0].horizon}일 뒤 시장 지수 대비 초과</small></h3>
    <div class="tile wide"><table class="score"><thead><tr><th></th><th>조건 있음</th><th>없음</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">기록이 쌓이면 어떤 조건이 실제로 도움이 됐는지 보입니다. 건수가 적을 때는 참고만 하세요.</p>`;
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
      <small>${r.n}종목 · 5일 등락 중앙값 <span class="${cls(r.rise_5d)}">${signed(r.rise_5d, 1, "%")}</span>${names.length ? " · " + names.map(esc).join(", ") : ""}</small></div>`;
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
    <div class="legend"><span><i style="background:var(--text)"></i>종가</span><span><i style="background:var(--up)"></i>20일선</span><span><i style="background:var(--down)"></i>60일선</span>
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
  $("#detail").showModal();
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
    <li>공시는 계약·수주·자사주 취득·실적 발표·특허·합병 등 재료가 되는 제목만 (제목만으로는 좋고 나쁨을 알 수 없는 실적·합병 공시도 포함)</li></ul>
    <h3>단타</h3>
    <ul><li>전 영업일 장 마감 뒤 ~ 기준일 사이 기사·공시</li><li>거래대금이 20일 평균의 ${r.scalp_value_mult}배 이상</li>
    <li>${r.scalp_min_change}% 이상 상승</li><li>기준일 기관+외국인 순매수가 그날 거래대금의 ${r.scalp_min_net_ratio ?? 0}% 이상</li></ul>
    <h3>스윙</h3>
    <ul><li>최근 3영업일 안에 기사·공시</li><li>최근 5일 중 ${r.swing_min_days}일 이상 기관+외국인 순매수</li>
    <li>5일 순매수 합이 시가총액의 ${r.swing_min_ratio}% 이상</li><li>주가 &gt; 20일 이동평균 &gt; 60일 이동평균, 20일선 상승 중</li>
    <li>5일 상승률 ${r.swing_max_5d_rise}% 이하 (이미 급등한 종목 제외)</li></ul>
    ${state.data.filter.value_rules ? valueRules(state.data.filter.value_rules, eok) : ""}
    <h3>점수 (정렬용)</h3>
    <ul><li>스윙: 시총 대비 5일 기관+외국인 순매수 비율(%) × (1 + 연속 순매수일 ÷ 5)</li>
    <li>단타: 0~100점 가중평균 — 큰손 확인(거래대금 중 기관+외국인 순매수 비중 0%→0, 20%→100) 50%, 거래 증가(평소의 3배→0, 8배→100, 15배 넘으면 깎여 30배에 0) 25%, 상승폭(2%→0, 5~15%→100, 29%→0) 25%</li>
    <li>저평가: 0~100점 가중평균 — 낙폭(20%→0, 50%→100) 25%, 1년 중앙 PBR 대비 할인(20%→0, 70%→100) 15%, PER(15→0, 5→100) 35%, 이익(EPS 1년 전의 0.8배→0, 1.5배→100, 추정 EPS 있으면 반반) 25%</li>
    <li>카드의 강도는 이 계산값이 지금까지 같은 탭 후보들 중 몇 % 위치인지 (세 탭 공통 0~100)</li></ul>`;
}

function valueRules(v, eok) {
  return `<h3>저평가</h3>
    <ul><li>시가총액 ${eok(v.min_cap)} 이상, 거래대금 ${eok(v.min_value)} 이상 (기사 없어도 됨)</li>
    <li>60일 최고가 대비 ${v.min_drop}% 이상 하락</li><li>PBR이 최근 1년(분기마다 1번, 4번) 중앙값의 ${Math.round(v.max_pbr_ratio * 100)}% 이하</li>
    <li>절대 수준도 싼 편: PER ${v.max_per ?? "–"} 이하, PBR ${v.max_pbr ?? "–"} 이하 (1년 내내 급등했던 종목 제외)</li>
    <li>흑자이고, EPS가 1년 전의 ${Math.round(v.min_eps_ratio * 100)}% 이상 (KRX EPS는 결산 실적이라 늦게 바뀜)</li></ul>`;
}

init();
