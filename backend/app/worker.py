"""Background worker: incremental sync + idle monthly backfill."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db import ensure_indexes, get_user_settings, seed_symbols_and_settings
from app.services.engine import compute_dashboard
from app.services.market_data import backfill_idle_chunk, hydrate_db_from_disk_cache, sync_all
from app.services.notify import build_payload, send, should_notify
from app.services import market_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("worker")


async def run_sync() -> None:
    logger.info("Starting market sync → SQLite warehouse…")
    result = await sync_all(use_mock=False)
    logger.info("Sync done live=%s rows=%s", result.get("live"), result.get("results"))
    dash = await compute_dashboard()
    logger.info(
        "Dashboard T-1=%s total_buy=%.2f items=%d",
        dash.date,
        dash.total_buy_amount,
        len(dash.items),
    )
    try:
        settings = await get_user_settings()
        should, reason = await should_notify(dash, settings)
        if should:
            ok = await send(settings.notify_url, build_payload(dash))
            logger.info("Notification sent reason=%s ok=%s", reason, ok)
    except Exception:
        logger.exception("Notification step failed")


async def run_idle_backfill(months: int = 3) -> None:
    status = market_store.data_status()
    if status["months_pending"] <= 0:
        logger.info("No pending months to backfill")
        return
    logger.info(
        "Idle backfill: pending_months=%s processing=%s",
        status["months_pending"],
        months,
    )
    result = await backfill_idle_chunk(months=months)
    logger.info("Backfill chunk: %s", result.get("processed"))


async def main() -> None:
    settings = get_settings()
    market_store.ensure_store()
    await ensure_indexes()
    await seed_symbols_and_settings()
    await hydrate_db_from_disk_cache()
    interval = max(60, settings.sync_interval_seconds)
    logger.info(
        "Worker started, sync_interval=%ss db=%s",
        interval,
        market_store.get_db_path(),
    )

    # First cycle: sync then backfill a few months
    while True:
        try:
            await run_sync()
        except Exception:
            logger.exception("Sync cycle failed")

        # Idle backfill between syncs: walk pending months
        idle_deadline = asyncio.get_event_loop().time() + interval
        while asyncio.get_event_loop().time() < idle_deadline:
            try:
                pending = market_store.data_status()["months_pending"]
                if pending <= 0:
                    await asyncio.sleep(min(60, idle_deadline - asyncio.get_event_loop().time()))
                    continue
                await run_idle_backfill(months=2)
            except Exception:
                logger.exception("Backfill chunk failed")
            # small pause between month chunks
            remaining = idle_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(15, remaining))


if __name__ == "__main__":
    asyncio.run(main())
