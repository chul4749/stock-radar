"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const signed = (v, d = 1, unit = "") => (v == null ? "–" : (v > 0 ? "+" : "") + num(v, d) + unit);
const cls = (v) => (v == null ? "na" : v > 0 ? "up" : v < 0 ? "down" : "");
const ymd = (s) => `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}`;

let state = { data: null, tab: "swing", score: null };
try { state.tab = localStorage.getItem("tab") || "swing"; } catch (e) { /* 저장 불가 환경 */ }

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
  renderFilter();
  renderRules();
  const n = d.filter.news_count;
  $("#sources").textContent = `데이터: KRX(시세·수급), 구글 뉴스 ${n.google}건 · 네이버 ${n.naver}건 · DART 공시 ${n.dart}건, CBOE(VIX), FRED(하이일드), CNN(공포·탐욕). 네이버·CNN은 비공식 경로라 바뀌면 빠질 수 있습니다.`;
  $("#errors").textContent = d.errors && d.errors.length ? `수집 실패: ${d.errors.join(", ")}` : "";
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
  return `<div class="tile${opts.wide ? " wide" : ""}"><div class="l">${label}</div><div class="v ${opts.vcls || ""}">${value}</div>` +
    `<div class="s">${sub}</div>${sparkVals ? `<div style="color:var(--faint)">${spark(sparkVals)}</div>` : ""}${opts.extra || ""}</div>`;
}

const pctText = (p) => (p == null ? "" : ` · 1년 중 ${p}% 위치`);

function renderTemp() {
  const t = state.data.temperature;
  $("#summary").innerHTML = (t.summary || []).map((s) =>
    `<div class="sum ${s.tone}"><div class="row"><span class="t">${esc(s.title)}</span><span class="lv">${esc(s.level)}</span></div><p>${esc(s.text)}</p></div>`
  ).join("") || `<div class="empty">시장 온도 데이터를 받지 못했습니다</div>`;

  const tiles = [];
  const ix = t.index || {};
  for (const [k, name] of [["kospi", "코스피"], ["kosdaq", "코스닥"]]) {
    const x = ix[k];
    if (x) tiles.push(tile(name, num(x.close, 2), `<span class="${cls(x.change)}">${signed(x.change, 2, "%")}</span> · 20일 ${signed(x.change_20d, 1, "%")}`, x.spark));
  }
  if (t.vix) tiles.push(tile("미국 VIX (공포지수)", num(t.vix.value, 1), `5일 ${signed(t.vix.change, 1)}${pctText(t.vix.pct_1y)}`, t.vix.spark));
  if (t.korea_vol) tiles.push(tile("국내 변동성 <small>(VKOSPI 대체)</small>", num(t.korea_vol.value, 1) + "%", `코스피200 20일 실제 변동성${pctText(t.korea_vol.pct_1y)}`, t.korea_vol.spark));
  if (t.fear_greed) {
    const fg = t.fear_greed;
    const kr = { "extreme fear": "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", "extreme greed": "극단적 탐욕" }[fg.rating] || fg.rating;
    tiles.push(tile("CNN 공포·탐욕", `${num(fg.score)} <small style="font-size:13px;font-weight:500">${esc(kr)}</small>`, `1주 전 ${num(fg.week_ago)} · 1달 전 ${num(fg.month_ago)}`));
  }
  if (t.hy_spread) tiles.push(tile("미국 하이일드 스프레드", num(t.hy_spread.value, 2) + "%p", `20일 ${signed(t.hy_spread.change, 2, "%p")}${pctText(t.hy_spread.pct_1y)}`, t.hy_spread.spark));
  if (t.breadth) {
    const b = t.breadth;
    tiles.push(tile("오르고 내린 종목", `<span class="up">${num(b.up)}</span> / <span class="down">${num(b.down)}</span>`, "상승 / 하락 (보통주)"));
    tiles.push(tile("60일 신저가 종목", num(b.new_lows.at(-1)), `신고가 ${num(b.new_highs.at(-1))} · 최근 20일 추이`, b.new_lows));
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
    tiles.push(`<div class="tile wide"><div class="l">시장 전체 순매수 (억 원)</div><table class="flowtbl"><thead><tr><th></th><th>오늘</th><th>5일</th><th>20일</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`);
  }
  tiles.push(tile("신용잔고·반대매매", `<span class="na">–</span>`, "아직 수집 경로를 찾지 못함"));
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
    ${news ? `<ul class="news">${news}</ul>` : `<p class="hint" style="margin:8px 0 0">최근 3일 관련 기사 없음</p>`}
  </article>`;
}

function cardHead(x) {
  return `<div class="hd">
      <a class="nm" href="https://m.stock.naver.com/domestic/stock/${x.code}" target="_blank" rel="noopener">${esc(x.name)}<span class="mk">${esc(x.market)} ${x.code}</span></a>
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
       ["연기금 5일", `<span class="${cls(fl.pension_5d)}">${signed(fl.pension_5d, 0, "억")}</span>`]]
    : [["거래대금", `${num(x.value_eok, 0)}억`],
       ["평소 대비", `${num(x.value_mult, 1)}배`],
       ["오늘 기관+외국인", `<span class="${cls(bothToday)}">${signed(bothToday, 0, "억")}</span>`]];
  const news = newsList(x.news);
  return `<article class="card">
    ${cardHead(x)}
    <div class="nums">${nums.map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join("")}</div>
    <div class="l" style="font-size:12px;color:var(--sub)">최근 5일 기관+외국인 순매수 (억)</div>
    ${bars(fl.daily)}
    <ul class="checks">${x.checks.map((c) => `<li><span>${esc(c.label)}</span><span>${esc(c.value)}</span></li>`).join("")}</ul>
    <ul class="news">${news}</ul>
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
    ${recent ? `<details class="rules"><summary>최근 기록 보기</summary><table class="score recent"><thead><tr><th>날짜</th><th>구분</th><th style="text-align:left">종목</th>${hs.map((h) => `<th>${h}일</th>`).join("")}</tr></thead><tbody>${recent}</tbody></table></details>` : ""}`;
}

function renderRules() {
  const r = state.data.filter.rules;
  const eok = (v) => num(v / 1e8) + "억";
  $("#rules").innerHTML = `
    <h3>공통</h3>
    <ul><li>보통주만 (우선주·스팩·관리종목 제외)</li><li>시가총액 ${eok(r.min_cap)} 이상, 거래대금 ${eok(r.min_value)} 이상</li>
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
