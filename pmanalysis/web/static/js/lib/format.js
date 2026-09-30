window.PM = window.PM || {};

PM.fmtUsd = (v) =>
  v != null ? "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";

PM.fmtUsdPrecise = (v) =>
  v != null
    ? "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";

PM.fmtProb = (v) =>
  v != null && !Number.isNaN(v) ? (Number(v) * 100).toFixed(1) + "¢" : "—";

PM.fmtSecs = (s) => {
  if (!s || s <= 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
};

PM.fmtCountdown = (s) => {
  if (!s || s <= 0) return { mins: "00", secs: "00" };
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return {
    mins: String(m).padStart(2, "0"),
    secs: String(sec).padStart(2, "0"),
  };
};

PM.fmtWindowRange = (startTs, endTs) => {
  if (!startTs || !endTs) return "—";
  const opts = { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", hour12: true };
  const datePart = new Date(startTs * 1000).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "long",
    day: "numeric",
  });
  const startTime = new Date(startTs * 1000).toLocaleString("en-US", opts).replace(/\s/g, "");
  const endTime = new Date(endTs * 1000).toLocaleString("en-US", opts).replace(/\s/g, "");
  return `${datePart}, ${startTime}-${endTime} ET`;
};

PM.fmtPriceDelta = (dist) => {
  if (dist == null) return { text: "", cls: "" };
  const abs = Math.abs(dist);
  const dollars = abs >= 1 ? `$${Math.round(abs)}` : `$${abs.toFixed(2)}`;
  if (dist >= 0) return { text: `▲ ${dollars}`, cls: "up" };
  return { text: `▼ ${dollars}`, cls: "down" };
};

PM.filterPointsForWindow = (points, startTs, endTs) => {
  if (!points?.length || !startTs || !endTs) return points || [];
  const startMs = startTs * 1000;
  const endMs = endTs * 1000;
  return points.filter((p) => p.ts_ms >= startMs - 500 && p.ts_ms <= endMs + 500);
};

PM.distFromOpening = (current, opening) => {
  if (current == null || opening == null) return null;
  return Math.round((current - opening) * 100) / 100;
};

/** Difference = current TWAP − initial TWAP. */
PM.distInitialMinusTwap = (initial, currentTwap) => {
  if (initial == null || currentTwap == null) return null;
  return Math.round((currentTwap - initial) * 100) / 100;
};

/** 5m and 15m both settle on 60s Chainlink TWAP (from 2026-08-14 UTC). */
PM.twapWindowSec = (_interval) => 60;

PM.twapLabel = (interval) => `TWAP ${PM.twapWindowSec(interval)}s`;

/** Pick live TWAP for asset/interval from oracle payload. */
PM.pickTwapPrice = (msg, asset, interval) => {
  if (!msg || !asset) return null;
  const win = String(PM.twapWindowSec(interval));
  const byWin = msg.twap_by_window?.[win]?.[asset];
  if (byWin != null) return byWin;
  return msg.twap_prices?.[asset] ?? null;
};

/** First Binance tick at/after window open — aligns chart at 0:00. */
PM.openingBinanceFromPoints = (points, startTs) => {
  if (!points?.length || !startTs) return null;
  const startMs = startTs * 1000;
  let best = null;
  for (const p of points) {
    if (p.binance_price == null || p.binance_price <= 0) continue;
    if (p.ts_ms < startMs - 2000 || p.ts_ms > startMs + 120_000) continue;
    if (!best || p.ts_ms < best.ts_ms) best = p;
  }
  return best?.binance_price ?? null;
};

/** First YES/NO tick at/after window open. */
PM.openingProbFromPoints = (points, startTs) => {
  if (!points?.length || !startTs) return null;
  const startMs = startTs * 1000;
  let best = null;
  for (const p of points) {
    if (!(p.yes_mid > 0 || p.no_mid > 0)) continue;
    if (p.ts_ms < startMs - 2000 || p.ts_ms > startMs + 120_000) continue;
    if (!best || p.ts_ms < best.ts_ms) best = p;
  }
  if (!best) return null;
  return { yes: best.yes_mid * 100, no: best.no_mid * 100 };
};

PM.openingFromPoints = (points, startTs) => {
  if (!points?.length || !startTs) return null;
  const startMs = startTs * 1000;
  let best = null;
  for (const p of points) {
    if (p.oracle_price == null || p.oracle_price <= 0) continue;
    if (p.ts_ms < startMs - 2000 || p.ts_ms > startMs + 15000) continue;
    if (!best || p.ts_ms < best.ts_ms) best = p;
  }
  return best?.oracle_price ?? null;
};

/** First TWAP sample near window open — this is the Initial Price (never Chainlink spot). */
PM.twapInitialFromPoints = (points, startTs) => {
  if (!points?.length) return null;
  const startMs = startTs ? startTs * 1000 : null;
  let best = null;
  for (const p of points) {
    const twap = p.twap_oracle;
    if (twap == null || twap <= 0) continue;
    if (startMs != null && (p.ts_ms < startMs - 2000 || p.ts_ms > startMs + 15000)) continue;
    if (!best || p.ts_ms < best.ts_ms) best = p;
  }
  if (best?.twap_oracle != null) return best.twap_oracle;
  // Earliest TWAP in the series after open — do not fall back to Chainlink spot.
  let earliest = null;
  for (const p of points) {
    if (p.twap_oracle == null || p.twap_oracle <= 0) continue;
    if (startMs != null && p.ts_ms < startMs - 2000) continue;
    if (!earliest || p.ts_ms < earliest.ts_ms) earliest = p;
  }
  return earliest?.twap_oracle ?? null;
};

PM.polymarketEventUrl = (slug) => {
  const clean = String(slug || "").trim().toLowerCase();
  if (!clean) return null;
  // Crypto up/down pages use the market slug directly, e.g.
  // https://polymarket.com/event/btc-updown-5m-1784264400
  return `https://polymarket.com/event/${clean}`;
};

PM.windowBounds = (m, interval) => {
  if (m?.start_ts && m?.end_ts) return { start: m.start_ts, end: m.end_ts };
  const slug = m?.slug || "";
  const match = slug.match(/-(\d+)$/);
  if (!match) return null;
  const start = parseInt(match[1], 10);
  const sec = window.PM_CONFIG?.intervalSeconds?.[interval] || 300;
  return { start, end: start + sec };
};

PM.marketMap = (markets) => {
  const map = {};
  (markets || []).forEach((m) => {
    map[`${m.asset}:${m.interval}`] = m;
  });
  return map;
};

PM.positionClass = (m) => {
  if (!m || m.dist_from_strike == null) return "idle";
  return m.dist_from_strike >= 0 ? "win" : "lose";
};
