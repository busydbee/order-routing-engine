"""Wires Stages 1-4 end to end: tag -> scan -> (fast path | sort -> claim)."""
from __future__ import annotations

from dataclasses import dataclass

from routing.contention import ContentionResult, leg_resources, scan
from routing.models import Assignment, Order, ServiceLevelCategory, World
from routing.shipping_calendar import next_business_day
from routing.stage1_eligibility import TimeToPromiseSnapshot, compute_time_to_promise_snapshots, tag_orders
from routing.stage3_sort import sort_contested
from routing.stage4_claim import claim_order
from routing.strategies import build_options


@dataclass
class PipelineResult:
    service_level_categories: dict[str, ServiceLevelCategory]
    time_to_promise_snapshots: dict[str, TimeToPromiseSnapshot]
    contention: ContentionResult
    assignments: dict[str, Assignment]
    processing_order: list[str]


def resolve_capacity_threats(options, oversubscribed, promise_date):
    """Drops on-time options that would miss `promise_date` after a
    hypothetical one-business-day slip on any oversubscribed (FC, ship
    date) resource. No-op when fewer than 2 options are on time (plan
    Section 5, Step 1)."""
    if sum(1 for o in options if o.on_time) < 2:
        return options

    kept = []
    for option in options:
        if not option.on_time:
            kept.append(option)
            continue
        pessimistic_etas = [
            next_business_day(leg.eta) if ("cap", leg.fc_id, leg.ship_date) in oversubscribed else leg.eta
            for leg in option.legs
        ]
        if all(eta <= promise_date for eta in pessimistic_etas):
            kept.append(option)
    return kept


def worst_case_adjust_multi_leg_options(options, oversubscribed):
    """Drops on-time multi-leg options where any leg touches an
    oversubscribed resource, but only when another on-time option would
    still remain -- never drops an order's last on-time option this way
    (plan Section 5, Multi-item orders)."""
    contested = [
        option for option in options
        if option.on_time and len(option.legs) > 1 and any(leg_resources(leg) & oversubscribed for leg in option.legs)
    ]
    if not contested:
        return options

    remaining_on_time = sum(1 for o in options if o.on_time) - len(contested)
    if remaining_on_time < 1:
        return options

    contested_ids = {id(option) for option in contested}
    return [option for option in options if id(option) not in contested_ids]


def run_pipeline(orders: list[Order], world: World) -> PipelineResult:
    service_level_categories = tag_orders(orders)
    # Snapshot every order's real buffer days/on-time-option count against the
    # batch's starting state, before the claim loop below commits anything.
    time_to_promise_snapshots = compute_time_to_promise_snapshots(orders, world)
    contention_result = scan(orders, world)

    uncontested = [o for o in orders if not contention_result.is_contested(o.order_id)]
    contested = [o for o in orders if contention_result.is_contested(o.order_id)]

    assignments: dict[str, Assignment] = {}
    processing_order: list[str] = []

    for order in uncontested:
        assignments[order.order_id] = claim_order(order, world, contested=False)
        processing_order.append(order.order_id)

    # Re-snapshot the *remaining* contested orders' options against current
    # `world` before every single pick, instead of sorting the whole group
    # once up front. A one-shot sort's category/regret-cost numbers go
    # stale the moment an earlier pick in this same group claims a
    # resource another remaining order was counting on -- claim_order's
    # live re-check still claims correctly either way, but the *order* in
    # which orders get first crack at scarce resources is decided here,
    # and that should track current reality, not a frozen snapshot (see
    # README "How the engine decides" / research.md).
    remaining = list(contested)
    while remaining:
        options_by_order = {}
        for order in remaining:
            options = build_options(order, world)
            options = resolve_capacity_threats(options, contention_result.oversubscribed_resources, order.promise_date)
            options = worst_case_adjust_multi_leg_options(options, contention_result.oversubscribed_resources)
            options_by_order[order.order_id] = options

        order, category, regret_cost = sort_contested(remaining, options_by_order)[0]
        assignments[order.order_id] = claim_order(
            order, world, category=category, regret_cost=regret_cost, contested=True
        )
        processing_order.append(order.order_id)
        remaining.remove(order)

    return PipelineResult(
        service_level_categories=service_level_categories,
        time_to_promise_snapshots=time_to_promise_snapshots,
        contention=contention_result,
        assignments=assignments,
        processing_order=processing_order,
    )
