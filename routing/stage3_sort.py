"""Stage 3: category-then-date-then-regret sort for the contested subset
only. Categorization only reads each option's `on_time` flag as given —
any capacity-pessimism adjustment is the caller's responsibility
(pipeline.py), done before these options reach this module."""
from __future__ import annotations

from datetime import date

from routing.cost import within_lateness_bound
from routing.models import Category, Option, Order

_CATEGORY_RANK = {Category.AT_RISK: 0, Category.SAFE: 1, Category.HOPELESS: 2}


def categorize(options: list[Option]) -> tuple[Category, float]:
    """Returns (category, regret_cost) for one order's options (plan
    Section 5, Step 2-4)."""
    on_time = sorted((o for o in options if o.on_time), key=lambda o: o.shipping_cost)
    late = sorted((o for o in options if not o.on_time), key=lambda o: o.effective_cost)

    if len(on_time) >= 2:
        return Category.SAFE, on_time[1].shipping_cost - on_time[0].shipping_cost

    if len(on_time) == 1:
        fallback_candidates = [o for o in late if within_lateness_bound(o.days_late)]
        if not fallback_candidates:
            return Category.AT_RISK, 0.0
        return Category.AT_RISK, fallback_candidates[0].effective_cost - on_time[0].shipping_cost

    if len(late) >= 2:
        return Category.HOPELESS, late[1].effective_cost - late[0].effective_cost
    return Category.HOPELESS, 0.0


def _sort_key(order: Order, category: Category, regret_cost: float) -> tuple[int, date, float]:
    return (_CATEGORY_RANK[category], order.promise_date, -regret_cost)


def sort_contested(
    orders: list[Order],
    options_by_order: dict[str, list[Option]],
) -> list[tuple[Order, Category, float]]:
    """Returns (order, category, regret_cost) triples in processing order:
    At-risk -> Safe -> Hopeless, then promise date ascending, then regret
    cost descending within each category."""
    scored = [
        (order, *categorize(options_by_order[order.order_id]))
        for order in orders
    ]
    scored.sort(key=lambda triple: _sort_key(*triple))
    return scored
