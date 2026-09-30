"""CLI entry points."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from pmanalysis.collector.service import CollectorService
from pmanalysis.config import WEB_HOST, WEB_PORT, ensure_data_dirs
from pmanalysis.db.clear import clear_database
from pmanalysis.web.app import run_web

log = logging.getLogger(__name__)


def _configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx/httpcore log an INFO line per HTTP request; at the REST /book poll
    # rate this alone filled the PM2 error log to multiple GB. Keep it quiet
    # unless -v is passed.
    if not verbose:
        for noisy in ("httpx", "httpcore", "websockets", "uvicorn.access"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _cmd_collector() -> None:
    ensure_data_dirs()
    service = CollectorService()
    try:
        asyncio.run(service.start())
    except KeyboardInterrupt:
        asyncio.run(service.stop())
        print("Collector stopped.")


def _cmd_start() -> None:
    log.info("Starting collector + web in one process (WebSocket live stream)")
    log.info("Listening on %s:%s", WEB_HOST, WEB_PORT)
    if WEB_HOST in ("0.0.0.0", "::"):
        log.info("Local: http://127.0.0.1:%s  |  Remote: http://<your-ip>:%s", WEB_PORT, WEB_PORT)
    else:
        log.info("Open http://%s:%s", WEB_HOST, WEB_PORT)
    run_web(embed_collector=True)


def _cmd_clear_db(yes: bool, keep_archives: bool) -> None:
    if not yes:
        print(
            "This will DELETE all windows, ticks, indicators, feed streams, candles, and gap logs."
        )
        if not keep_archives:
            print("Parquet archives and CSV exports will also be removed.")
        print("Market definitions (BTC/ETH/SOL) are kept.")
        answer = input("Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            return

    result = clear_database(include_archives=not keep_archives)
    cleared = result["cleared"]
    print("Database cleared.")
    print(f"  ticks:           {cleared.get('ticks', 0)}")
    print(f"  indicators:      {cleared.get('indicators', 0)}")
    print(f"  binance_trades:  {cleared.get('binance_trades', 0)}")
    print(f"  oracle_ticks:    {cleared.get('oracle_ticks', 0)}")
    print(f"  clob_quotes:     {cleared.get('clob_quotes', 0)}")
    print(f"  windows:         {cleared.get('windows', 0)}")
    print(f"  candles:         {cleared.get('candles', 0)}")
    print(f"  gap logs:        {cleared.get('collector_gaps', 0)}")
    if not keep_archives:
        print(f"  archive dir: {result['archive_entries_removed']} entries removed")
        print(f"  export dir:  {result['export_entries_removed']} entries removed")
    print(f"  db:          {result['db_path']} ({result['db_size_mb']} MB)")
    print("Restart the collector to begin recording fresh data.")


def _check_deps() -> None:
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        print("ERROR: Dependencies not installed.")
        print("Run once:")
        print("  pip install -e .")
        print("Or:  .\\scripts\\setup.ps1")
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    _check_deps()
    parser = argparse.ArgumentParser(
        description="Polymarket crypto analysis tool",
        epilog="Quick start: python -m pmanalysis start",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collector", help="Run data collector only")
    sub.add_parser("web", help="Run web dashboard only (DB sync mode)")
    sub.add_parser("start", help="Run collector + web together (recommended)")
    clear_p = sub.add_parser(
        "clear-db",
        help="Wipe collected data (ticks, windows, archives) for a fresh start",
    )
    clear_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    clear_p.add_argument(
        "--keep-archives",
        action="store_true",
        help="Keep parquet archive and CSV export files",
    )

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    ensure_data_dirs()

    if args.command == "collector":
        _cmd_collector()
    elif args.command == "web":
        run_web(embed_collector=False)
    elif args.command == "start":
        _cmd_start()
    elif args.command == "clear-db":
        _cmd_clear_db(yes=args.yes, keep_archives=args.keep_archives)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
