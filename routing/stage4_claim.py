"""Stage 4: the only writer to the inventory and capacity ledgers. Every
claim rebuilds options live against current ledger state and commits a
winning option's legs atomically, or not at all."""
from __future__ import annotations

from routing.contention import cheapest_option
from routing.cost import MAX_ACCEPTABLE_LATENESS, within_lateness_bound
from routing.models import Assignment, Category, Option, Order, World
from routing.strategies import build_options


def commit_option(option: Option, world: World) -> None:
    """Decrements inventory and capacity for one option's legs. Public so
    routing/optimization_ilp.py can commit its jointly-solved winners with
    the exact same ledger-write logic Stage 4 uses."""
    for leg in option.legs:
        units = sum(li.qty for li in leg.line_items)
        world.capacity.commit(leg.fc_id, leg.ship_date, units)
        for li in leg.line_items:
            world.inventory.commit(leg.fc_id, li.sku, li.qty)


def _describe(order: Order, option: Option | None, rejected: Option | None = None) -> str:
    if option is None:
        if rejected is None:
            return f"{order.order_id}: escalated — no option within the {order.promise_date} promise's lateness bound."
        return (
            f"{order.order_id}: escalated — cheapest available option would cost ${rejected.effective_cost:.2f} "
            f"effective cost, {rejected.days_late}d late (shipping ${rejected.shipping_cost:.2f} + penalty "
            f"${rejected.penalty_cost:.2f} + late refund ${rejected.late_refund:.2f}), past the "
            f"{MAX_ACCEPTABLE_LATENESS}-day lateness bound — flagged for human review instead of auto-routed."
        )
    fcs = ", ".join(sorted({leg.fc_id for leg in option.legs}))
    if option.on_time:
        return f"{order.order_id}: claimed {option.strategy.value} via {fcs} at ${option.shipping_cost:.2f}, on time."
    return (
        f"{order.order_id}: claimed {option.strategy.value} via {fcs} at "
        f"${option.effective_cost:.2f} effective cost, {option.days_late}d late "
        f"(shipping ${option.shipping_cost:.2f} + penalty ${option.penalty_cost:.2f} "
        f"+ late refund ${option.late_refund:.2f})."
    )


def claim_order(
    order: Order,
    world: World,
    category: Category | None = None,
    regret_cost: float | None = None,
    contested: bool = False,
) -> Assignment:
    """Live re-check, commit the cheapest surviving option, or escalate if
    nothing survives within the lateness bound."""
    options = build_options(order, world)
    best = cheapest_option(options)

    if best is None or (not best.on_time and not within_lateness_bound(best.days_late)):
        return Assignment(
            order_id=order.order_id, chosen_option=None, category=category,
            regret_cost=regret_cost, escalated=True,
            trade_off=_describe(order, None, rejected=best), contested=contested,
            escalated_option=best,
        )

    commit_option(best, world)
    return Assignment(
        order_id=order.order_id, chosen_option=best, category=category,
        regret_cost=regret_cost, escalated=False,
        trade_off=_describe(order, best), contested=contested,
    )
