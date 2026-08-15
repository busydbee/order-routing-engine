"""Tests for the real (non-decorative) eligibility gate in
routing/stage1_eligibility.py: a candidate-FC (geography) lookup and
each candidate's cutoff schedule, plus each order's own deadline."""
from datetime import date, time

from routing.models import FC, LineItem, Order, ServiceLevel
from routing.stage1_eligibility import (
    MAX_CANDIDATE_ZONE_DISTANCE,
    URGENT_DEADLINE_DAYS,
    candidate_fcs,
    decide_eligibility,
    decide_eligibility_for_orders,
    filter_eligible,
)
from routing.shipping_calendar import NOW
from routing.zone import DAYS_PER_EXTRA_ZONE

_FCS = {
    "FC-A": FC("FC-A", time(12, 0), handling_days=0, capacity_units_per_day=10, zone=1),
    "FC-E": FC("FC-E", time(23, 0), handling_days=0, capacity_units_per_day=50, zone=3),
}


def _order(order_id, promise_date, destination_zone=1):
    return Order(order_id, promise_date, ServiceLevel.STANDARD, (LineItem("SKU-1", 1),), 0, destination_zone=destination_zone)


def test_candidate_fcs_excludes_fcs_beyond_max_zone_distance():
    far_order = _order("FAR", date(2026, 8, 27), destination_zone=4)
    candidates = candidate_fcs(far_order, _FCS)
    assert {fc.fc_id for fc in candidates} == {"FC-E"}


def test_candidate_fcs_includes_all_fcs_within_max_zone_distance():
    near_order = _order("NEAR", date(2026, 8, 27), destination_zone=1)
    candidates = candidate_fcs(near_order, _FCS)
    assert {fc.fc_id for fc in candidates} == {"FC-A", "FC-E"}


def test_order_within_urgent_deadline_is_eligible_even_with_no_cutoff_passed():
    # NOW is 2026-08-12 14:00; zone 4 means only FC-E (cutoff 23:00, not yet
    # passed) is a candidate, but the promise is inside URGENT_DEADLINE_DAYS.
    urgent = _order("URGENT", date(2026, 8, 13), destination_zone=4)
    assert (urgent.promise_date - NOW.date()).days <= URGENT_DEADLINE_DAYS
    decision = decide_eligibility(urgent, _FCS, NOW)
    assert decision.eligible is True
    assert "urgency deadline" in decision.reason


def test_order_with_slack_and_no_candidate_cutoff_passed_is_not_yet_eligible():
    # zone 4 -> only FC-E is a candidate; FC-E's cutoff (23:00) hasn't
    # passed yet at NOW (14:00), and the promise date has plenty of slack.
    slack = _order("SLACK", date(2026, 8, 27), destination_zone=4)
    decision = decide_eligibility(slack, _FCS, NOW)
    assert decision.eligible is False
    assert "FC-E" in decision.reason


def test_order_with_slack_but_a_candidate_fc_cutoff_already_passed_is_eligible():
    # zone 1 -> FC-A is a candidate; FC-A's cutoff (12:00) already passed
    # at NOW (14:00) -- free batching room, no need to wait for the promise deadline.
    slack_but_batched = _order("BATCHED", date(2026, 8, 27), destination_zone=1)
    decision = decide_eligibility(slack_but_batched, _FCS, NOW)
    assert decision.eligible is True
    assert "FC-A" in decision.reason


def test_urgent_deadline_scales_up_for_a_farther_candidate_fc():
    # destination_zone=5 -> FC-A is out of range (distance 4), FC-E is the
    # only candidate at distance 2 (the max allowed). A flat 1-day deadline
    # would force this order in only 1 day before promise, but the nearest
    # candidate is 2 zones away -- 1 extra zone of transit buffer needed, so
    # the real deadline is URGENT_DEADLINE_DAYS + 1 * DAYS_PER_EXTRA_ZONE = 2.
    far = _order("FAR-URGENT", date(2026, 8, 14), destination_zone=5)
    assert (far.promise_date - NOW.date()).days == URGENT_DEADLINE_DAYS + 1 * DAYS_PER_EXTRA_ZONE
    decision = decide_eligibility(far, _FCS, NOW)
    assert decision.eligible is True
    assert "urgency deadline" in decision.reason


def test_flat_deadline_alone_would_have_missed_the_farther_order():
    # Same order, one day further out than the scaled deadline allows --
    # still not eligible via the deadline branch (FC-E's cutoff hasn't
    # passed either), proving the scaling isn't just "always eligible."
    far = _order("FAR-SLACK", date(2026, 8, 15), destination_zone=5)
    decision = decide_eligibility(far, _FCS, NOW)
    assert decision.eligible is False


def test_urgent_deadline_does_not_scale_for_a_zone_within_max_distance_of_one():
    # destination_zone=1 -> nearest candidate (FC-A) is distance 0, so the
    # deadline stays at the flat URGENT_DEADLINE_DAYS -- no unwarranted padding.
    near = _order("NEAR-URGENT", date(2026, 8, 13), destination_zone=1)
    decision = decide_eligibility(near, _FCS, NOW)
    assert decision.eligible is True


def test_decide_eligibility_for_orders_returns_one_decision_per_order():
    orders = [
        _order("A", date(2026, 8, 27), destination_zone=1),
        _order("B", date(2026, 8, 27), destination_zone=4),
    ]
    decisions = decide_eligibility_for_orders(orders, _FCS, NOW)
    assert set(decisions) == {"A", "B"}
    assert decisions["A"].eligible is True
    assert decisions["B"].eligible is False


def test_filter_eligible_splits_orders_into_eligible_and_deferred():
    eligible_order = _order("A", date(2026, 8, 27), destination_zone=1)
    deferred_order = _order("B", date(2026, 8, 27), destination_zone=4)
    eligible, deferred = filter_eligible([eligible_order, deferred_order], _FCS, NOW)
    assert eligible == [eligible_order]
    assert deferred == [deferred_order]


def test_max_candidate_zone_distance_and_urgent_deadline_days_are_positive():
    assert MAX_CANDIDATE_ZONE_DISTANCE > 0
    assert URGENT_DEADLINE_DAYS >= 0
