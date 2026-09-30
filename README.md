# Polymarket Analysis Tool

Live dashboard, tick storage, and backtesting for **Polymarket** BTC / ETH / SOL **Up or Down** markets (5m and 15m).

Streams Polymarket CLOB quotes, Chainlink TWAP, and Binance trades. Archives every window so you can replay history and run backtests.

**Python 3.11+** · system Python (no venv required)

---

## Features

**Live terminal** (`/`)
- BTC, ETH, SOL Up/Down — 5m and 15m
- Polymarket CLOB YES/NO bid-ask, mid, and spread
- Chainlink oracle, 60s TWAP, strike, and distance from strike
- Time remaining, implied YES probability, lean, arb edge
- 1-minute momentum and 5-minute volatility
- Lightweight Charts; load any slug or jump back to live

**History and status**
- `/history` — resolved windows, outcomes, charts, UP/DOWN win rate
- `/status` — collector health, feed lag, database size
- CSV export per window; Parquet archive after each window closes

**Collector**
- Auto-discovers the next Polymarket windows (Gamma API)
- 1-second merged ticks (oracle + CLOB + indicators)
- Optional raw streams: Binance trades, Chainlink ticks, CLOB BBO
- SQLite for live data; 72-hour tick retention after archive

**Backtest**
- Replay stored ticks (no live API needed)
- YES/NO ask-reversal counts (e.g. 90¢ flips)
- BTC grid scan and zip export for offline research

**Run**
- One process (`start`) or collector + web split
- Windows local / VPS scripts; PM2 on Linux

---

## Quick start

```powershell
pip install -e .
.\scripts\run.ps1
```

Open **http://127.0.0.1:8000**

```powershell
# split processes
.\scripts\collector.ps1    # terminal 1
.\scripts\web.ps1          # terminal 2

# or without scripts
python -m pmanalysis start
python -m pmanalysis collector
python -m pmanalysis web

# VPS (Windows firewall + all interfaces)
.\scripts\run-vps.ps1
```

Linux: `pm2 start ecosystem.config.js`

---

## Data

| Table | Source |
|-------|--------|
| `ticks` | 1s merged samples (always on) |
| `binance_trades` | Binance `@trade` (optional) |
| `oracle_ticks` | Chainlink / Polymarket RTDS (optional) |
| `clob_quotes` | Polymarket CLOB BBO (optional) |

Raw feed recording is **off** by default:

```powershell
$env:PM_FEED_RECORD = "1"
.\scripts\run.ps1
```

Windows archive to `data/archive/*.parquet`. SQLite ticks older than 72 hours are pruned.

---

## Backtest

```powershell
.\scripts\backtest.ps1
.\scripts\backtest.ps1 -All
.\scripts\backtest_btc_grid.ps1
```

---

## Config

```powershell
$env:PM_PORT = "8000"
$env:PM_HOST = "127.0.0.1"          # default is 0.0.0.0
$env:PM_ADMIN_PASSWORD = "..."      # set this on a VPS
.\scripts\web.ps1
```

```text
python -m pmanalysis clear-db              # wipe collected data
python -m pmanalysis clear-db -y --keep-archives
```

Research tool only — no trading, no financial advice.
