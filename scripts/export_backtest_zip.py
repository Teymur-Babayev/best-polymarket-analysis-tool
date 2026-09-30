"""Export July 1 through yesterday as a downloadable backtest zip."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "pmanalysis.db"
ARCHIVE_DIR = ROOT / "data" / "archive"
OUT_ROOT = ROOT / "data" / "exports"
STATIC_DL = ROOT / "pmanalysis" / "web" / "static" / "downloads"

START = datetime(2026, 7, 1, tzinfo=timezone.utc)
# Inclusive through yesterday (UTC). Today is 2026-08-21.
END_EXCLUSIVE = datetime(2026, 8, 21, tzinfo=timezone.utc)
START_TS = int(START.timestamp())
END_TS = int(END_EXCLUSIVE.timestamp())
BATCH_WINDOWS = 250
TICK_COLS = [
    "window_id",
    "ts_ms",
    "oracle_price",
    "binance_price",
    "yes_ask",
    "yes_bid",
    "no_ask",
    "no_bid",
    "yes_mid",
    "no_mid",
    "combined_ask",
    "yes_spread",
    "secs_remaining",
    "dist_from_strike",
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def export_windows(conn: sqlite3.Connection, dest: Path) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT id, slug, asset, interval, start_ts, end_ts, strike_price,
               opening_oracle_price, outcome, final_oracle_price, archived
        FROM windows
        WHERE start_ts >= ? AND end_ts < ?
          AND slug NOT LIKE 'test-%'
        ORDER BY start_ts, id
        """,
        conn,
        params=(START_TS, END_TS),
    )
    frame.to_parquet(dest / "windows.parquet", index=False)
    frame.to_csv(dest / "windows.csv", index=False)
    return frame


def export_ticks(conn: sqlite3.Connection, window_ids: list[int], dest: Path) -> int:
    ticks_dir = dest / "ticks"
    ticks_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    part = 0
    for offset in range(0, len(window_ids), BATCH_WINDOWS):
        batch = window_ids[offset : offset + BATCH_WINDOWS]
        placeholders = ",".join("?" * len(batch))
        sql = f"""
            SELECT {", ".join(TICK_COLS)}
            FROM ticks
            WHERE window_id IN ({placeholders})
            ORDER BY window_id, ts_ms
        """
        frame = pd.read_sql_query(sql, conn, params=tuple(batch))
        if frame.empty:
            continue
        path = ticks_dir / f"part-{part:05d}.parquet"
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        total += len(frame)
        part += 1
        if part % 20 == 0:
            print(f"  ticks parts={part} rows={total:,} windows={offset + len(batch):,}/{len(window_ids):,}", flush=True)
    return total


def copy_archives(windows: pd.DataFrame, dest: Path) -> int:
    copied = 0
    if windows.empty:
        return 0
    archived = windows[windows["archived"] == 1]
    out = dest / "archive"
    for row in archived.itertuples(index=False):
        src = ARCHIVE_DIR / str(row.asset).lower() / str(row.interval) / f"{row.slug}.parquet"
        if not src.exists():
            continue
        target = out / str(row.asset).lower() / str(row.interval)
        target.mkdir(parents=True, exist_ok=True)
        target.joinpath(src.name).write_bytes(src.read_bytes())
        copied += 1
    return copied


def write_readme(dest: Path, windows: pd.DataFrame, tick_rows: int, archives: int) -> None:
    dest.joinpath("README.txt").write_text(
        f"""Polymarket backtest dump
Range: {START.date()} through {END_EXCLUSIVE.date()} (exclusive end, UTC)
Windows: {len(windows):,}
Tick rows: {tick_rows:,}
Copied parquet archives: {archives}

Files
- windows.parquet / windows.csv  market windows (slug, asset, interval, strike, outcome, times)
- ticks/part-*.parquet           1s merged ticks keyed by window_id
- archive/<asset>/<interval>/*.parquet  already-archived windows (same schema as live export)

Join ticks.window_id = windows.id
Ask prices are in cents (0-100). start_ts/end_ts are unix seconds.
""",
        encoding="utf-8",
    )
    dest.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "start_utc": START.isoformat(),
                "end_exclusive_utc": END_EXCLUSIVE.isoformat(),
                "window_count": int(len(windows)),
                "tick_rows": int(tick_rows),
                "archive_files": int(archives),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def zip_dir(src: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src.parent))


def main() -> None:
    stamp = f"{START.date()}_{END_EXCLUSIVE.date()}"
    dest = OUT_ROOT / f"backtest_{stamp}"
    zip_path = OUT_ROOT / f"backtest_{stamp}.zip"
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Exporting {START.date()} .. {END_EXCLUSIVE.date()} from {DB_PATH}", flush=True)
    conn = connect()
    windows = export_windows(conn, dest)
    print(f"windows={len(windows):,}", flush=True)
    tick_rows = export_ticks(conn, windows["id"].astype(int).tolist(), dest)
    print(f"tick_rows={tick_rows:,}", flush=True)
    conn.close()
    archives = copy_archives(windows, dest)
    print(f"archives_copied={archives}", flush=True)
    write_readme(dest, windows, tick_rows, archives)

    if zip_path.exists():
        zip_path.unlink()
    print(f"Zipping {dest} -> {zip_path}", flush=True)
    zip_dir(dest, zip_path)

    STATIC_DL.mkdir(parents=True, exist_ok=True)
    static_zip = STATIC_DL / zip_path.name
    if static_zip.exists() or static_zip.is_symlink():
        static_zip.unlink()
    static_zip.symlink_to(zip_path)
    print(f"done zip={zip_path} size_mb={zip_path.stat().st_size / 1024 / 1024:.1f}", flush=True)
    print(f"download=/static/downloads/{zip_path.name}", flush=True)


if __name__ == "__main__":
    main()
