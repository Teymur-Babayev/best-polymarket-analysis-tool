"""FastAPI web application."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from pmanalysis.collector.registry import get_collector, set_collector
from pmanalysis.collector.service import CollectorService
from pmanalysis.config import ADMIN_PASSWORD, ASSETS, INTERVALS, INTERVAL_SECONDS, WEB_HOST, WEB_PORT
from pmanalysis.db.models import Window
from pmanalysis.db.repository import (
    count_windows,
    db_stats,
    list_binance_trades,
    list_clob_quotes,
    list_oracle_ticks,
    list_recent_previews,
    list_windows,
    stats_win_rate,
    window_ticks_payload,
)
from pmanalysis.db.clear import clear_database
from pmanalysis.db.session import db_health, get_session, init_db, run_db
from pmanalysis.export.parquet import export_window_csv
from pmanalysis.web.render import render
from pmanalysis.schemas import MarketCard
from pmanalysis.web.sync import db_sync_loop, hydrate_live_store_from_db
from pmanalysis.web.ws import manager as ws_manager

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent

_collector: CollectorService | None = None
_background_tasks: list[asyncio.Task] = []


def _embed_collector() -> bool:
    return os.getenv("PM_EMBED_COLLECTOR", "0") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collector, _background_tasks
    init_db()
    await hydrate_live_store_from_db()
    if _embed_collector():
        log.info("Embedded collector enabled (WebSocket streams from live feeds)")
        _collector = CollectorService()
        set_collector(_collector)
        _background_tasks.append(asyncio.create_task(_collector.start(), name="collector"))
    else:
        log.info("DB sync mode (start collector separately or use `pmanalysis start`)")
        _background_tasks.append(asyncio.create_task(db_sync_loop(), name="db-sync"))
    yield
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()
    if _collector:
        await _collector.stop()
        set_collector(None)
        _collector = None


def create_app() -> FastAPI:
    app = FastAPI(title="Polymarket Analysis", version="0.2.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(
            WEB_DIR / "static" / "img" / "p-origin-removebg.png",
            media_type="image/png",
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return render(
            request,
            "index.html",
            assets=ASSETS,
            intervals=INTERVALS,
            interval_seconds=INTERVAL_SECONDS,
        )

    @app.get("/market/{asset}/{interval}", response_class=HTMLResponse)
    async def market_redirect(request: Request, asset: str, interval: str):
        return render(
            request,
            "index.html",
            assets=ASSETS,
            intervals=INTERVALS,
            interval_seconds=INTERVAL_SECONDS,
            preselect_asset=asset.upper(),
            preselect_interval=interval,
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        return render(request, "history.html")

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        collector = get_collector()
        cached = collector.stats_cache if collector else None
        if cached is not None:
            health = {"path": cached["path"], "size_mb": cached["size_mb"], "ok": cached["ok"]}
            stats = {k: v for k, v in cached.items() if k not in health}
        else:
            # Fallback (no embedded collector yet, or cache not warmed up): may
            # briefly block behind the SQLite writer on first load only.
            def _load():
                health = db_health()
                with get_session() as session:
                    stats = db_stats(session)
                return health, stats

            health, stats = await run_db(_load)
        return render(
            request,
            "status.html",
            health=health,
            embed_collector=_embed_collector(),
            **stats,
        )

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await ws_manager.handle_client(websocket)

    @app.get("/api/markets", response_model=list[MarketCard])
    async def api_markets():
        from pmanalysis.db.repository import build_market_cards

        def _load():
            with get_session() as session:
                return build_market_cards(session)

        return await run_db(_load)

    @app.get("/api/windows")
    async def api_windows(
        limit: int = 200,
        offset: int = 0,
        asset: str | None = None,
        interval: str | None = None,
    ):
        def _load():
            with get_session() as session:
                total = count_windows(session, asset=asset, interval=interval)
                rows = list_windows(
                    session,
                    limit=limit,
                    offset=offset,
                    asset=asset,
                    interval=interval,
                )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [r.model_dump() if hasattr(r, "model_dump") else r for r in rows],
            }

        return await run_db(_load)

    @app.get("/api/feeds/stats")
    async def api_feed_stats():
        collector = get_collector()
        cached = collector.stats_cache if collector else None
        if cached is not None:
            return {
                "window_count": cached["window_count"],
                "tick_count": cached["tick_count"],
                "binance_trade_count": cached["binance_trade_count"],
                "oracle_tick_count": cached["oracle_tick_count"],
                "clob_quote_count": cached["clob_quote_count"],
                "latest_tick": cached["latest_tick"],
            }

        def _load():
            with get_session() as session:
                return db_stats(session)

        return await run_db(_load)

    @app.get("/api/feeds/binance")
    async def api_binance_trades(
        limit: int = 200,
        offset: int = 0,
        asset: str | None = None,
    ):
        def _load():
            with get_session() as session:
                items, total = list_binance_trades(
                    session, limit=limit, offset=offset, asset=asset
                )
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        return await run_db(_load)

    @app.get("/api/feeds/oracle")
    async def api_oracle_ticks(
        limit: int = 200,
        offset: int = 0,
        asset: str | None = None,
        source: str | None = None,
    ):
        def _load():
            with get_session() as session:
                items, total = list_oracle_ticks(
                    session, limit=limit, offset=offset, asset=asset, source=source
                )
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        return await run_db(_load)

    @app.get("/api/feeds/clob")
    async def api_clob_quotes(
        limit: int = 200,
        offset: int = 0,
        asset: str | None = None,
        side: str | None = None,
    ):
        def _load():
            with get_session() as session:
                items, total = list_clob_quotes(
                    session, limit=limit, offset=offset, asset=asset, side=side
                )
            return {"total": total, "limit": limit, "offset": offset, "items": items}

        return await run_db(_load)

    @app.get("/api/windows/{slug}/ticks")
    async def api_window_ticks(slug: str):
        def _load():
            with get_session() as session:
                return window_ticks_payload(session, slug)

        payload = await run_db(_load)
        if not payload:
            raise HTTPException(404, "Window not found")
        return payload

    @app.get("/api/previews/{asset}/{interval}")
    async def api_recent_previews(
        asset: str,
        interval: str,
        limit: int = 7,
        page: int = 0,
        skip_current: bool = False,
    ):
        asset = asset.upper()
        if asset not in ASSETS or interval not in INTERVALS:
            raise HTTPException(400, "Invalid asset or interval")

        def _load():
            with get_session() as session:
                return list_recent_previews(
                    session,
                    asset=asset,
                    interval=interval,
                    limit=max(1, min(limit, 10)),
                    page=max(0, page),
                    skip_current=skip_current,
                )

        return await run_db(_load)

    @app.get("/api/stats/{asset}/{interval}")
    async def api_stats(asset: str, interval: str):
        def _load():
            with get_session() as session:
                return stats_win_rate(session, asset, interval)

        rate = await run_db(_load)
        return {"win_rate_up_pct": rate}

    @app.post("/api/admin/clear-db")
    async def api_clear_db(request: Request):
        """Delete all collected data (windows, ticks, archives). Keeps market seeds."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("password") != ADMIN_PASSWORD:
            raise HTTPException(401, "Incorrect password")
        try:
            result = await run_db(lambda: clear_database(include_archives=True))
        except Exception as exc:
            log.exception("clear-db failed")
            raise HTTPException(500, f"Failed to clear database: {exc}") from exc

        collector = get_collector()
        if collector:
            def _reload():
                with get_session() as session:
                    collector.window_manager._active_ids.clear()
                    collector.window_manager.load_active_from_db(session)
                    collector._refresh_token_map(session)

            await run_db(_reload)
            await collector.live_store.set_snapshot([], {})

        await hydrate_live_store_from_db()
        return {"ok": True, **result}

    @app.get("/api/export/{window_id}")
    async def api_export(window_id: int, format: str = "csv"):
        def _load():
            with get_session() as session:
                window = session.query(Window).filter_by(id=window_id).first()
                if not window:
                    return None
                if format != "csv":
                    return "bad_format"
                return export_window_csv(session, window)

        path = await run_db(_load)
        if path is None:
            raise HTTPException(404, "Window not found")
        if path == "bad_format":
            raise HTTPException(400, "Only csv download supported via API")
        return FileResponse(path, filename=path.name)

    return app


def run_web(*, embed_collector: bool = False) -> None:
    import uvicorn

    if embed_collector:
        os.environ["PM_EMBED_COLLECTOR"] = "1"
    uvicorn.run(
        "pmanalysis.web.app:create_app",
        factory=True,
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
    )
