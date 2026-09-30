"""Backtest stored tick data: count alternating YES/NO ask >= 90c moments.

Run:
  .\\scripts\\backtest.ps1
  .\\scripts\\backtest.ps1 -All
  python scripts/backtest_reversal.py --all
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pmanalysis.backtest.reversal_count import (
    WindowReversalResult,
    analyze_windows,
    format_entry_secs,
    format_entry_utc,
    format_event_sequence,
)
from pmanalysis.config import EXPORT_DIR, ensure_data_dirs
from pmanalysis.db.session import get_session


def _fmt_ts(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _window_line(row: WindowReversalResult) -> str:
    outcome = row.outcome or "open"
    return (
        f"{row.slug}\tflips={row.reversal_count}\t"
        f"ticks={row.tick_count}\toutcome={outcome}\t"
        f"sequence={format_event_sequence(row.events)}\t"
        f"entry_secs={format_entry_secs(row.events)}\t"
        f"entry_utc={format_entry_utc(row.events)}"
    )


def _market_key(row: WindowReversalResult) -> tuple[str, str]:
    return row.asset, row.interval


def _market_filename(asset: str, interval: str) -> str:
    return f"{asset.lower()}_{interval}.txt"


def _build_summary_lines(results: list[WindowReversalResult], *, threshold: float) -> list[str]:
    total_reversals = sum(r.reversal_count for r in results)
    with_reversals = [r for r in results if r.reversal_count > 0]

    lines = [
        f"threshold_c={threshold:.0f}",
        f"windows={len(results)}",
        f"with_reversals={len(with_reversals)}",
        f"total_flips={total_reversals}",
    ]
    if with_reversals:
        lines.append(f"avg_flips_when_gt0={total_reversals / len(with_reversals):.2f}")
    if results:
        lines.append(f"avg_flips_all={total_reversals / len(results):.2f}")

    by_key: dict[tuple[str, str], list[WindowReversalResult]] = defaultdict(list)
    for row in results:
        by_key[_market_key(row)].append(row)

    lines.append("")
    lines.append("by_market")
    for (asset, interval), rows in sorted(by_key.items()):
        counts = [r.reversal_count for r in rows]
        hits = sum(1 for c in counts if c > 0)
        total = sum(counts)
        lines.append(
            f"{asset} {interval}\twindows={len(rows)}\t"
            f"with_flips={hits}\ttotal_flips={total}\t"
            f"max={max(counts)}\tavg={total / len(rows):.2f}"
        )
    return lines


def _export_results(
    results: list[WindowReversalResult],
    *,
    threshold: float,
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    by_market: dict[tuple[str, str], list[WindowReversalResult]] = defaultdict(list)
    for row in results:
        by_market[_market_key(row)].append(row)

    written: dict[str, Path] = {}
    for (asset, interval), rows in sorted(by_market.items()):
        filename = _market_filename(asset, interval)
        path = out_dir / filename
        lines = [_window_line(row) for row in rows]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        written[filename] = path

    summary_path = out_dir / "summary.txt"
    summary_lines = _build_summary_lines(results, threshold=threshold)
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    written["summary.txt"] = summary_path
    return written


def _print_results(
    results,
    *,
    threshold: float,
    verbose: bool,
    out_dir: Path | None = None,
    show_windows: bool = True,
) -> None:
    if not results:
        print("No windows with tick data matched your filters.")
        return

    total_reversals = sum(r.reversal_count for r in results)
    with_reversals = [r for r in results if r.reversal_count > 0]

    print(f"Threshold:      >= {threshold:.0f}c ask")
    print(f"Windows:        {len(results)}")
    print(f"With reversals: {len(with_reversals)}")
    print(f"Total flips:    {total_reversals}")
    if with_reversals:
        avg = total_reversals / len(with_reversals)
        print(f"Avg flips/window (when >0): {avg:.2f}")

    by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in results:
        by_key[(row.asset, row.interval)].append(row.reversal_count)

    print()
    print("By market:")
    for (asset, interval), counts in sorted(by_key.items()):
        hits = sum(1 for c in counts if c > 0)
        print(
            f"  {asset} {interval}: {len(counts)} windows, "
            f"{hits} with flips, max={max(counts)}, avg={sum(counts)/len(counts):.2f}"
        )

    print()
    if show_windows:
        print("Per window (newest first):")
        for row in results:
            outcome = row.outcome or "open"
            print(
                f"  {row.slug}  flips={row.reversal_count}  "
                f"ticks={row.tick_count}  outcome={outcome}"
            )
            if verbose and row.events:
                for ev in row.events:
                    print(
                        f"    #{ev.count} {ev.side}  "
                        f"yes_ask={ev.yes_ask:.1f}c  no_ask={ev.no_ask:.1f}c  "
                        f"secs_left={ev.secs_remaining}  at={_fmt_ts(ev.ts_ms)}"
                    )
    else:
        print(f"Per-window listing skipped ({len(results)} windows). See exported txt files.")

    if out_dir is not None:
        print()
        print(f"Exported to: {out_dir}")
        for name in sorted(out_dir.glob("*.txt")):
            line_count = sum(1 for _ in name.open(encoding="utf-8"))
            label = "summary" if name.name == "summary.txt" else f"{line_count} windows"
            print(f"  {name.name}  ({label})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest stored ticks: count YES/NO ask >= threshold reversals",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=90.0,
        help="Ask price threshold in cents (default: 90)",
    )
    parser.add_argument("--asset", choices=["BTC", "ETH", "SOL"], help="Filter by asset")
    parser.add_argument("--interval", choices=["5m", "15m"], help="Filter by interval")
    parser.add_argument("--slug", help="Analyze a single window slug")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all stored windows (whole DB)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max windows (default: all stored windows)",
    )
    parser.add_argument(
        "--resolved-only",
        action="store_true",
        help="Only windows with a known outcome",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each reversal with timestamp and prices",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Export folder (default: data/exports/backtest_reversal/<timestamp>)",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Skip writing txt files",
    )
    args = parser.parse_args()

    limit = None if args.slug or args.all or args.limit is None else args.limit

    ensure_data_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = None if args.no_export else (args.out_dir or EXPORT_DIR / "backtest_reversal" / stamp)

    label = "all windows" if limit is None else f"limit={limit}"
    print(f"Scanning stored data ({label})...")

    with get_session() as session:
        results = analyze_windows(
            session,
            threshold=args.threshold,
            asset=args.asset,
            interval=args.interval,
            slug=args.slug,
            limit=limit,
            resolved_only=args.resolved_only,
        )

    if out_dir is not None and results:
        _export_results(results, threshold=args.threshold, out_dir=out_dir)

    show_windows = limit is not None and len(results) <= 50
    _print_results(
        results,
        threshold=args.threshold,
        verbose=args.verbose,
        out_dir=out_dir,
        show_windows=show_windows,
    )


if __name__ == "__main__":
    main()
