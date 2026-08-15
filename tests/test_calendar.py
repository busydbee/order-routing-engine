"""Tests for shipping_calendar.py and capacity.py."""
from datetime import date, datetime, time

import pytest

from routing.capacity import CapacityLedger
from routing.models import FC
from routing.shipping_calendar import (
    add_business_days,
    earliest_ship_date,
    is_business_day,
    next_business_day,
)


@pytest.fixture
def fc():
    # Wednesday cutoff at noon, 0 handling days, plenty of capacity.
    return FC(fc_id="FC-A", cutoff_time=time(12, 0), handling_days=0, capacity_units_per_day=100)


@pytest.fixture
def ledger(fc):
    return CapacityLedger({fc.fc_id: fc})


def test_is_business_day():
    assert is_business_day(date(2026, 8, 12))  # Wednesday
    assert not is_business_day(date(2026, 8, 15))  # Saturday
    assert not is_business_day(date(2026, 8, 16))  # Sunday


def test_next_business_day_skips_weekend():
    assert next_business_day(date(2026, 8, 14)) == date(2026, 8, 17)  # Fri -> Mon
    assert next_business_day(date(2026, 8, 15)) == date(2026, 8, 17)  # Sat -> Mon
    assert next_business_day(date(2026, 8, 12)) == date(2026, 8, 13)  # Wed -> Thu


def test_add_business_days_crosses_weekend():
    # Thursday + 3 business days = Tue (Fri, Mon, Tue)
    assert add_business_days(date(2026, 8, 13), 3) == date(2026, 8, 18)


def test_cutoff_before_ships_today(fc, ledger):
    now = datetime(2026, 8, 12, 9, 0)  # before noon cutoff
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 12)
    assert threatened is False


def test_cutoff_after_ships_next_business_day(fc, ledger):
    now = datetime(2026, 8, 12, 15, 0)  # after noon cutoff
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 13)
    assert threatened is False


def test_cutoff_passed_friday_afternoon_ships_monday(fc, ledger):
    now = datetime(2026, 8, 14, 15, 0)  # Friday, after cutoff
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 17)  # Monday
    assert threatened is False


def test_handling_days_added_after_cutoff(ledger):
    fc = FC(fc_id="FC-A", cutoff_time=time(12, 0), handling_days=2, capacity_units_per_day=100)
    ledger = CapacityLedger({fc.fc_id: fc})
    now = datetime(2026, 8, 12, 9, 0)  # ships today per cutoff, then +2 handling business days
    ship_date, _ = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 14)  # Wed + 2 business days = Fri


def test_capacity_full_pushes_to_next_business_day(fc, ledger):
    now = datetime(2026, 8, 12, 9, 0)
    ledger.commit(fc.fc_id, date(2026, 8, 12), 100)  # fill today's capacity
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 13)
    assert threatened is True


def test_capacity_full_two_days_cascades_to_day_plus_2(fc, ledger):
    now = datetime(2026, 8, 12, 9, 0)
    ledger.commit(fc.fc_id, date(2026, 8, 12), 100)
    ledger.commit(fc.fc_id, date(2026, 8, 13), 100)
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=1, now=now)
    assert ship_date == date(2026, 8, 14)
    assert threatened is True


def test_capacity_partial_remaining_is_sufficient(fc, ledger):
    now = datetime(2026, 8, 12, 9, 0)
    ledger.commit(fc.fc_id, date(2026, 8, 12), 95)  # 5 units left
    ship_date, threatened = earliest_ship_date(fc, ledger, units_needed=5, now=now)
    assert ship_date == date(2026, 8, 12)
    assert threatened is False


def test_capacity_ledger_remaining_defaults_to_full():
    fc = FC(fc_id="FC-B", cutoff_time=time(12, 0), handling_days=0, capacity_units_per_day=50)
    ledger = CapacityLedger({fc.fc_id: fc})
    assert ledger.remaining(fc.fc_id, date(2026, 8, 12)) == 50
