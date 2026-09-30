"""Shared collector instance for web + WS handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmanalysis.collector.service import CollectorService

_collector: CollectorService | None = None


def set_collector(service: CollectorService | None) -> None:
    global _collector
    _collector = service


def get_collector() -> CollectorService | None:
    return _collector
