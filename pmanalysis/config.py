"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "pmanalysis.db"
ARCHIVE_DIR = DATA_DIR / "archive"
EXPORT_DIR = DATA_DIR / "exports"

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_URL = "wss://ws-live-data.polymarket.com"

ASSETS = ("BTC", "ETH", "SOL")
INTERVALS = ("5m", "15m")

INTERVAL_SECONDS = {"5m": 300, "15m": 900}

MARKET_SLUGS: list[tuple[str, str, str]] = [
    (asset, interval, f"{asset.lower()}-updown-{interval}")
    for asset in ASSETS
    for interval in INTERVALS
]

DISCOVERY_LOOKAHEAD_MINUTES = 70
STRIKE_POLL_INTERVAL_SEC = 3
WINDOW_ROLL_GRACE_SEC = 15
# How long finalize_expired retries Gamma for a winner before falling back to
# comparing final oracle price vs strike. Gamma often hasn't published
# outcomePrices > 0.9 within the old 15s grace period, which froze `outcome`
# at None forever; retrying for a few minutes fixes that for the vast
# majority of windows without ever blocking the roll to the next window.
RESOLUTION_MAX_WAIT_SEC = int(os.getenv("PM_RESOLUTION_MAX_WAIT_SEC", "300"))
COLLECTOR_TICK_INTERVAL_SEC = 1.0
# Live UI latency knobs (lower = faster; 0 = push ASAP).
# Small-VPS defaults: 62 Hz quote push + 2 Hz REST book poll used to pin the CPU
# and starve the CLOB WebSocket reader ("slow consumer"). CLOB WS pushes are
# already event-driven (see collector.service._on_clob_update); this loop is
# just a periodic heartbeat/fallback, so it does not need to be that fast.
LIVE_PUSH_MIN_INTERVAL_MS = int(os.getenv("PM_LIVE_PUSH_MS", "150"))
QUOTE_PUSH_INTERVAL_SEC = float(os.getenv("PM_QUOTE_PUSH_SEC", "0.25"))
# REST /book polling is now a stale-only fallback (see ClobFeed.stale_tokens):
# this is just how often we *check* staleness, not how often we hit the API.
BOOK_POLL_INTERVAL_SEC = float(os.getenv("PM_BOOK_POLL_SEC", "3.0"))
CLOB_STALE_SEC = float(os.getenv("PM_CLOB_STALE_SEC", "8.0"))
WS_SNAPSHOT_INTERVAL_SEC = float(os.getenv("PM_WS_SNAPSHOT_SEC", "3.0"))
WS_DRAIN_TIMEOUT_SEC = float(os.getenv("PM_WS_DRAIN_SEC", "0.008"))
DISCOVERY_INTERVAL_SEC = 30
OPENING_CAPTURE_DEBOUNCE_SEC = 0.5

# SQLite retention: once a window is archived to parquet (export_window_parquet),
# its ticks/indicators are safe to drop from the live DB after this many hours.
# Full-resolution history still lives forever in data/archive/*.parquet and the
# backtest tools fall back to it automatically (see backtest/reversal_count.py).
TICK_RETENTION_HOURS = int(os.getenv("PM_TICK_RETENTION_HOURS", "72"))
RETENTION_SWEEP_INTERVAL_SEC = int(os.getenv("PM_RETENTION_SWEEP_SEC", "1800"))

# Persist every Binance / Chainlink / CLOB WS update (batched)
# Default off — full feed recording balloons SQLite and can stall the live UI.
FEED_RECORD_ENABLED = os.getenv("PM_FEED_RECORD", "0") != "0"
FEED_RECORD_FLUSH_SEC = float(os.getenv("PM_FEED_FLUSH_SEC", "0.5"))
FEED_RECORD_BATCH_SIZE = int(os.getenv("PM_FEED_BATCH_SIZE", "200"))

# 0.0.0.0 = listen on all interfaces (VPS / remote access). Override: PM_HOST=127.0.0.1
WEB_HOST = os.getenv("PM_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("PM_PORT", "8000"))

# Required to confirm destructive admin actions (e.g. Clear Database) from the UI.
ADMIN_PASSWORD = os.getenv("PM_ADMIN_PASSWORD", "qweQWE123!@#")

# Chainlink TWAP lookback via Polymarket RTDS (30 or 60). See:
# https://docs.polymarket.com/market-data/chainlink-twap
# From 2026-08-14 00:00 UTC, 5m crypto markets also use 60s TWAP (same as 15m).
TWAP_WINDOW_SEC = int(os.getenv("PM_TWAP_WINDOW", "60"))
if TWAP_WINDOW_SEC not in (30, 60):
    TWAP_WINDOW_SEC = 60


def twap_window_for_interval(interval: str) -> int:
    """5m and 15m crypto markets both settle on 60s Chainlink TWAP."""
    return 60


@dataclass(frozen=True)
class MarketKey:
    asset: str
    interval: str
    base_slug: str

    @property
    def step_seconds(self) -> int:
        return INTERVAL_SECONDS[self.interval]


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
