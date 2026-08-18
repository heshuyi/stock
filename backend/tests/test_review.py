"""Tests for signal-history forward returns (R6)."""

from __future__ import annotations

from app.services import review


def _fake_series():
    return [
        {"date": f"2026-01-{d:02d}", "close": 100.0 + d}
        for d in range(1, 30)
    ]


def test_forward_returns_math(monkeypatch):
    class FakeSym:
        def __init__(self, id_):
            self.id = id_

    class FakeCfg:
        symbols = [FakeSym("A"), FakeSym("B")]

    monkeypatch.setattr(review, "load_app_config", lambda: FakeCfg())
    monkeypatch.setattr(
        review.market_store, "load_records", lambda symbol, **kw: _fake_series()
    )

    fwd = review.forward_returns("2026-01-05")
    # base close at 01-05 = 105; +5 sessions → 01-10 → 110
    assert fwd["5"] is not None
    assert abs(fwd["5"] - (110 / 105 - 1)) < 1e-9
    # +20 sessions → 01-25 → 125
    assert abs(fwd["20"] - (125 / 105 - 1)) < 1e-9
    # since → last session 01-29 → 129
    assert abs(fwd["since"] - (129 / 105 - 1)) < 1e-9


def test_forward_returns_missing_date_returns_none(monkeypatch):
    class FakeSym:
        def __init__(self, id_):
            self.id = id_

    class FakeCfg:
        symbols = [FakeSym("A")]

    monkeypatch.setattr(review, "load_app_config", lambda: FakeCfg())
    monkeypatch.setattr(
        review.market_store, "load_records", lambda symbol, **kw: _fake_series()
    )
    fwd = review.forward_returns("1999-01-01")
    assert all(v is None for v in fwd.values())


def test_forward_returns_many_reuses_series(monkeypatch):
    class FakeSym:
        def __init__(self, id_):
            self.id = id_

    class FakeCfg:
        symbols = [FakeSym("A")]

    calls = {"n": 0}

    def _load(symbol, **kw):
        calls["n"] += 1
        return _fake_series()

    monkeypatch.setattr(review, "load_app_config", lambda: FakeCfg())
    monkeypatch.setattr(review.market_store, "load_records", _load)
    out = review.forward_returns_many(["2026-01-05", "2026-01-06"])
    assert calls["n"] == 1
    assert out["2026-01-05"]["5"] is not None
    assert out["2026-01-06"]["5"] is not None
