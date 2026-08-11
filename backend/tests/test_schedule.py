"""Tests for DCA frequency schedule and period amount."""

from datetime import date

from app.services.schedule import (
    execution_calendar,
    extend_calendar,
    is_execution_day,
    is_trading_session,
    next_execution_date,
    period_amount,
    planning_calendar,
    resolve_monthly_execution,
    resolve_weekly_execution,
    weeks_with_trading_in_month,
)


def _july_2026_calendar() -> list[str]:
    """Synthetic A-share-like calendar for July 2026 (weekdays only)."""
    days = []
    for d in range(1, 32):
        dt = date(2026, 7, d)
        if dt.isoweekday() <= 5:
            days.append(dt.isoformat())
    return days


def test_period_amount_monthly():
    cal = _july_2026_calendar()
    assert period_amount(3000, "monthly", year=2026, month=7, trading_days=cal) == 3000


def test_period_amount_daily_splits_by_trading_days():
    cal = _july_2026_calendar()
    n = len([d for d in cal if d.startswith("2026-07")])
    amt = period_amount(3000, "daily", year=2026, month=7, trading_days=cal)
    assert abs(amt - round(3000 / n, 2)) < 1e-9


def test_period_amount_weekly_splits_by_weeks():
    cal = _july_2026_calendar()
    dates = [date.fromisoformat(d) for d in cal]
    w = weeks_with_trading_in_month(dates, 2026, 7)
    amt = period_amount(3000, "weekly", year=2026, month=7, trading_days=cal)
    assert w >= 4
    assert abs(amt - round(3000 / w, 2)) < 1e-9


def test_weekly_rolls_forward_within_week():
    cal = [d for d in _july_2026_calendar() if d != "2026-07-06"]
    days = [date.fromisoformat(x) for x in cal]
    latest = date(2026, 7, 5)
    resolved = resolve_weekly_execution(date(2026, 7, 7), 1, days)
    assert resolved == date(2026, 7, 7)
    assert is_execution_day(
        date(2026, 7, 7),
        "weekly",
        weekly_weekday=1,
        trading_days=days,
        latest_bar=latest,
    )
    assert not is_execution_day(
        date(2026, 7, 8),
        "weekly",
        weekly_weekday=1,
        trading_days=days,
        latest_bar=latest,
    )


def test_monthly_rolls_forward():
    cal = [d for d in _july_2026_calendar() if d != "2026-07-01"]
    days = [date.fromisoformat(x) for x in cal]
    latest = date(2026, 6, 30)
    resolved = resolve_monthly_execution(date(2026, 7, 2), 1, days)
    assert resolved == date(2026, 7, 2)
    assert is_execution_day(
        date(2026, 7, 2),
        "monthly",
        monthly_day=1,
        trading_days=days,
        latest_bar=latest,
    )
    assert not is_execution_day(
        date(2026, 7, 15),
        "monthly",
        monthly_day=1,
        trading_days=days,
        latest_bar=latest,
    )


def test_daily_every_trading_day():
    cal = _july_2026_calendar()
    days = [date.fromisoformat(d) for d in cal]
    latest = date(2026, 7, 2)
    assert is_execution_day(
        date(2026, 7, 3), "daily", trading_days=days, latest_bar=latest
    )
    assert not is_execution_day(
        date(2026, 7, 4), "daily", trading_days=days, latest_bar=latest
    )


def test_execution_calendar_provisional_today():
    wh = ["2026-07-30"]
    today = date(2026, 7, 31)
    cal = execution_calendar(wh, today=today, latest_bar=date(2026, 7, 30))
    assert date(2026, 7, 31) in cal
    assert is_execution_day(
        today, "daily", trading_days=cal, latest_bar=date(2026, 7, 30)
    )


def test_holiday_gap_not_provisional_trading_day():
    """Long break without bars → do not treat today as session."""
    wh = ["2026-09-30"]
    warehouse = {date.fromisoformat(d) for d in wh}
    today = date(2026, 10, 8)
    assert not is_trading_session(
        today,
        warehouse,
        today=today,
        latest_bar=date(2026, 9, 30),
    )


def test_planning_calendar_adds_weekday_beyond_last_bar():
    cal = planning_calendar(
        ["2026-07-30"],
        today=date(2026, 7, 30),
        latest_bar=date(2026, 7, 30),
        until=date(2026, 8, 5),
    )
    assert date(2026, 7, 31) in cal


def test_extend_calendar_legacy_wrapper():
    cal = extend_calendar(["2026-07-30"], until=date(2026, 8, 5))
    assert date(2026, 7, 30) in cal


def test_next_execution_after_monthly_day_passed():
    august = []
    for day in range(1, 32):
        dt = date(2026, 8, day)
        if dt.isoweekday() <= 5:
            august.append(dt.isoformat())
    cal = _july_2026_calendar() + august
    nxt = next_execution_date(
        date(2026, 7, 15),
        "monthly",
        monthly_day=1,
        warehouse_days=cal,
        latest_bar=date(2026, 7, 14),
    )
    assert nxt == "2026-08-03"


def test_next_execution_estimates_weekly_when_future_bar_missing():
    """Next Monday not in warehouse yet → schedule estimate."""
    cal = _july_2026_calendar()
    nxt = next_execution_date(
        date(2026, 7, 10),  # Friday
        "weekly",
        weekly_weekday=1,
        warehouse_days=cal,
        latest_bar=date(2026, 7, 9),
    )
    assert nxt == "2026-07-13"  # next Monday
