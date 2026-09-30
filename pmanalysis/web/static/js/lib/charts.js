window.PM = window.PM || {};

PM.DualCharts = class {
  constructor(oracleEl, probEl) {
    const LINE_CURVED = LightweightCharts.LineType?.Curved ?? 2;
    const crosshairOpts = {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        width: 1,
        color: "rgba(232, 238, 246, 0.22)",
        style: LightweightCharts.LineStyle.Dashed,
        labelVisible: true,
      },
      horzLine: {
        visible: false,
        labelVisible: false,
      },
    };

    this._oracleEl = oracleEl;
    this._probEl = probEl;

    const timeScaleOpts = {
      borderColor: "#1e2a3a",
      timeVisible: true,
      secondsVisible: true,
      shiftVisibleRangeOnNewBar: false,
      allowShiftVisibleRangeOnWhitespaceReplacement: false,
      lockVisibleTimeRangeOnResize: true,
      rightOffset: 0,
      // Allow click-drag pan; do not pin the left edge.
      fixLeftEdge: false,
      fixRightEdge: false,
      barSpacing: 0.5,
      minBarSpacing: 0.15,
      maxBarSpacing: 12,
      tickMarkFormatter: (time) => this._fmtTickLabel(time),
    };

    const interactionOpts = {
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
    };

    const baseOpts = {
      layout: {
        background: { color: "#121a24" },
        textColor: "#8b97a8",
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#1e2a3a" },
        horzLines: { color: "#1e2a3a" },
      },
      rightPriceScale: { borderColor: "#1e2a3a", minimumWidth: 58 },
      timeScale: timeScaleOpts,
      crosshair: crosshairOpts,
      localization: {
        timeFormatter: (time) => this._fmtElapsed(time, true),
      },
      ...interactionOpts,
    };

    this.oracleChart = LightweightCharts.createChart(oracleEl, {
      ...baseOpts,
      height: oracleEl.clientHeight || 280,
    });
    this.probChart = LightweightCharts.createChart(probEl, {
      ...baseOpts,
      height: probEl.clientHeight || 220,
      rightPriceScale: {
        borderColor: "#1e2a3a",
        // YES/NO asks are always 0–100¢ — pin the axis to that range.
        scaleMargins: { top: 0.02, bottom: 0.02 },
        autoScale: true,
      },
    });

    const curvedLine = {
      lineType: LINE_CURVED,
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
    };

    const chainlinkColor = "#e8b923";
    const binanceColor = "#3b82f6";
    const twapColor = "#38bdf8";
    const yesColor = "#22c55e";
    const noColor = "#ef4444";
    const fixedProbScale = {
      priceFormat: { type: "price", precision: 0, minMove: 1 },
      autoscaleInfoProvider: () => ({
        priceRange: { minValue: 0, maxValue: 100 },
      }),
    };

    this.oracleSeries = this.oracleChart.addLineSeries({
      ...curvedLine,
      color: chainlinkColor,
      crosshairMarkerBorderColor: chainlinkColor,
      crosshairMarkerBackgroundColor: chainlinkColor,
      title: "Chainlink",
    });
    this.binanceSeries = this.oracleChart.addLineSeries({
      ...curvedLine,
      color: binanceColor,
      crosshairMarkerBorderColor: binanceColor,
      crosshairMarkerBackgroundColor: binanceColor,
      title: "Binance",
    });
    this.twapSeries = this.oracleChart.addLineSeries({
      ...curvedLine,
      lineWidth: 2,
      color: twapColor,
      crosshairMarkerBorderColor: twapColor,
      crosshairMarkerBackgroundColor: twapColor,
      title: "TWAP 60s",
    });
    this._twapLabel = "TWAP 60s";
    this.yesSeries = this.probChart.addLineSeries({
      ...curvedLine,
      ...fixedProbScale,
      color: yesColor,
      crosshairMarkerBorderColor: yesColor,
      crosshairMarkerBackgroundColor: yesColor,
      title: "YES ask",
    });
    this.noSeries = this.probChart.addLineSeries({
      ...curvedLine,
      ...fixedProbScale,
      color: noColor,
      crosshairMarkerBorderColor: noColor,
      crosshairMarkerBackgroundColor: noColor,
      title: "NO ask",
    });

    this._strikeLine = null;
    this._currentLine = null;
    this._startTs = 0;
    this._endTs = 0;
    this._openingPrice = null;
    this._openingBinance = null;
    this._openingProb = null;
    this._lastChartTime = -1; // -1 = no bars yet (0 is a valid 0:00 slot)
    // Fine time slots so each websocket tick plots at its real elapsed time.
    // Gaps are forward-filled so LWC logical spacing stays linear with wall time.
    this._bucketMs = 100; // 100ms resolution
    this._timeScale = 10; // slots per second (1000 / bucketMs)
    this._tickLabelSec = 10;
    this._carry = { oracle: null, binance: null, twap: null, yes: null, no: null };
    this._hasStart = { oracle: false, binance: false, twap: false, yes: false, no: false };
    this._twapInitial = null;
    this._syncingRange = false;
    this._windowJustChanged = false;
    this._lockedView = null;
    this._restorePending = 0;
    this._priceScaleManual = { oracle: false, prob: false };
    this._livePaintLoopActive = false;
    this._livePaintLoopRaf = 0;
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(oracleEl);
    this._resizeObserver.observe(probEl);

    this._oracleTooltip = this._createTooltip(oracleEl);
    this._probTooltip = this._createTooltip(probEl);
    this._syncingCrosshair = false;
    this._oracleTooltipEntries = [
      { series: this.oracleSeries, label: "Chainlink", color: chainlinkColor, format: (v) => PM.fmtUsdPrecise(v) },
      { series: this.binanceSeries, label: "Binance", color: binanceColor, format: (v) => PM.fmtUsdPrecise(v) },
      { series: this.twapSeries, label: "TWAP 60s", color: twapColor, format: (v) => PM.fmtUsdPrecise(v) },
    ];
    this._twapTooltipEntry = this._oracleTooltipEntries[2];
    this._probTooltipEntries = [
      { series: this.yesSeries, label: "YES ask", color: yesColor, format: (v) => `${Number(v).toFixed(1)}¢` },
      { series: this.noSeries, label: "NO ask", color: noColor, format: (v) => `${Number(v).toFixed(1)}¢` },
    ];
    this._bindCrosshairSync();

    this._bindTimeScaleSync();
    this._bindUserViewTracking();
    this._bindPriceScaleInteraction(this.oracleChart, oracleEl, "oracle");
    this._bindPriceScaleInteraction(this.probChart, probEl, "prob");
    this._applyFixedProbScale();
    this._syncCompactMode();
  }

  _isCompact() {
    return (this._oracleEl?.clientWidth || 800) < 540;
  }

  _syncCompactMode() {
    const compact = this._isCompact();
    if (this._compact === compact) return;
    this._compact = compact;
    const chartOpts = {
      layout: {
        fontSize: compact ? 10 : 11,
        attributionLogo: false,
      },
      rightPriceScale: {
        borderColor: "#1e2a3a",
        minimumWidth: compact ? 50 : 58,
      },
    };
    try {
      this.oracleChart.applyOptions(chartOpts);
      this.probChart.applyOptions({
        ...chartOpts,
        rightPriceScale: {
          ...chartOpts.rightPriceScale,
          scaleMargins: { top: 0.02, bottom: 0.02 },
        },
      });
      this.oracleSeries.applyOptions({ title: compact ? "" : "Chainlink" });
      this.binanceSeries.applyOptions({
        title: compact ? "" : "Binance",
        lastValueVisible: !compact,
      });
      this.twapSeries.applyOptions({ title: compact ? "" : this._twapLabel });
      this.yesSeries.applyOptions({ title: compact ? "" : "YES ask" });
      this.noSeries.applyOptions({ title: compact ? "" : "NO ask" });
      if (this._strikeLine) {
        this._strikeLine.applyOptions({ title: compact ? "" : "TWAP Target" });
      }
    } catch (_) {
      /* ignore */
    }
  }

  _resetPriceScales() {
    this._priceScaleManual = { oracle: false, prob: false };
    this.oracleChart.priceScale("right").applyOptions({ autoScale: true });
    this._applyFixedProbScale();
  }

  _applyFixedProbScale() {
    this.probChart.priceScale("right").applyOptions({
      autoScale: true,
      scaleMargins: { top: 0.02, bottom: 0.02 },
    });
    // Keep visible range pinned even if a user drag disabled autoScale.
    try {
      this.probChart.priceScale("right").setVisibleRange({ from: 0, to: 100 });
    } catch {
      /* range applies once series have data */
    }
  }

  _bindPriceScaleInteraction(chart, el, key) {
    const priceScale = () => chart.priceScale("right");
    const isPriceAxisEvent = (e) => {
      const axisWidth = priceScale().width();
      if (!axisWidth) return false;
      const rect = el.getBoundingClientRect();
      return e.clientX >= rect.right - axisWidth;
    };

    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || !isPriceAxisEvent(e)) return;
      // Prob chart stays locked to 0–100; only oracle scale is freely draggable.
      if (key === "prob") return;
      priceScale().applyOptions({ autoScale: false });
      this._priceScaleManual[key] = true;
    });

    el.addEventListener("dblclick", (e) => {
      if (!isPriceAxisEvent(e)) return;
      if (key === "prob") {
        this._applyFixedProbScale();
        this._priceScaleManual.prob = false;
        return;
      }
      priceScale().applyOptions({ autoScale: true });
      this._priceScaleManual[key] = false;
    });
  }

  hasLiveData() {
    return this._lastChartTime >= 0;
  }

  _cloneLogicalRange(range) {
    if (!range) return null;
    return { from: range.from, to: range.to };
  }

  _saveTimeScaleView(chart = this.oracleChart) {
    const ts = chart.timeScale();
    const logicalRange = ts.getVisibleLogicalRange();
    return {
      range: ts.getVisibleRange(),
      logicalRange: this._cloneLogicalRange(logicalRange),
      barSpacing: ts.options().barSpacing,
    };
  }

  _rememberViewFromChart(chart = this.oracleChart) {
    this._rememberView(this._saveTimeScaleView(chart));
  }

  _rememberView(view) {
    if (!view?.logicalRange && !view?.range) return;
    this._lockedView = {
      range: view.range ? { from: view.range.from, to: view.range.to } : null,
      logicalRange: this._cloneLogicalRange(view.logicalRange),
      barSpacing: view.barSpacing,
    };
  }

  _applyTimeScaleView(view) {
    if (!view?.logicalRange && !view?.range) return;
    const spacingOpts =
      view.barSpacing != null ? { barSpacing: view.barSpacing } : null;
    if (spacingOpts) {
      this.oracleChart.timeScale().applyOptions(spacingOpts);
      this.probChart.timeScale().applyOptions(spacingOpts);
    }
    if (view.logicalRange) {
      this.oracleChart.timeScale().setVisibleLogicalRange(view.logicalRange);
      this.probChart.timeScale().setVisibleLogicalRange(view.logicalRange);
    } else if (view.range) {
      this.oracleChart.timeScale().setVisibleRange(view.range);
      this.probChart.timeScale().setVisibleRange(view.range);
    }
  }

  _scheduleRestoreView(view) {
    if (!view?.logicalRange && !view?.range) {
      this._syncingRange = false;
      return;
    }
    const ticket = ++this._restorePending;
    const restore = () => {
      if (ticket !== this._restorePending) return;
      this._syncingRange = true;
      try {
        this._applyTimeScaleView(view);
      } catch (_) {
        /* ignore */
      }
      this._syncingRange = false;
    };
    requestAnimationFrame(() => requestAnimationFrame(restore));
  }

  _preserveVisibleRange(fn) {
    const view = this._lockedView || this._saveTimeScaleView();
    if (view?.logicalRange || view?.range) {
      this._rememberView(view);
    }
    this._syncingRange = true;
    try {
      fn();
    } catch (err) {
      this._syncingRange = false;
      throw err;
    }
    this._scheduleRestoreView(this._lockedView || view);
  }

  _bindTimeScaleSync() {
    const sync = (source, target) => {
      source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || this._syncingRange) return;
        this._rememberViewFromChart(source);
        this._syncingRange = true;
        try {
          const spacing = source.timeScale().options().barSpacing;
          if (spacing != null) {
            target.timeScale().applyOptions({ barSpacing: spacing });
          }
          target.timeScale().setVisibleLogicalRange(range);
        } catch (_) {
          /* ignore */
        }
        this._syncingRange = false;
      });
    };
    sync(this.oracleChart, this.probChart);
    sync(this.probChart, this.oracleChart);
  }

  _bindUserViewTracking() {
    const track = (chart) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (this._syncingRange || !range) return;
        this._rememberViewFromChart(chart);
      });
    };
    track(this.oracleChart);
    track(this.probChart);
  }

  _createTooltip(chartEl) {
    const panel = chartEl.closest(".chart-panel") || chartEl.parentElement;
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.hidden = true;
    panel.appendChild(tip);
    return tip;
  }

  _renderTooltip(chartEl, tooltipEl, seriesEntries, param) {
    if (
      !param.point ||
      param.time == null ||
      param.point.x < 0 ||
      param.point.y < 0
    ) {
      tooltipEl.hidden = true;
      return false;
    }

    const rows = [];
    for (const entry of seriesEntries) {
      const data = param.seriesData.get(entry.series);
      if (data?.value == null) continue;
      rows.push(
        `<div class="chart-tooltip-row">` +
          `<span class="chart-tooltip-dot" style="background:${entry.color}"></span>` +
          `<span>${entry.label}</span>` +
          `<strong>${entry.format(data.value)}</strong>` +
          `</div>`
      );
    }

    if (!rows.length) {
      tooltipEl.hidden = true;
      return false;
    }

    tooltipEl.innerHTML =
      `<div class="chart-tooltip-time">${this._fmtElapsed(param.time, true)}</div>` + rows.join("");
    tooltipEl.hidden = false;

    const panel = chartEl.closest(".chart-panel") || chartEl.parentElement;
    const offsetLeft = chartEl.offsetLeft;
    const offsetTop = chartEl.offsetTop;
    const maxX = panel.clientWidth - tooltipEl.offsetWidth - 8;
    const maxY = offsetTop + chartEl.clientHeight - tooltipEl.offsetHeight - 8;
    const x = Math.max(8, Math.min(offsetLeft + param.point.x + 14, maxX));
    const y = Math.max(
      offsetTop + 8,
      Math.min(offsetTop + param.point.y - tooltipEl.offsetHeight - 14, maxY)
    );
    tooltipEl.style.left = `${x}px`;
    tooltipEl.style.top = `${y}px`;
    return true;
  }

  _hideCrosshairTooltips() {
    this._oracleTooltip.hidden = true;
    this._probTooltip.hidden = true;
  }

  _clearSyncedCrosshair() {
    this._syncingCrosshair = true;
    try {
      this.oracleChart.clearCrosshairPosition();
      this.probChart.clearCrosshairPosition();
    } catch (_) {
      /* ignore */
    }
    this._syncingCrosshair = false;
    this._hideCrosshairTooltips();
  }

  _bindCrosshairSync() {
    const onMove = (sourceIsOracle) => (param) => {
      if (this._syncingCrosshair) {
        if (sourceIsOracle) {
          this._renderTooltip(
            this._oracleEl,
            this._oracleTooltip,
            this._oracleTooltipEntries,
            param
          );
        } else {
          this._renderTooltip(
            this._probEl,
            this._probTooltip,
            this._probTooltipEntries,
            param
          );
        }
        return;
      }

      if (
        !param.point ||
        param.time == null ||
        param.point.x < 0 ||
        param.point.y < 0
      ) {
        this._clearSyncedCrosshair();
        return;
      }

      const time = param.time;
      this._syncingCrosshair = true;
      try {
        if (sourceIsOracle) {
          this._renderTooltip(
            this._oracleEl,
            this._oracleTooltip,
            this._oracleTooltipEntries,
            param
          );
          this.probChart.setCrosshairPosition(
            this._carry.yes ?? 50,
            time,
            this.yesSeries
          );
        } else {
          this._renderTooltip(
            this._probEl,
            this._probTooltip,
            this._probTooltipEntries,
            param
          );
          this.oracleChart.setCrosshairPosition(
            this._carry.oracle ?? this._carry.binance ?? 0,
            time,
            this.oracleSeries
          );
        }
      } catch (_) {
        /* ignore */
      }
      this._syncingCrosshair = false;
    };

    this.oracleChart.subscribeCrosshairMove(onMove(true));
    this.probChart.subscribeCrosshairMove(onMove(false));
  }

  resize() {
    const view = this._lockedView || this._saveTimeScaleView();
    this._compact = undefined;
    this._syncCompactMode();
    if (this._oracleEl) {
      this.oracleChart.applyOptions({
        width: this._oracleEl.clientWidth,
        height: this._oracleEl.clientHeight,
      });
    }
    if (this._probEl) {
      this.probChart.applyOptions({
        width: this._probEl.clientWidth,
        height: this._probEl.clientHeight,
      });
    }
    if (view?.logicalRange || view?.range) {
      this._syncingRange = true;
      try {
        this._applyTimeScaleView(view);
      } catch (_) {
        /* ignore */
      }
      this._syncingRange = false;
    }
  }

  _chartStartTime() {
    // Window open is always chart time 0 → label 0:00.
    return 0;
  }

  _chartEndTime() {
    if (!this._startTs || !this._endTs) return this._timeScale;
    return Math.max(this._timeScale, (this._endTs - this._startTs) * this._timeScale);
  }

  _elapsedSec(chartTime) {
    if (!this._startTs) return 0;
    return Math.max(0, chartTime / this._timeScale);
  }

  _barSpacingForWindow() {
    const width = this._oracleEl?.clientWidth || 800;
    const duration = Math.max(1, this._chartEndTime() - this._chartStartTime());
    return Math.max(0.15, Math.min(6, (width - 48) / duration));
  }

  /** Map wall-clock ms → fine elapsed slots from window open (follows WS timestamps). */
  _toChartTime(tsMs) {
    if (!this._startTs || !this._endTs) return Math.floor(tsMs / this._bucketMs);
    const startMs = this._startTs * 1000;
    const endMs = this._endTs * 1000;
    if (tsMs < startMs - 500 || tsMs > endMs + 500) return null;
    const slot = Math.floor((tsMs - startMs) / this._bucketMs);
    const maxSlot = this._chartEndTime();
    return Math.min(Math.max(0, slot), maxSlot);
  }

  /** Keep the full market window on screen with evenly spaced time. */
  _fitFullWindow() {
    if (!this._startTs || !this._endTs) return;
    const from = this._chartStartTime();
    const to = this._chartEndTime();
    const view = {
      range: { from, to },
      logicalRange: null,
      barSpacing: this._barSpacingForWindow(),
    };
    this._rememberView(view);
    this._syncingRange = true;
    try {
      this._applyTimeScaleView(view);
    } catch (_) {
      /* ignore */
    }
    this._syncingRange = false;
  }

  _liveChartTime(tsMs) {
    return this._toChartTime(tsMs ?? Date.now());
  }

  _stopLivePaintLoop() {
    this._livePaintLoopActive = false;
    if (this._livePaintLoopRaf) {
      cancelAnimationFrame(this._livePaintLoopRaf);
      this._livePaintLoopRaf = 0;
    }
  }

  _startLivePaintLoop() {
    if (this._livePaintLoopActive || !this._startTs) return;
    this._livePaintLoopActive = true;
    const loop = () => {
      if (!this._livePaintLoopActive || !this._startTs) {
        this._livePaintLoopRaf = 0;
        return;
      }
      const hasOracle =
        this._carry.oracle != null ||
        this._carry.binance != null ||
        this._carry.twap != null;
      const hasProb = this._carry.yes != null && this._carry.no != null;
      if (hasOracle || hasProb) {
        this._flushLivePaint(true);
      }
      this._livePaintLoopRaf = requestAnimationFrame(loop);
    };
    this._livePaintLoopRaf = requestAnimationFrame(loop);
  }

  _capLastChartTimeToNow() {
    const nowSlot = this._liveChartTime(Date.now());
    if (nowSlot != null && this._lastChartTime >= 0 && this._lastChartTime > nowSlot) {
      this._lastChartTime = nowSlot;
    }
  }

  _paintSeriesAt(chartTime, includeProb = true) {
    if (this._carry.oracle != null) {
      this.oracleSeries.update({ time: chartTime, value: this._carry.oracle });
      this._setCurrentLine(this._carry.oracle);
    }
    if (this._carry.binance != null) {
      this.binanceSeries.update({ time: chartTime, value: this._carry.binance });
    }
    if (this._carry.twap != null) {
      this.twapSeries.update({ time: chartTime, value: this._carry.twap });
    }
    if (includeProb && this._carry.yes != null && this._carry.no != null) {
      this.yesSeries.update({ time: chartTime, value: this._carry.yes });
      this.noSeries.update({ time: chartTime, value: this._carry.no });
    }
  }

  _flushLivePaint(includeProb = true, tsMs = null) {
    let chartTime = this._liveChartTime(tsMs ?? Date.now());
    if (chartTime == null) return;

    // History can land slightly ahead of the client clock; never clamp live time into the future.
    if (this._lastChartTime >= 0 && chartTime < this._lastChartTime) {
      chartTime = this._lastChartTime;
    }

    this._seedStartFromCarry();

    // Forward-fill skipped slots so the axis stays linear with wall time.
    if (this._lastChartTime >= 0 && chartTime > this._lastChartTime + 1) {
      for (let t = this._lastChartTime + 1; t < chartTime; t++) {
        this._paintSeriesAt(t, includeProb);
      }
    }

    this._paintSeriesAt(chartTime, includeProb);
    if (this._lastChartTime < 0 || chartTime >= this._lastChartTime) {
      this._lastChartTime = chartTime;
    }
  }

  _requestLiveUpdate(includeProb = true, tsMs = null) {
    // Paint immediately at the websocket timestamp, then RAF keeps time advancing.
    this._flushLivePaint(includeProb, tsMs);
    this._startLivePaintLoop();
  }

  updateFeedPrices({ ts_ms, oracle_price, binance_price, twap_oracle } = {}) {
    if (!this._startTs) return;

    if (oracle_price != null && oracle_price > 0) {
      this._carry.oracle = oracle_price;
    }
    if (binance_price != null && binance_price > 0) this._carry.binance = binance_price;
    // Chainlink TWAP from Polymarket RTDS — do not derive from spot.
    if (twap_oracle != null && twap_oracle > 0) {
      this._carry.twap = twap_oracle;
    }
    // Do not invent Initial Price from live TWAP — only setWindowBounds/setHistory may set it.
    if (!this._carry.oracle && !this._carry.binance && this._carry.twap == null) return;

    this._requestLiveUpdate(false, ts_ms);
  }

  /** YES/NO only — avoids fighting the oracle fast-path on busy markets (e.g. BTC 5m). */
  appendProbPoint(point) {
    if (!point) return;

    if (point.yes_ask > 0 || point.no_ask > 0 || point.yes_mid > 0 || point.no_mid > 0) {
      const yes = point.yes_ask > 0 ? point.yes_ask : point.yes_mid * 100;
      const no = point.no_ask > 0 ? point.no_ask : point.no_mid * 100;
      this._carry.yes = yes;
      this._carry.no = no;
    }
    if (point.twap_oracle != null && point.twap_oracle > 0) {
      this._carry.twap = point.twap_oracle;
    }
    if (this._carry.yes == null || this._carry.no == null) return;
    this._requestLiveUpdate(true, point.ts_ms);
  }

  appendPoint(point) {
    if (!point) return;

    if (point.oracle_price != null && point.oracle_price > 0) {
      this._carry.oracle = point.oracle_price;
    }
    if (point.binance_price != null && point.binance_price > 0) {
      this._carry.binance = point.binance_price;
    }
    if (point.twap_oracle != null && point.twap_oracle > 0) {
      this._carry.twap = point.twap_oracle;
    }
    if (point.yes_ask > 0 || point.no_ask > 0 || point.yes_mid > 0 || point.no_mid > 0) {
      const yes = point.yes_ask > 0 ? point.yes_ask : point.yes_mid * 100;
      const no = point.no_ask > 0 ? point.no_ask : point.no_mid * 100;
      this._carry.yes = yes;
      this._carry.no = no;
    }

    if (!this._carry.oracle && !this._carry.binance && this._carry.yes == null) return;
    this._requestLiveUpdate(true, point.ts_ms);
  }

  getTwap() {
    return this._carry.twap;
  }

  /** Window-open Chainlink TWAP (Initial Price). */
  getTwapInitial() {
    return this._twapInitial;
  }

  setTwapLabel(label) {
    if (!label || label === this._twapLabel) return;
    this._twapLabel = label;
    try {
      this.twapSeries.applyOptions({ title: this._isCompact() ? "" : label });
    } catch (_) {
      /* ignore */
    }
    if (this._twapTooltipEntry) this._twapTooltipEntry.label = label;
  }

  /** Set Initial Price / TWAP target — only from true window-open TWAP. */
  setTwapInitial(price) {
    if (price == null || !(price > 0)) return;
    if (this._twapInitial != null) return;
    this._twapInitial = price;
    this._setStrike(price);
    this._ensureStartUpdate(this.twapSeries, "twap", price);
  }

  /** Snap to fixed buckets aligned to window start. */
  _fmtElapsed(chartTime, snapBucket = false) {
    if (!this._startTs) return "";
    let elapsed = this._elapsedSec(chartTime);
    if (snapBucket) elapsed = Math.round(elapsed);
    const m = Math.floor(elapsed / 60);
    const s = Math.floor(elapsed % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  /** X-axis labels every 10 seconds (0:00, 0:10, 0:20, …). */
  _fmtTickLabel(chartTime) {
    if (!this._startTs) return null;
    // Only label whole-second boundaries that land on the 10s cadence.
    if (chartTime % this._timeScale !== 0) return null;
    const elapsed = Math.round(this._elapsedSec(chartTime));
    if (elapsed % this._tickLabelSec !== 0) return null;
    return this._fmtElapsed(chartTime);
  }

  _isValidPoint(p) {
    const hasOracle = p.oracle_price != null && p.oracle_price > 0;
    const hasBinance = p.binance_price != null && p.binance_price > 0;
    const hasTwap = p.twap_oracle != null && p.twap_oracle > 0;
    const hasProb = this._probYesValue(p) != null || this._probNoValue(p) != null;
    return hasOracle || hasBinance || hasTwap || hasProb;
  }

  _resetCarry() {
    this._carry = { oracle: null, binance: null, twap: null, yes: null, no: null };
    this._hasStart = { oracle: false, binance: false, twap: false, yes: false, no: false };
    this._twapInitial = null;
  }

  _clearSeries() {
    this.oracleSeries.setData([]);
    this.binanceSeries.setData([]);
    this.twapSeries.setData([]);
    this.yesSeries.setData([]);
    this.noSeries.setData([]);
    this._setCurrentLine(null);
    this._lastChartTime = -1;
    this._resetCarry();
  }

  setWindowBounds(
    startTs,
    endTs,
    openingPrice = null,
    openingBinance = null,
    openingProb = null,
    twapInitial = null
  ) {
    const windowChanged = this._startTs !== startTs || this._endTs !== endTs;
    this._startTs = startTs || 0;
    this._endTs = endTs || 0;
    if (openingPrice != null) this._openingPrice = openingPrice;
    if (openingBinance != null) this._openingBinance = openingBinance;
    if (openingProb != null) this._openingProb = openingProb;
    if (twapInitial != null) this._twapInitial = twapInitial;
    if (windowChanged) {
      this._clearSeries();
      this._windowJustChanged = true;
      this._lockedView = null;
      this._restorePending++;
      this._stopLivePaintLoop();
      this._resetPriceScales();
      if (twapInitial != null) this._twapInitial = twapInitial;
    }
    this._applyTimeScaleLayout();
  }

  _applyTimeScaleLayout() {
    if (!this._startTs || !this._endTs) return;
    const scaleOpts = {
      fixLeftEdge: false,
      fixRightEdge: false,
      shiftVisibleRangeOnNewBar: false,
      allowShiftVisibleRangeOnWhitespaceReplacement: false,
      lockVisibleTimeRangeOnResize: true,
      rightOffset: 0,
      secondsVisible: true,
      barSpacing: this._barSpacingForWindow(),
      minBarSpacing: 0.15,
      maxBarSpacing: 12,
      tickMarkFormatter: (time) => this._fmtTickLabel(time),
    };
    const apply = () => {
      this.oracleChart.timeScale().applyOptions(scaleOpts);
      this.probChart.timeScale().applyOptions(scaleOpts);
    };
    if (this._windowJustChanged) {
      apply();
    } else {
      this._preserveVisibleRange(apply);
    }
  }

  _probYesValue(p) {
    if (p.yes_ask > 0) return p.yes_ask;
    if (p.yes_mid > 0) return p.yes_mid * 100;
    return null;
  }

  _probNoValue(p) {
    if (p.no_ask > 0) return p.no_ask;
    if (p.no_mid > 0) return p.no_mid * 100;
    return null;
  }

  _mergePointIntoRow(row, p) {
    if (p.oracle_price != null && p.oracle_price > 0) row.oracle = p.oracle_price;
    if (p.binance_price != null && p.binance_price > 0) row.binance = p.binance_price;
    if (p.twap_oracle != null && p.twap_oracle > 0) row.twap = p.twap_oracle;
    const yes = this._probYesValue(p);
    const no = this._probNoValue(p);
    if (yes != null) row.yes = yes;
    if (no != null) row.no = no;
  }

  _prependStart(series, startValue, key) {
    const chartStart = this._chartStartTime();
    if (!this._startTs) return series;
    if (!series.length) {
      if (startValue == null) return series;
      this._hasStart[key] = true;
      return [{ time: chartStart, value: startValue }];
    }
    if (series[0].time === chartStart) {
      this._hasStart[key] = true;
      return series;
    }
    const seed = startValue ?? series[0].value;
    if (seed == null) return series;
    this._hasStart[key] = true;
    return [{ time: chartStart, value: seed }, ...series];
  }

  _buildSeries(points) {
    const timeline = new Map();
    const chartStart = this._chartStartTime();

    if (this._startTs) {
      timeline.set(chartStart, {});
    }

    for (const p of points) {
      if (!this._isValidPoint(p)) continue;
      const t = this._toChartTime(p.ts_ms);
      if (t == null) continue;
      const row = timeline.get(t) || {};
      this._mergePointIntoRow(row, p);
      timeline.set(t, row);
    }

    const startRow = timeline.get(chartStart) || {};
    if (this._openingPrice != null) startRow.oracle = this._openingPrice;
    if (this._openingBinance != null) startRow.binance = this._openingBinance;
    // Seed TWAP series with Chainlink TWAP initial (not spot open).
    if (this._twapInitial != null && startRow.twap == null) startRow.twap = this._twapInitial;
    if (this._openingProb) {
      startRow.yes = this._openingProb.yes;
      startRow.no = this._openingProb.no;
    }
    timeline.set(chartStart, startRow);

    const times = [...timeline.keys()].sort((a, b) => a - b);

    // Backfill 0:00 from the first known sample when opening seeds are missing.
    let firstOracle = startRow.oracle ?? null;
    let firstBinance = startRow.binance ?? null;
    let firstTwap = startRow.twap ?? null;
    let firstYes = startRow.yes ?? null;
    let firstNo = startRow.no ?? null;
    for (const t of times) {
      const row = timeline.get(t);
      if (firstOracle == null && row.oracle != null) firstOracle = row.oracle;
      if (firstBinance == null && row.binance != null) firstBinance = row.binance;
      if (firstTwap == null && row.twap != null) firstTwap = row.twap;
      if (firstYes == null && row.yes != null) firstYes = row.yes;
      if (firstNo == null && row.no != null) firstNo = row.no;
    }
    if (startRow.oracle == null && firstOracle != null) startRow.oracle = firstOracle;
    if (startRow.binance == null && firstBinance != null) startRow.binance = firstBinance;
    if (startRow.twap == null && firstTwap != null) startRow.twap = firstTwap;
    if (startRow.yes == null && firstYes != null) startRow.yes = firstYes;
    if (startRow.no == null && firstNo != null) startRow.no = firstNo;
    timeline.set(chartStart, startRow);

    // Expand onto a dense time grid from 0:00 → last sample / now so LWC spacing is linear.
    const sparseLast = times.length ? times[times.length - 1] : chartStart;
    const nowSlot = this._toChartTime(Date.now());
    const gridEnd = Math.max(
      chartStart,
      sparseLast,
      nowSlot != null ? nowSlot : chartStart
    );

    let lastOracle = startRow.oracle ?? null;
    let lastBinance = startRow.binance ?? null;
    let lastTwap = startRow.twap ?? null;
    let lastYes = startRow.yes ?? null;
    let lastNo = startRow.no ?? null;
    let sparseIdx = 0;

    const oracle = [];
    const binance = [];
    const twap = [];
    const yes = [];
    const no = [];

    for (let t = chartStart; t <= gridEnd; t++) {
      while (sparseIdx < times.length && times[sparseIdx] <= t) {
        const row = timeline.get(times[sparseIdx]);
        if (row.oracle != null) lastOracle = row.oracle;
        if (row.binance != null) lastBinance = row.binance;
        if (row.twap != null) lastTwap = row.twap;
        if (row.yes != null) lastYes = row.yes;
        if (row.no != null) lastNo = row.no;
        sparseIdx++;
      }
      if (lastOracle != null) oracle.push({ time: t, value: lastOracle });
      if (lastBinance != null) binance.push({ time: t, value: lastBinance });
      if (lastTwap != null) twap.push({ time: t, value: lastTwap });
      if (lastYes != null) yes.push({ time: t, value: lastYes });
      if (lastNo != null) no.push({ time: t, value: lastNo });
    }

    if (oracle.length) {
      this._carry.oracle = oracle[oracle.length - 1].value;
      this._hasStart.oracle = oracle[0].time === chartStart;
    }
    if (binance.length) {
      this._carry.binance = binance[binance.length - 1].value;
      this._hasStart.binance = binance[0].time === chartStart;
    }
    if (twap.length) {
      this._carry.twap = twap[twap.length - 1].value;
      this._hasStart.twap = twap[0].time === chartStart;
      if (this._twapInitial == null) this._twapInitial = twap[0].value;
    }
    if (yes.length) {
      this._carry.yes = yes[yes.length - 1].value;
      this._carry.no = no[no.length - 1].value;
      this._hasStart.yes = yes[0].time === chartStart;
      this._hasStart.no = no[0]?.time === chartStart;
    }

    return {
      oracle: this._prependStart(oracle, this._openingPrice ?? firstOracle, "oracle"),
      binance: this._prependStart(binance, this._openingBinance ?? firstBinance, "binance"),
      twap: this._prependStart(twap, this._twapInitial ?? firstTwap, "twap"),
      yes: this._prependStart(yes, this._openingProb?.yes ?? firstYes, "yes"),
      no: this._prependStart(no, this._openingProb?.no ?? firstNo, "no"),
    };
  }

  _ensureStartUpdate(series, key, value) {
    const chartStart = this._chartStartTime();
    if (!this._startTs || value == null || this._hasStart[key]) return;
    series.update({ time: chartStart, value });
    this._hasStart[key] = true;
    if (this._lastChartTime < 0) this._lastChartTime = chartStart;
  }

  _seedStartFromCarry() {
    this._ensureStartUpdate(
      this.oracleSeries,
      "oracle",
      this._openingPrice ?? this._carry.oracle
    );
    this._ensureStartUpdate(
      this.binanceSeries,
      "binance",
      this._openingBinance ?? this._carry.binance
    );
    this._ensureStartUpdate(
      this.twapSeries,
      "twap",
      this._twapInitial ?? this._carry.twap
    );
    if (this._carry.yes != null && this._carry.no != null) {
      this._ensureStartUpdate(
        this.yesSeries,
        "yes",
        this._openingProb?.yes ?? this._carry.yes
      );
      this._ensureStartUpdate(
        this.noSeries,
        "no",
        this._openingProb?.no ?? this._carry.no
      );
    }
  }

  setHistory(
    points,
    strikePrice,
    startTs,
    endTs,
    openingPrice = null,
    openingBinance = null,
    openingProb = null,
    twapInitial = null
  ) {
    if (startTs && endTs) {
      this.setWindowBounds(
        startTs,
        endTs,
        openingPrice,
        openingBinance,
        openingProb,
        twapInitial
      );
    } else {
      if (openingPrice != null) this._openingPrice = openingPrice;
      if (openingBinance != null) this._openingBinance = openingBinance;
      if (openingProb != null) this._openingProb = openingProb;
      if (twapInitial != null) this._twapInitial = twapInitial;
    }

    if (!points || !points.length) {
      this._clearSeries();
      const oracleVal = this._openingPrice;
      const binanceVal = this._openingBinance;
      const yesVal = this._openingProb?.yes;
      const noVal = this._openingProb?.no;
      if (this._startTs && (oracleVal != null || binanceVal != null || (yesVal != null && noVal != null))) {
        const chartStart = this._chartStartTime();
        if (oracleVal != null) {
          this.oracleSeries.setData([{ time: chartStart, value: oracleVal }]);
          this._carry.oracle = oracleVal;
          this._hasStart.oracle = true;
        }
        if (this._twapInitial != null) {
          this.twapSeries.setData([{ time: chartStart, value: this._twapInitial }]);
          this._carry.twap = this._twapInitial;
          this._hasStart.twap = true;
        }
        if (binanceVal != null) {
          this.binanceSeries.setData([{ time: chartStart, value: binanceVal }]);
          this._carry.binance = binanceVal;
          this._hasStart.binance = true;
        }
        if (yesVal != null && noVal != null) {
          this.yesSeries.setData([{ time: chartStart, value: yesVal }]);
          this.noSeries.setData([{ time: chartStart, value: noVal }]);
          this._carry.yes = yesVal;
          this._carry.no = noVal;
          this._hasStart.yes = true;
          this._hasStart.no = true;
        }
        this._lastChartTime = chartStart;
        this._capLastChartTimeToNow();
      }
      this._setStrike(this._twapInitial ?? strikePrice ?? this._openingPrice);
      this._applyFixedProbScale();
      if (this._windowJustChanged) {
        this._windowJustChanged = false;
        this._fitFullWindow();
      }
      this._startLivePaintLoop();
      return;
    }

    const { oracle, binance, twap, yes, no } = this._buildSeries(points);
    if (binance.length && this._openingBinance == null) {
      this._openingBinance = binance[0].value;
    }
    if (oracle.length && this._openingPrice == null) {
      this._openingPrice = oracle[0].value;
    }
    if (twap.length && this._twapInitial == null) {
      this._twapInitial = twap[0].value;
    }
    const applyData = () => {
      this.oracleSeries.setData(oracle);
      this.binanceSeries.setData(binance);
      this.twapSeries.setData(twap);
      this.yesSeries.setData(yes);
      this.noSeries.setData(no);
      // Target line tracks TWAP initial.
      this._setStrike(this._twapInitial ?? strikePrice ?? this._openingPrice);
      if (oracle.length) {
        this._setCurrentLine(oracle[oracle.length - 1].value);
      }
    };
    const windowJustChanged = this._windowJustChanged;
    if (windowJustChanged) {
      this._windowJustChanged = false;
      applyData();
      this._fitFullWindow();
    } else {
      this._preserveVisibleRange(applyData);
    }

    const lastTime = Math.max(
      oracle.at(-1)?.time ?? 0,
      binance.at(-1)?.time ?? 0,
      twap.at(-1)?.time ?? 0,
      yes.at(-1)?.time ?? 0,
      no.at(-1)?.time ?? 0,
      this._chartStartTime()
    );
    if (lastTime >= 0 && (oracle.length || binance.length || twap.length || yes.length)) {
      this._lastChartTime = lastTime;
    }
    this._capLastChartTimeToNow();
    this._applyFixedProbScale();
    this._startLivePaintLoop();
  }

  _setStrike(strike) {
    if (this._strikeLine) {
      this.oracleSeries.removePriceLine(this._strikeLine);
      this._strikeLine = null;
    }
    if (strike != null) {
      this._strikeLine = this.oracleSeries.createPriceLine({
        price: strike,
        color: "#5d6b7c",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: this._isCompact() ? "" : "TWAP Target",
      });
    }
  }

  _setCurrentLine(price) {
    if (price == null) {
      if (this._currentLine) {
        this.oracleSeries.removePriceLine(this._currentLine);
        this._currentLine = null;
      }
      return;
    }
    if (this._currentLine) {
      this._currentLine.applyOptions({ price });
      return;
    }
    this._currentLine = this.oracleSeries.createPriceLine({
      price,
      color: "#e8b923",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: false,
      title: "",
    });
  }

  destroy() {
    this._stopLivePaintLoop();
    this._resizeObserver.disconnect();
    this._oracleTooltip?.remove();
    this._probTooltip?.remove();
    this.oracleChart.remove();
    this.probChart.remove();
  }
};

/** Compact YES/NO path chart for recent-window previews. */
PM.MiniPreviewChart = class {
  constructor(el) {
    this._el = el;
    this._chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 240,
      height: el.clientHeight || 88,
      layout: {
        background: { color: "transparent" },
        textColor: "#5d6b7c",
        fontSize: 9,
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "#1e2a3a", style: LightweightCharts.LineStyle.Dotted },
      },
      rightPriceScale: {
        visible: true,
        borderVisible: false,
        entireTextOnly: true,
        minimumWidth: 32,
        scaleMargins: { top: 0.08, bottom: 0.08 },
        autoScale: true,
      },
      leftPriceScale: { visible: false },
      timeScale: {
        borderVisible: false,
        visible: false,
        rightOffset: 0,
      },
      crosshair: {
        vertLine: { visible: false },
        horzLine: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
    });
    const fixedProbScale = {
      autoscaleInfoProvider: () => ({
        priceRange: { minValue: 0, maxValue: 100 },
      }),
    };
    this._yes = this._chart.addLineSeries({
      color: "#22c55e",
      lineWidth: 1.5,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      ...fixedProbScale,
    });
    this._no = this._chart.addLineSeries({
      color: "#ef4444",
      lineWidth: 1.5,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      ...fixedProbScale,
    });
    this._mid = this._chart.addLineSeries({
      color: "rgba(232, 185, 35, 0.55)",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      ...fixedProbScale,
    });
  }

  setData(points) {
    const yes = [];
    const no = [];
    const mid = [];
    const sampled = PM.downsamplePoints(points, 64);
    sampled.forEach((p, i) => {
      const t = i + 1;
      const y = p.yes_ask ?? (p.yes_mid != null ? p.yes_mid * 100 : null);
      const n = p.no_ask ?? (p.no_mid != null ? p.no_mid * 100 : null);
      if (y != null && y > 0) yes.push({ time: t, value: Number(y) });
      if (n != null && n > 0) no.push({ time: t, value: Number(n) });
      mid.push({ time: t, value: 50 });
    });
    this._yes.setData(yes);
    this._no.setData(no);
    this._mid.setData(mid);
    try {
      this._chart.priceScale("right").setVisibleRange({ from: 0, to: 100 });
    } catch {
      /* ignore until series ready */
    }
    this._chart.timeScale().fitContent();
    this.resize();
  }

  resize() {
    if (!this._el) return;
    this._chart.applyOptions({
      width: this._el.clientWidth || 240,
      height: this._el.clientHeight || 88,
    });
  }

  destroy() {
    this._chart.remove();
  }
};

PM.downsamplePoints = function downsamplePoints(points, maxPoints = 64) {
  if (!points?.length || points.length <= maxPoints) return points || [];
  const step = Math.ceil(points.length / maxPoints);
  const out = [];
  for (let i = 0; i < points.length; i += step) out.push(points[i]);
  const last = points[points.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
};
