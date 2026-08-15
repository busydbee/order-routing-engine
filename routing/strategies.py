"""Build every Option (Consolidated + Split) for an order.

Consolidated: one FC ships every line item. Split: each item independently
picks its own best FC, then items on the same (FC, service level) merge
into one shipment. Returned only for multi-item orders that actually span
2+ FCs after merging."""
from __future__ import annotations

from routing.cost import price_option
from routing.models import CarrierRate, FC, LineItem, Option, Order, ServiceLevel, ShipmentLeg, StrategyType, World
from routing.shipping_calendar import compute_eta, cutoff_and_handling_baseline, next_business_day
from routing.stage2_feasibility import all_candidate_legs_for_fc, backorder_legs_for_fc, fc_can_supply
from routing.zone import zone_adjusted_rates_for_fc

# Window of ship-date alternatives build_multi_date_options enumerates per
# FC, wide enough that even the slowest transit_business_days combined with
# the max offset still lands well past cost.MAX_ACCEPTABLE_LATENESS -- any
# option beyond the lateness bound is discarded by the ILP's own filter,
# so being generous here costs nothing but a few extra solver columns.
MULTI_DATE_HORIZON_DAYS = 10


def _rates_for_order(fc: FC, order: Order, world: World) -> dict[ServiceLevel, CarrierRate]:
    return zone_adjusted_rates_for_fc(world.carrier_rates.get(fc.fc_id, {}), fc.zone, order.destination_zone)


def _flat_rates_for_order(order: Order, world: World) -> dict[tuple[str, ServiceLevel], CarrierRate]:
    return {
        (fc.fc_id, level): rate
        for fc in world.fcs.values()
        for level, rate in _rates_for_order(fc, order, world).items()
    }


def _price(legs: tuple[ShipmentLeg, ...], strategy: StrategyType, order: Order, world: World) -> Option:
    priced = price_option(legs, _flat_rates_for_order(order, world), world.unit_prices, order.promise_date)
    return Option(
        strategy=strategy,
        legs=legs,
        shipping_cost=priced.shipping_cost,
        on_time=priced.on_time,
        eta=max(leg.eta for leg in legs),
        days_late=priced.days_late,
        penalty_cost=priced.penalty_cost,
        late_refund=priced.late_refund,
    )


def build_consolidated_options(order: Order, world: World) -> list[Option]:
    options = []
    for fc in world.fcs.values():
        legs = all_candidate_legs_for_fc(
            fc, order.line_items, _rates_for_order(fc, order, world),
            world.inventory, world.capacity, order.promise_date, world.now,
        )
        for leg in legs:
            options.append(_price((leg,), StrategyType.CONSOLIDATED, order, world))
    return options


def build_backorder_fallback_options(order: Order, world: World) -> list[Option]:
    """Extra Consolidated options via each FC's backorder/restock path, even
    for FCs that currently have enough stock to supply this order fresh.

    `all_candidate_legs_for_fc` returns fresh-stock legs *or* backorder
    legs, never both, because Stage 4's live re-check only needs whichever
    one is true at the instant it claims -- if an earlier claim in the same
    pass has already consumed the fresh stock, the next order's live
    rebuild naturally lands on the backorder branch. routing/optimization_ilp.py
    can't rely on that: it snapshots every contested order's options once,
    up front, and solves them jointly, so a losing order needs its backorder
    fallback visible in that same snapshot or it looks unroutable when it
    isn't."""
    options = []
    for fc in world.fcs.values():
        if not fc_can_supply(fc.fc_id, order.line_items, world.inventory):
            continue  # build_options already covers this FC via the normal backorder branch
        legs = backorder_legs_for_fc(fc, order.line_items, _rates_for_order(fc, order, world), world.inventory, world.now)
        options.extend(_price((leg,), StrategyType.CONSOLIDATED, order, world) for leg in legs)
    return options


def build_multi_date_options(order: Order, world: World, horizon_days: int = MULTI_DATE_HORIZON_DAYS) -> list[Option]:
    """ILP-only: one Consolidated option per (FC, service level, ship date)
    across a window of ship dates, instead of feasible_legs_for_fc's single
    greedy 'earliest available' date.

    The sequential arms recompute their one date live, right before each
    claim, so a later order naturally lands on 'tomorrow' once today fills
    up. The ILP snapshots every contested order's options once, before any
    of them commit, so if every order only ever sees the same single
    greedy date, the joint solve can never discover 'ship a day later at
    the same FC' -- it looks like that FC has no room at all once the
    first date's capacity is spoken for. Enumerating the window here makes
    that choice visible to the solver directly; its own per-(FC, date)
    resource constraint (see optimization_ilp._solve_contested) decides
    who actually gets which date."""
    options: list[Option] = []
    for fc in world.fcs.values():
        if not fc_can_supply(fc.fc_id, order.line_items, world.inventory):
            continue
        rates = _rates_for_order(fc, order, world)
        ship_date = cutoff_and_handling_baseline(fc, world.now)
        for _ in range(horizon_days + 1):
            if world.capacity.remaining(fc.fc_id, ship_date) > 0:
                for service_level, rate in rates.items():
                    leg = ShipmentLeg(
                        fc_id=fc.fc_id,
                        service_level=service_level,
                        line_items=order.line_items,
                        ship_date=ship_date,
                        eta=compute_eta(ship_date, rate.transit_business_days),
                        capacity_threatened=False,
                    )
                    options.append(_price((leg,), StrategyType.CONSOLIDATED, order, world))
            ship_date = next_business_day(ship_date)
    return options


def _best_leg_for_item(item: LineItem, order: Order, world: World) -> ShipmentLeg | None:
    """Best (fc, service) leg for one item: on-time beats late, then
    lowest effective cost."""
    candidates: list[ShipmentLeg] = []
    for fc in world.fcs.values():
        candidates.extend(
            all_candidate_legs_for_fc(
                fc, (item,), _rates_for_order(fc, order, world),
                world.inventory, world.capacity, order.promise_date, world.now,
            )
        )
    if not candidates:
        return None

    flat_rates = _flat_rates_for_order(order, world)

    def rank(leg: ShipmentLeg) -> tuple[bool, float]:
        on_time = leg.eta <= order.promise_date
        priced = price_option((leg,), flat_rates, world.unit_prices, order.promise_date)
        return (not on_time, priced.effective_cost)

    return min(candidates, key=rank)


def build_split_option(order: Order, world: World) -> Option | None:
    if not order.is_multi_item:
        return None

    chosen_legs: list[ShipmentLeg] = []
    for item in order.line_items:
        best = _best_leg_for_item(item, order, world)
        if best is None:
            return None  # some item has no feasible source at all -> no Split option
        chosen_legs.append(best)

    fcs_used = {leg.fc_id for leg in chosen_legs}
    if len(fcs_used) < 2:
        return None  # collapsed onto one FC -> identical to Consolidated, not a real split

    merged: dict[tuple[str, str], ShipmentLeg] = {}
    for leg in chosen_legs:
        key = (leg.fc_id, leg.service_level)
        if key in merged:
            existing = merged[key]
            merged[key] = ShipmentLeg(
                fc_id=existing.fc_id,
                service_level=existing.service_level,
                line_items=existing.line_items + leg.line_items,
                ship_date=existing.ship_date,
                eta=existing.eta,
                capacity_threatened=existing.capacity_threatened or leg.capacity_threatened,
            )
        else:
            merged[key] = leg

    return _price(tuple(merged.values()), StrategyType.SPLIT, order, world)


def build_multi_date_split_options(
    order: Order, world: World, horizon_days: int = MULTI_DATE_HORIZON_DAYS
) -> list[Option]:
    """ILP-only: date-shift variants of build_split_option's one merged
    Split, one merged leg at a time.

    Split's per-item FC/service assignment is otherwise unchanged from
    build_split_option -- only each leg's *date* varies here, and only one
    leg at a time, holding every other leg at its original date. That
    covers the common case (one leg's FC is capacity-crowded, the other
    isn't) without a full cross-product search across every leg's dates,
    which would grow combinatorially with the number of FCs in the split."""
    base = build_split_option(order, world)
    if base is None:
        return []

    variants: list[Option] = []
    for i, leg in enumerate(base.legs):
        fc = world.fcs[leg.fc_id]
        rate = _rates_for_order(fc, order, world)[leg.service_level]
        ship_date = cutoff_and_handling_baseline(fc, world.now)
        for _ in range(horizon_days + 1):
            if world.capacity.remaining(fc.fc_id, ship_date) > 0:
                shifted_leg = ShipmentLeg(
                    fc_id=leg.fc_id,
                    service_level=leg.service_level,
                    line_items=leg.line_items,
                    ship_date=ship_date,
                    eta=compute_eta(ship_date, rate.transit_business_days),
                    capacity_threatened=False,
                )
                new_legs = base.legs[:i] + (shifted_leg,) + base.legs[i + 1 :]
                variants.append(_price(new_legs, StrategyType.SPLIT, order, world))
            ship_date = next_business_day(ship_date)
    return variants


def build_options(order: Order, world: World) -> list[Option]:
    options = build_consolidated_options(order, world)
    split = build_split_option(order, world)
    if split is not None:
        options.append(split)
    return options
