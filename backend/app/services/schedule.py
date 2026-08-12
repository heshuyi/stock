"""DCA schedule: execution-day detection and period amount from monthly budget."""

from __future__ import annotations

from datetime import date, timedelta
from math import floor
from typing import Iterable

import exchange_calendars as xcals

from app.models import BuyFrequency


class TradingCalendarUnavailable(RuntimeError):
    """Raised when the official XSHG calendar cannot serve a requested range."""


def _as_dates(trading_days: Iterable[str | date]) -> list[date]:
    out: list[date] = []
    for d in trading_days:
        if isinstance(d, date):
            out.append(d)
        else:
            out.append(date.fromisoformat(str(d)[:10]))
    return sorted(set(out))


def xshg_sessions(start: date, end: date) -> list[date]:
    """Return official Shanghai sessions; never synthesize weekday sessions."""
    if end < start:
        return []
    try:
        calendar = xcals.get_calendar("XSHG")
        bounded_start = max(start, calendar.first_session.date())
        bounded_end = min(end, calendar.last_session.date())
        if bounded_end < bounded_start:
            return []
        return [
            timestamp.date()
            for timestamp in calendar.sessions_in_range(bounded_start, bounded_end)
        ]
    except Exception as exc:
        raise TradingCalendarUnavailable(
            f"XSHG 交易日历不可用：{start.isoformat()} 至 {end.isoformat()}"
        ) from exc


def _expected_prev_session(day: date) -> date:
    """Last Mon–Fri strictly before `day` (approximate T-1 calendar)."""
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _gap_has_missing_sessions(
    latest_bar: date, today: date, warehouse: set[date]
) -> bool:
    """Any weekday strictly between latest_bar and today without a bar → not open yet."""
    d = latest_bar + timedelta(days=1)
    while d < today:
        if d.weekday() < 5 and d not in warehouse:
            return True
        d += timedelta(days=1)
    return False


def is_trading_session(
    day: date,
    warehouse: set[date],
    *,
    today: date,
    latest_bar: date | None,
) -> bool:
    """True for an official session whose previous close is available."""
    if day not in warehouse:
        return False
    if day != today:
        return True
    if latest_bar is None:
        return False
    previous = max((session for session in warehouse if session < day), default=None)
    return previous is None or latest_bar >= previous


def execution_calendar(
    warehouse_days: Iterable[str | date],
    *,
    today: date,
    latest_bar: date | None,
    until: date | None = None,
) -> list[date]:
    """Official XSHG sessions for execution checks and forward planning."""
    del warehouse_days, latest_bar
    start = date(today.year - 1, 1, 1)
    return xshg_sessions(start, until or today)


def planning_calendar(
    warehouse_days: Iterable[str | date],
    *,
    today: date,
    latest_bar: date | None,
    until: date,
) -> list[date]:
    """Deprecated compatibility wrapper over the official XSHG calendar."""
    return execution_calendar(
        warehouse_days,
        today=today,
        latest_bar=latest_bar,
        until=until,
    )


def _would_be_execution_day(
    day: date,
    frequency: BuyFrequency,
    *,
    weekly_weekday: int,
    monthly_day: int,
    schedule_days: list[date],
) -> bool:
    """If `day` were a trading session, would the DCA schedule fire?"""
    if day.weekday() >= 5:
        return False
    if frequency == "daily":
        return True
    if frequency == "weekly":
        return resolve_weekly_execution(day, weekly_weekday, schedule_days) == day
    return resolve_monthly_execution(day, monthly_day, schedule_days) == day


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
    """Convert a monthly budget into a cent-safe per-period allocation."""
    days = _as_dates(trading_days)
    month_days = trading_days_in_month(days, year, month)
    if not month_days:
        raise TradingCalendarUnavailable(
            f"XSHG 缺少 {year:04d}-{month:02d} 的完整交易日"
        )
    if frequency == "monthly":
        return round(float(base_amount), 2)
    if frequency == "daily":
        periods = len(month_days)
    else:
        periods = weeks_with_trading_in_month(days, year, month)
    if periods <= 0:
        raise TradingCalendarUnavailable(
            f"XSHG 缺少 {year:04d}-{month:02d} 的完整交易日"
        )
    # Floor instead of round so repeated allocations never exceed the budget.
    return floor(float(base_amount) * 100 / periods) / 100


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
    latest_bar: date | None = None,
) -> bool:
    days = _as_dates(trading_days)
    trade_set = set(days)
    if not is_trading_session(
        today, trade_set, today=today, latest_bar=latest_bar
    ):
        return False
    if frequency == "daily":
        return True
    if frequency == "weekly":
        resolved = resolve_weekly_execution(today, weekly_weekday, days)
        return resolved == today
    resolved = resolve_monthly_execution(today, monthly_day, days)
    return resolved == today


def extend_calendar(
    trading_days: Iterable[str | date],
    *,
    until: date,
    from_date: date | None = None,
) -> list[date]:
    """Legacy helper — prefer execution_calendar / planning_calendar."""
    today = date.today()
    latest = _as_dates(trading_days)[-1] if trading_days else None
    return planning_calendar(
        trading_days,
        today=today,
        latest_bar=latest,
        until=until,
    )


def next_execution_date(
    today: date,
    frequency: BuyFrequency,
    *,
    weekly_weekday: int = 1,
    monthly_day: int = 1,
    warehouse_days: Iterable[str | date] | None = None,
    trading_days: Iterable[str | date] | None = None,
    latest_bar: date | None = None,
    look_ahead_days: int = 400,
) -> str | None:
    """Return the next official XSHG execution date, strictly after `today`."""
    wh = _as_dates(warehouse_days if warehouse_days is not None else (trading_days or []))
    end = today + timedelta(days=look_ahead_days)
    exec_cal = execution_calendar(
        wh,
        today=today,
        latest_bar=latest_bar,
        until=end,
    )
    for session in exec_cal:
        if session <= today:
            continue
        if _would_be_execution_day(
            session,
            frequency,
            weekly_weekday=weekly_weekday,
            monthly_day=monthly_day,
            schedule_days=exec_cal,
        ):
            return session.isoformat()
    return None
