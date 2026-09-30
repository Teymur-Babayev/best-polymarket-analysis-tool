(() => {
  const { assets, intervals, preselect } = window.PM_CONFIG;
  let selected = { asset: preselect.asset, interval: preselect.interval };
  let marketByKey = {};
  let charts = null;
  let liveSlug = "";
  let selectedSlug = "";
  let slugWindows = [];
  let slugCacheKey = "";
  let slugRefreshAt = 0;
  let viewingLive = true;
  let activeBounds = null;
  let countdownEndTs = 0;
  let countdownTimer = null;
  let openingPollTimer = null;
  let chartOpeningPrice = null;
  let lastOraclePrices = {};
  let wsStatusTimer = null;
  let wsIsLive = false;
  let tickerRaf = 0;
  let navRaf = 0;
  let metricsRaf = 0;
  let pendingMetricsMarket = null;

  let previewCharts = [];
  let previewLoadToken = 0;
  let previewKey = "";
  let previewPage = 0;
  let previewHasOlder = false;
  let previewHasNewer = false;

  const el = (id) => document.getElementById(id);
  const SLUG_CACHE_MS = 60000;
  const PREVIEW_COUNT = 7;
  const SIDEBAR_KEY = "pm.sidebarCollapsed";
  const NARROW_MQ = window.matchMedia("(max-width: 780px)");

  function isNarrow() {
    return NARROW_MQ.matches;
  }

  function updateTwapLabels() {
    const label = PM.twapLabel(selected.interval);
    const heroLabel = el("hero-twap-label");
    if (heroLabel) heroLabel.textContent = label;
    const legend = el("legend-twap");
    if (legend) legend.textContent = label;
    if (typeof charts?.setTwapLabel === "function") charts.setTwapLabel(label);
  }

  function applySidebarCollapsed(collapsed, persist) {
    const layout = el("app-layout");
    const btn = el("sidebar-toggle");
    const backdrop = el("sidebar-backdrop");
    if (!layout) return;
    layout.classList.toggle("sidebar-collapsed", !!collapsed);
    if (btn) {
      const icon = btn.querySelector(".sidebar-toggle-icon") || btn;
      icon.textContent = collapsed ? "›" : "‹";
      const title = isNarrow()
        ? collapsed
          ? "Show markets"
          : "Close markets"
        : collapsed
          ? "Show markets panel"
          : "Hide markets panel";
      btn.title = title;
      btn.setAttribute("aria-label", title);
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    if (backdrop) backdrop.hidden = !isNarrow() || !!collapsed;
    if (persist !== false && !isNarrow()) {
      try {
        localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
    }
    requestAnimationFrame(() => {
      try {
        charts?.resize?.();
      } catch (_) {
        /* ignore */
      }
    });
  }

  function setCountdownDigits(mins, secs) {
    const timer = el("countdown-timer");
    const outcome = el("countdown-outcome");
    if (timer) timer.hidden = false;
    if (outcome) {
      outcome.hidden = true;
      outcome.textContent = "";
    }
    el("countdown").className = "countdown poly-countdown";
    if (el("countdown-mins")) el("countdown-mins").textContent = mins;
    if (el("countdown-secs")) el("countdown-secs").textContent = secs;
  }

  function setCountdownResolved(text) {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = null;
    el("countdown-timer").hidden = true;
    const outcome = el("countdown-outcome");
    outcome.hidden = false;
    outcome.textContent = text;
    el("countdown").className = "countdown poly-countdown resolved";
  }

  function clearCountdown() {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = null;
    setCountdownDigits("—", "—");
  }

  function setWsStatus(state) {
    const badge = el("ws-status");
    if (!badge) return;

    if (state === "connected") {
      wsIsLive = true;
      clearTimeout(wsStatusTimer);
      badge.textContent = "Live";
      badge.className = "ws-badge ws-on";
      return;
    }

    wsIsLive = false;
    clearTimeout(wsStatusTimer);
    wsStatusTimer = setTimeout(() => {
      if (wsIsLive) return;
      badge.textContent = state === "disconnected" ? "Reconnecting…" : "Connecting…";
      badge.className = "ws-badge ws-off";
    }, 1500);
  }

  function scheduleRenderTicker(oracle) {
    if (oracle) lastOraclePrices = { ...lastOraclePrices, ...oracle };
    if (tickerRaf) return;
    tickerRaf = requestAnimationFrame(() => {
      tickerRaf = 0;
      renderTicker();
    });
  }

  function scheduleNavBadges() {
    if (navRaf) return;
    navRaf = requestAnimationFrame(() => {
      navRaf = 0;
      updateNavBadges();
    });
  }
  function scheduleUpdateMetrics(m) {
    if (!m) return;
    pendingMetricsMarket = m;
    if (metricsRaf) return;
    metricsRaf = requestAnimationFrame(() => {
      metricsRaf = 0;
      if (pendingMetricsMarket) {
        updateMetrics(pendingMetricsMarket);
        pendingMetricsMarket = null;
      }
    });
  }

  function marketBounds(m, interval = selected.interval) {
    if (m?.start_ts && m?.end_ts) return { start: m.start_ts, end: m.end_ts };
    return PM.windowBounds(m, interval);
  }

  function renderTicker() {
    el("oracle-ticker").innerHTML = assets
      .map((asset) => {
        const price = lastOraclePrices[asset];
        const m = marketByKey[`${asset}:5m`];
        const opening = m?.twap_initial ?? null;
        const currentTwap = m?.twap_oracle ?? null;
        const current = price ?? m?.oracle_price ?? null;
        // Difference = current TWAP − initial TWAP.
        const diff = PM.distInitialMinusTwap(opening, currentTwap);
        const delta = PM.fmtPriceDelta(diff);
        const priceText = current != null ? PM.fmtUsd(current) : "—";
        const diffHtml =
          diff != null
            ? `<span class="ticker-diff ${delta.cls}">${delta.text}</span>`
            : "";
        return (
          `<div class="ticker-item">` +
          `<img class="ticker-icon" src="/static/img/coins/${asset.toLowerCase()}.svg?v=term37" alt="${asset}" title="${asset}" width="16" height="16">` +
          `<span class="ticker-price">${priceText}</span>` +
          diffHtml +
          `</div>`
        );
      })
      .join("");
  }

  function currentViewSlug() {
    if (selectedSlug) return selectedSlug;
    if (viewingLive && liveSlug) return liveSlug;
    const m = marketByKey[`${selected.asset}:${selected.interval}`];
    return m?.slug || "";
  }

  function updatePolymarketLinks(slug = currentViewSlug()) {
    const resolved = String(slug || currentViewSlug() || "").trim();
    const url = PM.polymarketEventUrl(resolved);
    ["poly-link-oracle", "poly-link-prob"].forEach((id) => {
      const link = el(id);
      if (!link) return;
      if (url) {
        link.href = url;
        link.dataset.slug = resolved;
        link.title = `Open ${resolved} on Polymarket`;
        link.setAttribute("aria-disabled", "false");
        link.classList.remove("is-disabled");
        link.hidden = false;
      } else {
        link.href = "#";
        link.dataset.slug = "";
        link.title = "No market selected";
        link.setAttribute("aria-disabled", "true");
        link.classList.add("is-disabled");
        link.hidden = false;
      }
    });
  }

  function openDisplayedPolymarket(ev) {
    if (ev) ev.preventDefault();
    const slug = currentViewSlug();
    const url = PM.polymarketEventUrl(slug);
    if (!url) {
      alert("No market slug to open yet");
      return false;
    }
    updatePolymarketLinks(slug);
    window.open(url, "_blank", "noopener,noreferrer");
    return false;
  }

  function updateChartTitle() {
    const title = el("oracle-chart-title");
    if (title) {
      title.textContent = `${selected.asset} Price Chart`;
    }
  }

  function updateNavBadges() {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      const key = `${btn.dataset.asset}:${btn.dataset.interval}`;
      const m = marketByKey[key];
      const badge = btn.querySelector(".badge");
      if (badge) badge.textContent = m?.slug ? PM.fmtProb(m.yes_mid) : "—";
      btn.classList.remove("win", "lose", "idle");
      btn.classList.add(PM.positionClass(m));
      btn.classList.toggle(
        "active",
        selected.asset === btn.dataset.asset && selected.interval === btn.dataset.interval
      );
    });
  }

  function renderNav() {
    const nav = el("market-nav");
    const expectedCount = assets.length * intervals.length;
    const needsBuild =
      !nav.querySelector(".nav-btn") ||
      nav.querySelectorAll(".nav-btn").length !== expectedCount;
    if (needsBuild) {
      nav.innerHTML = assets
        .map((asset) => {
          const buttons = intervals
            .map((interval) => {
              const key = `${asset}:${interval}`;
              const m = marketByKey[key];
              const cls = PM.positionClass(m);
              const active =
                selected.asset === asset && selected.interval === interval ? "active" : "";
              const badge = m?.slug ? PM.fmtProb(m.yes_mid) : "—";
              return `<button class="nav-btn ${cls} ${active}" data-asset="${asset}" data-interval="${interval}">
                <span>${interval}</span><span class="badge">${badge}</span>
              </button>`;
            })
            .join("");
          return `<div class="asset-group"><div class="asset-label"><img class="asset-label-icon" src="/static/img/coins/${asset.toLowerCase()}.svg?v=term37" alt="" width="14" height="14">${asset}</div>${buttons}</div>`;
        })
        .join("");

      nav.querySelectorAll(".nav-btn").forEach((btn) => {
        btn.addEventListener("click", () => switchMarket(btn.dataset.asset, btn.dataset.interval));
      });
    } else {
      updateNavBadges();
    }
  }

  async function switchMarket(asset, interval) {
    selected = { asset, interval };
    viewingLive = true;
    selectedSlug = "";
    slugRefreshAt = 0;
    activeBounds = null;
    chartOpeningPrice = null;
    updateLiveButton();
    updateTwapLabels();

    const m = marketByKey[`${asset}:${interval}`];
    el("sel-title").textContent = `${asset} Up or Down · ${interval}`;
    updateChartTitle();
    if (m?.slug) {
      liveSlug = m.slug;
      selectedSlug = liveSlug;
      const bounds = PM.windowBounds(m, interval);
      const openings = chartOpenings([], bounds?.start, m);
      applyBounds(bounds, true, openings);
      if (charts) {
        charts.setHistory(
          [],
          openings.twapInitial ?? m.twap_initial ?? null,
          bounds?.start,
          bounds?.end,
          openings.opening,
          openings.openingBinance,
          openings.openingProb,
          openings.twapInitial ?? m.twap_initial ?? null
        );
      }
      updateMetrics(m);
      startCountdown(m.end_ts);
    } else {
      liveSlug = "";
      clearCountdown();
      applyBounds(null, true);
      if (charts) charts.setHistory([], null, 0, 0, null);
    }

    updateNavBadges();
    socket.select(asset, interval);
    if (isNarrow()) applySidebarCollapsed(true, false);
    await refreshSlugDropdown(false);

    if (viewingLive && liveSlug) {
      try {
        await loadSlugHistory(liveSlug);
      } catch {
        /* history arrives via WebSocket */
      }
    }
    updatePolymarketLinks();
    void loadRecentPreviews(true);
  }

  function boundsFromWindow(w) {
    if (w?.start_ts && w?.end_ts) return { start: w.start_ts, end: w.end_ts };
    return PM.windowBounds({ slug: w?.slug }, selected.interval);
  }

  function spotOpeningForMarket(m, windowMeta) {
    return (
      m?.opening_oracle_price ??
      windowMeta?.opening_oracle_price ??
      m?.strike_price ??
      windowMeta?.strike_price ??
      null
    );
  }

  function chartOpenings(points, startTs, m, windowMeta) {
    const fromPoints =
      points?.length && startTs
        ? {
            oracle: PM.openingFromPoints(points, startTs),
            binance: PM.openingBinanceFromPoints(points, startTs),
            prob: PM.openingProbFromPoints(points, startTs),
            twap: PM.twapInitialFromPoints(points, startTs),
          }
        : {};
    return {
      // Spot open seeds Chainlink line / Target.
      opening: spotOpeningForMarket(m, windowMeta) ?? fromPoints.oracle ?? null,
      openingBinance: fromPoints.binance ?? null,
      openingProb: fromPoints.prob ?? null,
      // Chainlink TWAP at open seeds TWAP line + Initial Price.
      twapInitial:
        twapInitialPrice(m, windowMeta) ??
        fromPoints.twap ??
        null,
    };
  }

  function applyBounds(bounds, force = false, openings = null) {
    if (
      !force &&
      bounds &&
      activeBounds &&
      activeBounds.start === bounds.start &&
      activeBounds.end === bounds.end
    ) {
      return;
    }
    activeBounds = bounds;
    if (bounds && charts) {
      charts.setWindowBounds(
        bounds.start,
        bounds.end,
        openings?.opening ?? null,
        openings?.openingBinance ?? null,
        openings?.openingProb ?? null,
        openings?.twapInitial ?? null
      );
    }
    el("window-range").textContent = bounds
      ? PM.fmtWindowRange(bounds.start, bounds.end)
      : "—";
  }

  function setChartHistory(points, strikePrice, bounds, m, windowMeta) {
    const openings = chartOpenings(points, bounds?.start, m, windowMeta);
    chartOpeningPrice = openings.twapInitial ?? null;
    // Target = TWAP initial (fall back to explicit strikePrice only if TWAP missing).
    const target = openings.twapInitial ?? strikePrice ?? null;
    applyBounds(bounds, false, openings);
    if (charts) {
      charts.setHistory(
        points,
        target,
        bounds?.start,
        bounds?.end,
        openings.opening,
        openings.openingBinance,
        openings.openingProb,
        openings.twapInitial
      );
    }
    return openings;
  }

  function stopOpeningPoll() {
    if (openingPollTimer) {
      clearInterval(openingPollTimer);
      openingPollTimer = null;
    }
  }

  function scheduleOpeningPoll() {
    stopOpeningPoll();
    openingPollTimer = setInterval(async () => {
      if (!viewingLive) {
        stopOpeningPoll();
        return;
      }
      const m = marketByKey[`${selected.asset}:${selected.interval}`];
      if (!m?.slug) return;
      if (m.opening_oracle_price != null) {
        stopOpeningPoll();
        return;
      }
      try {
        const res = await fetch(`/api/windows/${encodeURIComponent(m.slug)}/ticks`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.window?.opening_oracle_price != null) {
          marketByKey[`${selected.asset}:${selected.interval}`] = {
            ...m,
            opening_oracle_price: data.window.opening_oracle_price,
            strike_price: data.window.strike_price ?? m.strike_price,
          };
          updateOracleHero(marketByKey[`${selected.asset}:${selected.interval}`], data.window);
          renderTicker();
          stopOpeningPoll();
        }
      } catch {
        /* retry */
      }
    }, 2000);
  }

  async function handleWindowRoll(market) {
    if (!market?.slug) return;
    const bounds = PM.windowBounds(market, selected.interval);
    activeBounds = null;
    chartOpeningPrice = null;
    const openings = chartOpenings([], bounds?.start, market);
    applyBounds(bounds, true, openings);
    if (charts) {
      charts.setHistory(
        [],
        openings.twapInitial ?? market.twap_initial ?? null,
        bounds?.start,
        bounds?.end,
        openings.opening,
        openings.openingBinance,
        openings.openingProb,
        openings.twapInitial ?? market.twap_initial ?? null
      );
    }
    updateOracleHero(market);
    slugRefreshAt = 0;
    await refreshSlugDropdown(true);
    selectedSlug = liveSlug;
    const select = el("slug-select");
    if (select && selectedSlug) select.value = selectedSlug;
    socket.select(selected.asset, selected.interval);
    startCountdown(market.end_ts);
    updateMetrics(market);
    if (market.opening_oracle_price == null) scheduleOpeningPoll();
    try {
      await loadSlugHistory(liveSlug);
    } catch {
      /* onHistory will backfill from RAM */
    }
    updatePolymarketLinks();
    void loadRecentPreviews(true);
  }

  async function refreshSlugDropdown(force = false) {
    const key = `${selected.asset}:${selected.interval}`;
    const now = Date.now();
    if (!force && key === slugCacheKey && now - slugRefreshAt < SLUG_CACHE_MS) {
      return;
    }

    const m = marketByKey[key];
    liveSlug = m?.slug || "";

    try {
      const res = await fetch(
        `/api/windows?asset=${selected.asset}&interval=${selected.interval}&limit=40`
      );
      const data = await res.json();
      slugWindows = Array.isArray(data) ? data : data.items || [];
      slugCacheKey = key;
      slugRefreshAt = now;
    } catch {
      if (!slugWindows.length) slugWindows = [];
    }

    const select = el("slug-select");
    const seen = new Set();
    const options = [];

    if (liveSlug) {
      options.push({ slug: liveSlug, label: `${liveSlug} (live)`, live: true });
      seen.add(liveSlug);
    }
    for (const w of slugWindows) {
      if (!w.slug || seen.has(w.slug)) continue;
      seen.add(w.slug);
      const end = new Date(w.end_ts * 1000).toLocaleString();
      const tag = w.outcome ? ` [${w.outcome}]` : "";
      options.push({ slug: w.slug, label: `${w.slug}${tag} · ${end}`, live: false });
    }

    select.innerHTML = options
      .map((o) => `<option value="${o.slug}">${o.label}</option>`)
      .join("");

    if (!options.length) {
      select.innerHTML = `<option value="">No windows</option>`;
      selectedSlug = "";
      return;
    }

    if (viewingLive && liveSlug) {
      selectedSlug = liveSlug;
    } else if (selectedSlug && seen.has(selectedSlug)) {
      // keep selection
    } else {
      selectedSlug = liveSlug || options[0].slug;
      viewingLive = selectedSlug === liveSlug;
    }
    select.value = selectedSlug;
    const input = el("slug-input");
    if (input && selectedSlug) input.value = selectedSlug;
    updateLiveButton();
  }

  function setChartOpening(points, startTs) {
    // Initial Price = TWAP initial only (never Chainlink spot).
    chartOpeningPrice = PM.twapInitialFromPoints(points, startTs);
  }

  function twapInitialPrice(m, windowMeta) {
    // Initial Price = Chainlink TWAP at window open (RTDS), not spot/Chainlink mid.
    return (
      m?.twap_initial ??
      windowMeta?.twap_initial ??
      (typeof charts?.getTwapInitial === "function" ? charts.getTwapInitial() : null) ??
      chartOpeningPrice ??
      null
    );
  }

  function updateOracleHero(m, windowMeta) {
    const opening = twapInitialPrice(m, windowMeta);
    const oracle = m?.oracle_price;
    const twap =
      m?.twap_oracle ??
      (typeof charts?.getTwap === "function" ? charts.getTwap() : null) ??
      null;
    // Difference = current TWAP − initial TWAP.
    const diff = PM.distInitialMinusTwap(opening, twap);

    el("hero-initial").textContent = opening != null ? PM.fmtUsdPrecise(opening) : "—";
    el("hero-current").textContent = oracle != null ? PM.fmtUsd(oracle) : "—";
    el("hero-twap").textContent = twap != null ? PM.fmtUsdPrecise(twap) : "—";

    const delta = PM.fmtPriceDelta(diff);
    const diffEl = el("hero-diff");
    diffEl.textContent = diff != null ? delta.text : "—";
    diffEl.className = `price-delta ${diff != null ? delta.cls : ""}`;
  }

  function startCountdown(endTs) {
    countdownEndTs = endTs || 0;
    if (countdownTimer) clearInterval(countdownTimer);
    const tick = () => {
      if (!countdownEndTs) {
        setCountdownDigits("—", "—");
        return;
      }
      const secs = Math.max(0, countdownEndTs - Math.floor(Date.now() / 1000));
      const parts = PM.fmtCountdown(secs);
      setCountdownDigits(parts.mins, parts.secs);
      if (secs <= 0) {
        slugRefreshAt = 0;
        void refreshSlugDropdown(true);
        if (countdownTimer) {
          clearInterval(countdownTimer);
          countdownTimer = null;
        }
      }
    };
    tick();
    countdownTimer = setInterval(tick, 250);
  }

  async function loadSlugHistory(slug) {
    if (!slug) return;
    const res = await fetch(`/api/windows/${encodeURIComponent(slug)}/ticks`);
    if (!res.ok) {
      throw new Error(res.status === 404 ? "Market not found" : `Failed to load (${res.status})`);
    }
    const data = await res.json();
    const bounds = boundsFromWindow(data.window);
    applyBounds(bounds);
    const points = PM.filterPointsForWindow(data.ticks, bounds?.start, bounds?.end);
    setChartOpening(points, bounds?.start);
    const firstTwap = (data.ticks || []).find((t) => t.twap_oracle != null)?.twap_oracle;
    const last = data.ticks.at(-1);
    const twapInitial = firstTwap ?? last?.twap_oracle ?? null;
    // Target line = TWAP initial (not spot strike).
    setChartHistory(points, twapInitial, bounds, { twap_initial: twapInitial }, data.window);
    updateOracleHero(
      {
        opening_oracle_price: data.window.opening_oracle_price,
        oracle_price: last?.oracle_price,
        strike_price: data.window.strike_price,
        twap_oracle: last?.twap_oracle,
        twap_initial: twapInitial,
      },
      data.window
    );
    if (!viewingLive && data.window.outcome) {
      setCountdownResolved(data.window.outcome);
    } else if (viewingLive) {
      startCountdown(data.window.end_ts);
    }
    updatePolymarketLinks(slug);
    return data;
  }

  function parseMarketSlug(raw) {
    const slug = String(raw || "").trim().toLowerCase();
    const match = slug.match(/^(btc|eth|sol)-updown-(5m|15m)-(\d+)$/);
    if (!match) return null;
    return {
      slug,
      asset: match[1].toUpperCase(),
      interval: match[2],
      startTs: Number(match[3]),
    };
  }

  function ensureSlugOption(slug, label = slug) {
    const select = el("slug-select");
    if (!select || !slug) return;
    const exists = [...select.options].some((o) => o.value === slug);
    if (!exists) {
      const opt = document.createElement("option");
      opt.value = slug;
      opt.textContent = label;
      select.appendChild(opt);
    }
    select.value = slug;
  }

  function updateLiveButton() {
    const btn = el("slug-live-btn");
    if (!btn) return;
    btn.classList.toggle("is-live", !!viewingLive);
    btn.title = viewingLive ? "Viewing live market" : "Jump back to live market";
  }

  async function goLive() {
    const btn = el("slug-live-btn");
    const m = marketByKey[`${selected.asset}:${selected.interval}`];
    const slug = m?.slug || liveSlug;
    if (!slug) {
      alert("No live market available yet");
      return;
    }

    if (btn) btn.disabled = true;
    try {
      liveSlug = slug;
      selectedSlug = slug;
      viewingLive = true;
      ensureSlugOption(slug, `${slug} (live)`);
      const input = el("slug-input");
      if (input) input.value = slug;
      updateLiveButton();
      socket.select(selected.asset, selected.interval);
      await loadSlugHistory(slug);
      updateMetrics(m);
      startCountdown(m?.end_ts);
      document.querySelectorAll(".preview-card").forEach((c) => {
        c.classList.toggle("active", c.dataset.slug === slug);
      });
    } catch (err) {
      console.error("go live", err);
      alert(err?.message || "Failed to load live market");
    } finally {
      if (btn) btn.disabled = false;
      updateLiveButton();
    }
  }

  async function loadSlugFromInput() {
    const input = el("slug-input");
    const btn = el("slug-load-btn");
    const parsed = parseMarketSlug(input?.value);
    if (!parsed) {
      alert("Enter a slug like btc-updown-5m-1784264400");
      input?.focus();
      return;
    }

    if (btn) btn.disabled = true;
    try {
      if (parsed.asset !== selected.asset || parsed.interval !== selected.interval) {
        selected = { asset: parsed.asset, interval: parsed.interval };
        el("sel-title").textContent = `${parsed.asset} Up or Down · ${parsed.interval}`;
        updateChartTitle();
        renderNav();
        void loadRecentPreviews(true);
        socket.select(parsed.asset, parsed.interval);
      }

      const m = marketByKey[`${parsed.asset}:${parsed.interval}`];
      liveSlug = m?.slug || liveSlug;
      selectedSlug = parsed.slug;
      viewingLive = selectedSlug === liveSlug && !!liveSlug;
      ensureSlugOption(parsed.slug, viewingLive ? `${parsed.slug} (live)` : parsed.slug);
      if (input) input.value = parsed.slug;
      updateLiveButton();

      await loadSlugHistory(parsed.slug);
      if (viewingLive) updateMetrics(marketByKey[`${selected.asset}:${selected.interval}`]);
      document.querySelectorAll(".preview-card").forEach((c) => {
        c.classList.toggle("active", c.dataset.slug === parsed.slug);
      });
    } catch (err) {
      console.error("slug load", err);
      alert(err?.message || "Failed to load market");
    } finally {
      if (btn) btn.disabled = false;
      updateLiveButton();
    }
  }

  function destroyPreviews() {
    previewCharts.forEach((c) => {
      try {
        c.destroy();
      } catch {
        /* ignore */
      }
    });
    previewCharts = [];
  }

  function fmtPreviewTime(endTs) {
    if (!endTs) return "—";
    return new Date(endTs * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function previewOutcomeMeta(outcome) {
    if (!outcome) return { text: "OPEN", cls: "open" };
    const up = String(outcome).toUpperCase() === "UP" || String(outcome).toUpperCase() === "YES";
    const down = String(outcome).toUpperCase() === "DOWN" || String(outcome).toUpperCase() === "NO";
    if (up) return { text: "UP", cls: "up" };
    if (down) return { text: "DOWN", cls: "down" };
    return { text: String(outcome).toUpperCase(), cls: "open" };
  }

  function updatePreviewPager() {
    const prevBtn = el("preview-prev");
    const nextBtn = el("preview-next");
    const label = el("preview-page-label");
    // Prev = older markets, Next = newer (toward live).
    if (prevBtn) prevBtn.disabled = !previewHasOlder;
    if (nextBtn) nextBtn.disabled = !previewHasNewer;
    if (label) label.textContent = `p${previewPage + 1}`;
  }

  async function loadRecentPreviews(force = false, page = null) {
    const key = `${selected.asset}:${selected.interval}`;
    const sub = el("preview-subtitle");
    if (sub) sub.textContent = `${selected.asset} · ${selected.interval}`;

    if (page != null) {
      previewPage = Math.max(0, page);
    } else if (previewKey !== key || force) {
      // Asset change / window roll / refresh → back to newest previous page.
      previewPage = 0;
    }

    if (!force && previewKey === key && previewCharts.length && page == null) {
      updatePreviewPager();
      return;
    }

    const token = ++previewLoadToken;
    previewKey = key;
    const list = el("recent-previews");
    if (!list) return;
    list.innerHTML = `<div class="preview-empty">Loading previews…</div>`;
    destroyPreviews();
    updatePreviewPager();

    try {
      const qs = new URLSearchParams({
        limit: String(PREVIEW_COUNT),
        page: String(previewPage),
        skip_current: "false",
      });
      const res = await fetch(
        `/api/previews/${encodeURIComponent(selected.asset)}/${encodeURIComponent(selected.interval)}?${qs}`
      );
      if (!res.ok) throw new Error(`preview ${res.status}`);
      const data = await res.json();
      if (token !== previewLoadToken) return;

      // Support both new `{items}` payload and legacy array responses.
      const payloads = Array.isArray(data) ? data : data.items || [];
      previewHasOlder = Array.isArray(data) ? false : !!data.has_older;
      previewHasNewer = Array.isArray(data) ? previewPage > 0 : !!data.has_newer;
      if (typeof data.page === "number") previewPage = data.page;
      updatePreviewPager();

      if (!payloads.length) {
        list.innerHTML = `<div class="preview-empty">${
          previewPage > 0 ? "No older markets" : "No previous markets"
        }</div>`;
        return;
      }

      list.innerHTML = "";
      payloads.forEach((payload, idx) => {
        const w = payload.window || {};
        const isLiveWindow = !!(w.slug && liveSlug && w.slug === liveSlug);
        const outcome = isLiveWindow
          ? { text: "LIVE", cls: "live" }
          : previewOutcomeMeta(w.outcome);
        const isActive = w.slug && w.slug === selectedSlug;
        const card = document.createElement("button");
        card.type = "button";
        card.className = `preview-card ${outcome.cls === "up" ? "win" : outcome.cls === "down" ? "lose" : ""} ${isLiveWindow ? "is-live-window" : ""} ${isActive ? "active" : ""}`;
        card.dataset.slug = w.slug || "";
        card.innerHTML = `
          <div class="preview-card-head">
            <span class="preview-card-time">${fmtPreviewTime(w.end_ts)}</span>
            <span class="preview-card-meta ${outcome.cls}">${outcome.text}</span>
          </div>
          <div class="preview-chart" id="preview-chart-${idx}"></div>
          <div class="preview-card-foot">
            <span>${payload.tick_count != null ? payload.tick_count : (payload.ticks || []).length} ticks</span>
            <span class="preview-slug">${(w.slug || "").replace(/^.*?-updown-/, "")}</span>
          </div>
        `;
        card.addEventListener("click", async () => {
          if (!w.slug) return;
          const select = el("slug-select");
          selectedSlug = w.slug;
          viewingLive = selectedSlug === liveSlug && !!liveSlug;
          updateLiveButton();
          if (select) {
            const exists = [...select.options].some((o) => o.value === w.slug);
            if (!exists) {
              const opt = document.createElement("option");
              opt.value = w.slug;
              opt.textContent = w.slug;
              select.appendChild(opt);
            }
            select.value = w.slug;
          }
          const input = el("slug-input");
          if (input) input.value = w.slug;
          document.querySelectorAll(".preview-card").forEach((c) => c.classList.remove("active"));
          card.classList.add("active");
          await loadSlugHistory(w.slug);
          if (viewingLive) updateMetrics(marketByKey[`${selected.asset}:${selected.interval}`]);
        });
        list.appendChild(card);

        const chartEl = card.querySelector(".preview-chart");
        const mini = new PM.MiniPreviewChart(chartEl);
        mini.setData(payload.ticks || []);
        previewCharts.push(mini);
      });
    } catch (err) {
      if (token !== previewLoadToken) return;
      list.innerHTML = `<div class="preview-empty">Failed to load previews</div>`;
      previewHasOlder = false;
      previewHasNewer = previewPage > 0;
      updatePreviewPager();
      console.error("preview load", err);
    }
  }

  el("preview-prev")?.addEventListener("click", () => {
    if (!previewHasOlder) return;
    void loadRecentPreviews(true, previewPage + 1);
  });
  el("preview-next")?.addEventListener("click", () => {
    if (!previewHasNewer) return;
    void loadRecentPreviews(true, previewPage - 1);
  });

  el("sidebar-toggle")?.addEventListener("click", () => {
    const layout = el("app-layout");
    const collapsed = !layout?.classList.contains("sidebar-collapsed");
    applySidebarCollapsed(collapsed);
  });
  el("sidebar-backdrop")?.addEventListener("click", () => applySidebarCollapsed(true, false));
  function syncSidebarForViewport() {
    if (isNarrow()) {
      applySidebarCollapsed(true, false);
    } else {
      let stored = false;
      try {
        stored = localStorage.getItem(SIDEBAR_KEY) === "1";
      } catch (_) {
        stored = false;
      }
      applySidebarCollapsed(stored);
    }
  }
  syncSidebarForViewport();
  if (typeof NARROW_MQ.addEventListener === "function") {
    NARROW_MQ.addEventListener("change", syncSidebarForViewport);
  } else if (typeof NARROW_MQ.addListener === "function") {
    NARROW_MQ.addListener(syncSidebarForViewport);
  }
  updateTwapLabels();

  async function bootstrapFromRest() {
    try {
      const res = await fetch("/api/markets");
      if (!res.ok) return;
      const markets = await res.json();
      if (!markets?.length) return;
      marketByKey = PM.marketMap(markets);
      renderNav();
      updateSelectedHeader();
      const m = marketByKey[`${selected.asset}:${selected.interval}`];
      if (m?.slug && viewingLive) {
        liveSlug = m.slug;
        selectedSlug = liveSlug;
        updateMetrics(m);
        startCountdown(m.end_ts);
        updatePolymarketLinks(liveSlug);
        void loadSlugHistory(liveSlug);
      }
      void loadRecentPreviews(true);
    } catch (err) {
      console.error("bootstrap", err);
    }
  }

  function updateMetrics(m) {
    if (!m) return;
    if (m.twap_initial != null && typeof charts?.setTwapInitial === "function") {
      charts.setTwapInitial(m.twap_initial);
    }
    const prob = el("prob-last");
    if (prob) prob.textContent = `${PM.fmtProb(m.yes_mid)} / ${PM.fmtProb(m.no_mid)}`;
    updateOracleHero(m);
  }

  function updateSelectedHeader() {
    const m = marketByKey[`${selected.asset}:${selected.interval}`];
    el("sel-title").textContent = `${selected.asset} Up or Down · ${selected.interval}`;
    if (m?.slug && viewingLive) {
      liveSlug = m.slug;
      if (!selectedSlug || viewingLive) selectedSlug = liveSlug;
      const bounds = PM.windowBounds(m, selected.interval);
      applyBounds(bounds);
      startCountdown(m.end_ts);
      updateMetrics(m);
    } else if (viewingLive) {
      clearCountdown();
      applyBounds(null);
    }
    refreshSlugDropdown(false);
    updateChartTitle();
    updatePolymarketLinks();
    void loadRecentPreviews(false);
  }

  el("slug-select").addEventListener("change", async (e) => {
    selectedSlug = e.target.value;
    viewingLive = selectedSlug === liveSlug && !!liveSlug;
    updateLiveButton();
    const input = el("slug-input");
    if (input) input.value = selectedSlug || "";
    await loadSlugHistory(selectedSlug);
    if (viewingLive) {
      const m = marketByKey[`${selected.asset}:${selected.interval}`];
      updateMetrics(m);
    }
  });

  el("slug-load-btn")?.addEventListener("click", () => {
    void loadSlugFromInput();
  });
  el("slug-live-btn")?.addEventListener("click", () => {
    void goLive();
  });
  el("slug-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void loadSlugFromInput();
    }
  });
  ["poly-link-oracle", "poly-link-prob"].forEach((id) => {
    el(id)?.addEventListener("click", openDisplayedPolymarket);
  });
  updateLiveButton();
  updatePolymarketLinks();

  const socket = new PM.LiveSocket("/ws/live", {
    onStatus: setWsStatus,
    onOracle: (msg) => {
      scheduleRenderTicker(msg.oracle_prices);
      if (!viewingLive || selectedSlug !== liveSlug || !charts) return;
      const liveTwap = PM.pickTwapPrice(msg, selected.asset, selected.interval);
      charts.updateFeedPrices({
        ts_ms: msg.ts_ms,
        oracle_price: msg.oracle_prices?.[selected.asset],
        binance_price: msg.binance_prices?.[selected.asset],
        twap_oracle: liveTwap,
      });
      const m = marketByKey[`${selected.asset}:${selected.interval}`];
      if (m) {
        if (msg.oracle_prices?.[selected.asset] != null) {
          m.oracle_price = msg.oracle_prices[selected.asset];
        }
        if (liveTwap != null) {
          m.twap_oracle = liveTwap;
        }
        // Never seed Initial Price from live TWAP — only true window-open TWAP.
        if (msg.twap_initial != null && m.twap_initial == null) {
          m.twap_initial = msg.twap_initial;
        }
      }
      updateOracleHero(m);
    },
    onSnapshot: (msg) => {
      marketByKey = PM.marketMap(msg.markets);
      scheduleRenderTicker(msg.oracle_prices);
      renderNav();
      updateSelectedHeader();
    },
    onHistory: async (msg) => {
      if (msg.asset !== selected.asset || msg.interval !== selected.interval) return;
      if (!viewingLive) return;
      try {
        const m = marketByKey[`${selected.asset}:${selected.interval}`];
        const bounds =
          msg.start_ts && msg.end_ts
            ? { start: msg.start_ts, end: msg.end_ts }
            : marketBounds(m, selected.interval);

        let points = PM.filterPointsForWindow(msg.points, bounds?.start, bounds?.end);
        if (!points.length && msg.slug) {
          if (charts?.hasLiveData()) return;
          await loadSlugHistory(msg.slug);
          return;
        }

        if (charts?.hasLiveData()) {
          if (bounds) {
            applyBounds(bounds, false, chartOpenings(points, bounds?.start, m, {
              strike_price: msg.strike_price,
              opening_oracle_price: msg.opening_oracle_price,
            }));
          }
          updateOracleHero(m, {
            strike_price: msg.strike_price,
            opening_oracle_price: msg.opening_oracle_price,
          });
          if (!msg.opening_oracle_price && m?.opening_oracle_price == null) scheduleOpeningPoll();
          else stopOpeningPoll();
          return;
        }

        setChartOpening(points, bounds?.start);
        setChartHistory(points, m?.twap_initial ?? null, bounds, m, {
          strike_price: msg.strike_price,
          opening_oracle_price: msg.opening_oracle_price,
          twap_initial: m?.twap_initial,
        });
        updateOracleHero(m, {
          strike_price: msg.strike_price,
          opening_oracle_price: msg.opening_oracle_price,
          twap_initial: m?.twap_initial,
        });
        if (!msg.opening_oracle_price && m?.opening_oracle_price == null) scheduleOpeningPoll();
        else stopOpeningPoll();
      } catch (err) {
        console.error("history handler", err);
      }
    },
    onTick: (msg) => {
      const key = `${msg.asset}:${msg.interval}`;
      if (msg.market) marketByKey[key] = msg.market;
      if (msg.asset === selected.asset && msg.interval === selected.interval) {
        if (msg.market?.slug) {
          const slugChanged = liveSlug && liveSlug !== msg.market.slug;
          liveSlug = msg.market.slug;
          if (viewingLive) selectedSlug = liveSlug;
          if (slugChanged) {
            void handleWindowRoll(msg.market);
          } else {
            updatePolymarketLinks(liveSlug);
          }
        }
        if (viewingLive && selectedSlug === liveSlug) {
          const bounds = marketBounds(msg.market, selected.interval);
          if (bounds && (!activeBounds || activeBounds.start !== bounds.start)) {
            applyBounds(bounds, false);
          }
          if (msg.point && charts) {
            // YES/NO immediately; also refresh spot/TWAP from the same tick if present.
            charts.appendProbPoint(msg.point);
            if (
              msg.point.oracle_price != null ||
              msg.point.binance_price != null ||
              msg.point.twap_oracle != null
            ) {
              charts.updateFeedPrices({
                ts_ms: msg.point.ts_ms,
                oracle_price: msg.point.oracle_price,
                binance_price: msg.point.binance_price,
                twap_oracle: msg.point.twap_oracle,
              });
            }
          }
          scheduleUpdateMetrics(msg.market);
          if (msg.market?.end_ts) countdownEndTs = msg.market.end_ts;
          if (msg.point) {
            el("prob-last").textContent = `${PM.fmtProb(msg.point.yes_mid)} / ${PM.fmtProb(msg.point.no_mid)}`;
          }
        }
      }
      scheduleRenderTicker();
      scheduleNavBadges();
    },
  });

  socket._selected = selected;
  // Connect + REST first so the UI works even if chart init fails.
  void bootstrapFromRest();
  socket.connect();
  setInterval(() => {
    if (!wsIsLive) void bootstrapFromRest();
  }, 3000);

  try {
    if (typeof LightweightCharts === "undefined") {
      throw new Error("LightweightCharts failed to load");
    }
    charts = new PM.DualCharts(el("chart-oracle"), el("chart-prob"));
    updateTwapLabels();
  } catch (err) {
    console.error("chart init failed", err);
    charts = null;
    const badge = el("ws-status");
    if (badge && !wsIsLive) {
      badge.textContent = "Charts offline";
      badge.className = "ws-badge ws-off";
    }
  }
})();
