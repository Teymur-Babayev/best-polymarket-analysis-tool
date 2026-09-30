"""SQLAlchemy models for tick storage and backtest replay."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(8), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    base_slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    windows: Mapped[list[Window]] = relationship(back_populates="market")


class Window(Base):
    __tablename__ = "windows"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_windows_slug"),
        Index("ix_windows_asset_interval_end", "asset", "interval", "end_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    asset: Mapped[str] = mapped_column(String(8), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(80), default="")
    yes_token_id: Mapped[str] = mapped_column(String(80), default="")
    no_token_id: Mapped[str] = mapped_column(String(80), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    start_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strike_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    opening_oracle_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    strike_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    final_oracle_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    market: Mapped[Market] = relationship(back_populates="windows")
    ticks: Mapped[list[Tick]] = relationship(back_populates="window", cascade="all, delete-orphan")
    indicators: Mapped[list[Indicator]] = relationship(
        back_populates="window", cascade="all, delete-orphan"
    )


class Tick(Base):
    __tablename__ = "ticks"
    __table_args__ = (Index("ix_ticks_window_ts", "window_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    window_id: Mapped[int] = mapped_column(ForeignKey("windows.id"), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    oracle_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    binance_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[float] = mapped_column(Float, default=0.0)
    yes_bid: Mapped[float] = mapped_column(Float, default=0.0)
    no_ask: Mapped[float] = mapped_column(Float, default=0.0)
    no_bid: Mapped[float] = mapped_column(Float, default=0.0)
    yes_mid: Mapped[float] = mapped_column(Float, default=0.0)
    no_mid: Mapped[float] = mapped_column(Float, default=0.0)
    combined_ask: Mapped[float] = mapped_column(Float, default=0.0)
    yes_spread: Mapped[float] = mapped_column(Float, default=0.0)
    secs_remaining: Mapped[int] = mapped_column(Integer, default=0)
    dist_from_strike: Mapped[float | None] = mapped_column(Float, nullable=True)

    window: Mapped[Window] = relationship(back_populates="ticks")


class Indicator(Base):
    __tablename__ = "indicators"
    __table_args__ = (Index("ix_indicators_window_ts", "window_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    window_id: Mapped[int] = mapped_column(ForeignKey("windows.id"), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    implied_yes_prob: Mapped[float] = mapped_column(Float, default=0.0)
    arb_edge: Mapped[float] = mapped_column(Float, default=0.0)
    lean: Mapped[float] = mapped_column(Float, default=0.0)
    momentum_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_5m: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_decay_factor: Mapped[float] = mapped_column(Float, default=0.0)
    twap_oracle: Mapped[float | None] = mapped_column(Float, nullable=True)

    window: Mapped[Window] = relationship(back_populates="indicators")


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("asset", "interval", "ts", name="uq_candles_asset_interval_ts"),
        Index("ix_candles_asset_ts", "asset", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(8), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), default="1m")
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)


class CollectorGap(Base):
    __tablename__ = "collector_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")


class BinanceTrade(Base):
    """Every Binance spot trade from the WebSocket stream."""

    __tablename__ = "binance_trades"
    __table_args__ = (Index("ix_binance_trades_asset_ts", "asset", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(8), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OracleTick(Base):
    """Every Chainlink/RTDS (and fallback) oracle price update."""

    __tablename__ = "oracle_ticks"
    __table_args__ = (Index("ix_oracle_ticks_asset_ts", "asset", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(8), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="chainlink")


class ClobQuote(Base):
    """Every CLOB best-bid/ask change from the market WebSocket."""

    __tablename__ = "clob_quotes"
    __table_args__ = (
        Index("ix_clob_quotes_token_ts", "token_id", "ts_ms"),
        Index("ix_clob_quotes_window_ts", "window_id", "ts_ms"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_id: Mapped[str] = mapped_column(String(80), nullable=False)
    window_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset: Mapped[str | None] = mapped_column(String(8), nullable=True)
    interval: Mapped[str | None] = mapped_column(String(8), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    best_ask: Mapped[float] = mapped_column(Float, default=0.0)
    best_bid: Mapped[float] = mapped_column(Float, default=0.0)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
