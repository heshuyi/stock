"""Durable market warehouse (SQLite) + monthly backfill bookkeeping.

MongoDB still stores settings / portfolio / signals.
Full OHLCV + valuation history lives here for fast local reads and T-1 signals.
"""

from __future__ import annotations

import sqlite3
import base64
import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "market.db"


def get_db_path() -> Path:
    """Allow MARKET_DB_PATH override (Docker shared volume)."""
    try:
        from app.config import get_settings

        return Path(get_settings().market_db_path)
    except Exception:
        return DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    etf_close REAL,
    ma_short REAL,
    ma_long REAL,
    high_1y REAL,
    drawdown REAL,
    pe REAL,
    pb REAL,
    pe_percentile REAL,
    pb_percentile REAL,
    source TEXT,
    updated_at TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_date
    ON market_bars(symbol, date);
CREATE INDEX IF NOT EXISTS idx_market_bars_date_symbol
    ON market_bars(date DESC, symbol DESC);

CREATE TABLE IF NOT EXISTS valuation_observations (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    pe_ttm REAL,
    pb REAL,
    source TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'verified',
    PRIMARY KEY (symbol, date, source)
);
CREATE INDEX IF NOT EXISTS idx_valuation_symbol_date
    ON valuation_observations(symbol, date);

CREATE TABLE IF NOT EXISTS backfill_months (
    symbol TEXT NOT NULL,
    year_month TEXT NOT NULL,  -- YYYY-MM
    status TEXT NOT NULL,      -- pending|done|error
    rows INTEGER DEFAULT 0,
    last_error TEXT,
    updated_at TEXT,
    PRIMARY KEY (symbol, year_month)
);

CREATE TABLE IF NOT EXISTS sync_meta (
    symbol TEXT PRIMARY KEY,
    last_sync_at TEXT,
    latest_date TEXT,
    source TEXT,
    row_count INTEGER DEFAULT 0
);
"""


def ensure_store() -> Path:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(market_bars)").fetchall()
        }
        if "etf_close" not in columns:
            conn.execute("ALTER TABLE market_bars ADD COLUMN etf_close REAL")
        conn.commit()
    finally:
        conn.close()
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = ensure_store()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def delete_symbol(symbol: str) -> dict[str, int]:
    """Remove a retired symbol's warehouse rows (bars, valuations, plans)."""
    ensure_store()
    with connect() as conn:
        bars = conn.execute(
            "DELETE FROM market_bars WHERE symbol=?", (symbol,)
        ).rowcount
        vals = conn.execute(
            "DELETE FROM valuation_observations WHERE symbol=?", (symbol,)
        ).rowcount
        months = conn.execute(
            "DELETE FROM backfill_months WHERE symbol=?", (symbol,)
        ).rowcount
        conn.execute("DELETE FROM sync_meta WHERE symbol=?", (symbol,))
    return {
        "bars": int(bars or 0),
        "valuations": int(vals or 0),
        "months": int(months or 0),
    }


def purge_symbols_not_in(active_ids: set[str] | list[str]) -> list[dict[str, Any]]:
    """Drop warehouse symbols that are no longer in the live config."""
    active = set(active_ids)
    ensure_store()
    with connect() as conn:
        existing = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT symbol FROM market_bars"
            ).fetchall()
        ]
    removed: list[dict[str, Any]] = []
    for symbol in existing:
        if symbol not in active:
            stats = delete_symbol(symbol)
            removed.append({"symbol": symbol, **stats})
    return removed


def upsert_records(symbol: str, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    now = datetime.utcnow().isoformat() + "Z"
    rows = [
        (
            symbol,
            r["date"],
            r.get("open"),
            r.get("high"),
            r.get("low"),
            r.get("close"),
            r.get("volume"),
            r.get("etf_close"),
            r.get("ma_short"),
            r.get("ma_long"),
            r.get("high_1y"),
            r.get("drawdown"),
            r.get("pe"),
            r.get("pb"),
            r.get("pe_percentile"),
            r.get("pb_percentile"),
            r.get("source"),
            now,
        )
        for r in records
    ]
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO market_bars (
                symbol, date, open, high, low, close, volume,
                etf_close, ma_short, ma_long, high_1y, drawdown,
                pe, pb, pe_percentile, pb_percentile, source, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                etf_close=COALESCE(excluded.etf_close, market_bars.etf_close),
                ma_short=excluded.ma_short,
                ma_long=excluded.ma_long,
                high_1y=excluded.high_1y,
                drawdown=excluded.drawdown,
                pe=COALESCE(excluded.pe, market_bars.pe),
                pb=COALESCE(excluded.pb, market_bars.pb),
                pe_percentile=COALESCE(excluded.pe_percentile, market_bars.pe_percentile),
                pb_percentile=COALESCE(excluded.pb_percentile, market_bars.pb_percentile),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        latest = records[-1]["date"]
        conn.execute(
            """
            INSERT INTO sync_meta(symbol, last_sync_at, latest_date, source, row_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                last_sync_at=excluded.last_sync_at,
                latest_date=excluded.latest_date,
                source=excluded.source,
                row_count=(SELECT COUNT(*) FROM market_bars WHERE symbol=?)
            """,
            (
                symbol,
                now,
                latest,
                records[-1].get("source"),
                len(records),
                symbol,
            ),
        )
        # mark months covered by these records
        months = sorted({r["date"][:7] for r in records})
        for ym in months:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND date LIKE ?",
                (symbol, f"{ym}%"),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO backfill_months(symbol, year_month, status, rows, updated_at)
                VALUES (?, ?, 'done', ?, ?)
                ON CONFLICT(symbol, year_month) DO UPDATE SET
                    status='done', rows=excluded.rows, updated_at=excluded.updated_at,
                    last_error=NULL
                """,
                (symbol, ym, cnt, now),
            )
    return len(records)


def load_records(symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if limit:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM market_bars WHERE symbol=? ORDER BY date DESC LIMIT ?
                ) t ORDER BY date ASC
                """,
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM market_bars WHERE symbol=? ORDER BY date ASC",
                (symbol,),
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_valuation_observations(
    symbol: str,
    records: list[dict[str, Any]],
    *,
    source: str,
    source_symbol: str,
    quality_status: str = "verified",
) -> int:
    """Persist raw valuation observations without overwriting other providers."""
    if not records:
        return 0
    now = datetime.utcnow().isoformat() + "Z"
    rows = [
        (
            symbol,
            record["date"],
            record.get("pe"),
            record.get("pb"),
            source,
            source_symbol,
            now,
            quality_status,
        )
        for record in records
        if record.get("pe") is not None or record.get("pb") is not None
    ]
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO valuation_observations (
                symbol, date, pe_ttm, pb, source, source_symbol,
                fetched_at, quality_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date, source) DO UPDATE SET
                pe_ttm=COALESCE(excluded.pe_ttm, valuation_observations.pe_ttm),
                pb=COALESCE(excluded.pb, valuation_observations.pb),
                source_symbol=excluded.source_symbol,
                fetched_at=excluded.fetched_at,
                quality_status=excluded.quality_status
            """,
            rows,
        )
    return len(rows)


def materialize_valuation_metrics(
    symbol: str, records: list[dict[str, Any]]
) -> int:
    """Update strategy-facing PE/PB fields after point-in-time percentile calculation."""
    rows = [
        (
            record.get("pe"),
            record.get("pb"),
            record.get("pe_percentile"),
            record.get("pb_percentile"),
            symbol,
            record["date"],
        )
        for record in records
    ]
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            UPDATE market_bars SET
                pe=COALESCE(?, pe),
                pb=COALESCE(?, pb),
                pe_percentile=COALESCE(?, pe_percentile),
                pb_percentile=COALESCE(?, pb_percentile)
            WHERE symbol=? AND date=?
            """,
            rows,
        )
    return len(rows)


def get_bar(symbol: str, as_of: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM market_bars WHERE symbol=? AND date=? LIMIT 1",
            (symbol, as_of),
        ).fetchone()
    return dict(row) if row else None


def get_latest_bar(symbol: str, on_or_before: str | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        if on_or_before:
            row = conn.execute(
                """
                SELECT * FROM market_bars
                WHERE symbol=? AND date<=?
                ORDER BY date DESC LIMIT 1
                """,
                (symbol, on_or_before),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM market_bars WHERE symbol=?
                ORDER BY date DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
    return dict(row) if row else None


def resolve_signal_date(today: date | None = None) -> str | None:
    """Use previous available trading day (T-1): max(date) strictly before today."""
    today = today or date.today()
    cutoff = today.isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(date) AS d FROM market_bars
            WHERE date < ?
            """,
            (cutoff,),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def list_trading_dates(
    symbol: str = "HS300",
    start: str | None = None,
    end: str | None = None,
) -> list[str]:
    """Distinct trading dates from warehouse bars, ascending."""
    ensure_store()
    clauses = ["symbol=?"]
    params: list[Any] = [symbol]
    if start:
        clauses.append("date>=?")
        params.append(start)
    if end:
        clauses.append("date<=?")
        params.append(end)
    sql = (
        "SELECT DISTINCT date FROM market_bars WHERE "
        + " AND ".join(clauses)
        + " ORDER BY date ASC"
    )
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    dates = [str(r["date"]) for r in rows]
    if dates:
        return dates
    # Fallback: any symbol's calendar
    clauses = ["1=1"]
    params = []
    if start:
        clauses.append("date>=?")
        params.append(start)
    if end:
        clauses.append("date<=?")
        params.append(end)
    sql = (
        "SELECT DISTINCT date FROM market_bars WHERE "
        + " AND ".join(clauses)
        + " ORDER BY date ASC"
    )
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [str(r["date"]) for r in rows]


def month_range(start: date, end: date) -> list[str]:
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def ensure_month_plan(symbol: str, start_ym: str = "1991-01") -> int:
    """Create pending month slots from start_ym through current month."""
    start = date.fromisoformat(f"{start_ym}-01")
    end = date.today()
    now = datetime.utcnow().isoformat() + "Z"
    created = 0
    with connect() as conn:
        for ym in month_range(start, end):
            cur = conn.execute(
                "SELECT 1 FROM backfill_months WHERE symbol=? AND year_month=?",
                (symbol, ym),
            ).fetchone()
            if cur:
                continue
            conn.execute(
                """
                INSERT INTO backfill_months(symbol, year_month, status, rows, updated_at)
                VALUES (?, ?, 'pending', 0, ?)
                """,
                (symbol, ym, now),
            )
            created += 1
    return created


def next_pending_month(symbol: str | None = None) -> tuple[str, str] | None:
    with connect() as conn:
        if symbol:
            row = conn.execute(
                """
                SELECT symbol, year_month FROM backfill_months
                WHERE symbol=? AND status='pending'
                ORDER BY year_month ASC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT symbol, year_month FROM backfill_months
                WHERE status='pending'
                ORDER BY year_month ASC LIMIT 1
                """,
            ).fetchone()
    if not row:
        return None
    return row["symbol"], row["year_month"]


def mark_month(symbol: str, year_month: str, status: str, rows: int = 0, error: str | None = None) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO backfill_months(symbol, year_month, status, rows, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, year_month) DO UPDATE SET
                status=excluded.status,
                rows=excluded.rows,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (symbol, year_month, status, rows, error, now),
        )


def data_status() -> dict[str, Any]:
    with connect() as conn:
        metas = [dict(r) for r in conn.execute("SELECT * FROM sync_meta").fetchall()]
        pending = conn.execute(
            "SELECT COUNT(*) FROM backfill_months WHERE status='pending'"
        ).fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM backfill_months WHERE status='done'"
        ).fetchone()[0]
        total_bars = conn.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0]
    return {
        "db_path": str(get_db_path()),
        "total_bars": total_bars,
        "months_done": done,
        "months_pending": pending,
        "symbols": metas,
        "signal_date": resolve_signal_date(),
    }


def data_overview() -> dict[str, Any]:
    """Return compact warehouse statistics without loading raw rows."""
    path = ensure_store()
    with connect() as conn:
        overall = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT symbol) AS symbol_count,
                    MIN(date) AS earliest_date,
                    MAX(date) AS latest_date,
                    SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) AS missing_close,
                    SUM(CASE WHEN etf_close IS NULL THEN 1 ELSE 0 END) AS missing_etf_close,
                    SUM(CASE WHEN pe IS NULL THEN 1 ELSE 0 END) AS missing_pe,
                    SUM(CASE WHEN pb IS NULL THEN 1 ELSE 0 END) AS missing_pb,
                    SUM(CASE WHEN ma_long IS NULL THEN 1 ELSE 0 END) AS missing_ma_long
                FROM market_bars
                """
            ).fetchone()
        )
        symbols = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    symbol,
                    COUNT(*) AS rows,
                    MIN(date) AS earliest_date,
                    MAX(date) AS latest_date,
                    MAX(updated_at) AS updated_at,
                    GROUP_CONCAT(DISTINCT source) AS sources,
                    SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) AS missing_close,
                    SUM(CASE WHEN etf_close IS NULL THEN 1 ELSE 0 END) AS missing_etf_close,
                    SUM(CASE WHEN pe IS NULL THEN 1 ELSE 0 END) AS missing_pe,
                    SUM(CASE WHEN pb IS NULL THEN 1 ELSE 0 END) AS missing_pb
                FROM market_bars
                GROUP BY symbol
                ORDER BY symbol
                """
            ).fetchall()
        ]
        monthly = [
            dict(row)
            for row in conn.execute(
                """
                SELECT substr(date, 1, 7) AS month, COUNT(*) AS rows
                FROM market_bars
                GROUP BY substr(date, 1, 7)
                ORDER BY month DESC
                LIMIT 36
                """
            ).fetchall()
        ]
        sync_meta = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM sync_meta ORDER BY symbol"
            ).fetchall()
        ]
        valuations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT symbol, COUNT(*) AS rows, MIN(date) AS earliest_date,
                       MAX(date) AS latest_date,
                       GROUP_CONCAT(DISTINCT source) AS sources,
                       MAX(fetched_at) AS fetched_at
                FROM valuation_observations
                GROUP BY symbol
                ORDER BY symbol
                """
            ).fetchall()
        ]

    total = int(overall.get("total_rows") or 0)
    missing = sum(
        int(overall.get(key) or 0)
        for key in ("missing_close", "missing_pe", "missing_pb")
    )
    overall["quality_score"] = (
        round(max(0.0, 1.0 - missing / max(total * 3, 1)) * 100, 2)
        if total
        else 0.0
    )
    return {
        "db_path": str(path),
        "db_size_bytes": path.stat().st_size if path.exists() else 0,
        "overall": overall,
        "symbols": symbols,
        "monthly": list(reversed(monthly)),
        "sync_meta": sync_meta,
        "valuations": valuations,
        "signal_date": resolve_signal_date(),
    }


def _encode_cursor(date_value: str, symbol: str) -> str:
    raw = json.dumps([date_value, symbol], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    padded = cursor + "=" * (-len(cursor) % 4)
    date_value, symbol = json.loads(base64.urlsafe_b64decode(padded).decode())
    return str(date_value), str(symbol)


def query_market_page(
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Seek-pagination query ordered by date/symbol descending.

    Cursor pagination keeps latency stable even when the table grows large;
    it avoids expensive OFFSET scans and never returns the whole dataset.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if cursor:
        cursor_date, cursor_symbol = _decode_cursor(cursor)
        clauses.append("(date < ? OR (date = ? AND symbol < ?))")
        params.extend([cursor_date, cursor_date, cursor_symbol])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    page_size = max(20, min(limit, 200))
    with connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    symbol, date, open, high, low, close, volume, etf_close,
                    ma_short, ma_long, drawdown, pe, pb,
                    pe_percentile, pb_percentile, source, updated_at
                FROM market_bars
                {where}
                ORDER BY date DESC, symbol DESC
                LIMIT ?
                """,
                [*params, page_size + 1],
            ).fetchall()
        ]

    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last["date"], last["symbol"])
    return {
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": page_size,
    }
