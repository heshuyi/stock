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
    """True if `day` is a known session, or today with warehouse already at T-1."""
    if day in warehouse:
        return True
    if day != today or day.weekday() >= 5 or latest_bar is None:
        return False
    if latest_bar < _expected_prev_session(day):
        return False
    if _gap_has_missing_sessions(latest_bar, day, warehouse):
        return False
    return True


def execution_calendar(
    warehouse_days: Iterable[str | date],
    *,
    today: date,
    latest_bar: date | None,
) -> list[date]:
    """Sessions for execution checks and period_amount — warehouse + maybe today."""
    days = _as_dates(warehouse_days)
    known = set(days)
    if is_trading_session(today, known, today=today, latest_bar=latest_bar):
        if today not in known:
            days.append(today)
    return sorted(set(days))


def planning_calendar(
    warehouse_days: Iterable[str | date],
    *,
    today: date,
    latest_bar: date | None,
    until: date,
) -> list[date]:
    """Deprecated: adds naive weekday placeholders. Prefer next_execution_date()."""
    days = execution_calendar(warehouse_days, today=today, latest_bar=latest_bar)
    start = (days[-1] + timedelta(days=1)) if days else today
    known = set(days)
    cursor = max(start, today)
    while cursor <= until:
        if cursor.isoweekday() <= 5 and cursor not in known:
            days.append(cursor)
        cursor += timedelta(days=1)
    return sorted(set(days))


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
    """Next DCA execution: warehouse sessions first, then schedule estimate."""
    wh = _as_dates(warehouse_days if warehouse_days is not None else (trading_days or []))
    if not wh and trading_days:
        wh = _as_dates(trading_days)
    exec_cal = execution_calendar(wh, today=today, latest_bar=latest_bar)
    wh_set = set(wh)
    end = today + timedelta(days=look_ahead_days)

    for d in exec_cal:
        if d < today or d > end:
            continue
        if is_execution_day(
            d,
            frequency,
            weekly_weekday=weekly_weekday,
            monthly_day=monthly_day,
            trading_days=exec_cal,
            latest_bar=latest_bar,
        ):
            return d.isoformat()

    # Estimate: next calendar slot that matches frequency (may precede bar sync).
    cursor = today + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() >= 5:
            cursor += timedelta(days=1)
            continue
        hypo = sorted(set(exec_cal + [cursor]))
        if not _would_be_execution_day(
            cursor,
            frequency,
            weekly_weekday=weekly_weekday,
            monthly_day=monthly_day,
            schedule_days=hypo,
        ):
            cursor += timedelta(days=1)
            continue
        if (
            latest_bar
            and cursor <= today
            and _gap_has_missing_sessions(latest_bar, cursor, wh_set)
        ):
            cursor += timedelta(days=1)
            continue
        return cursor.isoformat()
    return None
