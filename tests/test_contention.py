"""Tests for contention.py's cheapest_option tie-break."""
from datetime import date

from routing.contention import cheapest_option
from routing.models import Option, ServiceLevel, ShipmentLeg, StrategyType


def make_option(on_time, days_late, shipping_cost, penalty_cost, late_refund):
    leg = ShipmentLeg(
        fc_id="FC-X", service_level=ServiceLevel.GROUND, line_items=(),
        ship_date=date(2026, 8, 12), eta=date(2026, 8, 12),
        capacity_threatened=False,
    )
    return Option(
        strategy=StrategyType.CONSOLIDATED, legs=(leg,), shipping_cost=shipping_cost,
        on_time=on_time, eta=leg.eta, days_late=days_late, penalty_cost=penalty_cost,
        late_refund=late_refund,
    )


def test_cheapest_option_prefers_in_bound_late_option_even_when_it_costs_more():
    """An option past MAX_ACCEPTABLE_LATENESS would escalate if chosen, no
    matter how cheap it looks on paper -- Stage 4 never gets to auto-assign
    it. cheapest_option must not pick a cheaper-but-unroutable option over
    a pricier-but-still-routable one; that would force an escalation that
    wasn't necessary."""
    in_bound = make_option(on_time=False, days_late=7, shipping_cost=10.0, penalty_cost=5.0, late_refund=5.0)
    out_of_bound = make_option(on_time=False, days_late=10, shipping_cost=1.0, penalty_cost=5.0, late_refund=0.0)
    assert out_of_bound.effective_cost < in_bound.effective_cost  # cheaper, but not a real choice

    assert cheapest_option([out_of_bound, in_bound]) is in_bound
    assert cheapest_option([in_bound, out_of_bound]) is in_bound


def test_cheapest_option_falls_back_to_out_of_bound_when_nothing_else_survives():
    out_of_bound = make_option(on_time=False, days_late=10, shipping_cost=7.2, penalty_cost=5.0, late_refund=2.2)
    assert cheapest_option([out_of_bound]) is out_of_bound
