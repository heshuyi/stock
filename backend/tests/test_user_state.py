"""Disk persistence for settings/portfolio across memory-mongo restarts."""

import asyncio

from app.db import get_portfolio, save_portfolio, save_user_settings, seed_symbols_and_settings
from app.models import Holding, Portfolio, UserSettings
from app.services import user_state


def test_portfolio_survives_memory_mongo_reseed(monkeypatch, tmp_path):
    state_file = tmp_path / "user_state.json"
    monkeypatch.setattr(user_state, "user_state_path", lambda: state_file)

    async def _run() -> None:
        import app.db as dbmod

        dbmod._client = None
        await seed_symbols_and_settings()

        await save_portfolio(
            Portfolio(
                cash=123456,
                holdings=[
                    Holding(symbol="HS300", shares=100, cost_price=3.5),
                ],
            )
        )
        await save_user_settings(
            UserSettings(base_amount=8888, target_weights={"HS300": 0.35})
        )
        assert state_file.exists()

        # Simulate process restart: new mongomock, reseed from disk.
        dbmod._client = None
        await seed_symbols_and_settings()
        port = await get_portfolio()
        assert port.cash == 123456
        assert port.holdings[0].symbol == "HS300"
        assert port.holdings[0].shares == 100

    asyncio.run(_run())
