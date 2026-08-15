"""End-to-end smoke test against the 14-order fabricated batch. See the
plan's "Fabricated scenario" section for what each order proves."""
from routing.models import Category
from routing.pipeline import run_pipeline

from data.orders import ORDERS
from data.world import build_world


def test_fourteen_orders_categorized_and_claimed_as_designed():
    world = build_world()
    result = run_pipeline(ORDERS, world)

    assignments = result.assignments
    assert set(assignments) == {o.order_id for o in ORDERS}

    # O1/O2: last-unit inventory contention.
    assert assignments["O1"].chosen_option.on_time
    assert not assignments["O2"].chosen_option.on_time
    assert not assignments["O2"].escalated

    # O3: stockout fallback makes Split the cheapest true claim. Category
    # is SAFE, not AT_RISK -- the incremental re-sort (pipeline.py) rebuilds
    # O3's options live, after O1/O2's claims, so by the time O3 is
    # evaluated the contention that would have made it AT_RISK is already
    # resolved.
    assert assignments["O3"].category == Category.SAFE
    assert assignments["O3"].chosen_option.strategy.value == "split"
    assert assignments["O3"].chosen_option.on_time

    # O4: survives the capacity-slip pessimism check.
    assert assignments["O4"].category == Category.SAFE
    assert assignments["O4"].chosen_option.on_time

    # O5: fails the capacity-slip pessimism check, rerouted to FC-C. Category
    # is SAFE, not AT_RISK, for the same live-re-sort reason as O3.
    assert assignments["O5"].category == Category.SAFE
    assert assignments["O5"].chosen_option.on_time
    assert {leg.fc_id for leg in assignments["O5"].chosen_option.legs} == {"FC-C"}

    # O6/O7: wholly uncontested, fast path, no category assigned. O6's
    # customer is 2 zones from FC-C, so its cost is zone-adjusted; O7's
    # customer is 1 zone from FC-B (adjacent), so its cost is not.
    assert assignments["O6"].category is None
    assert assignments["O6"].chosen_option.on_time
    assert assignments["O6"].chosen_option.shipping_cost == 15.4  # 6.0 + (1.2 + 3.5) * 2 units
    assert assignments["O7"].category is None
    assert assignments["O7"].chosen_option.on_time
    assert assignments["O7"].chosen_option.shipping_cost == 13.0  # unaffected: 1 zone away is still free

    # O8/O9: risk-first vs. date-first disagreement.
    assert assignments["O8"].category == Category.SAFE
    assert assignments["O9"].category == Category.AT_RISK
    assert assignments["O8"].chosen_option.on_time
    assert assignments["O9"].chosen_option.on_time
    assert result.processing_order.index("O9") < result.processing_order.index("O8")

    # O10: regret-cost tiebreak partner for O4.
    assert assignments["O10"].category == Category.SAFE
    assert result.processing_order.index("O10") < result.processing_order.index("O4")

    # O11/O12: Hopeless-blocks-At-risk.
    assert assignments["O12"].category == Category.AT_RISK
    assert assignments["O12"].chosen_option.on_time
    assert assignments["O11"].category == Category.HOPELESS
    assert assignments["O11"].escalated
    assert result.processing_order.index("O12") < result.processing_order.index("O11")

    # O13: drains FC-A's capacity for real ahead of O5.
    assert assignments["O13"].category == Category.AT_RISK
    assert assignments["O13"].chosen_option.on_time
    assert result.processing_order.index("O13") < result.processing_order.index("O5")

    # O14: FC-E's list rate is cheapest, but it's 2 zones from the customer;
    # the zone surcharge flips the winner to the nearer, nominally pricier FC-B.
    assert assignments["O14"].category is None
    assert {leg.fc_id for leg in assignments["O14"].chosen_option.legs} == {"FC-B"}
    assert assignments["O14"].chosen_option.shipping_cost == 15.5


def test_uncontested_orders_claim_before_contested_orders():
    world = build_world()
    result = run_pipeline(ORDERS, world)

    uncontested_positions = [result.processing_order.index(oid) for oid in ("O6", "O7")]
    contested_positions = [
        result.processing_order.index(o.order_id)
        for o in ORDERS
        if o.order_id not in ("O6", "O7")
    ]
    assert max(uncontested_positions) < min(contested_positions)


def test_no_order_is_dropped_and_every_assignment_is_final():
    world = build_world()
    result = run_pipeline(ORDERS, world)

    for order in ORDERS:
        assignment = result.assignments[order.order_id]
        assert assignment.escalated or assignment.chosen_option is not None
