"""Tests for DCA frequency schedule and period amount."""

from datetime import date

from app.services.schedule import (
    extend_calendar,
    is_execution_day,
    next_execution_date,
    period_amount,
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
    # Monday 2026-07-06 holiday; Tuesday is first trade of week target Mon.
    cal = [d for d in _july_2026_calendar() if d != "2026-07-06"]
    resolved = resolve_weekly_execution(date(2026, 7, 7), 1, [date.fromisoformat(x) for x in cal])
    assert resolved == date(2026, 7, 7)
    assert is_execution_day(
        date(2026, 7, 7), "weekly", weekly_weekday=1, trading_days=cal
    )
    assert not is_execution_day(
        date(2026, 7, 8), "weekly", weekly_weekday=1, trading_days=cal
    )


def test_monthly_rolls_forward():
    # Target day 1 is holiday → next trading day
    cal = [d for d in _july_2026_calendar() if d != "2026-07-01"]
    # 2026-07-01 is Wednesday; remove it → should be 2026-07-02
    resolved = resolve_monthly_execution(
        date(2026, 7, 2), 1, [date.fromisoformat(x) for x in cal]
    )
    assert resolved == date(2026, 7, 2)
    assert is_execution_day(
        date(2026, 7, 2), "monthly", monthly_day=1, trading_days=cal
    )
    assert not is_execution_day(
        date(2026, 7, 15), "monthly", monthly_day=1, trading_days=cal
    )


def test_daily_every_trading_day():
    cal = _july_2026_calendar()
    assert is_execution_day(date(2026, 7, 3), "daily", trading_days=cal)
    assert not is_execution_day(date(2026, 7, 4), "daily", trading_days=cal)  # Sat


def test_extend_calendar_adds_weekday_beyond_last_bar():
    cal = extend_calendar(
        ["2026-07-30"],
        until=date(2026, 7, 31),
        from_date=date(2026, 7, 31),
    )
    assert date(2026, 7, 31) in cal  # Friday provisional
    assert is_execution_day(date(2026, 7, 31), "daily", trading_days=cal)


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
        trading_days=cal,
    )
    assert nxt == "2026-08-03"  # Aug 1 Sat → Aug 3 Mon
