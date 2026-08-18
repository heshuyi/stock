"""Integration test for the position-reconciliation API (R2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_trade_api_roundtrip(monkeypatch, tmp_path):
    from app.services import user_state
    import app.db as dbmod

    # isolate from the real on-disk snapshot + get a fresh in-memory mongo
    state_file = tmp_path / "user_state.json"
    monkeypatch.setattr(user_state, "user_state_path", lambda: state_file)
    dbmod._client = None

    with TestClient(app) as client:
        # deposit
        r = client.post(
            "/api/portfolio/trades",
            json={"symbol": "HS300", "kind": "deposit", "amount": 5000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["portfolio"]["cash"] == 5000
        assert len(body["portfolio"]["trades"]) == 1

        # buy draws from tracked cash
        r = client.post(
            "/api/portfolio/trades",
            json={"symbol": "HS300", "kind": "buy", "amount": 400, "price": 4.0},
        )
        assert r.status_code == 200
        h = next(
            x for x in r.json()["portfolio"]["holdings"] if x["symbol"] == "HS300"
        )
        assert h["shares"] == 100.0
        assert h["cost_price"] == 4.0
        assert r.json()["portfolio"]["cash"] == 4600

        # dividend (cash) — total-return bookkeeping
        r = client.post(
            "/api/portfolio/trades",
            json={"symbol": "HS300", "kind": "dividend", "amount": 30},
        )
        assert r.status_code == 200
        h = next(
            x for x in r.json()["portfolio"]["holdings"] if x["symbol"] == "HS300"
        )
        assert h["dividends_received"] == 30
        assert r.json()["portfolio"]["cash"] == 4630

        # ledger error surfaces as 422
        r = client.post(
            "/api/portfolio/trades",
            json={"symbol": "HS300", "kind": "buy", "amount": 999999, "price": 4.0},
        )
        assert r.status_code == 422
        assert "可支配储备不足" in r.json()["detail"]
