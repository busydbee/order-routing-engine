"""Stage 1: the real eligibility gate (decide_eligibility/filter_eligible)
plus a service-level tag and time-to-promise snapshot, both display-only
in this prototype. research.md Section 4 has the full design this gate
implements a minimal version of."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from routing.models import FC, Order, ServiceLevel, ServiceLevelCategory, World
from routing.shipping_calendar import is_business_day
from routing.strategies import build_options
from routing.zone import DAYS_PER_EXTRA_ZONE, zone_distance

_HIGH_URGENCY_LEVELS = {ServiceLevel.EXPEDITED, ServiceLevel.OVERNIGHT}

# Candidate FCs are a geography-only lookup (zone distance), not a stock
# check -- cheap, static reference data, unlike Stage 2's live feasibility
# build. See research.md Section 4's "what Stage 1 would actually need."
MAX_CANDIDATE_ZONE_DISTANCE = 2

# An order this close to its own promise date can't wait for a shared FC
# cutoff without risking the date -- it enters routing immediately.
URGENT_DEADLINE_DAYS = 1


@dataclass(frozen=True)
class EligibilityDecision:
    """Plain-language explanation of Stage 1's admit/hold decision for one
    order, matching the trade_off-string convention the other stages use."""
    eligible: bool
    reason: str


def candidate_fcs(order: Order, fcs: dict[str, FC]) -> list[FC]:
    """FCs within shippable geographic range of the order's destination --
    a static zone-distance lookup, not a stock or capacity check."""
    return [fc for fc in fcs.values() if zone_distance(fc.zone, order.destination_zone) <= MAX_CANDIDATE_ZONE_DISTANCE]


def _cutoff_already_passed_today(fc: FC, now: datetime) -> bool:
    return is_business_day(now.date()) and now.time() >= fc.cutoff_time


def _required_lead_days(order: Order, candidates: list[FC]) -> int:
    """Same zone_distance table Stage 2/pricing use (routing/zone.py), not a
    flat number -- a farther candidate FC needs more transit days, so a flat
    URGENT_DEADLINE_DAYS would force distant orders in too late to route on
    time. Still static/zone-only, no rates or stock lookup."""
    if not candidates:
        return URGENT_DEADLINE_DAYS
    nearest_distance = min(zone_distance(fc.zone, order.destination_zone) for fc in candidates)
    extra_zones = max(0, nearest_distance - 1)
    return URGENT_DEADLINE_DAYS + extra_zones * DAYS_PER_EXTRA_ZONE


def decide_eligibility(order: Order, fcs: dict[str, FC], now: datetime) -> EligibilityDecision:
    """min(next relevant FC cutoff, order's time-to-promise deadline) from
    research.md Section 4, evaluated at `now`: eligible the instant either
    side of that minimum has already been reached."""
    candidates = candidate_fcs(order, fcs)
    days_to_promise = (order.promise_date - now.date()).days
    required_lead_days = _required_lead_days(order, candidates)
    if days_to_promise <= required_lead_days:
        return EligibilityDecision(
            True,
            f"Promise date is {days_to_promise} day(s) out -- inside this order's {required_lead_days}-day "
            "urgency deadline (scaled for zone distance to its nearest candidate FC), so it enters "
            "routing now regardless of FC cutoffs.",
        )

    passed = sorted(fc.fc_id for fc in candidates if _cutoff_already_passed_today(fc, now))
    if passed:
        return EligibilityDecision(
            True,
            f"{', '.join(passed)}'s pack cutoff already passed today -- free batching room, "
            "order enters this wave.",
        )

    if not candidates:
        return EligibilityDecision(
            False,
            f"No FC is within shippable range (zone distance <= {MAX_CANDIDATE_ZONE_DISTANCE}) of "
            f"destination zone {order.destination_zone}, and this order has {days_to_promise} days "
            "of slack -- not yet eligible; queued for a later decision point.",
        )

    waiting_on = ", ".join(sorted(fc.fc_id for fc in candidates))
    return EligibilityDecision(
        False,
        f"None of the candidate FCs ({waiting_on}) have hit their pack cutoff yet today, and this "
        f"order has {days_to_promise} days of slack -- not yet eligible; queued for a later decision point.",
    )


def decide_eligibility_for_orders(orders: list[Order], fcs: dict[str, FC], now: datetime) -> dict[str, EligibilityDecision]:
    return {order.order_id: decide_eligibility(order, fcs, now) for order in orders}


def filter_eligible(orders: list[Order], fcs: dict[str, FC], now: datetime) -> tuple[list[Order], list[Order]]:
    """Splits a batch into (eligible, deferred) using decide_eligibility.
    Deferred orders stay queued -- they never reach Stage 2 or the
    contention scan this pass."""
    decisions = decide_eligibility_for_orders(orders, fcs, now)
    eligible = [o for o in orders if decisions[o.order_id].eligible]
    deferred = [o for o in orders if not decisions[o.order_id].eligible]
    return eligible, deferred


def tag_service_level(order: Order) -> ServiceLevelCategory:
    if order.service_level_requested in _HIGH_URGENCY_LEVELS:
        return ServiceLevelCategory.HIGH_URGENCY
    return ServiceLevelCategory.LOW_URGENCY


def tag_orders(orders: list[Order]) -> dict[str, ServiceLevelCategory]:
    return {order.order_id: tag_service_level(order) for order in orders}


@dataclass(frozen=True)
class TimeToPromiseSnapshot:
    """One order's real numbers from Stages 1-2, computed in isolation
    against the batch's starting inventory/capacity -- before any order
    has claimed anything, so contention hasn't yet changed what's left."""
    buffer_days: int | None  # promise date minus the fastest feasible ETA; None if no FC has any path at all
    on_time_options: int  # Stage 2's real count of feasible options that hit the promise date


def compute_time_to_promise_snapshot(order: Order, world: World) -> TimeToPromiseSnapshot:
    options = build_options(order, world)
    on_time_options = sum(1 for option in options if option.on_time)
    if not options:
        return TimeToPromiseSnapshot(buffer_days=None, on_time_options=0)
    best_eta = min(option.eta for option in options)
    return TimeToPromiseSnapshot(buffer_days=(order.promise_date - best_eta).days, on_time_options=on_time_options)


def compute_time_to_promise_snapshots(orders: list[Order], world: World) -> dict[str, TimeToPromiseSnapshot]:
    return {order.order_id: compute_time_to_promise_snapshot(order, world) for order in orders}
