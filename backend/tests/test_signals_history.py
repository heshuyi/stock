"""Review timeline API: route must not be shadowed; cache invalidation keeps history."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.main import _invalidate_dashboard_cache, app


def test_signals_history_not_shadowed_by_date_route():
    with TestClient(app) as client:
        response = client.get("/api/signals/history?limit=1")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body.get("history"), list)
        assert "disclaimer" not in body


def test_invalidate_dashboard_cache_only_drops_current_t1(monkeypatch):
    deleted: list[dict] = []

    class FakeColl:
        async def delete_one(self, query):
            deleted.append(query)

        async def delete_many(self, query):
            raise AssertionError("must not wipe signals_daily history")

    monkeypatch.setattr(
        "app.main.market_store.resolve_signal_date", lambda: "2026-08-17"
    )
    monkeypatch.setattr(
        "app.main.get_db",
        lambda: type("DB", (), {"signals_daily": FakeColl()})(),
    )
    asyncio.run(_invalidate_dashboard_cache())
    assert deleted == [{"date": "2026-08-17"}]


def test_invalidate_dashboard_cache_noop_without_t1(monkeypatch):
    monkeypatch.setattr("app.main.market_store.resolve_signal_date", lambda: None)

    class Boom:
        async def delete_one(self, query):
            raise AssertionError("no T-1: must not touch signals_daily")

        async def delete_many(self, query):
            raise AssertionError("no T-1: must not touch signals_daily")

    monkeypatch.setattr(
        "app.main.get_db",
        lambda: type("DB", (), {"signals_daily": Boom()})(),
    )
    asyncio.run(_invalidate_dashboard_cache())
