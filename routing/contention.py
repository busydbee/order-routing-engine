"""Upfront contention scan: splits the batch into uncontested (fast path)
and contested (Stage 3 sort + Stage 4 claim) orders, based on each
order's cheapest feasible option only (its primary demand)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from routing.cost import within_lateness_bound
from routing.models import Option, Order, World
from routing.strategies import build_options


def cheapest_option(options: list[Option]) -> Option | None:
    """On-time options are compared on shipping cost alone (no penalty to
    weigh); once none are on-time, effective cost (shipping + penalty +
    late refund) breaks the tie among late options -- but an option past
    MAX_ACCEPTABLE_LATENESS never wins that tie-break over one that's
    still within it, no matter its cost. An option past the bound would
    just get escalated anyway (Stage 4 checks lateness on whatever this
    function returns); picking it here over a genuinely routable option
    would force an escalation that wasn't necessary."""
    on_time = [o for o in options if o.on_time]
    if on_time:
        return min(on_time, key=lambda o: o.shipping_cost)
    if options:
        return min(options, key=lambda o: (not within_lateness_bound(o.days_late), o.effective_cost))
    return None


@dataclass
class ContentionResult:
    primary_options: dict[str, Option | None]
    contested_order_ids: set[str]
    oversubscribed_resources: set[tuple]

    def is_contested(self, order_id: str) -> bool:
        return order_id in self.contested_order_ids


def scan(orders: list[Order], world: World) -> ContentionResult:
    primary_options: dict[str, Option | None] = {}
    order_resources: dict[str, set[tuple]] = {}
    inventory_demand: dict[tuple[str, str, str], int] = {}  # (fc, sku) -> units
    capacity_demand: dict[tuple[str, str, date], int] = {}  # (fc, ship_date) -> units

    for order in orders:
        option = cheapest_option(build_options(order, world))
        primary_options[order.order_id] = option
        resources: set[tuple] = set()
        if option is None:
            order_resources[order.order_id] = resources
            continue

        for leg in option.legs:
            units_on_leg = sum(li.qty for li in leg.line_items)
            cap_key = ("cap", leg.fc_id, leg.ship_date)
            capacity_demand[cap_key] = capacity_demand.get(cap_key, 0) + units_on_leg
            resources.add(cap_key)
            for li in leg.line_items:
                inv_key = ("inv", leg.fc_id, li.sku)
                inventory_demand[inv_key] = inventory_demand.get(inv_key, 0) + li.qty
                resources.add(inv_key)
        order_resources[order.order_id] = resources

    oversubscribed: set[tuple] = set()
    for key, demand in inventory_demand.items():
        _, fc_id, sku = key
        if demand > world.inventory.qty_on_hand(fc_id, sku):
            oversubscribed.add(key)
    for key, demand in capacity_demand.items():
        _, fc_id, ship_date = key
        if demand > world.fcs[fc_id].capacity_units_per_day:
            oversubscribed.add(key)

    contested_ids = {
        order_id for order_id, resources in order_resources.items()
        if resources & oversubscribed or primary_options[order_id] is None
    }

    return ContentionResult(
        primary_options=primary_options,
        contested_order_ids=contested_ids,
        oversubscribed_resources=oversubscribed,
    )


def leg_resources(leg) -> set[tuple]:
    """The resource keys one shipment leg draws on -- same key shape used
    by the demand scan above, so pipeline.py can check an option's legs
    against `oversubscribed_resources` directly."""
    resources = {("cap", leg.fc_id, leg.ship_date)}
    resources.update(("inv", leg.fc_id, li.sku) for li in leg.line_items)
    return resources
