"""Signal reminder notifications (R7).

Sends a JSON payload to a user-configured webhook (Server酱, 钉钉, 企业微信,
or any HTTP endpoint) on execution days with an actionable signal, and
optionally when the per-symbol signal signature changes versus the prior day.
"""

from __future__ import annotations

import logging

import httpx

from app.db import get_db
from app.models import DashboardResponse, UserSettings

logger = logging.getLogger(__name__)


def build_payload(dash: DashboardResponse) -> dict:
    """Stable JSON payload describing today's dashboard decision."""
    buys = [
        {"symbol": i.symbol, "name": i.name, "amount": i.amount}
        for i in dash.items
        if i.action == "buy" and i.amount > 0
    ]
    reduces = [
        {"symbol": i.symbol, "name": i.name, "ratio": i.reduce_ratio}
        for i in dash.items
        if i.action == "reduce" and i.reduce_ratio
    ]
    pauses = [
        {"symbol": i.symbol, "name": i.name}
        for i in dash.items
        if i.action == "pause"
    ]
    return {
        "title": "定投执行日信号" if dash.execution_today else "定投观察",
        "date": dash.date,
        "execution_today": dash.execution_today,
        "total_buy_amount": dash.total_buy_amount,
        "buys": buys,
        "reduces": reduces,
        "pauses": pauses,
        "warning": dash.warning,
        "pool_factor": dash.pool_factor,
    }


async def should_notify(
    dash: DashboardResponse, settings: UserSettings
) -> tuple[bool, str]:
    """Whether to send a notification, and why."""
    if not (settings.notify_enabled and settings.notify_url):
        return False, ""
    actionable = dash.execution_today and (
        dash.total_buy_amount > 0
        or any(i.action == "reduce" for i in dash.items)
    )
    if settings.notify_on_execution and actionable:
        return True, "execution"

    if settings.notify_on_signal_change and dash.items:
        signature = {i.symbol: i.action for i in dash.items}
        db = get_db()
        prev_docs = (
            await db.signals_daily.find({"date": {"$lt": dash.date}})
            .sort("date", -1)
            .limit(1)
            .to_list(1)
        )
        prev = prev_docs[0] if prev_docs else None
        if prev and prev.get("items"):
            prev_sig = {
                (i.get("symbol"), i.get("action")) for i in prev["items"]
            }
            cur_sig = set(signature.items())
            if prev_sig != cur_sig:
                return True, "signal_change"
    return False, ""


async def send(url: str, payload: dict) -> bool:
    """POST the payload to the webhook; never raises."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            ok = response.status_code < 300
            if not ok:
                logger.warning("Notify webhook returned %s", response.status_code)
            return ok
    except Exception:
        logger.exception("Notify webhook failed")
        return False
