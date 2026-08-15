"""Hybrid ILP comparison arm (research.md Section 2/3): the contested
subset is solved as one joint mixed-integer program instead of Stage 3's
risk-sorted claim queue, so cross-order swaps a single sorted pass can't
see are visible to the solver. Uncontested orders still take the exact
same fast path as the pipeline -- only the contested remainder's
Stage 3+4 is replaced."""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from routing.contention import ContentionResult, cheapest_option, scan
from routing.cost import within_lateness_bound
from routing.models import Assignment, Option, Order, World
from routing.stage4_claim import claim_order, commit_option
from routing.strategies import (
    build_backorder_fallback_options,
    build_multi_date_options,
    build_multi_date_split_options,
    build_options,
)

# Large weights fold Stage 4's lexicographic preference -- on-time beats any
# cost difference, escalation is worse than any real option -- into pulp's
# single scalar objective, so this arm's priorities stay comparable to the
# other two (see README.md "How the engine decides").
LATE_PENALTY = 100_000.0
ESCALATION_PENALTY = 200_000.0


@dataclass
class ILPResult:
    assignments: dict[str, Assignment]
    processing_order: list[str]  # uncontested fast-path order only; the
    # contested remainder is solved jointly in one shot, not sequenced --
    # see report.md's note under "Processing order".
    contention: ContentionResult
    solver_status: str


# Sequential arms never cap backorder/restock quantity (backorder_legs_for_fc
# has no qty check) -- once the restock date arrives, supply is treated as
# unlimited. This mirrors that assumption for the ILP's resource constraint.
_UNCONSTRAINED_BACKORDER_SUPPLY = 10_000


def _resource_usage(option: Option, world: World) -> dict[tuple, int]:
    """Units one option's legs draw from each (capacity, inventory) key.
    Capacity is genuinely date-scoped (each day gets a fresh quota), but
    on-hand inventory is a single one-time pool that doesn't reset per
    ship date -- build_multi_date_options offers the same fresh unit on
    several different ship dates, so keying inventory by ship_date would
    let the LP "spend" that one unit once per date it's offered on. The
    inventory key instead distinguishes only current on-hand stock from
    the future restock pool (matched by this leg's ship_date equaling the
    known restock_date), never the exact date within either pool."""
    usage: dict[tuple, int] = {}
    for leg in option.legs:
        units = sum(li.qty for li in leg.line_items)
        cap_key = ("cap", leg.fc_id, leg.ship_date)
        usage[cap_key] = usage.get(cap_key, 0) + units
        for li in leg.line_items:
            is_backorder = world.inventory.restock_date(leg.fc_id, li.sku) == leg.ship_date
            inv_key = ("inv", leg.fc_id, li.sku, is_backorder)
            usage[inv_key] = usage.get(inv_key, 0) + li.qty
    return usage


def _available_quantity(resource_key: tuple, world: World) -> int:
    if resource_key[0] == "cap":
        _, fc_id, ship_date = resource_key
        return world.capacity.remaining(fc_id, ship_date)
    _, fc_id, sku, is_backorder = resource_key
    if is_backorder:
        return _UNCONSTRAINED_BACKORDER_SUPPLY
    return world.inventory.qty_on_hand(fc_id, sku)


def _describe_ilp(order: Order, option: Option | None, rejected: Option | None = None) -> str:
    if option is None:
        if rejected is None:
            return (
                f"{order.order_id}: escalated — the joint ILP solve found no option within the "
                f"{order.promise_date} promise's lateness bound once shared inventory/capacity was allocated."
            )
        return (
            f"{order.order_id}: escalated — cheapest option the joint solve priced would cost "
            f"${rejected.effective_cost:.2f} effective cost, {rejected.days_late}d late (shipping "
            f"${rejected.shipping_cost:.2f} + penalty ${rejected.penalty_cost:.2f} + late refund "
            f"${rejected.late_refund:.2f}), past the lateness bound once shared inventory/capacity "
            "was allocated."
        )
    fcs = ", ".join(sorted({leg.fc_id for leg in option.legs}))
    if option.on_time:
        return (
            f"{order.order_id}: ILP-assigned {option.strategy.value} via {fcs} at ${option.shipping_cost:.2f}, "
            "on time — chosen jointly with the rest of the contested batch to minimize total penalty-weighted cost."
        )
    return (
        f"{order.order_id}: ILP-assigned {option.strategy.value} via {fcs} at ${option.effective_cost:.2f} effective "
        f"cost, {option.days_late}d late (shipping ${option.shipping_cost:.2f} + penalty ${option.penalty_cost:.2f} "
        f"+ late refund ${option.late_refund:.2f}) — chosen jointly with the rest of the "
        "contested batch to minimize total penalty-weighted cost."
    )


def _is_selected(var: pulp.LpVariable) -> bool:
    value = var.value()
    return value is not None and value > 0.5


def _solve_contested(
    contested: list[Order],
    options_by_order: dict[str, list[Option]],
    world: World,
) -> tuple[dict[str, Assignment], str]:
    problem = pulp.LpProblem("ilp_contention", pulp.LpMinimize)

    choice_vars: dict[tuple[str, int], pulp.LpVariable] = {}
    escalate_vars: dict[str, pulp.LpVariable] = {}
    valid_options: dict[str, list[tuple[int, Option]]] = {}

    for order in contested:
        options = [
            (i, option)
            for i, option in enumerate(options_by_order[order.order_id])
            if option.on_time or within_lateness_bound(option.days_late)
        ]
        valid_options[order.order_id] = options
        for i, _ in options:
            choice_vars[(order.order_id, i)] = pulp.LpVariable(f"x_{order.order_id}_{i}", cat="Binary")
        escalate_vars[order.order_id] = pulp.LpVariable(f"escalate_{order.order_id}", cat="Binary")

    # Every order resolves to exactly one outcome: one of its valid options, or escalate.
    for order in contested:
        problem += (
            pulp.lpSum(choice_vars[(order.order_id, i)] for i, _ in valid_options[order.order_id])
            + escalate_vars[order.order_id]
            == 1
        )

    # Shared inventory/capacity can't be oversold across the orders chosen for it.
    resource_terms: dict[tuple, list] = {}
    for order in contested:
        for i, option in valid_options[order.order_id]:
            for resource_key, units in _resource_usage(option, world).items():
                resource_terms.setdefault(resource_key, []).append(choice_vars[(order.order_id, i)] * units)
    for resource_key, terms in resource_terms.items():
        problem += pulp.lpSum(terms) <= _available_quantity(resource_key, world)

    # Minimize penalty-weighted cost: on-time options compete on effective
    # cost alone; a late option carries LATE_PENALTY on top so it only wins
    # when escalation or a worse trade is the only alternative; escalation
    # is the most expensive outcome of all.
    objective_terms = []
    for order in contested:
        for i, option in valid_options[order.order_id]:
            weight = option.effective_cost + (0.0 if option.on_time else LATE_PENALTY)
            objective_terms.append(choice_vars[(order.order_id, i)] * weight)
        objective_terms.append(escalate_vars[order.order_id] * ESCALATION_PENALTY)
    problem += pulp.lpSum(objective_terms)

    problem.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[problem.status]

    assignments: dict[str, Assignment] = {}
    escalated_orders: list[Order] = []
    for order in contested:
        chosen: Option | None = None
        if not _is_selected(escalate_vars[order.order_id]):
            for i, option in valid_options[order.order_id]:
                if _is_selected(choice_vars[(order.order_id, i)]):
                    chosen = option
                    break
        if chosen is not None:
            commit_option(chosen, world)
        else:
            escalated_orders.append(order)
        assignments[order.order_id] = Assignment(
            order_id=order.order_id,
            chosen_option=chosen,
            category=None,  # Stage 3's category doesn't apply -- there's no sort order, just a joint solve
            regret_cost=None,
            escalated=chosen is None,
            trade_off=_describe_ilp(order, chosen),
            contested=True,
        )

    # Every winner is committed by now, so an escalated order's rejected
    # option can be priced against what the joint solve actually left on
    # the ledger -- the options_by_order snapshot predates all of this
    # solve's commits and would misleadingly show a resource a winner just
    # took. This mirrors stage4_claim.py's own live re-check.
    for order in escalated_orders:
        rejected = cheapest_option(build_options(order, world))
        assignment = assignments[order.order_id]
        assignment.escalated_option = rejected
        assignment.trade_off = _describe_ilp(order, None, rejected=rejected)

    return assignments, status


def run_ilp_contention(orders: list[Order], world: World) -> ILPResult:
    """Same uncontested/contested split as run_pipeline, but the contested
    remainder is solved as one joint ILP instead of Stage 3's sort +
    Stage 4's sequential claim."""
    contention_result = scan(orders, world)

    uncontested = [o for o in orders if not contention_result.is_contested(o.order_id)]
    contested = [o for o in orders if contention_result.is_contested(o.order_id)]

    assignments: dict[str, Assignment] = {}
    processing_order: list[str] = []
    for order in uncontested:
        assignments[order.order_id] = claim_order(order, world, contested=False)
        processing_order.append(order.order_id)

    options_by_order: dict[str, list[Option]] = {}
    for order in contested:
        # No resolve_capacity_threats/worst_case_adjust_multi_leg_options
        # here: those exist to de-risk Stage 3's *sequential* sort, which
        # can't see the whole batch and so must pessimistically assume an
        # oversubscribed resource might go to a rival order. The ILP's
        # resource constraints in _solve_contested see the whole contested
        # batch exactly, so pre-filtering here would just strip a cheaper
        # on-time option the joint solve could safely award to one order.
        #
        # build_multi_date_options replaces build_options' single-date
        # Consolidated legs with one per (FC, service, ship date) across a
        # window -- without it, every contested order wanting the same FC
        # only ever sees that FC's one greedy "earliest available" date, so
        # once that date's capacity is spoken for, the solver has no "ship
        # a day later at the same FC" choice and is forced to a pricier FC
        # instead. build_multi_date_split_options does the same for Split,
        # one merged leg's date at a time. build_options is still needed
        # for Split's base per-item FC/service assignment.
        options_by_order[order.order_id] = (
            build_options(order, world)
            + build_multi_date_options(order, world)
            + build_multi_date_split_options(order, world)
            + build_backorder_fallback_options(order, world)
        )

    solver_status = "Optimal"  # nothing to solve when the batch has no contention
    if contested:
        contested_assignments, solver_status = _solve_contested(contested, options_by_order, world)
        assignments.update(contested_assignments)

    return ILPResult(
        assignments=assignments,
        processing_order=processing_order,
        contention=contention_result,
        solver_status=solver_status,
    )
