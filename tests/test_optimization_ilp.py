"""Tests for routing/optimization_ilp.py -- the Hybrid ILP comparison arm:
the contested subset is solved as one joint mixed-integer program instead
of Stage 3's risk-sorted claim queue. Uncontested orders still take the
same fast path as the pipeline."""
from datetime import date, datetime, time

from routing.capacity import CapacityLedger
from routing.models import CarrierRate, FC, LineItem, Order, ServiceLevel, World
from routing.optimization_ilp import run_ilp_contention
from routing.stage2_feasibility import InventoryLedger


def _fc(fc_id: str, cap: int = 100) -> FC:
    return FC(fc_id, time(23, 0), handling_days=0, capacity_units_per_day=cap)


def test_uncontested_order_takes_same_fast_path_as_pipeline():
    fcs = {"FC-A": _fc("FC-A")}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)

    result = run_ilp_contention([order], world)

    assignment = result.assignments["O1"]
    assert assignment.contested is False
    assert assignment.escalated is False
    assert assignment.chosen_option.legs[0].fc_id == "FC-A"
    assert world.inventory.qty_on_hand("FC-A", "SKU-1") == 9


def test_last_unit_contention_winner_gets_it_loser_escalates():
    fcs = {"FC-A": _fc("FC-A")}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 1})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order_1 = Order("O1", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 20), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_1, order_2], world)

    a1, a2 = result.assignments["O1"], result.assignments["O2"]
    winner, loser = (a1, a2) if a1.chosen_option is not None else (a2, a1)

    assert winner.chosen_option.legs[0].fc_id == "FC-A"
    assert winner.chosen_option.on_time is True
    assert winner.escalated is False
    assert loser.chosen_option is None
    assert loser.escalated is True
    assert a1.contested is True
    assert a2.contested is True


def test_ilp_prefers_on_time_expensive_option_over_cheaper_late_option():
    """Lexicographic priority: even inside the joint solve, an on-time
    option must beat a cheaper late option -- LATE_PENALTY dominates any
    realistic cost delta in this dataset."""
    fcs = {"FC-A": _fc("FC-A"), "FC-B": _fc("FC-B"), "FC-C": _fc("FC-C")}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 1.0, 1.0, 1)},
        "FC-B": {ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, 2.0, 1.0, 5)},
        "FC-C": {ServiceLevel.EXPEDITED: CarrierRate("FC-C", ServiceLevel.EXPEDITED, 50.0, 5.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 1, ("FC-B", "SKU-1"): 100, ("FC-C", "SKU-1"): 100})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 14, 0))
    order_x = Order("OX", date(2026, 8, 13), ServiceLevel.STANDARD, (LineItem("SKU-1", 1),), 0)
    order_y = Order("OY", date(2026, 8, 13), ServiceLevel.STANDARD, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_x, order_y], world)

    loser = next(
        a for a in result.assignments.values()
        if not (a.chosen_option and a.chosen_option.legs[0].fc_id == "FC-A")
    )
    assert loser.chosen_option is not None
    assert loser.chosen_option.legs[0].fc_id == "FC-C"  # not FC-B, despite FC-B's much lower effective cost
    assert loser.chosen_option.on_time is True


def test_loser_of_last_unit_race_gets_backorder_fallback_not_escalation():
    """When a restock date exists, the losing order's backorder option must
    not be blocked by the *current* on-hand quantity -- it draws from a
    different (future) supply pool, same as the sequential arms assume."""
    fcs = {"FC-A": _fc("FC-A")}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger(
        {("FC-A", "SKU-1"): 1},
        restock_dates={("FC-A", "SKU-1"): date(2026, 8, 20)},
    )
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order_1 = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_1, order_2], world)

    for assignment in result.assignments.values():
        assert assignment.chosen_option is not None
        assert assignment.escalated is False


def test_escalated_order_still_surfaces_its_priced_but_rejected_option():
    """The joint solve prices every option before deciding an order can't be
    routed within the lateness bound -- that priced-but-rejected option
    should be kept for display, the same as the sequential arm does, even
    though nothing is committed to the ledger for it."""
    fcs = {"FC-A": _fc("FC-A")}
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
    }
    inv = InventoryLedger(
        {("FC-A", "SKU-1"): 1},
        restock_dates={("FC-A", "SKU-1"): date(2026, 9, 15)},  # far past the lateness bound
    )
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order_1 = Order("O1", date(2026, 8, 13), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 13), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_1, order_2], world)

    a1, a2 = result.assignments["O1"], result.assignments["O2"]
    winner, loser = (a1, a2) if a1.chosen_option is not None else (a2, a1)

    assert winner.escalated is False
    assert loser.escalated is True
    assert loser.chosen_option is None  # nothing committed to the ledger

    rejected = loser.escalated_option
    assert rejected is not None
    assert rejected.days_late > 7
    assert rejected.effective_cost > 0


def test_full_batch_every_order_gets_a_final_outcome():
    from data.orders import ORDERS
    from data.world import build_world

    world = build_world()
    result = run_ilp_contention(ORDERS, world)

    assert set(result.assignments) == {o.order_id for o in ORDERS}
    for assignment in result.assignments.values():
        assert assignment.escalated or assignment.chosen_option is not None
    assert result.solver_status == "Optimal"


def test_ilp_does_not_over_apply_sequential_pessimism_when_joint_capacity_suffices():
    """resolve_capacity_threats/worst_case_adjust_multi_leg_options exist to
    de-risk Stage 3's *sequential* sort/claim, which can't see the whole
    batch at once. The ILP's resource constraints already see the whole
    batch exactly, so applying that same pessimistic pre-filter only strips
    a cheaper on-time option the joint solve could have safely given to one
    of the two orders."""
    fcs = {
        "FC-A": _fc("FC-A", cap=1),
        "FC-B": _fc("FC-B", cap=100),
    }
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
        "FC-B": {ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, 20.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10, ("FC-B", "SKU-1"): 10})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order_1 = Order("O1", date(2026, 8, 13), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 13), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_1, order_2], world)

    for assignment in result.assignments.values():
        assert assignment.chosen_option is not None
        assert assignment.chosen_option.on_time

    fc_a_users = [
        a for a in result.assignments.values()
        if any(leg.fc_id == "FC-A" for leg in a.chosen_option.legs)
    ]
    assert len(fc_a_users) == 1, "the joint solve should let exactly one order use FC-A's 1 unit of scarce capacity"


def test_ilp_ships_a_day_later_at_the_cheap_fc_instead_of_jumping_to_a_pricier_fc():
    """Reproduces the fixture-batch finding: when three orders all want the
    same scarce FC on the same day, the sequential arm's live re-check
    naturally slides the overflow to 'tomorrow at the same FC' (cheap).
    Without build_multi_date_options, the ILP's snapshot only ever offers
    that one date, so the overflow looked FC-A-infeasible and got routed
    to the pricier FC-B instead -- this is the bug that fix closes."""
    fcs = {
        "FC-A": _fc("FC-A", cap=1),
        "FC-B": _fc("FC-B", cap=100),
    }
    carrier_rates = {
        "FC-A": {ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, 5.0, 1.0, 1)},
        "FC-B": {ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, 20.0, 1.0, 1)},
    }
    inv = InventoryLedger({("FC-A", "SKU-1"): 10, ("FC-B", "SKU-1"): 10})
    cap = CapacityLedger(fcs)
    world = World(fcs, carrier_rates, {"SKU-1": 20.0}, inv, cap, datetime(2026, 8, 12, 9, 0))
    order_1 = Order("O1", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 0)
    order_2 = Order("O2", date(2026, 8, 25), ServiceLevel.GROUND, (LineItem("SKU-1", 1),), 1)

    result = run_ilp_contention([order_1, order_2], world)

    for assignment in result.assignments.values():
        assert assignment.chosen_option is not None
        assert assignment.chosen_option.on_time

    fc_b_users = [
        a for a in result.assignments.values()
        if any(leg.fc_id == "FC-B" for leg in a.chosen_option.legs)
    ]
    assert fc_b_users == [], "both orders should ship from FC-A (today + tomorrow) instead of one paying FC-B's premium"


def test_committed_units_never_oversell_inventory():
    """SKUs with no restock date have no legitimate backorder pool, so
    current on-hand stock must never go negative. SKUs *with* a restock
    date are exempt: the sequential arms treat restocked supply as
    unconstrained too (backorder_legs_for_fc has no qty check), so a
    backorder commit legitimately drives on-hand below zero there as well
    -- that's a pre-existing model limitation shared by every arm, not
    something this ILP-specific test should police."""
    from data.orders import ORDERS
    from data.world import build_world

    world = build_world()
    run_ilp_contention(ORDERS, world)

    skus = {li.sku for order in ORDERS for li in order.line_items}
    for fc_id in world.fcs:
        for sku in skus:
            if world.inventory.restock_date(fc_id, sku) is not None:
                continue
            assert world.inventory.qty_on_hand(fc_id, sku) >= 0
