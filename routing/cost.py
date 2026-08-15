"""Single canonical source for pricing an Option."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from routing.models import CarrierRate, ServiceLevel, ShipmentLeg

MAX_ACCEPTABLE_LATENESS = 7  # days; beyond this, ESCALATE instead of auto-assign


def compute_late_penalty(line_item_value: float) -> float:
    return max(5.0, 0.03 * line_item_value)


def compute_days_late(eta: date, promise_date: date) -> int:
    return max(0, (eta - promise_date).days)


def within_lateness_bound(days_late: int) -> bool:
    return days_late <= MAX_ACCEPTABLE_LATENESS


def _group_units(legs: tuple[ShipmentLeg, ...]) -> dict[tuple[str, ServiceLevel], int]:
    """One shipment per (fc, service_level) pair, so legs sharing both
    share one base fee."""
    groups: dict[tuple[str, ServiceLevel], int] = {}
    for leg in legs:
        key = (leg.fc_id, leg.service_level)
        units = sum(li.qty for li in leg.line_items)
        groups[key] = groups.get(key, 0) + units
    return groups


@dataclass(frozen=True)
class PricedOption:
    shipping_cost: float
    on_time: bool
    days_late: int
    penalty_cost: float
    late_refund: float
    effective_cost: float


def price_option(
    legs: tuple[ShipmentLeg, ...],
    carrier_rates: dict[tuple[str, ServiceLevel], CarrierRate],
    unit_prices: dict[str, float],
    promise_date: date,
) -> PricedOption:
    worst_case_eta = max(leg.eta for leg in legs)
    days_late = compute_days_late(worst_case_eta, promise_date)
    on_time = days_late == 0

    groups = _group_units(legs)
    shipping_cost = sum(
        carrier_rates[key].base_fee + carrier_rates[key].per_unit_fee * units
        for key, units in groups.items()
    )

    if on_time:
        penalty_cost = 0.0
        late_refund = 0.0
    else:
        penalty_cost = sum(
            compute_late_penalty(unit_prices[li.sku] * li.qty)
            for leg in legs
            for li in leg.line_items
        )
        # Severe-lateness goodwill refund: we don't model actual carrier
        # delivery performance (eta is our own estimate, not a tracked
        # event), so this is our own policy, not a carrier guarantee.
        # Shipping cost is a sunk expense -- it's already been paid to the
        # carrier, late or not -- so the refund is an *additional* payout
        # on top of it, not something that cancels it out. It's capped so
        # penalty + refund together never exceed shipping cost, our
        # stand-in for what the merchant paid: that's a full refund, not
        # a bonus on top of one.
        if days_late >= MAX_ACCEPTABLE_LATENESS:
            late_refund = max(0.0, shipping_cost - penalty_cost)
        else:
            late_refund = 0.0

    effective_cost = shipping_cost + penalty_cost + late_refund
    return PricedOption(
        shipping_cost=shipping_cost,
        on_time=on_time,
        days_late=days_late,
        penalty_cost=penalty_cost,
        late_refund=late_refund,
        effective_cost=effective_cost,
    )
