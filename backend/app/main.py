from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import (
    ensure_indexes,
    get_portfolio,
    get_user_settings,
    load_app_config,
    save_portfolio,
    save_user_settings,
    seed_symbols_and_settings,
)
from app.models import Portfolio, UserSettings
from app.services.engine import compute_dashboard
from app.services.market_data import (
    backfill_idle_chunk,
    get_market_series,
    hydrate_db_from_disk_cache,
    sync_all,
)
from app.services import market_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    market_store.ensure_store()
    await ensure_indexes()
    await seed_symbols_and_settings()
    # Warm from SQLite warehouse so /dashboard/today stays fast
    await hydrate_db_from_disk_cache()
    yield


app = FastAPI(title="A股宽基定投投顾看板", version="0.1.0", lifespan=lifespan)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/symbols")
async def symbols():
    cfg = load_app_config()
    return {"symbols": [s.model_dump() for s in cfg.symbols]}


@app.get("/api/dashboard/today")
async def dashboard_today():
    return await compute_dashboard()


@app.get("/api/signals/{date}")
async def signals_by_date(date: str):
    """Historical or T-1 signals. Date is always clamped to <= warehouse T-1."""
    from app.db import get_db
    from app.services import market_store

    t1 = market_store.resolve_signal_date()
    clamped = min(date, t1) if t1 else date
    db = get_db()
    if clamped == date:
        doc = await db.signals_daily.find_one({"date": clamped})
        if doc:
            doc.pop("_id", None)
            return doc
    return await compute_dashboard(as_of=clamped)


@app.get("/api/market/{symbol}")
async def market(symbol: str, limit: int = 365):
    series = await get_market_series(symbol.upper(), limit=limit)
    return {"symbol": symbol.upper(), "series": series}


@app.get("/api/portfolio")
async def read_portfolio():
    return await get_portfolio()


@app.put("/api/portfolio")
async def update_portfolio(portfolio: Portfolio):
    return await save_portfolio(portfolio)


@app.get("/api/settings")
async def read_settings():
    return await get_user_settings()


@app.put("/api/settings")
async def update_settings(body: UserSettings):
    return await save_user_settings(body)


@app.post("/api/jobs/sync")
async def job_sync(use_mock: bool | None = None, force: bool = False):
    """Sync market data. Skips symbols already fresh at T-1 unless force=true."""
    return await sync_all(use_mock=use_mock, force=force)


@app.post("/api/jobs/backfill")
async def job_backfill(months: int = 3):
    """Idle/manual monthly backfill into local warehouse."""
    return await backfill_idle_chunk(months=months)


@app.get("/api/data/status")
async def data_status():
    return market_store.data_status()


@app.get("/api/data/overview")
async def data_overview():
    """Small aggregate payload suitable for a dashboard."""
    return await asyncio.to_thread(market_store.data_overview)


@app.get("/api/data/rows")
async def data_rows(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=20, le=200),
):
    """Cursor-paginated raw rows; never loads the full market table."""
    try:
        return await asyncio.to_thread(
            market_store.query_market_page,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            cursor=cursor,
            limit=limit,
        )
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="无效的分页游标") from exc
