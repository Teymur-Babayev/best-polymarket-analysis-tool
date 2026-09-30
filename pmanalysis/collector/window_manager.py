"""Track active windows in the database."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from pmanalysis.config import INTERVAL_SECONDS, RESOLUTION_MAX_WAIT_SEC
from pmanalysis.db.models import Market, Window
from pmanalysis.feeds.chainlink import ChainlinkFeed
from pmanalysis.feeds.gamma import DiscoveredWindow, GammaClient, strike_to_float
from pmanalysis.feeds.slugs import boundary_ts
from pmanalysis.collector.window_cache import CachedWindow
from pmanalysis.feeds.opening_price import (
    apply_opening_price,
    capture_final_oracle_price,
    oracle_from_ticks_at_end,
    seed_opening_from_previous,
)


class WindowManager:
    def __init__(self, gamma: GammaClient) -> None:
        self.gamma = gamma
        self._active_ids: dict[str, int] = {}

    def load_active_from_db(self, session: Session) -> None:
        rows = (
            session.query(Window)
            .filter(Window.outcome.is_(None))
            .order_by(Window.end_ts.desc())
            .all()
        )
        seen: set[str] = set()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        for row in rows:
            key = f"{row.asset}:{row.interval}"
            if key in seen:
                continue
            if row.end_ts > now_ts:
                self._active_ids[key] = row.id
                seen.add(key)

    def get_active_windows(self, session: Session) -> list[Window]:
        if not self._active_ids:
            return []
        ids = list(self._active_ids.values())
        rows = session.query(Window).filter(Window.id.in_(ids)).all()
        by_id = {row.id: row for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def needs_roll_discovery(self, session: Session, now_ts: int) -> bool:
        for window in self.get_active_windows(session):
            if now_ts >= window.end_ts:
                return True
            expected_start = boundary_ts(now_ts, window.interval)
            if window.start_ts != expected_start:
                return True
        return False

    def sync_discovered(
        self,
        session: Session,
        discovered: list[DiscoveredWindow],
        chainlink: ChainlinkFeed | None = None,
    ) -> tuple[list[str], list[tuple[str, str, int | None]]]:
        token_ids: list[str] = []
        rolled: list[tuple[str, str, int | None]] = []
        market_map = {m.base_slug: m for m in session.query(Market).all()}
        for item in discovered:
            key = f"{item.asset}:{item.interval}"
            market = market_map.get(item.base_slug)
            if not market:
                continue
            prev_id = self._active_ids.get(key)
            prev_row = None
            prev_slug = None
            if prev_id:
                prev_row = session.query(Window).filter_by(id=prev_id).first()
                prev_slug = prev_row.slug if prev_row else None
            existing = session.query(Window).filter_by(slug=item.slug).first()
            if existing:
                self._active_ids[key] = existing.id
                if prev_slug and prev_slug != item.slug:
                    rolled.append((item.asset, item.interval, prev_id))
                    if chainlink and prev_row:
                        capture_final_oracle_price(session, prev_row, chainlink)
                        seed_opening_from_previous(session, existing, chainlink)
            else:
                window = Window(
                    market_id=market.id,
                    slug=item.slug,
                    asset=item.asset,
                    interval=item.interval,
                    condition_id=item.condition_id,
                    yes_token_id=item.yes_token_id,
                    no_token_id=item.no_token_id,
                    question=item.question,
                    start_ts=item.start_ts,
                    end_ts=item.end_ts,
                    strike_price=strike_to_float(item.strike_label),
                    strike_source="question" if item.strike_label != "—" else None,
                )
                session.add(window)
                session.flush()
                self._active_ids[key] = window.id
                if prev_slug and prev_slug != item.slug:
                    rolled.append((item.asset, item.interval, prev_id))
                    if chainlink and prev_row:
                        capture_final_oracle_price(session, prev_row, chainlink)
                        seed_opening_from_previous(session, window, chainlink)
            token_ids.extend([item.yes_token_id, item.no_token_id])
        session.commit()
        return list(dict.fromkeys(token_ids)), rolled

    def seed_openings_from_previous(
        self, session: Session, chainlink: ChainlinkFeed
    ) -> None:
        """Fill missing opening prices from the prior window's close."""
        for window in self.get_active_windows(session):
            if window.opening_oracle_price is not None:
                continue
            seed_opening_from_previous(session, window, chainlink)

    async def refresh_opening_prices(
        self, session: Session, chainlink: ChainlinkFeed
    ) -> None:
        self.seed_openings_from_previous(session, chainlink)
        for window in self.get_active_windows(session):
            await apply_opening_price(session, window, chainlink, self.gamma)
        session.commit()

    async def capture_openings_for_asset(
        self, session: Session, asset: str, chainlink: ChainlinkFeed
    ) -> bool:
        """Try to fill missing opening prices for active windows of one asset."""
        changed = False
        for window in self.get_active_windows(session):
            if window.asset != asset:
                continue
            if await apply_opening_price(session, window, chainlink, self.gamma):
                changed = True
        if changed:
            session.commit()
        return changed

    async def refresh_strikes(self, session: Session, chainlink: ChainlinkFeed) -> None:
        for window in self.get_active_windows(session):
            await apply_opening_price(session, window, chainlink, self.gamma)
        session.commit()

    async def finalize_expired(
        self,
        session: Session,
        now_ts: int,
        grace_sec: int,
        chainlink: ChainlinkFeed | None = None,
    ) -> list[Window]:
        """Resolve+finalize windows past end_ts+grace that aren't resolved yet.

        Queries `resolved_at IS NULL` directly instead of iterating
        `_active_ids`: the next window's roll discovery replaces
        `_active_ids[key]` almost immediately at the boundary (often before
        this runs), so relying on `_active_ids` alone meant an expired window
        could silently fall out of tracking and never get resolved. Gamma also
        frequently hasn't published a winner within the old fixed grace
        period, which froze `outcome=None` forever; we now retry for up to
        `RESOLUTION_MAX_WAIT_SEC` before falling back to a final-oracle vs
        strike comparison.
        """
        finalized: list[Window] = []
        candidates = (
            session.query(Window)
            .filter(Window.resolved_at.is_(None), Window.end_ts + grace_sec <= now_ts)
            .all()
        )
        for window in candidates:
            outcome, _ = await self.gamma.fetch_resolution(window.slug)
            overdue = now_ts - window.end_ts >= RESOLUTION_MAX_WAIT_SEC
            if not outcome and not overdue:
                continue  # Gamma hasn't published a winner yet; retry next sweep.

            if chainlink:
                capture_final_oracle_price(session, window, chainlink)
            elif window.final_oracle_price is None:
                final = oracle_from_ticks_at_end(session, window.id, window.end_ts)
                if final is not None:
                    window.final_oracle_price = final

            if outcome:
                window.outcome = outcome
            elif window.strike_price is not None and window.final_oracle_price is not None:
                # Gamma fallback: infer from final oracle vs strike (best-effort,
                # matches Polymarket's own crypto up/down settlement rule).
                window.outcome = (
                    "UP" if window.final_oracle_price > window.strike_price else "DOWN"
                )

            window.resolved_at = datetime.now(timezone.utc)
            session.add(window)
            finalized.append(window)
            key = f"{window.asset}:{window.interval}"
            if self._active_ids.get(key) == window.id:
                del self._active_ids[key]
        session.commit()
        return finalized

    def window_duration(self, window: Window | CachedWindow) -> int:
        return INTERVAL_SECONDS.get(window.interval, window.end_ts - window.start_ts)
