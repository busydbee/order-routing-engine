"""Tests for stage2_feasibility.py (feasibility legs) and stage4_claim.py
(atomic claim + live re-check)."""
from datetime import date, datetime, time

import pytest

from routing.capacity import CapacityLedger
from routing.models import CarrierRate, FC, LineItem, ServiceLevel
from routing.stage2_feasibility import (
    InventoryLedger,
    backorder_legs_for_fc,
    fc_can_supply,
    feasible_legs_for_fc,
)


@pytest.fixture
def fc():
    return FC(fc_id="FC-A", cutoff_time=time(23, 0), handling_days=0, capacity_units_per_day=100)


@pytest.fixture
def capacity(fc):
    return CapacityLedger({fc.fc_id: fc})


@pytest.fixture
def rates():
    return {
        ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 6.0, 1.0, 3),
        ServiceLevel.EXPEDITED: CarrierRate("FC-A", ServiceLevel.EXPEDITED, 20.0, 2.0, 1),
    }


def test_fc_can_supply_true_when_stock_sufficient():
    inv = InventoryLedger({("FC-A", "SKU-1"): 5})
    assert fc_can_supply("FC-A", (LineItem("SKU-1", 3),), inv) is True


def test_fc_can_supply_false_when_stock_insufficient():
    inv = InventoryLedger({("FC-A", "SKU-1"): 2})
    assert fc_can_supply("FC-A", (LineItem("SKU-1", 3),), inv) is False


def test_fc_can_supply_false_when_any_item_missing():
    inv = InventoryLedger({("FC-A", "SKU-1"): 5})
    items = (LineItem("SKU-1", 1), LineItem("SKU-2", 1))
    assert fc_can_supply("FC-A", items, inv) is False


def test_feasible_legs_returns_empty_when_out_of_stock(fc, capacity, rates):
    inv = InventoryLedger({("FC-A", "SKU-1"): 0})
    legs = feasible_legs_for_fc(
        fc, (LineItem("SKU-1", 1),), rates, inv, capacity,
        promise_date=date(2026, 8, 20), now=datetime(2026, 8, 12, 9, 0),
    )
    assert legs == []


def test_feasible_legs_collapses_to_cheapest_on_time_service(fc, capacity, rates):
    inv = InventoryLedger({("FC-A", "SKU-1"): 5})
    legs = feasible_legs_for_fc(
        fc, (LineItem("SKU-1", 1),), rates, inv, capacity,
        promise_date=date(2026, 8, 25), now=datetime(2026, 8, 12, 9, 0),
    )
    # Both Ground and Expedited meet a far-out promise; Ground is cheaper -> collapse to 1.
    assert len(legs) == 1
    assert legs[0].service_level == ServiceLevel.GROUND


def test_feasible_legs_keeps_services_distinct_when_none_on_time(fc, capacity, rates):
    inv = InventoryLedger({("FC-A", "SKU-1"): 5})
    legs = feasible_legs_for_fc(
        fc, (LineItem("SKU-1", 1),), rates, inv, capacity,
        promise_date=date(2026, 8, 12), now=datetime(2026, 8, 12, 9, 0),
    )
    # Ground (3 transit days) and Expedited (1 transit day) both miss same-day promise,
    # and differ in days late -> both kept.
    assert len(legs) == 2
    assert {leg.service_level for leg in legs} == {ServiceLevel.GROUND, ServiceLevel.EXPEDITED}


def test_backorder_legs_none_when_no_restock_date(fc, rates):
    inv = InventoryLedger({("FC-A", "SKU-1"): 0})
    legs = backorder_legs_for_fc(fc, (LineItem("SKU-1", 1),), rates, inv, now=datetime(2026, 8, 12, 9, 0))
    assert legs == []


def test_backorder_legs_use_restock_date_as_ship_date(fc, rates):
    inv = InventoryLedger(
        {("FC-A", "SKU-1"): 0},
        restock_dates={("FC-A", "SKU-1"): date(2026, 8, 20)},
    )
    legs = backorder_legs_for_fc(fc, (LineItem("SKU-1", 1),), rates, inv, now=datetime(2026, 8, 12, 9, 0))
    assert len(legs) == 2  # both service levels kept distinct, per the late-branch rule
    ground = next(leg for leg in legs if leg.service_level == ServiceLevel.GROUND)
    assert ground.ship_date == date(2026, 8, 20)


# --- Stage 4: atomic claim + live re-check ---

from routing.models import Order, World  # noqa: E402
from routing.stage4_claim import claim_order  # noqa: E402


@pytest.fixture
def two_fc_world():
    fcs = {
        "FC-A": FC("FC-A", time(23, 0), 0, 100),
        "FC-B": FC("FC-B", time(23, 0), 0, 100),
    }
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 6.0, 1.0, 1)},
        "FC-B": {ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, 20.0, 3.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 1, ("FC-B", "SKU-1"): 5})
    cap = CapacityLedger(fcs)
    return World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))


def test_claim_commits_inventory_and_capacity_together(two_fc_world):
    order = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    assignment = claim_order(order, two_fc_world)
    assert assignment.escalated is False
    assert assignment.chosen_option.legs[0].fc_id == "FC-A"  # cheaper FC, one on-time on-hand unit
    assert two_fc_world.inventory.qty_on_hand("FC-A", "SKU-1") == 0
    assert two_fc_world.capacity.remaining("FC-A", date(2026, 8, 12)) == 99


def test_second_order_live_rechecks_after_first_claims_last_unit(two_fc_world):
    order_1 = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    claim_order(order_1, two_fc_world)  # consumes FC-A's only unit
    assignment_2 = claim_order(order_2, two_fc_world)

    assert assignment_2.escalated is False
    assert assignment_2.chosen_option.legs[0].fc_id == "FC-B"  # live re-check finds FC-A gone, falls back


def test_escalates_when_no_option_within_lateness_bound():
    fcs = {"FC-A": FC("FC-A", time(23, 0), 0, 100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 6.0, 1.0, 20)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 5})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 13), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    assignment = claim_order(order, world)
    assert assignment.escalated is True
    assert assignment.chosen_option is None  # nothing committed to the ledger

    # The rejected option's cost is still priced and surfaced for display --
    # it's a real, already-known cost (the order is already this late) even
    # though ops, not the router, decides what actually happens next.
    rejected = assignment.escalated_option
    assert rejected is not None
    assert rejected.shipping_cost == 7.0  # base_fee 6.0 + per_unit_fee 1.0 * 1 unit
    assert rejected.days_late > 7
    assert rejected.penalty_cost == 5.0  # max($5, 3% of $20 line value)
    assert rejected.late_refund == 2.0  # max(0, shipping 7.00 - penalty 5.00)
    assert rejected.effective_cost == 14.0

    # Nothing was actually committed -- the ledger is untouched.
    assert world.inventory.qty_on_hand("FC-A", "SKU-1") == 5
