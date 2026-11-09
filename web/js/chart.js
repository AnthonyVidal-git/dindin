// Hand-rolled canvas charts. Two charts share one Viewport so zoom, pan
// and the hover crosshair stay in sync between price and equity.

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const MIN_SPAN = 20;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const easeOut = (p) => 1 - (1 - p) ** 4;
const easeOutBack = (p) => 1 + 2.4 * (p - 1) ** 3 + 1.4 * (p - 1) ** 2;

export class Viewport {
  constructor() {
    this.len = 0; this.start = 0; this.end = 0; this.hover = null;
    this.subs = new Set();
  }
  subscribe(fn) { this.subs.add(fn); }
  emit() { this.subs.forEach((fn) => fn()); }
  get span() { return this.end - this.start; }

  reset(len, show = len) {
    this.len = len;
    this.end = len;
    this.start = Math.max(0, len - show);
    this.hover = null;
    this.emit();
  }
  /** New bars arrived (live mode): keep following the right edge if we were there. */
  follow(len, shift = len - this.len) {
    const atEnd = this.end >= this.len - 0.5;
    const span = this.span || len;
    this.len = len;
    if (atEnd) { this.end = len; this.start = Math.max(0, len - span); }
    else { this.start = Math.max(0, this.start - shift); this.end = Math.max(this.start + MIN_SPAN, this.end - shift); }
    this.emit();
  }
  zoom(factor, anchor) {
    if (!this.len) return;
    const span = clamp(this.span * factor, Math.min(MIN_SPAN, this.len), this.len);
    const ratio = this.span ? (anchor - this.start) / this.span : 0.5;
    this.start = clamp(anchor - ratio * span, 0, this.len - span);
    this.end = this.start + span;
    this.emit();
  }
  pan(bars) {
    const span = this.span;
    this.start = clamp(this.start + bars, 0, this.len - span);
    this.end = this.start + span;
    this.emit();
  }
  center(index) {
    const span = Math.min(this.span || 120, Math.max(MIN_SPAN, 160));
    this.start = clamp(index - span / 2, 0, Math.max(0, this.len - span));
    this.end = Math.min(this.len, this.start + span);
    this.emit();
  }
}

const PAD = { l: 10, r: 64, t: 12, b: 26 };

export class Chart {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {Viewport} vp
   * @param {{kind: "price"|"line", format: (v:number)=>string, dateFormat: (t:number)=>string, onReadout?: (i:number|null)=>void, emptyText?: ()=>string}} opts
   */
  constructor(canvas, vp, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.vp = vp;
    this.opts = opts;
    this.data = null;
    this.selected = null;
    this._drag = null;
    vp.subscribe(() => this.requestDraw());
    new ResizeObserver(() => this.resize()).observe(canvas);
    this._bind();
    this.resize();
  }

  setData(data) { this.data = data; this.requestDraw(); }

  /** Draw the series from left to right, like a pen on a chart recorder. */
  animateIn(duration = 1100) {
    if (reducedMotion()) return;
    this._reveal = { start: performance.now(), duration };
    this._loop();
  }

  /** Live mode: keep a pulse on the last price. */
  setLive(on) {
    this._live = on && !reducedMotion();
    if (this._live) this._loop();
  }

  _loop() {
    if (this._looping) return;
    this._looping = true;
    const tick = () => {
      const revealing = this._reveal && performance.now() - this._reveal.start < this._reveal.duration;
      this.draw();
      if (revealing || this._live) requestAnimationFrame(tick);
      else { this._looping = false; this._reveal = null; this.draw(); }
    };
    requestAnimationFrame(tick);
  }
  select(index) { this.selected = index; this.requestDraw(); }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = this.canvas.getBoundingClientRect();
    this.w = width; this.h = height;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.requestDraw();
  }

  requestDraw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.draw(); });
  }

  // ---- interaction ------------------------------------------------------
  _indexAt(clientX) {
    const x = clientX - this.canvas.getBoundingClientRect().left;
    const plotW = this.w - PAD.l - PAD.r;
    return this.vp.start + ((x - PAD.l) / plotW) * this.vp.span;
  }
  _bind() {
    const c = this.canvas;
    c.addEventListener("wheel", (e) => {
      if (!this.vp.len) return;
      e.preventDefault();
      const factor = Math.exp(clamp(e.deltaY, -120, 120) * 0.0025);
      this.vp.zoom(factor, this._indexAt(e.clientX));
    }, { passive: false });
    c.addEventListener("pointerdown", (e) => {
      if (!this.vp.len) return;
      c.setPointerCapture(e.pointerId);
      this._drag = { x: e.clientX, start: this.vp.start };
    });
    c.addEventListener("pointermove", (e) => {
      if (this._drag) {
        const plotW = this.w - PAD.l - PAD.r;
        const bars = ((this._drag.x - e.clientX) / plotW) * this.vp.span;
        this.vp.pan(this._drag.start + bars - this.vp.start);
      }
      const i = Math.floor(this._indexAt(e.clientX));
      this.vp.hover = i >= 0 && i < this.vp.len ? i : null;
      this.vp.emit();
    });
    const end = () => { this._drag = null; };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("pointerleave", () => { this.vp.hover = null; this.vp.emit(); });
    c.addEventListener("dblclick", () => this.vp.reset(this.vp.len));
    c.addEventListener("keydown", (e) => {
      const step = Math.max(1, this.vp.span * 0.1);
      const mid = this.vp.start + this.vp.span / 2;
      const map = { ArrowLeft: () => this.vp.pan(-step), ArrowRight: () => this.vp.pan(step),
        "+": () => this.vp.zoom(0.8, mid), "=": () => this.vp.zoom(0.8, mid), "-": () => this.vp.zoom(1.25, mid),
        Home: () => this.vp.reset(this.vp.len) };
      if (map[e.key]) { e.preventDefault(); map[e.key](); }
    });
  }

  // ---- drawing ----------------------------------------------------------
  draw() {
    const { ctx, w, h } = this;
    if (!w || !h) return;
    ctx.clearRect(0, 0, w, h);
    const colors = {
      ink2: cssVar("--ink-2"), ink3: cssVar("--ink-3"), line: cssVar("--line-soft"), lineStrong: cssVar("--line"),
      up: cssVar("--up"), down: cssVar("--down"), bot: cssVar("--bot"), panel: cssVar("--panel"),
    };
    ctx.font = `500 11px ${cssVar("--font")}`;
    const d = this.data;
    const len = this.vp.len;
    if (!d || !len) {
      ctx.fillStyle = colors.ink3;
      ctx.textAlign = "center";
      ctx.fillText(this.opts.emptyText ? this.opts.emptyText() : "", w / 2, h / 2);
      this.opts.onReadout?.(null);
      return;
    }

    const i0 = Math.max(0, Math.floor(this.vp.start));
    const i1 = Math.min(len - 1, Math.ceil(this.vp.end));
    const plot = { x: PAD.l, y: PAD.t, w: w - PAD.l - PAD.r, h: h - PAD.t - PAD.b };
    const ddBand = this.opts.kind === "line" && d.drawdown ? plot.h * 0.22 : 0;
    const mainH = plot.h - (ddBand ? ddBand + 8 : 0);
    const X = (i) => plot.x + ((i + 0.5 - this.vp.start) / this.vp.span) * plot.w;

    // y range from what is visible
    let lo = Infinity, hi = -Infinity;
    const take = (v) => { if (v != null && Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } };
    if (this.opts.kind === "price") {
      for (let i = i0; i <= i1; i++) { take(d.candles[i].low); take(d.candles[i].high); }
    } else {
      for (const s of d.series) for (let i = i0; i <= i1; i++) take(s.values[i]);
    }
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    if (hi === lo) { hi += 1; lo -= 1; }
    const padY = (hi - lo) * 0.08;
    lo -= padY; hi += padY;
    const Y = (v) => plot.y + (1 - (v - lo) / (hi - lo)) * mainH;

    // grid + y labels
    ctx.strokeStyle = colors.line; ctx.lineWidth = 1; ctx.fillStyle = colors.ink3;
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    const ticks = niceTicks(lo, hi, Math.max(3, Math.floor(mainH / 60)));
    for (const v of ticks) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(plot.x, y); ctx.lineTo(plot.x + plot.w, y); ctx.stroke();
      ctx.fillText(this.opts.format(v), plot.x + plot.w + 8, y);
    }
    // x labels
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const times = d.times;
    const every = Math.max(1, Math.ceil((i1 - i0) / Math.max(2, Math.floor(plot.w / 110))));
    for (let i = Math.ceil(i0 / every) * every; i <= i1; i += every) {
      const label = this.opts.dateFormat(times[i]);
      const half = ctx.measureText(label).width / 2;
      if (X(i) - half < plot.x || X(i) + half > plot.x + plot.w) continue; // never clip a label
      ctx.fillText(label, X(i), plot.y + plot.h + 8);
    }

    const reveal = this._reveal ? easeOut(Math.min(1, (performance.now() - this._reveal.start) / this._reveal.duration)) : 1;
    ctx.save();
    ctx.beginPath(); ctx.rect(plot.x, plot.y - 4, plot.w * reveal, plot.h + 8); ctx.clip();

    if (this.opts.kind === "price") this._drawCandles(d, i0, i1, X, Y, plot, colors);
    else this._drawLines(d, i0, i1, X, Y, colors, plot, mainH, ddBand);

    // crosshair
    const hv = this.vp.hover;
    if (hv != null && hv >= i0 && hv <= i1) {
      const x = Math.round(X(hv)) + 0.5;
      ctx.strokeStyle = colors.ink3; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, plot.y); ctx.lineTo(x, plot.y + plot.h); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
    if (reveal === 1) this._drawLastTag(d, i1, X, Y, plot, colors);
    this.opts.onReadout?.(hv);
  }

  _drawLastTag(d, i1, X, Y, plot, colors) {
    const ctx = this.ctx;
    const last = this.vp.len - 1;
    const value = this.opts.kind === "price" ? (this._smooth?.close ?? d.candles[last]?.close) : d.series?.[0]?.values[last];
    if (value == null || last > i1 + 1) return;
    const y = Math.round(Y(value)) + 0.5;
    if (y < plot.y || y > plot.y + plot.h) return;
    ctx.strokeStyle = colors.bot; ctx.globalAlpha = 0.55; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(plot.x, y); ctx.lineTo(plot.x + plot.w, y); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    const label = this.opts.format(value);
    ctx.font = `650 11px ${cssVar("--font")}`;
    const w = ctx.measureText(label).width + 12;
    ctx.fillStyle = colors.bot;
    ctx.beginPath(); ctx.roundRect(plot.x + plot.w + 3, y - 9, w, 18, 4); ctx.fill();
    ctx.fillStyle = cssVar("--on-bot"); ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(label, plot.x + plot.w + 9, y + 0.5);
    if (this._live && last >= this.vp.start && last <= this.vp.end) {
      const x = X(last);
      const phase = (performance.now() % 1400) / 1400;
      ctx.fillStyle = colors.bot;
      ctx.globalAlpha = 0.35 * (1 - phase);
      ctx.beginPath(); ctx.arc(x, y, 4 + phase * 14, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
    }
  }

  /** Last close eased toward the newest trade price, so the live candle glides. */
  _displayClose(d) {
    const last = d.candles[d.candles.length - 1];
    if (!last) return null;
    if (!this._live) { this._smooth = null; return last.close; }
    const s = this._smooth;
    if (!s || s.time !== last.time) this._smooth = { time: last.time, close: last.close };
    else s.close += (last.close - s.close) * 0.18;
    return this._smooth.close;
  }

  _drawCandles(d, i0, i1, X, Y, plot, colors) {
    const ctx = this.ctx;
    const bw = plot.w / this.vp.span;
    const body = Math.max(1, Math.min(14, bw * 0.66));
    const lastIdx = d.candles.length - 1;
    const shown = this._displayClose(d);
    for (let i = i0; i <= i1; i++) {
      let c = d.candles[i];
      if (i === lastIdx && shown != null) c = { ...c, close: shown, high: Math.max(c.high, shown), low: Math.min(c.low, shown) };
      const up = c.close >= c.open;
      ctx.strokeStyle = ctx.fillStyle = up ? colors.up : colors.down;
      const x = Math.round(X(i));
      ctx.beginPath(); ctx.moveTo(x + 0.5, Y(c.high)); ctx.lineTo(x + 0.5, Y(c.low)); ctx.stroke();
      const top = Y(Math.max(c.open, c.close));
      const height = Math.max(1, Y(Math.min(c.open, c.close)) - top);
      ctx.fillRect(Math.round(x - body / 2 + 0.5), top, Math.max(1, Math.round(body)), height);
    }
    // overlays
    (d.overlays || []).forEach((o) => this._path(o.values, i0, i1, X, Y, o.color, 1.6, o.dash));
    if (d.liveFrom > 0 && d.liveFrom >= i0 && d.liveFrom <= i1) {
      const x = Math.round(X(d.liveFrom) - bw / 2) + 0.5;
      ctx.strokeStyle = colors.bot; ctx.globalAlpha = 0.6; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, plot.y); ctx.lineTo(x, plot.y + plot.h); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      const label = this.opts.liveLabel?.() || "";
      if (label) {
        ctx.font = `700 10px ${cssVar("--font")}`;
        const w = ctx.measureText(label.toUpperCase()).width + 12;
        const lx = x + 4 + w > plot.x + plot.w ? x - 4 - w : x + 4; // flip left near the edge
        ctx.fillStyle = colors.bot;
        ctx.beginPath(); ctx.roundRect(lx, plot.y + 2, w, 16, 8); ctx.fill();
        ctx.fillStyle = cssVar("--on-bot"); ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(label.toUpperCase(), lx + 6, plot.y + 10.5);
      }
    }
    // the bot's orders as markers; fresh ones pop in with a ripple
    const now = performance.now();
    for (const f of d.fills || []) {
      const i = f.index;
      if (i < i0 || i > i1) continue;
      const c = d.candles[i];
      const x = X(i);
      const buy = f.side === "buy";
      const y = buy ? Y(c.low) + 10 : Y(c.high) - 10;
      const age = f.born ? now - f.born : Infinity;
      if (age < 1200) {
        const t = age / 1200;
        ctx.strokeStyle = colors.bot; ctx.globalAlpha = 1 - t; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 6 + t * 30, 0, Math.PI * 2); ctx.stroke();
        ctx.globalAlpha = 1; ctx.lineWidth = 1;
      }
      const s = 6 * (age < 500 ? Math.max(0.01, easeOutBack(age / 500)) : 1);
      ctx.fillStyle = colors.bot;
      ctx.beginPath();
      if (buy) { ctx.moveTo(x, y - s); ctx.lineTo(x + s, y + s * 0.8); ctx.lineTo(x - s, y + s * 0.8); }
      else { ctx.moveTo(x, y + s); ctx.lineTo(x + s, y - s * 0.8); ctx.lineTo(x - s, y - s * 0.8); }
      ctx.closePath(); ctx.fill();
      if (this.selected === f.n) {
        ctx.strokeStyle = colors.bot; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 11, 0, Math.PI * 2); ctx.stroke(); ctx.lineWidth = 1;
      }
    }
  }

  _drawLines(d, i0, i1, X, Y, colors, plot, mainH, ddBand) {
    const ctx = this.ctx;
    if (ddBand) {
      const base = plot.y + mainH + 8;
      let min = 0;
      for (let i = i0; i <= i1; i++) min = Math.min(min, d.drawdown[i] ?? 0);
      const scale = min < 0 ? ddBand / -min : 0;
      ctx.fillStyle = colors.down; ctx.globalAlpha = 0.22;
      ctx.beginPath(); ctx.moveTo(X(i0), base);
      for (let i = i0; i <= i1; i++) ctx.lineTo(X(i), base - (d.drawdown[i] ?? 0) * scale);
      ctx.lineTo(X(i1), base); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = colors.lineStrong;
      ctx.beginPath(); ctx.moveTo(plot.x, base + 0.5); ctx.lineTo(plot.x + plot.w, base + 0.5); ctx.stroke();
      ctx.fillStyle = colors.ink3; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      if (min < 0) ctx.fillText(`${(min * 100).toFixed(1)}%`, plot.x + plot.w + 8, base + ddBand);
    }
    d.series.forEach((s) => this._path(s.values, i0, i1, X, Y, s.color, s.width || 1.8, s.dash));
  }

  _path(values, i0, i1, X, Y, color, width, dash) {
    const ctx = this.ctx;
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round";
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    let pen = false;
    for (let i = i0; i <= i1; i++) {
      const v = values[i];
      if (v == null) { pen = false; continue; }
      if (pen) ctx.lineTo(X(i), Y(v)); else { ctx.moveTo(X(i), Y(v)); pen = true; }
    }
    ctx.stroke();
    ctx.setLineDash([]); ctx.lineWidth = 1;
  }
}

function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(+v.toFixed(10));
  return out;
}
