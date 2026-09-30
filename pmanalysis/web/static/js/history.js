const PAGE_SIZE = 200;
let replayCharts = null;
let currentTab = "windows";
let pageOffset = 0;
let pageTotal = 0;

function fmtTs(tsMs) {
  if (!tsMs) return "—";
  return new Date(tsMs).toLocaleString();
}

function fmtNum(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString();
}

function setFiltersForTab(tab) {
  const interval = document.getElementById("filter-interval");
  const side = document.getElementById("filter-side");
  const source = document.getElementById("filter-source");
  const replay = document.getElementById("replay-panel");

  interval.classList.toggle("hidden", tab !== "windows");
  side.classList.toggle("hidden", tab !== "clob");
  source.classList.toggle("hidden", tab !== "oracle");
  replay.classList.toggle("hidden", tab !== "windows");
}

async function loadStats() {
  const el = document.getElementById("history-stats");
  try {
    const res = await fetch("/api/feeds/stats");
    const s = await res.json();
    el.innerHTML = [
      ["Windows", s.window_count],
      ["Ticks (1s)", s.tick_count],
      ["Binance trades", s.binance_trade_count],
      ["Oracle ticks", s.oracle_tick_count],
      ["CLOB quotes", s.clob_quote_count],
    ]
      .map(
        ([label, n]) =>
          `<div class="stat-chip"><span class="stat-label">${label}</span><span class="stat-value">${fmtNum(n)}</span></div>`
      )
      .join("");
  } catch (err) {
    el.textContent = `Failed to load counts: ${err.message}`;
  }
}

function updatePager() {
  const label = document.getElementById("page-label");
  const prev = document.getElementById("page-prev");
  const next = document.getElementById("page-next");
  const from = pageTotal === 0 ? 0 : pageOffset + 1;
  const to = Math.min(pageOffset + PAGE_SIZE, pageTotal);
  label.textContent = `${fmtNum(from)}–${fmtNum(to)} of ${fmtNum(pageTotal)}`;
  prev.disabled = pageOffset <= 0;
  next.disabled = pageOffset + PAGE_SIZE >= pageTotal;
}

function setTable(headers, rowsHtml) {
  const thead = document.querySelector("#history-table thead");
  const tbody = document.querySelector("#history-table tbody");
  thead.innerHTML = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>`;
  tbody.innerHTML = rowsHtml || `<tr><td colspan="${headers.length}">No rows</td></tr>`;
}

async function loadWindows() {
  const asset = document.getElementById("filter-asset").value;
  const interval = document.getElementById("filter-interval").value;
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(pageOffset),
  });
  if (asset) params.set("asset", asset);
  if (interval) params.set("interval", interval);

  const res = await fetch(`/api/windows?${params}`);
  const data = await res.json();
  const rows = data.items || [];
  pageTotal = data.total || 0;
  updatePager();

  setTable(
    ["Slug", "Asset", "Interval", "End", "Strike", "Outcome", "Ticks", "Export"],
    rows
      .map(
        (r) => `<tr>
      <td><a href="#" data-slug="${r.slug}">${r.slug}</a></td>
      <td>${r.asset}</td><td>${r.interval}</td>
      <td>${new Date(r.end_ts * 1000).toLocaleString()}</td>
      <td>${r.strike_price ?? "—"}</td><td>${r.outcome ?? "—"}</td>
      <td>${fmtNum(r.tick_count)}</td>
      <td><a href="/api/export/${r.id}?format=csv">CSV</a></td>
    </tr>`
      )
      .join("")
  );

  document.querySelectorAll("a[data-slug]").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      const tickRes = await fetch(
        `/api/windows/${encodeURIComponent(el.dataset.slug)}/ticks`
      );
      const tickData = await tickRes.json();
      if (!replayCharts) {
        replayCharts = new PM.DualCharts(
          document.getElementById("replay-oracle"),
          document.getElementById("replay-prob")
        );
      }
      const points = tickData.ticks.map((t) => ({
        ts_ms: t.ts_ms,
        oracle_price: t.oracle_price,
        binance_price: t.binance_price,
        yes_mid: t.yes_mid,
        no_mid: t.no_mid,
      }));
      replayCharts.setHistory(
        points,
        tickData.window.strike_price,
        tickData.window.interval || "5m"
      );
    });
  });
}

async function loadBinance() {
  const asset = document.getElementById("filter-asset").value;
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(pageOffset),
  });
  if (asset) params.set("asset", asset);
  const res = await fetch(`/api/feeds/binance?${params}`);
  const data = await res.json();
  pageTotal = data.total || 0;
  updatePager();
  setTable(
    ["Time", "Asset", "Price", "Qty", "Trade ID"],
    (data.items || [])
      .map(
        (r) => `<tr>
      <td>${fmtTs(r.ts_ms)}</td>
      <td>${r.asset}</td>
      <td>${r.price}</td>
      <td>${r.qty ?? "—"}</td>
      <td>${r.trade_id ?? "—"}</td>
    </tr>`
      )
      .join("")
  );
}

async function loadOracle() {
  const asset = document.getElementById("filter-asset").value;
  const source = document.getElementById("filter-source").value;
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(pageOffset),
  });
  if (asset) params.set("asset", asset);
  if (source) params.set("source", source);
  const res = await fetch(`/api/feeds/oracle?${params}`);
  const data = await res.json();
  pageTotal = data.total || 0;
  updatePager();
  setTable(
    ["Time", "Asset", "Price", "Source"],
    (data.items || [])
      .map(
        (r) => `<tr>
      <td>${fmtTs(r.ts_ms)}</td>
      <td>${r.asset}</td>
      <td>${r.price}</td>
      <td>${r.source}</td>
    </tr>`
      )
      .join("")
  );
}

async function loadClob() {
  const asset = document.getElementById("filter-asset").value;
  const side = document.getElementById("filter-side").value;
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(pageOffset),
  });
  if (asset) params.set("asset", asset);
  if (side) params.set("side", side);
  const res = await fetch(`/api/feeds/clob?${params}`);
  const data = await res.json();
  pageTotal = data.total || 0;
  updatePager();
  setTable(
    ["Time", "Asset", "Interval", "Side", "Ask", "Bid", "Event", "Token"],
    (data.items || [])
      .map(
        (r) => `<tr>
      <td>${fmtTs(r.ts_ms)}</td>
      <td>${r.asset ?? "—"}</td>
      <td>${r.interval ?? "—"}</td>
      <td>${r.side ?? "—"}</td>
      <td>${r.best_ask}</td>
      <td>${r.best_bid}</td>
      <td>${r.event_type ?? "—"}</td>
      <td class="mono-cell" title="${r.token_id}">${(r.token_id || "").slice(0, 12)}…</td>
    </tr>`
      )
      .join("")
  );
}

async function loadHistory() {
  setFiltersForTab(currentTab);
  if (currentTab === "windows") await loadWindows();
  else if (currentTab === "binance") await loadBinance();
  else if (currentTab === "oracle") await loadOracle();
  else if (currentTab === "clob") await loadClob();
}

async function clearDatabase() {
  const ok = window.confirm(
    "Delete ALL collected data?\n\n" +
      "This removes every window, tick, indicator, Binance/oracle/CLOB stream, archive, and export.\n" +
      "Market definitions are kept. This cannot be undone."
  );
  if (!ok) return;

  const password = window.prompt("Enter admin password to confirm:");
  if (password === null) return;

  const btn = document.getElementById("clear-db-btn");
  btn.disabled = true;
  btn.textContent = "Clearing…";
  try {
    const res = await fetch("/api/admin/clear-db", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Clear failed");
    if (replayCharts) {
      replayCharts.destroy();
      replayCharts = null;
    }
    pageOffset = 0;
    await loadStats();
    await loadHistory();
    alert(
      "Database cleared.\n" +
        `Windows: ${data.cleared?.windows ?? 0}\n` +
        `Ticks: ${data.cleared?.ticks ?? 0}\n` +
        `Binance: ${data.cleared?.binance_trades ?? 0}\n` +
        `Oracle: ${data.cleared?.oracle_ticks ?? 0}\n` +
        `CLOB: ${data.cleared?.clob_quotes ?? 0}`
    );
  } catch (err) {
    alert(`Clear failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Clear Database";
  }
}

document.getElementById("clear-db-btn").addEventListener("click", clearDatabase);
document.getElementById("filter-asset").addEventListener("change", () => {
  pageOffset = 0;
  loadHistory();
});
document.getElementById("filter-interval").addEventListener("change", () => {
  pageOffset = 0;
  loadHistory();
});
document.getElementById("filter-side").addEventListener("change", () => {
  pageOffset = 0;
  loadHistory();
});
document.getElementById("filter-source").addEventListener("change", () => {
  pageOffset = 0;
  loadHistory();
});
document.getElementById("page-prev").addEventListener("click", () => {
  pageOffset = Math.max(0, pageOffset - PAGE_SIZE);
  loadHistory();
});
document.getElementById("page-next").addEventListener("click", () => {
  pageOffset += PAGE_SIZE;
  loadHistory();
});

document.querySelectorAll(".history-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".history-tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentTab = btn.dataset.tab;
    pageOffset = 0;
    loadHistory();
  });
});

loadStats();
loadHistory();
