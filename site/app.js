"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const signed = (v, d = 1, unit = "") => (v == null ? "–" : (v > 0 ? "+" : "") + num(v, d) + unit);
const cls = (v) => (v == null ? "na" : v > 0 ? "up" : v < 0 ? "down" : "");
const ymd = (s) => `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}`;

let state = { data: null, tab: "swing", score: null, expAll: false };
try {
  state.tab = localStorage.getItem("tab") || "swing";
  state.expAll = localStorage.getItem("expAll") === "1";
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
      .then((sc) => { state.score = sc; renderScore(); }).catch(() => renderScore());
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
  renderFilter();
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
  creditbal: "빚을 내서 산 주식(신용잔고)과, 빚을 못 갚아 강제로 팔린 물량(반대매매)입니다. 강제 매도가 몰리면 좋은 종목도 같이 급락합니다. 아직 수집 경로를 찾지 못했습니다.",
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
  const box = b.closest(".tile, .thermo, .part").querySelector(`[data-exp="${b.dataset.info}"]`);
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
      <div class="part-hd"><span>${esc(p.name)} ${infoBtn(p.key)}</span><b style="color:${THERMO_COLOR(p.score)}">${p.score} <small>${THERMO_KR(p.score)}</small></b></div>
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
    <details class="rules"${state.expAll ? " open" : ""}><summary>무엇으로 계산했나 (${th.parts.length}개 지표)</summary>${parts}</details>
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

function tile(label, value, sub, sparkVals, opts = {}) {
  const key = opts.key;
  const line = key ? interpret(key, state.data.temperature) : "";
  return `<div class="tile${opts.wide ? " wide" : ""}"><div class="l">${label} ${key ? infoBtn(key) : ""}</div><div class="v ${opts.vcls || ""}">${value}</div>` +
    `<div class="s">${sub}</div>${sparkVals ? `<div style="color:var(--faint)">${spark(sparkVals)}</div>` : ""}${opts.extra || ""}` +
    `${line ? `<div class="why">${esc(line)}</div>` : ""}${key ? infoBox(key) : ""}</div>`;
}

const pctText = (p) => (p == null ? "" : ` · 1년 중 ${p}% 위치`);

function renderTemp() {
  const t = state.data.temperature;
  renderThermo();
  $("#summary").innerHTML = (t.summary || []).filter((s) => !(t.thermo && s.key === "fear")).map((s) =>
    `<div class="sum ${s.tone}"><div class="row"><span class="t">${esc(s.title)}</span><span class="lv">${esc(s.level)}</span></div><p>${esc(s.text)}</p></div>`
  ).join("") || `<div class="empty">시장 온도 데이터를 받지 못했습니다</div>`;

  const tiles = [];
  const ix = t.index || {};
  for (const [k, name] of [["kospi", "코스피"], ["kosdaq", "코스닥"]]) {
    const x = ix[k];
    if (x) tiles.push(tile(name, num(x.close, 2), `<span class="${cls(x.change)}">${signed(x.change, 2, "%")}</span> · 20일 ${signed(x.change_20d, 1, "%")}`, x.spark, { key: k }));
  }
  if (t.vix) tiles.push(tile("미국 VIX (공포지수)", num(t.vix.value, 1), `5일 ${signed(t.vix.change, 1)}${pctText(t.vix.pct_1y)}`, t.vix.spark, { key: "vix" }));
  if (t.korea_vol) tiles.push(tile("국내 변동성 <small>(VKOSPI 대체)</small>", num(t.korea_vol.value, 1) + "%", `코스피200 20일 실제 변동성${pctText(t.korea_vol.pct_1y)}`, t.korea_vol.spark, { key: "korea_vol" }));
  if (t.fear_greed) {
    const fg = t.fear_greed;
    const kr = { "extreme fear": "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", "extreme greed": "극단적 탐욕" }[fg.rating] || fg.rating;
    tiles.push(tile("CNN 공포·탐욕", `${num(fg.score)} <small style="font-size:13px;font-weight:500">${esc(kr)}</small>`,
      `전일 ${num(fg.prev)} · 1주 전 ${num(fg.week_ago)} · 1달 전 ${num(fg.month_ago)}${fg.year_ago != null ? ` · 1년 전 ${num(fg.year_ago)}` : ""}`, fg.spark, { key: "cnn" }));
  }
  if (t.hy_spread) tiles.push(tile("미국 하이일드 스프레드", num(t.hy_spread.value, 2) + "%p", `20일 ${signed(t.hy_spread.change, 2, "%p")}${pctText(t.hy_spread.pct_1y)}`, t.hy_spread.spark, { key: "credit" }));
  if (t.usdkrw) tiles.push(tile("원/달러 환율", num(t.usdkrw.value, 1) + "원", `5일 ${signed(t.usdkrw.change, 1)}${pctText(t.usdkrw.pct_1y)} · ${t.usdkrw.date.slice(5)} 기준`, t.usdkrw.spark, { key: "usdkrw" }));
  if (t.us10y) tiles.push(tile("미국 10년 국채금리", num(t.us10y.value, 2) + "%", `5일 ${signed(t.us10y.change, 2, "%p")}${pctText(t.us10y.pct_1y)}`, t.us10y.spark, { key: "us10y" }));
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
    tiles.push(tile("60일 신저가 종목", num(b.new_lows.at(-1)), `신고가 ${num(b.new_highs.at(-1))} · 최근 20일 추이`, b.new_lows, { key: "breadth" }));
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
  tiles.push(tile("신용잔고·반대매매", `<span class="na">–</span>`, "아직 수집 경로를 찾지 못함", null, { key: "creditbal" }));
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
      <a class="nm" href="https://m.stock.naver.com/domestic/stock/${x.code}" target="_blank" rel="noopener">${esc(x.name)}<span class="mk">${esc(x.market)} ${x.code}${x.sector ? " · " + esc(x.sector) : ""}</span></a>
      <div class="px"><b>${num(x.close)}</b><span class="${cls(x.change)}">${signed(x.change, 1, "%")}</span></div>
    </div>${x.overlap ? `<span class="tag both">두 관점 겹침</span>` : ""}`;
}

function newsList(items) {
  return (items || []).map((n) => {
    const at = n.at.slice(5, 16).replace("T", " ").replace("-", "/");
    const tag = n.kind === "공시" ? `<span class="tag dart">공시</span>` : "";
    return `<li>${tag}<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a><small>${esc(n.source)} ${at}</small></li>`;
  }).join("");
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
    <div class="nums">${nums.map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join("")}</div>
    <div class="l" style="font-size:12px;color:var(--sub)">최근 5일 기관+외국인 순매수 (억)</div>
    ${bars(fl.daily)}
    ${qualityRow(x)}
    <ul class="checks">${x.checks.map((c) => `<li><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul>
    <ul class="news">${news}</ul>
    ${detailBtn(x)}
  </article>`;
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
  $("#scorecard").innerHTML = `<div class="tile wide"><table class="score">
      <thead><tr><th></th>${hs.map((h) => `<th>${h}일 뒤</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>${warn}
    ${featureTable(sc.features)}
    ${recent ? `<details class="rules"><summary>최근 기록 보기</summary><table class="score recent"><thead><tr><th>날짜</th><th>구분</th><th style="text-align:left">종목</th>${hs.map((h) => `<th>${h}일</th>`).join("")}</tr></thead><tbody>${recent}</tbody></table></details>` : ""}`;
}

function featureTable(fs) {
  if (!fs || !fs.length) return "";
  const cell = (s) => (s ? `<b class="${cls(s.avg)}">${signed(s.avg, 1, "%")}</b><br><small>상승 ${s.win}% · ${s.n}건</small>` : `<span class="na">–</span>`);
  const rows = fs.map((f) => `<tr><th>${esc(f.label)}</th><td>${cell(f.yes)}</td><td>${cell(f.no)}</td></tr>`).join("");
  return `<h3 class="sub">조건별 성적 <small>스윙·단타 후보, ${fs[0].horizon}일 뒤</small></h3>
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

function openDetail(code) {
  const f = state.data.filter;
  const x = [...(f.swing || []), ...(f.scalp || []), ...(f.value || [])].find((y) => y.code === code);
  if (!x) return;
  const groups = ["swing", "scalp", "value"].filter((g) => (f[g] || []).some((y) => y.code === code)).map((g) => TAB_NAME[g]);
  const q = x.quality || {};
  const fl = x.flow;
  $("#detail-body").innerHTML = `${cardHead(x)}
    <p class="hint">${groups.join(" · ")} 후보${x.sector ? " · " + esc(x.sector) : ""} · 시가총액 ${num(x.cap_eok)}억 · 거래대금 ${num(x.value_eok)}억</p>
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
    <li>공시는 계약·수주·자사주 취득·실적·특허 등 호재성 제목만</li></ul>
    <h3>단타</h3>
    <ul><li>전 영업일 장 마감 뒤 ~ 기준일 사이 기사·공시</li><li>거래대금이 20일 평균의 ${r.scalp_value_mult}배 이상</li>
    <li>${r.scalp_min_change}% 이상 상승</li><li>기준일 기관+외국인 순매수 &gt; 0</li></ul>
    <h3>스윙</h3>
    <ul><li>최근 3영업일 안에 기사·공시</li><li>최근 5일 중 ${r.swing_min_days}일 이상 기관+외국인 순매수</li>
    <li>5일 순매수 합이 시가총액의 ${r.swing_min_ratio}% 이상</li><li>주가 &gt; 20일 이동평균 &gt; 60일 이동평균, 20일선 상승 중</li>
    <li>5일 상승률 ${r.swing_max_5d_rise}% 이하 (이미 급등한 종목 제외)</li></ul>
    ${state.data.filter.value_rules ? valueRules(state.data.filter.value_rules, eok) : ""}
    <h3>점수 (정렬용)</h3>
    <ul><li>단타: 거래대금 배수 × (1 + 기관·외국인 순매수 / 거래대금)</li><li>스윙: 시총 대비 5일 순매수 비율 × (1 + 연속일 / 5)</li></ul>`;
}

function valueRules(v, eok) {
  return `<h3>저평가</h3>
    <ul><li>시가총액 ${eok(v.min_cap)} 이상, 거래대금 ${eok(v.min_value)} 이상 (기사 없어도 됨)</li>
    <li>60일 최고가 대비 ${v.min_drop}% 이상 하락</li><li>PBR이 최근 1년(분기마다 1번, 4번) 중앙값의 ${Math.round(v.max_pbr_ratio * 100)}% 이하</li>
    <li>절대 수준도 싼 편: PER ${v.max_per ?? "–"} 이하, PBR ${v.max_pbr ?? "–"} 이하 (1년 내내 급등했던 종목 제외)</li>
    <li>흑자이고, EPS가 1년 전의 ${Math.round(v.min_eps_ratio * 100)}% 이상 (KRX EPS는 결산 실적이라 늦게 바뀜)</li>
    <li>점수: 하락률 × (1년 중앙 PBR / 현재 PBR)</li></ul>`;
}

init();
