"""Tests for routing/strategies.py's backorder-fallback option builder --
needed by the joint ILP arm (routing/optimization_ilp.py), which snapshots
each contested order's options once, up front, instead of live-rechecking
one order at a time the way Stage 4 does."""
from datetime import date, datetime, time

from routing.capacity import CapacityLedger
from routing.models import CarrierRate, FC, LineItem, Order, ServiceLevel, World
from routing.stage2_feasibility import InventoryLedger
from routing.strategies import (
    build_backorder_fallback_options,
    build_consolidated_options,
    build_multi_date_options,
    build_multi_date_split_options,
)


def _world_with_restock():
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 1}, restock_dates={("FC-A", "SKU-1"): date(2026, 8, 25)})
    cap = CapacityLedger(fcs)
    return World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))


def test_backorder_fallback_offered_even_when_fc_currently_has_stock():
    world = _world_with_restock()
    order = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    fresh = build_consolidated_options(order, world)
    fallback = build_backorder_fallback_options(order, world)

    assert len(fresh) == 1
    assert len(fallback) >= 1
    assert all(leg.ship_date == date(2026, 8, 25) for opt in fallback for leg in opt.legs)
    assert fresh[0].legs[0].ship_date < fallback[0].legs[0].ship_date


def test_no_backorder_fallback_when_no_restock_date_known():
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 1})  # no restock date at all
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    assert build_backorder_fallback_options(order, world) == []


def test_no_backorder_fallback_when_fc_is_already_out_of_stock():
    """build_options already covers the out-of-stock case via the normal
    backorder branch in all_candidate_legs_for_fc -- this function only
    adds the *extra* hedge for FCs that currently look available."""
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 0}, restock_dates={("FC-A", "SKU-1"): date(2026, 8, 25)})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    assert build_backorder_fallback_options(order, world) == []


def test_multi_date_options_offers_several_distinct_ship_dates():
    """Unlike feasible_legs_for_fc's single greedy 'earliest available'
    date, the ILP's joint solve needs every plausible ship date at a FC
    exposed as its own choice so it can pick 'ship a day later at the same
    FC' when today's date is already claimed by another order in the same
    batch."""
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    options = build_multi_date_options(order, world)
    ship_dates = sorted({leg.ship_date for opt in options for leg in opt.legs})

    assert len(ship_dates) > 1, "expected several distinct ship-date choices, not just the single earliest one"
    assert ship_dates[0] == date(2026, 8, 12)


def test_multi_date_options_skips_dates_with_no_remaining_capacity():
    """A date already fully claimed by an earlier (uncontested) commit has
    nothing left to offer -- it should be pruned, not offered as a
    zero-capacity choice the solver has to reject at solve time."""
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=1)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    world.capacity.commit("FC-A", date(2026, 8, 12), 1)  # baseline date fully claimed already
    order = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    options = build_multi_date_options(order, world)
    ship_dates = {leg.ship_date for opt in options for leg in opt.legs}

    assert date(2026, 8, 12) not in ship_dates
    assert date(2026, 8, 13) in ship_dates


def test_multi_date_options_skips_fcs_out_of_stock():
    fcs = {"FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=100)}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 0})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    assert build_multi_date_options(order, world) == []


def _world_two_fc_split():
    fcs = {
        "FC-A": FC("FC-A", time(23, 0), handling_days=0, capacity_units_per_day=1),
        "FC-B": FC("FC-B", time(23, 0), handling_days=0, capacity_units_per_day=1),
    }
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
        "FC-B": {ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, 8.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10, ("FC-B", "SKU-2"): 10})
    cap = CapacityLedger(fcs)
    return World(fcs, carrier_rates, {"SKU-1": 20.0, "SKU-2": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))


def test_multi_date_split_options_shifts_one_leg_at_a_time():
    """Each merged leg of the base Split gets its own date-shift variants,
    holding the other leg(s) fixed -- covers 'just the crowded leg slides
    a day' without a full cross-product search across every leg."""
    world = _world_two_fc_split()
    order = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1), LineItem("SKU-2", 1)), 0)

    variants = build_multi_date_split_options(order, world)

    assert variants, "expected at least one Split date-shift variant"
    fc_a_dates = sorted({
        leg.ship_date for opt in variants for leg in opt.legs if leg.fc_id == "FC-A"
    })
    fc_b_dates = sorted({
        leg.ship_date for opt in variants for leg in opt.legs if leg.fc_id == "FC-B"
    })
    assert len(fc_a_dates) > 1, "expected several ship-date choices for the FC-A leg"
    assert len(fc_b_dates) > 1, "expected several ship-date choices for the FC-B leg"
    for opt in variants:
        assert len(opt.legs) == 2, "the other leg must stay put while one leg's date shifts"


def test_multi_date_split_options_empty_for_single_item_order():
    world = _world_two_fc_split()
    order = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    assert build_multi_date_split_options(order, world) == []
