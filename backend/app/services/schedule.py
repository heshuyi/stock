"""DCA schedule: execution-day detection and period amount from monthly budget."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from app.models import BuyFrequency


def _as_dates(trading_days: Iterable[str | date]) -> list[date]:
    out: list[date] = []
    for d in trading_days:
        if isinstance(d, date):
            out.append(d)
        else:
            out.append(date.fromisoformat(str(d)[:10]))
    return sorted(set(out))


def extend_calendar(
    trading_days: Iterable[str | date],
    *,
    until: date,
    from_date: date | None = None,
) -> list[date]:
    """Merge warehouse dates with Mon–Fri placeholders beyond last known bar.

    Bars are T-1, so "today" is often absent from the warehouse; weekdays after
    the last bar are treated as provisional trading days until holidays appear.
    """
    days = _as_dates(trading_days)
    start = from_date or (days[-1] + timedelta(days=1) if days else date.today())
    cursor = start
    known = set(days)
    while cursor <= until:
        if cursor.isoweekday() <= 5 and cursor not in known:
            days.append(cursor)
        cursor += timedelta(days=1)
    return sorted(set(days))


def trading_days_in_month(trading_days: list[date], year: int, month: int) -> list[date]:
    return [d for d in trading_days if d.year == year and d.month == month]


def weeks_with_trading_in_month(trading_days: list[date], year: int, month: int) -> int:
    """Count ISO weeks in the month that contain at least one trading day."""
    month_days = trading_days_in_month(trading_days, year, month)
    weeks = {(d.isocalendar()[0], d.isocalendar()[1]) for d in month_days}
    return max(len(weeks), 1) if month_days else 1


def period_amount(
    base_amount: float,
    frequency: BuyFrequency,
    *,
    year: int,
    month: int,
    trading_days: Iterable[str | date],
) -> float:
    """Convert monthly budget into per-period baseline amount."""
    days = _as_dates(trading_days)
    if frequency == "monthly":
        return round(float(base_amount), 2)
    if frequency == "daily":
        n = len(trading_days_in_month(days, year, month)) or 20
        return round(float(base_amount) / n, 2)
    # weekly
    w = weeks_with_trading_in_month(days, year, month)
    return round(float(base_amount) / w, 2)


def resolve_weekly_execution(
    today: date,
    weekly_weekday: int,
    trading_days: list[date],
) -> date | None:
    """Target weekday this ISO week; roll forward within the week if holiday."""
    wd = max(1, min(5, int(weekly_weekday)))
    # Monday=1 … Sunday=7 in date.isoweekday()
    week_monday = today - timedelta(days=today.isoweekday() - 1)
    target = week_monday + timedelta(days=wd - 1)
    week_friday = week_monday + timedelta(days=4)
    trade_set = set(trading_days)
    cursor = target
    while cursor <= week_friday:
        if cursor in trade_set:
            return cursor
        cursor += timedelta(days=1)
    return None


def resolve_monthly_execution(
    today: date,
    monthly_day: int,
    trading_days: list[date],
) -> date | None:
    """Target calendar day this month; roll forward; else next month first trade."""
    day = max(1, min(28, int(monthly_day)))
    trade_set = set(trading_days)
    try:
        target = date(today.year, today.month, day)
    except ValueError:
        target = date(today.year, today.month, 28)

    # Remaining trading days in this month on/after target
    for d in trading_days:
        if d.year == today.year and d.month == today.month and d >= target:
            if d in trade_set:
                return d
    # Next month first trading day
    if today.month == 12:
        ny, nm = today.year + 1, 1
    else:
        ny, nm = today.year, today.month + 1
    for d in trading_days:
        if d.year == ny and d.month == nm:
            return d
    return None


def is_execution_day(
    today: date,
    frequency: BuyFrequency,
    *,
    weekly_weekday: int = 1,
    monthly_day: int = 1,
    trading_days: Iterable[str | date],
) -> bool:
    days = _as_dates(trading_days)
    trade_set = set(days)
    if today not in trade_set:
        return False
    if frequency == "daily":
        return True
    if frequency == "weekly":
        resolved = resolve_weekly_execution(today, weekly_weekday, days)
        return resolved == today
    resolved = resolve_monthly_execution(today, monthly_day, days)
    return resolved == today


def next_execution_date(
    today: date,
    frequency: BuyFrequency,
    *,
    weekly_weekday: int = 1,
    monthly_day: int = 1,
    trading_days: Iterable[str | date],
    look_ahead_days: int = 400,
) -> str | None:
    days = _as_dates(trading_days)
    if not days:
        return None
    end = today + timedelta(days=look_ahead_days)
    # Search chronologically among known trading days
    for d in days:
        if d < today:
            continue
        if d > end:
            break
        if is_execution_day(
            d,
            frequency,
            weekly_weekday=weekly_weekday,
            monthly_day=monthly_day,
            trading_days=days,
        ):
            return d.isoformat()
    return None
