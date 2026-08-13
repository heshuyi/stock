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
            UserSettings(
                base_amount=8888,
                target_weights={
                    "HS300": 0.35,
                    "ZZ500": 0.25,
                    "CYB200": 0.15,
                    "KCB50": 0.10,
                    "SZ50": 0.15,
                },
            )
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


def test_save_portfolio_resets_profit_state_without_shares(
    monkeypatch, tmp_path
):
    state_file = tmp_path / "user_state.json"
    monkeypatch.setattr(user_state, "user_state_path", lambda: state_file)

    async def _run() -> None:
        import app.db as dbmod

        dbmod._client = None
        saved = await save_portfolio(
            Portfolio(
                holdings=[
                    Holding(
                        symbol="HS300",
                        shares=0,
                        cost_price=3.5,
                        take_profit_stage=2,
                        trailing_armed=True,
                        trail_peak_price=4.2,
                    )
                ]
            )
        )
        holding = saved.holdings[0]
        assert holding.take_profit_stage == 0
        assert holding.trailing_armed is False
        assert holding.trail_peak_price is None

    asyncio.run(_run())
