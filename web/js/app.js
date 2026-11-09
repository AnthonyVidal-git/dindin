import { Chart, Viewport } from "./chart.js";
import { detectLang, locale, setLang, t } from "./i18n.js";
import { MarketStream } from "./market.js";

const $ = (sel) => document.querySelector(sel);
const form = $("#config");
const statusEl = $("#status");

const state = {
  mode: "backtest",
  catalog: [],
  view: null,       // what is on screen: { kind, data }
  selected: null,   // fill number highlighted on tape + chart
  live: { timer: 0, fills: [], fillCount: 0, lastBar: -1, running: false, real: false, formingTime: null, nextClose: 0 },
  timeframe: { backtest: "1h", compare: "1h", live: "1m" },
  intraday: false,
};

// ---- formatting ---------------------------------------------------------
const nf = (opts) => new Intl.NumberFormat(locale(), opts);
const fmtMoney = (v) => nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
const fmtPrice = (v) => nf({ maximumFractionDigits: v >= 1000 ? 0 : 2, minimumFractionDigits: v >= 1000 ? 0 : 2 }).format(v);
const fmtQty = (v) => nf({ maximumFractionDigits: v >= 100 ? 0 : 3 }).format(v);
const fmtPct = (v, sign = true) => (v == null ? "n/a" : `${sign && v > 0 ? "+" : ""}${nf({ maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(v * 100)}%`);
const fmtNum = (v) => (v == null ? "n/a" : nf({ maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(v));
const fmtDate = (ts) => new Intl.DateTimeFormat(locale(), state.intraday
  ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "short", year: "2-digit" }).format(ts * 1000);
const fmtDateLong = (ts) => new Intl.DateTimeFormat(locale(), state.intraday
  ? { dateStyle: "short", timeStyle: "short" } : { dateStyle: "medium" }).format(ts * 1000);
const fmtClock = (ms) => new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(ms);
const priceDigits = (v) => (v >= 1000 ? 2 : v >= 10 ? 3 : v >= 1 ? 4 : 5);
const fmtMarket = (v) => nf({ minimumFractionDigits: priceDigits(v), maximumFractionDigits: priceDigits(v) }).format(v);
const fmtMkQty = (v) => nf({ maximumFractionDigits: v >= 100 ? 2 : v >= 1 ? 4 : 5 }).format(v);
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const icon = (id) => `<svg aria-hidden="true"><use href="#i-${id}"/></svg>`;
const signClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ---- motion ---------------------------------------------------------------
/** Roll a number from its previous value to the new one. */
function tween(el, from, to, format, duration = 650) {
  if (from == null || to == null || from === to || reducedMotion()) { el.textContent = format(to); return; }
  const start = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - start) / duration);
    const e = 1 - (1 - p) ** 3;
    el.textContent = format(from + (to - from) * e);
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = format(to);
  };
  requestAnimationFrame(step);
}

/** The coin in the logo flips and the money floats up. */
function celebrate(amount) {
  if (amount == null || !Number.isFinite(amount) || Math.abs(amount) < 0.005) return;
  const coin = $("#coin");
  const pop = $("#cash-pop");
  pop.textContent = `${amount > 0 ? "+" : "−"}${fmtMoney(Math.abs(amount))}`;
  pop.classList.toggle("down", amount < 0);
  for (const el of amount > 0 ? [coin, pop] : [pop]) {
    el.classList.remove("flip", "show");
    void el.offsetWidth; // restart the CSS animation
    el.classList.add(el === coin ? "flip" : "show");
  }
}

function moveInk() {
  const sel = document.querySelector('[data-mode][aria-selected="true"]');
  const ink = document.querySelector(".modes-ink");
  if (!sel || !ink) return;
  ink.style.width = `${sel.offsetWidth}px`;
  ink.style.transform = `translateX(${sel.offsetLeft}px)`;
}
window.addEventListener("resize", moveInk);

// ---- charts ---------------------------------------------------------------
const vp = new Viewport();
const priceChart = new Chart($("#price-chart"), vp, {
  kind: "price", format: (v) => (state.live.real && state.mode === "live" ? fmtMarket(v) : fmtPrice(v)),
  dateFormat: fmtDate, emptyText: () => t("chart.empty"), liveLabel: () => t("chart.liveFrom"),
  onReadout: (i) => {
    const c = state.view?.chart?.candles?.[i];
    $("#price-readout").textContent = c
      ? `${fmtDateLong(c.time)}  O ${fmtPrice(c.open)}  H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}  C ${fmtPrice(c.close)}`
      : "";
  },
});
const equityChart = new Chart($("#equity-chart"), vp, {
  kind: "line", format: (v) => nf({ notation: "compact", maximumFractionDigits: 1 }).format(v),
  dateFormat: fmtDate, emptyText: () => t("chart.empty"),
  onReadout: (i) => {
    const series = state.view?.equity?.series;
    if (i == null || !series) { $("#equity-readout").textContent = ""; return; }
    $("#equity-readout").textContent = series
      .filter((s) => s.values[i] != null)
      .map((s) => `${s.name} ${fmtMoney(s.values[i])}`).join("   ");
  },
});

// ---- api ------------------------------------------------------------------
async function api(path, body) {
  let res;
  try {
    res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error(t("status.offline"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(t("status.error", { msg: data.error || res.statusText }));
  return data;
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

// ---- form -----------------------------------------------------------------
function currentStrategy() {
  return state.catalog.find((s) => s.key === form.strategy.value) || state.catalog[0];
}

function renderStrategyOptions() {
  const keep = form.strategy.value;
  form.strategy.innerHTML = state.catalog
    .map((s) => `<option value="${s.key}">${esc(t(`strategy.${s.key}`))}</option>`).join("");
  if (keep) form.strategy.value = keep;
}

function renderParams() {
  const strat = currentStrategy();
  if (!strat) return;
  const old = Object.fromEntries([...document.querySelectorAll("#params input")].map((i) => [i.name, i.value]));
  $("#params").innerHTML = strat.params.map((p) => `
    <label class="field"><span>${esc(t(`param.${p.name}`))}</span>
      <input name="p:${p.name}" type="number" min="${p.minimum}" max="${p.maximum}" step="${p.step}"
        value="${old[`p:${p.name}`] ?? p.default}" required></label>`).join("");
  $("#strategy-hint").textContent = t(`hint.${strat.key}`);
}

function readNumber(name, scale = 1) {
  const input = form.elements[name];
  const value = Number(input.value);
  const bad = input.value === "" || !Number.isFinite(value)
    || (input.min !== "" && value < Number(input.min)) || (input.max !== "" && value > Number(input.max));
  input.setAttribute("aria-invalid", String(bad));
  if (bad) {
    const label = input.closest("label")?.querySelector("span")?.textContent || name;
    throw new Error(t("status.error", { msg: `${label}: ${input.min}–${input.max}` }));
  }
  return value * scale;
}

async function collect({ withStrategy = true } = {}) {
  const body = {
    initial_cash: readNumber("initial_cash"),
    seed: Math.trunc(readNumber("seed")),
    risk: {
      position_size: readNumber("position_size", 0.01),
      stop_loss: readNumber("stop_loss", 0.01),
      take_profit: readNumber("take_profit", 0.01),
      fee_rate: readNumber("fee_rate", 0.01),
      slippage: readNumber("slippage", 0.01),
    },
  };
  if (withStrategy) {
    body.strategy = form.strategy.value;
    body.params = {};
    for (const p of currentStrategy().params) body.params[p.name] = readNumber(`p:${p.name}`);
  }
  const source = form.source.value;
  if (source === "binance") Object.assign(body, { source, symbol: form.symbol.value, timeframe: form.timeframe.value });
  if (state.mode === "live") {
    if (source !== "binance") body.interval = Number(form.interval.value);
    return body;
  }
  if (source === "csv") {
    const file = form.csv.files[0];
    if (!file) throw new Error(t("status.needCsv"));
    body.csv = await file.text();
  } else {
    body.bars = Math.trunc(readNumber("bars"));
  }
  return body;
}

// ---- view building ------------------------------------------------------
const OVERLAY_COLORS = ["--ov1", "--ov2", "--ov3"];

function chartModel(candles, overlays, fills, offset = 0, liveFrom = 0) {
  return {
    candles,
    liveFrom,
    times: candles.map((c) => c.time),
    overlays: Object.entries(overlays || {}).map(([name, values], i) => ({
      name, values, color: css(OVERLAY_COLORS[i % 3]), dash: name.includes("upper") || name.includes("lower") ? [4, 3] : null,
    })),
    fills: fills.map((f) => ({ ...f, index: f.bar - offset })),
  };
}

function legend(el, items) {
  el.innerHTML = items.map((it) => `<span style="color:${it.color}"><i class="${it.dash ? "dash" : ""}"></i><span style="color:var(--ink-2)">${esc(it.name)}</span></span>`).join("");
}

function showChart(view, { keepViewport = false, shift = 0 } = {}) {
  state.view = view;
  const c = view.chart.candles;
  state.intraday = c.length > 1 && c[1].time - c[0].time < 86400;
  priceChart.setData(view.chart);
  equityChart.setData(view.equity);
  const len = view.chart.candles.length;
  if (keepViewport) vp.follow(len, shift);
  else {
    vp.reset(len, Math.min(len, view.initialSpan || 260));
    priceChart.animateIn();
    equityChart.animateIn(1300);
  }
  legend($("#price-legend"), view.chart.overlays);
  legend($("#equity-legend"), view.equity.series);
}

function equityModel(times, equity, benchmark, drawdown) {
  return {
    times,
    drawdown,
    series: [
      { name: t("chart.strategy"), values: equity, color: css("--bot"), width: 2 },
      { name: t("chart.benchmark"), values: benchmark, color: css("--ink-3"), dash: [5, 4], width: 1.4 },
    ],
  };
}

// ---- stats ------------------------------------------------------------------
function renderStats(m, position) {
  const el = $("#stats");
  el.hidden = false;
  el.classList.toggle("empty", !m);
  const dash = "–";
  const arrow = (v) => (v > 0 ? icon("up") : v < 0 ? icon("down") : "");
  const int = (v) => String(Math.round(v));
  const pctU = (v) => fmtPct(v, false);
  const items = m ? [
    { k: "stat.return", n: m.total_return, f: fmtPct, cls: signClass(m.total_return), a: arrow(m.total_return), lead: true },
    { k: "stat.excess", n: m.excess_return, f: fmtPct, cls: signClass(m.excess_return), a: arrow(m.excess_return) },
    { k: "stat.equity", n: m.end_equity, f: fmtMoney },
    { k: "stat.dd", n: -m.max_drawdown, f: fmtPct, cls: m.max_drawdown ? "down" : "" },
    { k: "stat.sharpe", n: m.sharpe, f: fmtNum },
    { k: "stat.sortino", n: m.sortino, f: fmtNum },
    { k: "stat.trades", n: m.trades, f: int },
    { k: "stat.win", n: m.win_rate, f: pctU },
    { k: "stat.pf", n: m.profit_factor, f: fmtNum },
    { k: "stat.exposure", n: m.exposure, f: pctU },
  ].map((it) => ({ ...it, v: it.f(it.n) })) : ["stat.return", "stat.excess", "stat.equity", "stat.dd", "stat.sharpe", "stat.trades", "stat.win", "stat.pf"].map((k) => ({ k, v: dash }));
  if (position !== undefined) {
    if (m) items.splice(5, 1); // live: drop Sortino to keep two even rows
    items.splice(3, 0, position
      ? { k: "stat.position", v: `${fmtQty(position.quantity)} @ ${fmtPrice(position.entry_price)}`, cls: `small ${signClass(position.unrealized)}` }
      : { k: "stat.position", v: t("stat.flat") });
  }
  el.innerHTML = items.map((it) => `<div class="${it.lead ? "lead" : ""}" data-k="${it.k}"><dt>${esc(t(it.k))}</dt><dd class="${it.cls || ""}" title="${esc(it.v)}">${it.a || ""}<span>${esc(it.v)}</span></dd></div>`).join("");
  const prev = renderStats.prev || {};
  const live = state.mode === "live";
  for (const it of items) {
    if (it.n == null) continue;
    const cell = el.querySelector(`[data-k="${it.k}"]`);
    tween(cell.querySelector("dd span"), prev[it.k], it.n, it.f, live ? 380 : 900);
    if (live && prev[it.k] != null && it.n !== prev[it.k] && !reducedMotion()) {
      cell.classList.add(it.n > prev[it.k] ? "flash-up" : "flash-down");
      requestAnimationFrame(() => requestAnimationFrame(() => cell.classList.remove("flash-up", "flash-down")));
    }
  }
  renderStats.prev = Object.fromEntries(items.filter((it) => it.n != null).map((it) => [it.k, it.n]));
  if (state.mode === "live" && !m) el.hidden = true;
}

// ---- tape: the order log, printed like a ticker -----------------------------
function tickHTML(f, pnl) {
  const buy = f.side === "buy";
  const reason = f.reason === "signal" ? "" : `<span class="meta">${esc(t(`reason.${f.reason}`))}</span>`;
  const result = pnl == null ? "" : `<span class="pnl ${signClass(pnl)}">${pnl > 0 ? "+" : ""}${fmtMoney(pnl)}</span>`;
  return `<button class="tick ${f.side}" type="button" data-n="${f.n}" aria-pressed="${state.selected === f.n}">
    <span class="side">${icon(buy ? "up" : "down")}</span>
    <span>${esc(t(buy ? "tape.buy" : "tape.sell"))}</span>
    <span class="px">${fmtPrice(f.price)}</span>
    <span class="meta">×${fmtQty(f.quantity)}</span>${reason}${result}
  </button>`;
}

function pnlByExit(trades) {
  const map = new Map();
  for (const tr of trades || []) map.set(tr.exit_time, tr.pnl);
  return map;
}

function renderTape(fills, trades, { append = 0, live = false } = {}) {
  const track = $("#tape");
  const pnl = pnlByExit(trades);
  const html = (f) => tickHTML(f, f.side === "sell" ? pnl.get(f.time) : null);
  if (!fills.length) {
    track.innerHTML = `<p class="tape-empty">${esc(t("tape.empty"))}</p>`;
    return;
  }
  // Live polling with no new orders: leave the tape exactly as it is (no redraw, no flicker).
  if (live && !append && track.querySelector(".tick")) return;
  if (append && track.querySelector(".tick")) {
    const fresh = fills.slice(-append);
    const before = fills[fills.length - append - 1];
    track.insertAdjacentHTML("beforeend", fresh.map(html).join(""));
    // The tape stays still; the numbers of each new order roll up to their value.
    [...track.querySelectorAll(".tick")].slice(-append).forEach((el, i) => {
      const prev = i ? fresh[i - 1] : before;
      tween(el.querySelector(".px"), prev?.price ?? fresh[i].price * 0.98, fresh[i].price, fmtPrice, 900);
      const pnlEl = el.querySelector(".pnl");
      const value = pnl.get(fresh[i].time);
      if (pnlEl && value != null) tween(pnlEl, 0, value, (v) => `${v > 0 ? "+" : ""}${fmtMoney(v)}`, 1100);
    });
    const extra = track.querySelectorAll(".tick").length - 400; // keep the DOM small
    for (let i = 0; i < extra; i++) track.firstElementChild.remove();
  } else {
    track.innerHTML = fills.map(html).join("");
    const ticks = [...track.querySelectorAll(".tick")].slice(-30);
    ticks.forEach((el, i) => { el.style.setProperty("--i", i); el.classList.add("enter"); });
  }
  track.scrollTo({ left: track.scrollWidth, behavior: append ? "smooth" : "auto" });
}

function selectFill(n) {
  state.selected = n;
  document.querySelectorAll(".tick").forEach((el) => el.setAttribute("aria-pressed", String(Number(el.dataset.n) === n)));
  const fill = state.view?.chart?.fills.find((f) => f.n === n);
  priceChart.select(n);
  if (fill && fill.index >= 0) vp.center(fill.index);
}

$("#tape").addEventListener("click", (e) => {
  const tick = e.target.closest(".tick");
  if (tick) selectFill(Number(tick.dataset.n));
});

// ---- tables ---------------------------------------------------------------
function renderTradesTable(trades, total) {
  $("#table-title").textContent = t("table.trades");
  $("#table-count").textContent = t("table.count", { n: total ?? trades.length });
  $("#table thead").innerHTML = `<tr><th>#</th><th class="l">${t("col.entry")}</th><th class="l">${t("col.exit")}</th>
    <th>${t("col.entryPx")}</th><th>${t("col.exitPx")}</th><th>${t("col.pnl")}</th><th>${t("col.ret")}</th>
    <th class="l">${t("col.reason")}</th><th>${t("col.bars")}</th></tr>`;
  const offset = (total ?? trades.length) - trades.length;
  $("#table tbody").innerHTML = trades.length ? trades.map((tr, i) => `
    <tr data-time="${tr.entry_time}">
      <td>${offset + i + 1}</td><td class="l">${fmtDateLong(tr.entry_time)}</td><td class="l">${fmtDateLong(tr.exit_time)}</td>
      <td>${fmtPrice(tr.entry_price)}</td><td>${fmtPrice(tr.exit_price)}</td>
      <td class="${signClass(tr.pnl)}">${tr.pnl > 0 ? "+" : ""}${fmtMoney(tr.pnl)}</td>
      <td class="${signClass(tr.return_pct)}">${fmtPct(tr.return_pct)}</td>
      <td class="l"><span class="tag">${esc(t(`reason.${tr.exit_reason}`))}</span></td><td>${tr.bars_held}</td>
    </tr>`).reverse().join("") : `<tr><td class="empty" colspan="9">${t("table.empty")}</td></tr>`;
}

function renderCompareTable(rows) {
  $("#table-title").textContent = t("table.compare");
  $("#table-count").textContent = "";
  $("#table thead").innerHTML = `<tr><th>#</th><th class="l">${t("col.strategy")}</th><th>${t("stat.return")}</th>
    <th>${t("stat.excess")}</th><th>${t("stat.sharpe")}</th><th>${t("stat.dd")}</th><th>${t("stat.trades")}</th>
    <th>${t("stat.win")}</th><th>${t("stat.pf")}</th></tr>`;
  $("#table tbody").innerHTML = rows.map((r, i) => {
    const m = r.metrics;
    return `<tr data-strategy="${r.strategy.key}">
      <td class="rank">${i + 1}</td><td class="l"><i class="swatch" style="background:${r.color}"></i>${esc(t(`strategy.${r.strategy.key}`))}</td>
      <td class="${signClass(m.total_return)}">${fmtPct(m.total_return)}</td>
      <td class="${signClass(m.excess_return)}">${fmtPct(m.excess_return)}</td>
      <td>${fmtNum(m.sharpe)}</td><td class="${m.max_drawdown ? "down" : ""}">${fmtPct(-m.max_drawdown)}</td>
      <td>${m.trades}</td><td>${fmtPct(m.win_rate, false)}</td><td>${fmtNum(m.profit_factor)}</td></tr>`;
  }).join("");
}

$("#table tbody").addEventListener("click", (e) => {
  const row = e.target.closest("tr");
  if (!row) return;
  if (row.dataset.strategy) {           // compare: open that strategy as a backtest
    form.strategy.value = row.dataset.strategy;
    renderParams();
    setMode("backtest");
    form.requestSubmit();
    return;
  }
  const time = Number(row.dataset.time);
  const fill = state.view?.chart?.fills.find((f) => f.time === time && f.side === "buy");
  document.querySelectorAll("#table tbody tr").forEach((r) => r.setAttribute("aria-selected", String(r === row)));
  if (fill) selectFill(fill.n);
});

// ---- modes ----------------------------------------------------------------
async function runBacktest() {
  const body = await collect();
  setStatus(t("status.running"));
  const started = performance.now();
  const res = await api("/api/backtest", body);
  const fills = res.fills.map((f, n) => ({ ...f, n }));
  state.selected = null;
  state.view = null;
  const view = {
    kind: "backtest", res,
    chart: chartModel(res.candles, res.overlays, fills),
    equity: equityModel(res.candles.map((c) => c.time), res.equity, res.benchmark, res.drawdown),
  };
  showChart(view);
  renderStats(res.metrics);
  renderTape(fills, res.trades);
  renderTradesTable(res.trades);
  setStatus(t("status.done", { n: nf({}).format(res.candles.length), ms: Math.round(performance.now() - started) }));
  setTimeout(() => celebrate(res.metrics.end_equity - res.metrics.start_equity), 500);
}

const SERIES_COLORS = ["--bot", "--ov2", "--s3", "--s4"];

async function runCompare() {
  const body = await collect({ withStrategy: false });
  setStatus(t("status.running"));
  const res = await api("/api/compare", body);
  const rows = res.rows.map((r, i) => ({ ...r, color: css(SERIES_COLORS[i % 4]) }));
  const view = {
    kind: "compare", res: { rows, benchmark: res.benchmark },
    chart: chartModel(res.candles, {}, []),
    equity: {
      times: res.times,
      series: [
        ...rows.map((r) => ({ name: t(`strategy.${r.strategy.key}`), values: r.equity, color: r.color, width: 1.8 })),
        { name: t("chart.benchmark"), values: res.benchmark, color: css("--ink-3"), dash: [5, 4], width: 1.4 },
      ],
    },
  };
  showChart(view);
  $("#stats").hidden = true;
  renderTape([], []);
  renderCompareTable(rows);
  setStatus(t("status.compareDone", { n: rows.length, bars: nf({}).format(res.times.length) }));
}

// live --------------------------------------------------------------------
function applyLiveSnapshot(snap, { fresh = false } = {}) {
  const L = state.live;
  if (!snap.started) { renderStats(null); return; }
  const keepView = state.view?.kind === "live" && !fresh;
  const born = keepView ? performance.now() : 0;
  const newFills = snap.fills.map((f, i) => ({ ...f, n: (fresh ? 0 : L.fillCount) + i, born }));
  L.fills = fresh ? newFills : L.fills.concat(newFills).slice(-400);
  L.fillCount = snap.fill_count;
  const offset = snap.bar - snap.candles.length + 1;
  const shift = L.lastBar < 0 || fresh ? 0 : snap.bar - L.lastBar;
  L.lastBar = snap.bar;
  const keep = state.view?.kind === "live" && !fresh;
  const cfg = snap.config;
  L.real = cfg.source === "binance";
  // The candle still forming on the exchange is drawn too; the bot only trades closed ones.
  const forming = snap.forming;
  const pad = (arr, v) => (forming ? [...arr, v] : arr);
  const candles = pad(snap.candles, forming);
  const overlays = Object.fromEntries(Object.entries(snap.overlays).map(([k, v]) => [k, pad(v, null)]));
  L.formingTime = forming ? forming.time : null;
  L.nextClose = forming ? (forming.time + TF_SECONDS[cfg.timeframe]) * 1000 : 0;
  const view = {
    kind: "live", initialSpan: L.real ? 120 : 140,
    chart: chartModel(candles, overlays, L.fills, offset, snap.live_from),
    equity: equityModel(candles.map((c) => c.time), pad(snap.equity, snap.forming_equity),
      candles.map((c) => cfg.initial_cash * c.close / candles[0].close), pad(snap.drawdown, snap.drawdown.at(-1) ?? 0)),
  };
  setMarketPanel(L.real && state.mode === "live", cfg.symbol);
  // keep the hover readout pointing at the same candle while the window slides
  if (keep && vp.hover != null) vp.hover = Math.max(0, vp.hover - shift);
  showChart(view, { keepViewport: keep, shift });
  renderStats(snap.metrics, snap.position);
  renderTape(L.fills, snap.trades, { append: keep ? newFills.length : 0, live: keep });
  if (keep) {
    const pnl = pnlByExit(snap.trades);
    newFills.filter((f) => f.side === "sell").forEach((f) => celebrate(pnl.get(f.time)));
  }
  renderTradesTable(snap.trades, snap.trade_count);
  setLiveRunning(snap.running, snap.bar);
  if (snap.error) setStatus(t("status.error", { msg: snap.error }), true);
}

function setLiveRunning(running, bar) {
  state.live.running = running;
  const on = running && state.mode === "live";
  $("#live-badge").hidden = !on;
  priceChart.setLive(on);
  equityChart.setLive(on);
  if (state.mode !== "live") return;
  const btn = $("#run");
  btn.classList.toggle("is-stop", running);
  btn.querySelector("use").setAttribute("href", running ? "#i-stop" : "#i-play");
  $("#run-label").textContent = t(running ? "run.liveStop" : "run.liveStart");
  setStatus(running ? liveStatus(bar) : (state.view?.kind === "live" ? t("status.liveOff") : ""));
}

function liveStatus(bar) {
  const L = state.live;
  if (!L.real) return t("status.liveOn", { bar });
  const left = Math.max(0, Math.round((L.nextClose - Date.now()) / 1000));
  const mm = Math.floor(left / 60), ss = String(left % 60).padStart(2, "0");
  return t("status.liveReal", { sym: (mk.symbol || "").replace(/USDT$/, "/USDT"), tf: form.timeframe.value, left: `${mm}:${ss}` });
}

function stopPolling() { clearInterval(state.live.timer); state.live.timer = 0; }

function startPolling() {
  stopPolling();
  state.live.timer = setInterval(async () => {
    try {
      const snap = await api(`/api/live?since=${state.live.fillCount}`);
      applyLiveSnapshot(snap);
      if (!snap.running) stopPolling();
    } catch (err) {
      stopPolling();
      setStatus(err.message, true);
    }
  }, 500);
}

async function toggleLive() {
  if (state.live.running) {
    stopPolling();
    applyLiveSnapshot(await api("/api/live/stop", {}));
    return;
  }
  const body = await collect();
  if (body.source === "binance") setStatus(t("status.loadingMarket"));
  const snap = await api("/api/live/start", body);
  state.live.fillCount = 0; state.live.lastBar = -1; state.selected = null;
  applyLiveSnapshot(snap, { fresh: true });
  startPolling();
}

async function enterLive() {
  const snap = await api("/api/live");
  state.live.fillCount = 0; state.live.lastBar = -1;
  if (snap.started) {
    applyLiveSnapshot({ ...snap }, { fresh: true });
    if (snap.running) startPolling();
  } else {
    clearView();
    setLiveRunning(false);
  }
}

function clearView() {
  state.view = null;
  priceChart.setData(null); equityChart.setData(null); vp.reset(0);
  $("#price-legend").innerHTML = ""; $("#equity-legend").innerHTML = "";
  renderStats(null);
  renderTape([], []);
  renderTradesTable([]);
}

function setMode(mode) {
  if (mode === state.mode) return;
  state.mode = mode;
  stopPolling();
  renderStats.prev = null;
  $("#live-badge").hidden = true;
  priceChart.setLive(false);
  equityChart.setLive(false);
  document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  moveInk();
  document.querySelectorAll("[data-only-in]").forEach((el) => { el.hidden = !el.dataset.onlyIn.split(" ").includes(mode); });
  document.querySelectorAll("[data-hide-in]").forEach((el) => { el.hidden = el.dataset.hideIn.split(" ").includes(mode); });
  syncSource();
  const btn = $("#run");
  btn.classList.remove("is-stop");
  btn.querySelector("use").setAttribute("href", "#i-play");
  $("#run-label").textContent = t(mode === "compare" ? "run.compare" : mode === "live" ? "run.liveStart" : "run.backtest");
  $("#equity-title").textContent = t(mode === "compare" ? "chart.equityCompare" : "chart.equity");
  setStatus("");
  setMarketPanel(false);
  if (mode === "live") enterLive().catch((e) => setStatus(e.message, true));
  else clearView();
}

function syncSource() {
  const live = state.mode === "live";
  if (live && form.source.value === "csv") form.querySelector('input[name="source"][value="binance"]').checked = true;
  const src = form.source.value;
  $("#csv-option").hidden = live;
  $("#csv-field").hidden = src !== "csv";
  $("#market-fields").hidden = src !== "binance";
  $("#market-hint").hidden = src !== "binance";
  $("#bars-field").hidden = live || src === "csv";
  $("#seed-field").hidden = src !== "synthetic";
  $("#speed-field").hidden = !(live && src === "synthetic");
  form.elements.bars.max = src === "binance" ? 5000 : 20000;
  if (form.timeframe.querySelector(`option[value="${state.timeframe[state.mode]}"]`)) form.timeframe.value = state.timeframe[state.mode];
}
form.timeframe.addEventListener("change", () => { state.timeframe[state.mode] = form.timeframe.value; });

// ---- live market panel (order book, trades, ticker) -------------------------
const mk = { symbol: null, depth: null, ticker: null, pending: [], times: [], last: null, dir: 0, priceStr: "", avg: 0, raf: 0, flash: 0 };
const stream = new MarketStream({
  onDepth: (d) => { mk.depth = d; scheduleMarket(); },
  onTicker: (tk) => { mk.ticker = tk; scheduleMarket(); },
  onTrade: (tr) => {
    mk.dir = mk.last == null ? 0 : tr.price > mk.last ? 1 : tr.price < mk.last ? -1 : mk.dir;
    mk.last = tr.price;
    mk.pending.push(tr);
    mk.times.push(performance.now());
    // move the live candle with every real trade
    const v = state.view;
    const c = v?.kind === "live" ? v.chart.candles[v.chart.candles.length - 1] : null;
    if (c && c.time === state.live.formingTime) {
      c.close = tr.price; c.high = Math.max(c.high, tr.price); c.low = Math.min(c.low, tr.price);
    }
    scheduleMarket();
  },
  onStatus: (st) => { $("#mk-conn").dataset.state = st; $("#mk-conn-label").textContent = t(`market.${st}`); },
});

function setMarketPanel(on, symbol) {
  $("#market-panel").hidden = !on;
  if (!on) { stream.close(); mk.symbol = null; return; }
  if (mk.symbol !== symbol) {
    Object.assign(mk, { symbol, depth: null, ticker: null, pending: [], times: [], last: null, priceStr: "", avg: 0 });
    $("#mk-symbol").textContent = symbol.replace(/USDT$/, " / USDT");
    $("#mk-price").textContent = "–";
    $("#trade-list").innerHTML = "";
    const row = '<div class="book-row"><span></span><span></span><span></span></div>';
    $("#book-asks").innerHTML = row.repeat(10);
    $("#book-bids").innerHTML = row.repeat(10);
  }
  stream.open(symbol);
}

function scheduleMarket() { if (!mk.raf) mk.raf = requestAnimationFrame(renderMarket); }

function renderPrice(value) {
  const el = $("#mk-price");
  const str = fmtMarket(value);
  if (str === mk.priceStr) return;
  // At dozens of trades per second a digit would restart its roll before finishing
  // and never be readable; roll at most every 280 ms and catch up afterwards.
  const wait = 280 - (performance.now() - (mk.priceAt || 0));
  if (wait > 0) {
    clearTimeout(mk.priceTimer);
    mk.priceTimer = setTimeout(() => renderPrice(mk.last), wait);
    return;
  }
  mk.priceAt = performance.now();
  const prev = mk.priceStr;
  const roll = mk.dir >= 0 ? "roll-up" : "roll-down";
  el.innerHTML = [...str].map((ch, i) => `<span class="d${prev && prev[i] !== ch && !reducedMotion() ? ` ${roll}` : ""}">${ch}</span>`).join("");
  mk.priceStr = str;
  el.classList.remove("up", "down");
  if (mk.dir) el.classList.add(mk.dir > 0 ? "up" : "down");
  clearTimeout(mk.flash);
  mk.flash = setTimeout(() => el.classList.remove("up", "down"), 450);
}

function renderMarket() {
  mk.raf = 0;
  if (mk.last != null) renderPrice(mk.last);

  if (mk.pending.length) {
    const list = $("#trade-list");
    const html = mk.pending.slice(-14).reverse().map((tr) => {
      const value = tr.price * tr.qty;
      mk.avg = mk.avg ? mk.avg * 0.97 + value * 0.03 : value;
      const big = value > mk.avg * 4;
      return `<li class="${tr.side}${big ? " big" : ""}"><span>${fmtMarket(tr.price)}</span><span>${fmtMkQty(tr.qty)}</span><span>${fmtClock(tr.time)}</span></li>`;
    }).join("");
    list.insertAdjacentHTML("afterbegin", html);
    while (list.children.length > 40) list.lastElementChild.remove();
    mk.pending = [];
  }
  const now = performance.now();
  mk.times = mk.times.filter((x) => now - x < 5000);
  $("#mk-rate").textContent = t("market.rate", { n: nf({ maximumFractionDigits: 1 }).format(mk.times.length / 5) });

  if (mk.depth) {
    const asks = mk.depth.asks.slice(0, 10);
    const bids = mk.depth.bids.slice(0, 10);
    const cum = (rows) => { let s = 0; return rows.map(([p, q]) => [p, q, (s += q)]); };
    const a = cum(asks), b = cum(bids);
    const max = Math.max(a.at(-1)?.[2] || 0, b.at(-1)?.[2] || 0) || 1;
    const fill = (el, rows) => {
      [...el.children].forEach((row, i) => {
        const r = rows[i];
        const cells = row.children;
        if (!r) { cells[0].textContent = cells[1].textContent = cells[2].textContent = ""; row.style.setProperty("--w", 0); return; }
        cells[0].textContent = fmtMarket(r[0]); cells[1].textContent = fmtMkQty(r[1]); cells[2].textContent = fmtMkQty(r[2]);
        row.style.setProperty("--w", (r[2] / max).toFixed(3));
      });
    };
    fill($("#book-asks"), [...a].reverse());
    fill($("#book-bids"), b);
    if (asks.length && bids.length) {
      const mid = (asks[0][0] + bids[0][0]) / 2;
      const midEl = $("#book-mid");
      midEl.textContent = fmtMarket(mid);
      midEl.className = `book-mid ${mk.dir > 0 ? "up" : mk.dir < 0 ? "down" : ""}`;
      $("#mk-spread").textContent = t("market.spread", { v: fmtMarket(asks[0][0] - bids[0][0]) });
      const bidVol = b.at(-1)[2], askVol = a.at(-1)[2];
      const share = bidVol / (bidVol + askVol);
      $("#pressure-bar").style.width = `${(share * 100).toFixed(1)}%`;
      $("#pressure-label").textContent = t("market.pressure", { b: Math.round(share * 100), s: 100 - Math.round(share * 100) });
    }
  }

  if (mk.ticker) {
    const tk = mk.ticker;
    const chg = $("#mk-change");
    chg.className = `ticker-chg ${signClass(tk.changePct)}`;
    chg.innerHTML = `${tk.changePct > 0 ? icon("up") : tk.changePct < 0 ? icon("down") : ""} ${fmtPct(tk.changePct / 100)}`;
    $("#mk-high").textContent = fmtMarket(tk.high);
    $("#mk-low").textContent = fmtMarket(tk.low);
    $("#mk-vol").textContent = new Intl.NumberFormat(locale(), { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(tk.quoteVolume);
    if (mk.last == null) renderPrice(tk.last);
  }
}

// ---- re-render on language/theme change -------------------------------------
function rerender() {
  $("#mk-conn-label").textContent = t(`market.${$("#mk-conn").dataset.state}`);
  renderStrategyOptions();
  renderParams();
  const v = state.view;
  $("#run-label").textContent = t(state.mode === "compare" ? "run.compare" : state.mode === "live"
    ? (state.live.running ? "run.liveStop" : "run.liveStart") : "run.backtest");
  $("#equity-title").textContent = t(state.mode === "compare" ? "chart.equityCompare" : "chart.equity");
  if (!v) { renderStats(null); renderTape([], []); if (state.mode !== "compare") renderTradesTable([]); return; }
  if (v.kind === "backtest") {
    const { res } = v;
    const fills = v.chart.fills;
    const span = [vp.start, vp.end];
    showChart({ ...v, chart: chartModel(res.candles, res.overlays, fills),
      equity: equityModel(v.equity.times, res.equity, res.benchmark, res.drawdown) });
    [vp.start, vp.end] = span; vp.emit();
    renderStats(res.metrics);
    renderTape(fills, res.trades);
    renderTradesTable(res.trades);
  } else if (v.kind === "compare") {
    const rows = v.res.rows.map((r, i) => ({ ...r, color: css(SERIES_COLORS[i % 4]) }));
    v.res.rows = rows;
    v.equity.series = [
      ...rows.map((r) => ({ name: t(`strategy.${r.strategy.key}`), values: r.equity, color: r.color, width: 1.8 })),
      { name: t("chart.benchmark"), values: v.res.benchmark, color: css("--ink-3"), dash: [5, 4], width: 1.4 },
    ];
    priceChart.requestDraw(); equityChart.setData(v.equity);
    legend($("#equity-legend"), v.equity.series);
    renderCompareTable(rows);
  } else if (v.kind === "live") {
    v.equity.series[0].color = css("--bot"); v.equity.series[1].color = css("--ink-3");
    v.equity.series[0].name = t("chart.strategy"); v.equity.series[1].name = t("chart.benchmark");
    v.chart.overlays.forEach((o, i) => { o.color = css(OVERLAY_COLORS[i % 3]); });
    priceChart.requestDraw(); equityChart.requestDraw();
    legend($("#price-legend"), v.chart.overlays); legend($("#equity-legend"), v.equity.series);
    setLiveRunning(state.live.running, state.live.lastBar);
  }
}

// ---- theme -------------------------------------------------------------------
function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
$("#theme").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("dd-theme", next); } catch { /* ignore */ }
  rerender();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => rerender());

// ---- wiring --------------------------------------------------------------------
document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => { setLang(b.dataset.lang); rerender(); moveInk(); }));
document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
document.querySelector(".modes").addEventListener("keydown", (e) => {
  if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const tabs = [...document.querySelectorAll("[data-mode]")];
  const i = tabs.findIndex((b) => b.dataset.mode === state.mode);
  const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
  next.focus(); setMode(next.dataset.mode);
});
form.strategy.addEventListener("change", () => { $("#params").innerHTML = ""; renderParams(); });
form.addEventListener("change", (e) => { if (e.target.name === "source") syncSource(); });
form.addEventListener("input", (e) => e.target.removeAttribute("aria-invalid"));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#run");
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  try {
    if (state.mode === "backtest") await runBacktest();
    else if (state.mode === "compare") await runCompare();
    else await toggleLive();
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
});

async function init() {
  setLang(detectLang());
  moveInk();
  document.fonts?.ready.then(moveInk);
  document.querySelectorAll("[data-only-in]").forEach((el) => { el.hidden = !el.dataset.onlyIn.split(" ").includes(state.mode); });
  renderStats(null);
  renderTradesTable([]);
  try {
    const [{ strategies }, markets] = await Promise.all([api("/api/strategies"), api("/api/market")]);
    state.catalog = strategies;
    form.symbol.innerHTML = markets.symbols.map((sym) => `<option value="${sym}">${sym.replace(/USDT$/, " / USDT")}</option>`).join("");
    form.timeframe.innerHTML = markets.timeframes.map((tf) => `<option value="${tf}">${tf}</option>`).join("");
    renderStrategyOptions();
    renderParams();
    syncSource();
    try {
      await runBacktest(); // first visit shows a real result instead of an empty screen
    } catch (err) {
      if (form.source.value !== "binance") throw err;
      form.querySelector('input[name="source"][value="synthetic"]').checked = true; // exchange unreachable
      syncSource();
      await runBacktest();
    }
  } catch (err) {
    setStatus(err.message, true);
  }
}

init();
