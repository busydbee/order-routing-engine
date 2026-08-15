"""Stage 2: per-item, per-FC live feasibility filter. Collapses each FC to
its cheapest on-time service; the late branch keeps every service level
distinct since they differ in days late and refund eligibility."""
from __future__ import annotations

from datetime import date, datetime

from routing.capacity import CapacityLedger
from routing.models import CarrierRate, FC, LineItem, ServiceLevel, ShipmentLeg
from routing.shipping_calendar import NOW, compute_eta, earliest_ship_date


class InventoryLedger:
    """(FC, SKU) -> on-hand qty, + restock_date for backordered SKUs."""

    def __init__(
        self,
        on_hand: dict[tuple[str, str], int],
        restock_dates: dict[tuple[str, str], date] | None = None,
    ):
        self._on_hand = dict(on_hand)
        self._restock_dates = dict(restock_dates or {})

    def qty_on_hand(self, fc_id: str, sku: str) -> int:
        return self._on_hand.get((fc_id, sku), 0)

    def restock_date(self, fc_id: str, sku: str) -> date | None:
        return self._restock_dates.get((fc_id, sku))

    def commit(self, fc_id: str, sku: str, qty: int) -> None:
        key = (fc_id, sku)
        self._on_hand[key] = self._on_hand.get(key, 0) - qty


def fc_can_supply(fc_id: str, line_items: tuple[LineItem, ...], inventory: InventoryLedger) -> bool:
    return all(inventory.qty_on_hand(fc_id, li.sku) >= li.qty for li in line_items)


def _rate_cost(rate: CarrierRate, units: int) -> float:
    return rate.base_fee + rate.per_unit_fee * units


def feasible_legs_for_fc(
    fc: FC,
    line_items: tuple[LineItem, ...],
    carrier_rates_at_fc: dict[ServiceLevel, CarrierRate],
    inventory: InventoryLedger,
    capacity: CapacityLedger,
    promise_date: date,
    now: datetime = NOW,
) -> list[ShipmentLeg]:
    """One leg per distinct option this FC offers: the cheapest on-time
    service if any meets the promise, else every service distinctly.
    Empty if the FC can't supply these items from current stock."""
    if not fc_can_supply(fc.fc_id, line_items, inventory):
        return []

    units_needed = sum(li.qty for li in line_items)
    ship_date, capacity_threatened = earliest_ship_date(fc, capacity, units_needed, now)

    candidate_legs = [
        ShipmentLeg(
            fc_id=fc.fc_id,
            service_level=service_level,
            line_items=line_items,
            ship_date=ship_date,
            eta=compute_eta(ship_date, rate.transit_business_days),
            capacity_threatened=capacity_threatened,
        )
        for service_level, rate in carrier_rates_at_fc.items()
    ]

    on_time_legs = [leg for leg in candidate_legs if leg.eta <= promise_date]
    if not on_time_legs:
        return candidate_legs

    cheapest = min(
        on_time_legs,
        key=lambda leg: _rate_cost(carrier_rates_at_fc[leg.service_level], units_needed),
    )
    return [cheapest]


def backorder_legs_for_fc(
    fc: FC,
    line_items: tuple[LineItem, ...],
    carrier_rates_at_fc: dict[ServiceLevel, CarrierRate],
    inventory: InventoryLedger,
    now: datetime = NOW,
) -> list[ShipmentLeg]:
    """Late-option fallback when current stock can't supply these items.
    Requires every item to have a restock_date at this FC; ship date is
    the latest of them."""
    restock_dates = [inventory.restock_date(fc.fc_id, li.sku) for li in line_items]
    if any(rd is None for rd in restock_dates):
        return []

    ship_date = max(restock_dates)
    return [
        ShipmentLeg(
            fc_id=fc.fc_id,
            service_level=service_level,
            line_items=line_items,
            ship_date=ship_date,
            eta=compute_eta(ship_date, rate.transit_business_days),
            capacity_threatened=False,
        )
        for service_level, rate in carrier_rates_at_fc.items()
    ]


def all_candidate_legs_for_fc(
    fc: FC,
    line_items: tuple[LineItem, ...],
    carrier_rates_at_fc: dict[ServiceLevel, CarrierRate],
    inventory: InventoryLedger,
    capacity: CapacityLedger,
    promise_date: date,
    now: datetime = NOW,
) -> list[ShipmentLeg]:
    """Current-stock legs if the FC can supply now, else the backorder
    fallback."""
    if fc_can_supply(fc.fc_id, line_items, inventory):
        return feasible_legs_for_fc(fc, line_items, carrier_rates_at_fc, inventory, capacity, promise_date, now)
    return backorder_legs_for_fc(fc, line_items, carrier_rates_at_fc, inventory, now)
