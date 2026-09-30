"""BTC tiered-entry grid backtest: 11 x 21 threshold combinations.

Run:
  .\\scripts\\backtest_btc_grid.ps1
  python scripts/backtest_btc_grid.py
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
    analyze_tiered_grid,
    format_entry_secs,
    format_entry_utc,
    format_event_sequence,
    load_btc_window_ticks,
)
from pmanalysis.config import EXPORT_DIR, ensure_data_dirs
from pmanalysis.db.session import get_session

FIRST_ENTRY_C = 90.0
SECOND_RANGE = range(80, 91)   # 11 values: 80..90
THIRD_RANGE = range(70, 91)    # 21 values: 70..90


def _window_line(row: WindowReversalResult) -> str:
    outcome = row.outcome or "open"
    return (
        f"{row.slug}\t{row.interval}\tflips={row.reversal_count}\t"
        f"ticks={row.tick_count}\toutcome={outcome}\t"
        f"sequence={format_event_sequence(row.events)}\t"
        f"entry_secs={format_entry_secs(row.events)}\t"
        f"entry_utc={format_entry_utc(row.events)}"
    )


def _summary_for_interval(rows: list[WindowReversalResult], interval: str) -> str:
    subset = [r for r in rows if r.interval == interval]
    if not subset:
        return f"# {interval}: no data"
    counts = [r.reversal_count for r in subset]
    hits = sum(1 for c in counts if c > 0)
    total = sum(counts)
    return (
        f"# {interval}\twindows={len(subset)}\twith_flips={hits}\t"
        f"total_flips={total}\tmax={max(counts)}\tavg={total / len(subset):.2f}"
    )


def _build_file_lines(
    results: list[WindowReversalResult],
    *,
    second: int,
    third: int,
) -> list[str]:
    total_flips = sum(r.reversal_count for r in results)
    with_flips = sum(1 for r in results if r.reversal_count > 0)

    header = [
        "# BTC tiered YES/NO entry backtest",
        f"# entry1 (first flip):  {FIRST_ENTRY_C:.0f}c  [fixed]",
        f"# entry2 (second flip): {second}c  [swept 80-90]",
        f"# entry3 (third+ flip): {third}c  [swept 70-90]",
        "#",
        "# Logic: alternating YES/NO ask must reach the threshold for that entry",
        "# number (1st=90c, 2nd=entry2, 3rd+=entry3). Impossible quotes skipped.",
        "#",
        f"# ALL\twindows={len(results)}\twith_flips={with_flips}\t"
        f"total_flips={total_flips}\t"
        f"avg={total_flips / len(results):.2f}" if results else "# ALL\twindows=0",
        _summary_for_interval(results, "5m"),
        _summary_for_interval(results, "15m"),
        "#",
        "# slug\tinterval\tflips=\tticks=\toutcome=\tsequence=\tentry_secs=\tentry_utc=",
        "# sequence: SIDE[secs_remaining@HH:MM:SS,ask_c] per entry, joined by >",
    ]
    body = [_window_line(row) for row in results]
    return header + body


def _param_filename(second: int, third: int) -> str:
    return f"entry2_{second:02d}_entry3_{third:02d}.txt"


def _run_grid(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    combo_summaries: list[str] = []

    with get_session() as session:
        loaded = load_btc_window_ticks(session)

    if not loaded:
        print("No BTC window tick data found.")
        return {"files": 0, "out_dir": str(out_dir)}

    print(f"Loaded {len(loaded)} BTC windows. Running 11 x 21 = 231 parameter sets...")

    for second in SECOND_RANGE:
        for third in THIRD_RANGE:
            results = analyze_tiered_grid(
                loaded,
                first=FIRST_ENTRY_C,
                second=float(second),
                third=float(third),
            )
            lines = _build_file_lines(results, second=second, third=third)
            path = out_dir / _param_filename(second, third)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            total = sum(r.reversal_count for r in results)
            with_flips = sum(1 for r in results if r.reversal_count > 0)
            combo_summaries.append(
                f"entry2={second}\tentry3={third}\twindows={len(results)}\t"
                f"with_flips={with_flips}\ttotal_flips={total}\t"
                f"avg={total / len(results):.2f}"
            )

    master = [
        "# BTC tiered grid master summary",
        f"# entry1 fixed at {FIRST_ENTRY_C:.0f}c",
        "# entry2 range: 80-90 (11 values)",
        "# entry3 range: 70-90 (21 values)",
        "# total combinations: 231",
        "#",
        "# entry2\tentry3\twindows\twith_flips\ttotal_flips\tavg",
        *combo_summaries,
    ]
    (out_dir / "master_summary.txt").write_text("\n".join(master) + "\n", encoding="utf-8")

    return {"files": 231, "out_dir": str(out_dir), "windows": len(loaded)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC grid backtest: entry1=90c, entry2=80-90, entry3=70-90",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output folder (default: data/exports/backtest_btc_grid/<timestamp>)",
    )
    args = parser.parse_args()

    ensure_data_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or EXPORT_DIR / "backtest_btc_grid" / stamp

    result = _run_grid(out_dir)
    print()
    print(f"Done. {result['files']} parameter files written.")
    print(f"Windows analyzed: {result.get('windows', 0)}")
    print(f"Output: {result['out_dir']}")
    print("Also see master_summary.txt for all 231 combinations.")


if __name__ == "__main__":
    main()
