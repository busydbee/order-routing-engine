"""Core dataclasses shared by every stage of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum


class ServiceLevel(str, Enum):
    GROUND = "ground"
    STANDARD = "standard"
    EXPEDITED = "expedited"
    OVERNIGHT = "overnight"


class Category(str, Enum):
    SAFE = "safe"
    AT_RISK = "at_risk"
    HOPELESS = "hopeless"


class StrategyType(str, Enum):
    CONSOLIDATED = "consolidated"
    SPLIT = "split"


class ServiceLevelCategory(str, Enum):
    """Stage 1's service-level classification. Expedited/Overnight implies
    the shopper needs it soon (tight promise, little calendar slack) --
    high urgency. Standard/Ground implies more calendar room -- low
    urgency. See research.md Section 4."""
    HIGH_URGENCY = "high_urgency"  # Expedited / Overnight
    LOW_URGENCY = "low_urgency"  # Standard / Ground


@dataclass(frozen=True)
class LineItem:
    sku: str
    qty: int


@dataclass(frozen=True)
class FC:
    fc_id: str
    cutoff_time: time  # naive; compared directly against NOW, no per-FC timezone
    handling_days: int
    capacity_units_per_day: int
    zone: int = 1  # shipping-zone number, see routing/zone.py


@dataclass(frozen=True)
class CarrierRate:
    fc_id: str
    service_level: ServiceLevel
    base_fee: float
    per_unit_fee: float
    transit_business_days: int


@dataclass(frozen=True)
class Order:
    order_id: str
    promise_date: date
    service_level_requested: ServiceLevel  # proxy used by Stage 1 for service level classification
    line_items: tuple[LineItem, ...]
    arrival_index: int  # input order; used only as the final deterministic tiebreak
    destination_zone: int = 1  # shipping-zone number of the customer; see routing/zone.py

    @property
    def is_multi_item(self) -> bool:
        return len(self.line_items) > 1


@dataclass(frozen=True)
class ShipmentLeg:
    """One FC shipping some subset of an order's line items on one service level."""
    fc_id: str
    service_level: ServiceLevel
    line_items: tuple[LineItem, ...]
    ship_date: date
    eta: date
    capacity_threatened: bool  # True if ship_date already reflects a capacity slip


@dataclass(frozen=True)
class Option:
    """One complete way to fulfill an order: one or more shipment legs.
    `on_time`/`eta` reflect the latest (worst-case) leg."""
    strategy: StrategyType
    legs: tuple[ShipmentLeg, ...]
    shipping_cost: float
    on_time: bool
    eta: date
    days_late: int  # 0 if on_time
    penalty_cost: float  # 0 if on_time
    late_refund: float  # additional payout once days_late >= MAX_ACCEPTABLE_LATENESS, capped so penalty + refund <= shipping_cost; 0 otherwise
    effective_cost: float = field(init=False)

    def __post_init__(self):
        object.__setattr__(
            self,
            "effective_cost",
            self.shipping_cost + self.penalty_cost + self.late_refund,
        )


@dataclass
class World:
    """Bundles static config (fcs, rates, prices) with the two live
    ledgers (inventory, capacity) every stage needs."""
    fcs: dict[str, FC]
    carrier_rates: dict[str, dict[ServiceLevel, CarrierRate]]
    unit_prices: dict[str, float]
    inventory: "InventoryLedger"
    capacity: "CapacityLedger"
    now: datetime


@dataclass
class Assignment:
    """Stage 4's output for one order: the option it claimed (or none)."""
    order_id: str
    chosen_option: Option | None  # None only when escalated -- nothing was
    # committed to the inventory/capacity ledger for this order.
    category: Category | None  # None for uncontested orders (not computed)
    regret_cost: float | None
    escalated: bool
    trade_off: str  # plain-language explanation, Section 7's output contract
    contested: bool
    escalated_option: Option | None = None  # only set when escalated: the
    # cheapest option that was actually priced and rejected for being past
    # the lateness bound. Its cost is real -- the order is already this
    # late regardless of the human-review outcome -- so it is reported and
    # counted the same as any other order's cost, just never committed to
    # the ledger (see chosen_option above).
