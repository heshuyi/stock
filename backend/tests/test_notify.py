"""Tests for signal notification logic (R7)."""

from __future__ import annotations

import asyncio

from app.models import DashboardResponse, EnsembleResult, UserSettings
from app.services import notify


def _dash(*, execution_today: bool, buy_amount: float = 0.0) -> DashboardResponse:
    items: list[EnsembleResult] = []
    if buy_amount > 0:
        items.append(
            EnsembleResult(
                symbol="HS300",
                name="沪深300",
                etf_code="510300",
                target_weight=0.35,
                action="buy",
                multiplier=1.0,
                amount=buy_amount,
                reason="r",
                strategies=[],
            )
        )
    return DashboardResponse(
        date="2026-08-14",
        base_amount=3100,
        total_buy_amount=buy_amount,
        normalized=False,
        execution_today=execution_today,
        items=items,
    )


def test_should_notify_disabled():
    s = UserSettings(notify_enabled=False, notify_url="http://x")
    assert asyncio.run(
        notify.should_notify(_dash(execution_today=True, buy_amount=100), s)
    ) == (False, "")


def test_should_notify_execution_actionable():
    s = UserSettings(notify_enabled=True, notify_url="http://x", notify_on_execution=True)
    assert asyncio.run(
        notify.should_notify(_dash(execution_today=True, buy_amount=100), s)
    ) == (True, "execution")


def test_should_notify_not_actionable():
    s = UserSettings(notify_enabled=True, notify_url="http://x", notify_on_execution=True)
    assert asyncio.run(
        notify.should_notify(_dash(execution_today=True, buy_amount=0), s)
    ) == (False, "")


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n=None):
        return self._docs[:n] if n else self._docs


class _Signals:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def find(self, query: dict):
        lt = query["date"]["$lt"]
        matched = sorted(
            (d for d in self.docs if d["date"] < lt),
            key=lambda d: d["date"],
            reverse=True,
        )
        return _Cursor(matched)


def test_should_notify_signal_change_uses_latest_prior_day(monkeypatch):
    s = UserSettings(
        notify_enabled=True,
        notify_url="http://x",
        notify_on_execution=False,
        notify_on_signal_change=True,
    )
    docs = [
        {"date": "2026-08-01", "items": [{"symbol": "HS300", "action": "buy"}]},
        {"date": "2026-08-13", "items": [{"symbol": "HS300", "action": "pause"}]},
    ]
    monkeypatch.setattr(notify, "get_db", lambda: type("DB", (), {"signals_daily": _Signals(docs)})())
    assert asyncio.run(
        notify.should_notify(_dash(execution_today=False, buy_amount=100), s)
    ) == (True, "signal_change")

    docs[-1]["items"][0]["action"] = "buy"
    assert asyncio.run(
        notify.should_notify(_dash(execution_today=False, buy_amount=100), s)
    ) == (False, "")


def test_build_payload_shape():
    payload = notify.build_payload(_dash(execution_today=True, buy_amount=100))
    assert payload["title"] == "定投执行日信号"
    assert payload["total_buy_amount"] == 100
    assert payload["buys"] == [{"symbol": "HS300", "name": "沪深300", "amount": 100}]


def test_send_ok_and_failure(monkeypatch):
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status: int):
            self.status_code = status

    class FakeClient:
        def __init__(self, **kw):
            self._kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            calls.append({"url": url, "json": json})
            return FakeResponse(200)

    monkeypatch.setattr(notify.httpx, "AsyncClient", FakeClient)
    ok = asyncio.run(notify.send("http://hook", {"a": 1}))
    assert ok is True
    assert calls[0]["url"] == "http://hook"
    assert calls[0]["json"] == {"a": 1}

    class FakeErrorClient(FakeClient):
        async def post(self, url, json):
            raise RuntimeError("boom")

    monkeypatch.setattr(notify.httpx, "AsyncClient", FakeErrorClient)
    ok = asyncio.run(notify.send("http://hook", {"a": 1}))
    assert ok is False
