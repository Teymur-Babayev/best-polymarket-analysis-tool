window.PM = window.PM || {};

PM.LiveSocket = class {
  constructor(url, handlers = {}) {
    this.url = url;
    this.handlers = handlers;
    this.ws = null;
    this._retry = 1000;
    this._shouldRun = true;
    this._connecting = false;
    this._reconnectTimer = null;
    this._selected = { asset: "BTC", interval: "5m" };
  }

  select(asset, interval) {
    this._selected = { asset, interval };
    this._send({ type: "select", asset, interval });
  }

  _send(payload) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  connect() {
    if (!this._shouldRun) return;
    if (this._connecting) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    clearTimeout(this._reconnectTimer);
    this._connecting = true;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}${this.url}`);
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this._connecting = false;
      this._retry = 1000;
      this.handlers.onStatus?.("connected");
      this.select(this._selected.asset, this._selected.interval);
    };

    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this._connecting = false;
      if (!this._shouldRun) return;
      this.handlers.onStatus?.("disconnected");
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = setTimeout(() => this.connect(), this._retry);
      this._retry = Math.min(this._retry * 2, 10000);
    };

    ws.onerror = () => {
      /* onclose handles reconnect and status */
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") this.handlers.onSnapshot?.(msg);
        else if (msg.type === "oracle") this.handlers.onOracle?.(msg);
        else if (msg.type === "history") this.handlers.onHistory?.(msg);
        else if (msg.type === "tick") this.handlers.onTick?.(msg);
      } catch (e) {
        console.error("ws parse", e);
      }
    };
  }

  close() {
    this._shouldRun = false;
    clearTimeout(this._reconnectTimer);
    this.ws?.close();
    this.ws = null;
    this._connecting = false;
  }
};
