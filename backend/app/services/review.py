"""Signal-history review helpers (R6).

Powers the review timeline: for each past signal date we report the forward
return of the broad-basket equal-weighted across the configured symbols over
several horizons, using the local warehouse's point-in-time index closes.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.db import load_app_config
from app.services import market_store

FORWARD_HORIZONS = (5, 20, 60)

ClosesBySymbol = dict[str, list[tuple[str, float]]]


def _closes_from_rows(rows: Sequence[dict]) -> list[tuple[str, float]]:
    return [
        (r["date"], float(r["close"]))
        for r in rows
        if r.get("close") is not None
    ]


def load_closes_by_symbol() -> ClosesBySymbol:
    cfg = load_app_config()
    return {
        sym.id: _closes_from_rows(market_store.load_records(sym.id))
        for sym in cfg.symbols
    }


def forward_returns(
    signal_date: str,
    horizons: Sequence[int] = FORWARD_HORIZONS,
    *,
    closes_by_symbol: ClosesBySymbol | None = None,
) -> dict[str, float | None]:
    """Per-symbol forward returns averaged across symbols (T-1 close based).

    ``signal_date`` → close at that date, then close ``h`` sessions later
    (each symbol on its own calendar, so pre-listing symbols are skipped).
    ``since`` is the return from ``signal_date`` to the warehouse tip.
    """
    series = closes_by_symbol if closes_by_symbol is not None else load_closes_by_symbol()
    buckets: dict[str, list[float]] = {str(h): [] for h in horizons}
    buckets["since"] = []
    for closes in series.values():
        dates = [d for d, _ in closes]
        if signal_date not in dates:
            continue
        i = dates.index(signal_date)
        base = closes[i][1]
        if not base or base <= 0:
            continue
        for h in horizons:
            j = i + h
            if j < len(closes) and closes[j][1]:
                buckets[str(h)].append(closes[j][1] / base - 1)
        if i < len(closes) - 1 and closes[-1][1]:
            buckets["since"].append(closes[-1][1] / base - 1)

    def avg(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {str(k): avg(v) for k, v in buckets.items()}


def forward_returns_many(
    dates: Sequence[str], horizons: Sequence[int] = FORWARD_HORIZONS
) -> dict[str, dict[str, float | None]]:
    """Same as ``forward_returns`` but loads each symbol's series once."""
    if not dates:
        return {}
    series = load_closes_by_symbol()
    return {
        date: forward_returns(date, horizons, closes_by_symbol=series)
        for date in dates
    }
