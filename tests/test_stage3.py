"""Tests for stage3_sort.py, using synthetic Order/Option fixtures only."""
from datetime import date

import pytest

from routing.models import Category, Option, Order, ServiceLevel, ShipmentLeg, StrategyType
from routing.stage3_sort import categorize, sort_contested


def make_option(shipping_cost, on_time, days_late=0, penalty_cost=0.0, late_refund=0.0, capacity_threatened=False):
    leg = ShipmentLeg(
        fc_id="FC-X", service_level=ServiceLevel.GROUND, line_items=(),
        ship_date=date(2026, 8, 12), eta=date(2026, 8, 12),
        capacity_threatened=capacity_threatened,
    )
    return Option(
        strategy=StrategyType.CONSOLIDATED, legs=(leg,), shipping_cost=shipping_cost,
        on_time=on_time, eta=leg.eta, days_late=days_late, penalty_cost=penalty_cost,
        late_refund=late_refund,
    )


def make_order(order_id, promise_date, arrival_index=0):
    return Order(order_id, promise_date, ServiceLevel.GROUND, (), arrival_index)


def test_categorize_safe_with_two_on_time_options():
    options = [make_option(10.0, on_time=True), make_option(15.0, on_time=True)]
    category, regret = categorize(options)
    assert category == Category.SAFE
    assert regret == pytest.approx(5.0)


def test_categorize_at_risk_with_one_on_time_option():
    options = [make_option(10.0, on_time=True), make_option(20.0, on_time=False, days_late=2, penalty_cost=6.0)]
    category, regret = categorize(options)
    assert category == Category.AT_RISK
    assert regret == pytest.approx(26.0 - 10.0)


def test_categorize_hopeless_with_zero_on_time_options():
    options = [
        make_option(20.0, on_time=False, days_late=2, penalty_cost=6.0),
        make_option(25.0, on_time=False, days_late=3, penalty_cost=9.0),
    ]
    category, regret = categorize(options)
    assert category == Category.HOPELESS
    assert regret == pytest.approx(34.0 - 26.0)


def test_categorize_capacity_threatened_on_time_option_stays_on_time():
    # A capacity-threatened option that is still on_time must count toward
    # the on-time tally like any other -- the flag is informational only.
    options = [
        make_option(10.0, on_time=True, capacity_threatened=True),
        make_option(15.0, on_time=True),
    ]
    category, regret = categorize(options)
    assert category == Category.SAFE
    assert regret == pytest.approx(5.0)


def test_sort_processes_at_risk_before_hopeless_even_when_input_is_hopeless_first():
    hopeless_order = make_order("HOPELESS-1", date(2026, 8, 20))
    at_risk_order = make_order("AT-RISK-1", date(2026, 8, 25))
    options_by_order = {
        "HOPELESS-1": [make_option(20.0, on_time=False, days_late=2, penalty_cost=6.0)],
        "AT-RISK-1": [
            make_option(10.0, on_time=True),
            make_option(30.0, on_time=False, days_late=2, penalty_cost=6.0),
        ],
    }
    result = sort_contested([hopeless_order, at_risk_order], options_by_order)
    assert [r[0].order_id for r in result] == ["AT-RISK-1", "HOPELESS-1"]


def test_sort_breaks_same_category_same_date_tie_by_regret_descending():
    order_low_regret = make_order("LOW-REGRET", date(2026, 8, 20))
    order_high_regret = make_order("HIGH-REGRET", date(2026, 8, 20))
    options_by_order = {
        "LOW-REGRET": [make_option(10.0, on_time=True), make_option(12.0, on_time=True)],
        "HIGH-REGRET": [make_option(10.0, on_time=True), make_option(50.0, on_time=True)],
    }
    result = sort_contested([order_low_regret, order_high_regret], options_by_order)
    assert [r[0].order_id for r in result] == ["HIGH-REGRET", "LOW-REGRET"]


def test_sort_orders_by_promise_date_within_same_category():
    order_later = make_order("LATER", date(2026, 8, 25))
    order_sooner = make_order("SOONER", date(2026, 8, 20))
    options_by_order = {
        "LATER": [make_option(10.0, on_time=True), make_option(20.0, on_time=True)],
        "SOONER": [make_option(10.0, on_time=True), make_option(20.0, on_time=True)],
    }
    result = sort_contested([order_later, order_sooner], options_by_order)
    assert [r[0].order_id for r in result] == ["SOONER", "LATER"]
