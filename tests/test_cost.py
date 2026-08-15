"""Tests for cost.py."""
from datetime import date

import pytest

from routing.cost import (
    MAX_ACCEPTABLE_LATENESS,
    compute_days_late,
    compute_late_penalty,
    price_option,
    within_lateness_bound,
)
from routing.models import CarrierRate, LineItem, ServiceLevel, ShipmentLeg


def leg(fc_id, service_level, line_items, ship_date, eta):
    return ShipmentLeg(
        fc_id=fc_id,
        service_level=service_level,
        line_items=tuple(line_items),
        ship_date=ship_date,
        eta=eta,
        capacity_threatened=False,
    )


@pytest.fixture
def rates():
    return {
        ("FC-A", ServiceLevel.GROUND): CarrierRate(
            fc_id="FC-A", service_level=ServiceLevel.GROUND, base_fee=6.0, per_unit_fee=1.0,
            transit_business_days=3,
        ),
        ("FC-A", ServiceLevel.EXPEDITED): CarrierRate(
            fc_id="FC-A", service_level=ServiceLevel.EXPEDITED, base_fee=20.0, per_unit_fee=2.0,
            transit_business_days=1,
        ),
        ("FC-B", ServiceLevel.GROUND): CarrierRate(
            fc_id="FC-B", service_level=ServiceLevel.GROUND, base_fee=8.0, per_unit_fee=1.5,
            transit_business_days=3,
        ),
    }


def test_late_penalty_uses_five_dollar_floor():
    assert compute_late_penalty(line_item_value=50.0) == 5.0  # 3% of 50 = 1.50, floor wins


def test_late_penalty_uses_three_percent_above_floor():
    assert compute_late_penalty(line_item_value=1000.0) == 30.0  # 3% of 1000


def test_days_late_zero_when_on_time():
    assert compute_days_late(eta=date(2026, 8, 12), promise_date=date(2026, 8, 14)) == 0


def test_days_late_positive_when_late():
    assert compute_days_late(eta=date(2026, 8, 17), promise_date=date(2026, 8, 14)) == 3


def test_within_lateness_bound():
    assert within_lateness_bound(MAX_ACCEPTABLE_LATENESS) is True
    assert within_lateness_bound(MAX_ACCEPTABLE_LATENESS + 1) is False


def test_shipment_grouping_charges_base_fee_once_per_fc_service(rates):
    # Two line items, same FC, same service level -> one shipment, one base fee.
    items = [LineItem("SKU-1", 2), LineItem("SKU-2", 3)]
    legs = (leg("FC-A", ServiceLevel.GROUND, items, date(2026, 8, 12), date(2026, 8, 17)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 10.0, "SKU-2": 10.0}, promise_date=date(2026, 8, 20))
    # base 6 + per-unit 1 * 5 units = 11
    assert result.shipping_cost == pytest.approx(11.0)


def test_split_across_two_fcs_charges_base_fee_twice(rates):
    legs = (
        leg("FC-A", ServiceLevel.GROUND, [LineItem("SKU-1", 2)], date(2026, 8, 12), date(2026, 8, 17)),
        leg("FC-B", ServiceLevel.GROUND, [LineItem("SKU-2", 3)], date(2026, 8, 12), date(2026, 8, 17)),
    )
    result = price_option(legs, rates, unit_prices={"SKU-1": 10.0, "SKU-2": 10.0}, promise_date=date(2026, 8, 20))
    # FC-A: 6 + 1*2 = 8; FC-B: 8 + 1.5*3 = 12.5; total 20.5
    assert result.shipping_cost == pytest.approx(20.5)


def test_on_time_option_has_no_penalty_or_refund(rates):
    legs = (leg("FC-A", ServiceLevel.GROUND, [LineItem("SKU-1", 1)], date(2026, 8, 12), date(2026, 8, 17)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 10.0}, promise_date=date(2026, 8, 20))
    assert result.on_time is True
    assert result.days_late == 0
    assert result.penalty_cost == 0.0
    assert result.late_refund == 0.0
    assert result.effective_cost == result.shipping_cost


def test_late_within_bound_pays_penalty_no_refund(rates):
    legs = (leg("FC-A", ServiceLevel.GROUND, [LineItem("SKU-1", 1)], date(2026, 8, 12), date(2026, 8, 17)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 1000.0}, promise_date=date(2026, 8, 14))
    assert result.on_time is False
    assert result.days_late == 3
    assert result.penalty_cost == pytest.approx(30.0)  # 3% of 1000
    assert result.late_refund == 0.0
    assert result.effective_cost == pytest.approx(result.shipping_cost + 30.0)


def test_late_at_or_past_max_acceptable_lateness_adds_capped_refund_on_top_of_shipping_cost(rates):
    """Shipping cost is a sunk expense -- it's already been paid to the
    carrier to move it, late or not -- so the refund is an *additional* payout on
    top of that, not something that cancels it. The refund is capped so
    that penalty + refund never exceeds shipping cost (our stand-in for
    "what the merchant paid"): the merchant gets that money back exactly
    once, whether as a late fee, a top-up refund, or both."""
    # Ship 2026-08-12, eta 2026-08-19 -> 7 days late (== MAX_ACCEPTABLE_LATENESS).
    legs = (leg("FC-A", ServiceLevel.EXPEDITED, [LineItem("SKU-1", 1)], date(2026, 8, 12), date(2026, 8, 19)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 100.0}, promise_date=date(2026, 8, 12))
    assert result.on_time is False
    assert result.days_late == MAX_ACCEPTABLE_LATENESS
    # shipping cost = 20 + 2*1 = 22; penalty = max(5, 3% of 100) = 5.
    assert result.shipping_cost == pytest.approx(22.0)
    assert result.penalty_cost == pytest.approx(5.0)
    # refund tops the penalty up to a full refund of shipping cost: 22 - 5 = 17.
    assert result.late_refund == pytest.approx(17.0)
    assert result.effective_cost == pytest.approx(44.0)  # 22 + 5 + 17


def test_refund_is_floored_at_zero_when_penalty_alone_already_exceeds_shipping_cost(rates):
    """A high-value line item's penalty can already exceed the shipping
    cost on its own. The refund only tops up the remainder -- it never
    goes negative and never turns into a bonus."""
    legs = (leg("FC-A", ServiceLevel.EXPEDITED, [LineItem("SKU-1", 1)], date(2026, 8, 12), date(2026, 8, 19)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 1000.0}, promise_date=date(2026, 8, 12))
    assert result.shipping_cost == pytest.approx(22.0)
    assert result.penalty_cost == pytest.approx(30.0)  # 3% of 1000 already exceeds shipping cost
    assert result.late_refund == pytest.approx(0.0)
    assert result.effective_cost == pytest.approx(52.0)  # 22 + 30 + 0


def test_penalty_sums_across_multiple_late_line_items(rates):
    items = [LineItem("SKU-1", 1), LineItem("SKU-2", 1)]
    legs = (leg("FC-A", ServiceLevel.GROUND, items, date(2026, 8, 12), date(2026, 8, 17)),)
    result = price_option(legs, rates, unit_prices={"SKU-1": 1000.0, "SKU-2": 50.0}, promise_date=date(2026, 8, 14))
    # SKU-1: 3% of 1000 = 30; SKU-2: max(5, 3%*50=1.5) = 5
    assert result.penalty_cost == pytest.approx(35.0)
