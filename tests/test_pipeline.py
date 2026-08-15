"""Tests for pipeline.py's capacity-pessimism adjustments."""
from datetime import date, time
from unittest.mock import patch

import routing.pipeline as pipeline_module
import routing.stage4_claim as stage4_claim_module
from routing.capacity import CapacityLedger
from routing.models import CarrierRate, Category, FC, LineItem, Order, ServiceLevel, World
from routing.pipeline import run_pipeline
from routing.shipping_calendar import NOW
from routing.stage2_feasibility import InventoryLedger
from routing.strategies import build_options


def _world():
    fcs = {
        "FC-X": FC("FC-X", time(23, 0), handling_days=0, capacity_units_per_day=5),
        "FC-Y": FC("FC-Y", time(23, 0), handling_days=0, capacity_units_per_day=50),
    }
    carrier_rates = {
        "FC-X": {ServiceLevel.GROUND: CarrierRate("FC-X", ServiceLevel.GROUND, 1.0, 1.0, 1)},
        "FC-Y": {ServiceLevel.GROUND: CarrierRate("FC-Y", ServiceLevel.GROUND, 1.0, 1.0, 1)},
    }
    inventory = InventoryLedger({("FC-X", "SKU-X"): 100, ("FC-Y", "SKU-Y"): 100})
    capacity = CapacityLedger(fcs)
    unit_prices = {"SKU-X": 10.0, "SKU-Y": 10.0}
    return World(fcs, carrier_rates, unit_prices, inventory, capacity, now=NOW)


def test_sole_split_option_is_not_worst_cased_into_hopeless():
    world = _world()
    drain = Order("DRAIN", date(2026, 8, 25), ServiceLevel.STANDARD, (LineItem("SKU-X", 4),), 0)
    split_only = Order(
        "SPLIT-ONLY", date(2026, 8, 25), ServiceLevel.STANDARD,
        (LineItem("SKU-X", 2), LineItem("SKU-Y", 2)), 1,
    )

    result = run_pipeline([drain, split_only], world)

    assert "SPLIT-ONLY" in result.contention.contested_order_ids
    assignment = result.assignments["SPLIT-ONLY"]
    assert assignment.category == Category.AT_RISK
    assert not assignment.escalated


def _three_way_contested_world():
    """FC-X's daily capacity (1 unit) can only satisfy one of three orders
    that all want it as their cheap on-time route; FC-Y is an ample,
    always-on-time fallback for everyone. All three land in the same
    contested group."""
    fcs = {
        "FC-X": FC("FC-X", time(23, 0), handling_days=0, capacity_units_per_day=1),
        "FC-Y": FC("FC-Y", time(23, 0), handling_days=0, capacity_units_per_day=50),
    }
    carrier_rates = {
        "FC-X": {ServiceLevel.GROUND: CarrierRate("FC-X", ServiceLevel.GROUND, 1.0, 1.0, 1)},
        "FC-Y": {ServiceLevel.GROUND: CarrierRate("FC-Y", ServiceLevel.GROUND, 1.0, 5.0, 1)},
    }
    inventory = InventoryLedger({("FC-X", "SKU-Z"): 100, ("FC-Y", "SKU-Z"): 100})
    capacity = CapacityLedger(fcs)
    unit_prices = {"SKU-Z": 10.0}
    return World(fcs, carrier_rates, unit_prices, inventory, capacity, now=NOW)


def test_contested_claim_loop_rebuilds_options_live_before_every_pick():
    """Stage 3's sort/category snapshot must be recomputed against current
    world state before each contested pick, not taken once up front --
    otherwise a later order's category can be stale by the time its turn
    comes (see research.md / README "sequencing-quality" discussion).

    Proof of that liveness: with N contested orders, a one-shot sort calls
    build_options N times to build the snapshot, then N more times inside
    claim_order's live re-check = 2N total. An incremental resort that
    re-snapshots the shrinking remaining set before every pick calls it
    N + (N-1) + ... + 1 times for ranking, plus N more inside claim_order =
    N*(N+3)/2 total. For N=3 that's 6 vs 9 -- a call count only the
    incremental version can produce."""
    world = _three_way_contested_world()
    orders = [
        Order("A", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("SKU-Z", 1),), 0),
        Order("B", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("SKU-Z", 1),), 1),
        Order("C", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("SKU-Z", 1),), 2),
    ]

    calls = {"count": 0}

    def _counting_build_options(order, world):
        calls["count"] += 1
        return build_options(order, world)

    with patch.object(pipeline_module, "build_options", side_effect=_counting_build_options), \
         patch.object(stage4_claim_module, "build_options", side_effect=_counting_build_options):
        result = run_pipeline(orders, world)

    assert {"A", "B", "C"} <= result.contention.contested_order_ids
    assert calls["count"] == 9, (
        f"expected 9 build_options calls (incremental resort), got {calls['count']} "
        "(6 would mean the sort is still a one-shot snapshot)"
    )
