"""Builds a fresh World from the fixture data. Returns a new instance on
every call -- callers must not share one World across multiple runs."""
from __future__ import annotations

from routing.capacity import CapacityLedger
from routing.models import World
from routing.shipping_calendar import NOW
from routing.stage2_feasibility import InventoryLedger

from data.carriers import CARRIER_RATES
from data.fulfillment_centers import FULFILLMENT_CENTERS
from data.inventory import ON_HAND, RESTOCK_DATES, UNIT_PRICES


def build_world() -> World:
    inventory = InventoryLedger(dict(ON_HAND), dict(RESTOCK_DATES))
    capacity = CapacityLedger(FULFILLMENT_CENTERS)
    return World(
        fcs=FULFILLMENT_CENTERS,
        carrier_rates=CARRIER_RATES,
        unit_prices=UNIT_PRICES,
        inventory=inventory,
        capacity=capacity,
        now=NOW,
    )
