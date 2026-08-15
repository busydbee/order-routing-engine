"""Comparison baselines — comparison-only, never used for live routing."""
from __future__ import annotations

from dataclasses import dataclass

from routing.models import Assignment, Order, World
from routing.stage4_claim import claim_order


@dataclass
class BaselineResult:
    assignments: dict[str, Assignment]
    processing_order: list[str]


def run_naive_baseline(orders: list[Order], world: World) -> BaselineResult:
    """Process every order in raw arrival order with the same Stage 4 claim
    logic as the pipeline, but no contention scan and no Stage 3 sort."""
    processing_order: list[str] = []
    assignments: dict[str, Assignment] = {}

    for order in sorted(orders, key=lambda o: o.arrival_index):
        assignments[order.order_id] = claim_order(order, world, contested=False)
        processing_order.append(order.order_id)

    return BaselineResult(assignments=assignments, processing_order=processing_order)
