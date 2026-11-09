// Live market feed straight from Binance's public WebSocket streams:
// order book (top 10 levels), aggregated trades and the 24h ticker.
// Public market data only: no account, no key, no orders.

const HOSTS = ["wss://data-stream.binance.vision", "wss://stream.binance.com:9443"];

export class MarketStream {
  /**
   * @param {{onDepth?: Function, onTrade?: Function, onTicker?: Function, onStatus?: (s: "connecting"|"open"|"retry") => void}} handlers
   */
  constructor(handlers) {
    this.h = handlers;
    this.ws = null;
    this.symbol = null;
    this.host = 0;
    this.attempt = 0;
    this.closed = true;
  }

  open(symbol) {
    if (this.symbol === symbol && !this.closed) return;
    this.close();
    this.symbol = symbol;
    this.closed = false;
    this.attempt = 0;
    this._connect();
  }

  close() {
    this.closed = true;
    clearTimeout(this._retry);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    this.ws = null;
  }

  _connect() {
    const s = this.symbol.toLowerCase();
    const url = `${HOSTS[this.host]}/stream?streams=${s}@depth10@100ms/${s}@aggTrade/${s}@ticker`;
    this.h.onStatus?.("connecting");
    let ws;
    try { ws = new WebSocket(url); } catch { this._scheduleRetry(); return; }
    this.ws = ws;
    ws.onopen = () => { this.attempt = 0; this.h.onStatus?.("open"); };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      const { stream = "", data } = msg;
      if (!data) return;
      if (stream.endsWith("@aggTrade")) {
        // m = buyer was the maker, so the aggressor sold
        this.h.onTrade?.({ price: +data.p, qty: +data.q, time: data.T, side: data.m ? "sell" : "buy" });
      } else if (stream.includes("@depth")) {
        this.h.onDepth?.({ bids: data.bids.map(([p, q]) => [+p, +q]), asks: data.asks.map(([p, q]) => [+p, +q]) });
      } else if (stream.endsWith("@ticker")) {
        this.h.onTicker?.({ last: +data.c, changePct: +data.P, high: +data.h, low: +data.l, quoteVolume: +data.q });
      }
    };
    ws.onclose = () => { if (!this.closed) this._scheduleRetry(); };
    ws.onerror = () => ws.close();
  }

  _scheduleRetry() {
    this.h.onStatus?.("retry");
    this.host = (this.host + 1) % HOSTS.length; // alternate hosts
    const delay = Math.min(15000, 800 * 2 ** this.attempt++);
    this._retry = setTimeout(() => { if (!this.closed) this._connect(); }, delay);
  }
}
