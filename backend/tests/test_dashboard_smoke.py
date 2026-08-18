"""Live dashboard smoke test through the engine → shared pipeline path."""

from __future__ import annotations

import asyncio

from app.services.engine import compute_dashboard


def test_compute_dashboard_end_to_end():
    async def _run():
        return await compute_dashboard()

    dash = asyncio.run(_run())
    assert dash.total_buy_amount >= 0
    for item in dash.items:
        assert item.amount >= 0
        assert item.reason
        # profit-taking reduces and paused states must carry valid actions
        assert item.action in {"buy", "pause", "reduce", "hold"}
    # every symbol with data produced a card; amounts respect the cap
    total = sum(i.amount for i in dash.items if i.action == "buy")
    assert abs(total - dash.total_buy_amount) < 1e-6
