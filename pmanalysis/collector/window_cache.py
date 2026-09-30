"""In-memory snapshot of active windows for hot paths (no DB per tick)."""

from __future__ import annotations

from dataclasses import dataclass

from pmanalysis.db.models import Window


@dataclass(frozen=True)
class CachedWindow:
    id: int
    asset: str
    interval: str
    slug: str
    question: str
    yes_token_id: str
    no_token_id: str
    start_ts: int
    end_ts: int
    strike_price: float | None
    opening_oracle_price: float | None
    outcome: str | None

    @classmethod
    def from_row(cls, row: Window) -> CachedWindow:
        return cls(
            id=row.id,
            asset=row.asset,
            interval=row.interval,
            slug=row.slug,
            question=row.question or "",
            yes_token_id=row.yes_token_id or "",
            no_token_id=row.no_token_id or "",
            start_ts=row.start_ts,
            end_ts=row.end_ts,
            strike_price=row.strike_price,
            opening_oracle_price=row.opening_oracle_price,
            outcome=row.outcome,
        )
