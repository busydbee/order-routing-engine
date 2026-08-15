"""EDD arithmetic: cutoff and capacity -> earliest ship date, business-day
transit."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from routing.capacity import CapacityLedger
from routing.models import FC

# Fixed for determinism; a Wednesday so at least one FC's cutoff has passed.
NOW = datetime(2026, 8, 12, 14, 0)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5  # Mon=0 ... Sun=6


def next_business_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_business_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def add_business_days(d: date, n: int) -> date:
    result = d
    for _ in range(n):
        result = next_business_day(result)
    return result


def cutoff_and_handling_baseline(fc: FC, now: datetime) -> date:
    """Earliest ship date given only cutoff and handling days -- ignores
    capacity entirely. Shared by earliest_ship_date's single greedy date
    and routing/strategies.py's build_multi_date_options, which enumerates
    a window of dates starting here instead of picking just one."""
    cutoff_date = now.date() if now.time() < fc.cutoff_time else next_business_day(now.date())
    if not is_business_day(cutoff_date):
        cutoff_date = next_business_day(cutoff_date)
    return add_business_days(cutoff_date, fc.handling_days)


def earliest_ship_date(
    fc: FC,
    capacity_ledger: CapacityLedger,
    units_needed: int,
    now: datetime = NOW,
) -> tuple[date, bool]:
    """Returns (ship_date, capacity_threatened); the flag is True only
    when capacity pushed the date past the cutoff-and-handling baseline."""
    candidate = cutoff_and_handling_baseline(fc, now)

    capacity_threatened = False
    while capacity_ledger.remaining(fc.fc_id, candidate) < units_needed:
        candidate = next_business_day(candidate)
        capacity_threatened = True

    return candidate, capacity_threatened


def compute_eta(ship_date: date, transit_business_days: int) -> date:
    return add_business_days(ship_date, transit_business_days)
