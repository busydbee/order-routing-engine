#!/usr/bin/env python3
"""Run the routing pipeline and naive baseline, then write report.md."""
from __future__ import annotations

from data.fulfillment_centers import FULFILLMENT_CENTERS
from data.orders import ORDERS
from data.world import build_world
from report.report import write_report
from routing.baselines import run_naive_baseline
from routing.optimization_ilp import run_ilp_contention
from routing.pipeline import run_pipeline
from routing.shipping_calendar import NOW
from routing.stage1_eligibility import decide_eligibility_for_orders


def main() -> None:
    orders = ORDERS
    # Stage 1's real eligibility gate runs once, ahead of all three arms --
    # every arm should see the same set of orders actually in front of the
    # router this pass. Deferred orders never reach any arm's Stage 2.
    eligibility = decide_eligibility_for_orders(orders, FULFILLMENT_CENTERS, NOW)
    eligible_orders = [o for o in orders if eligibility[o.order_id].eligible]

    pipeline_world = build_world()
    pipeline_result = run_pipeline(eligible_orders, pipeline_world)

    naive_world = build_world()
    naive_result = run_naive_baseline(eligible_orders, naive_world)

    ilp_world = build_world()
    ilp_result = run_ilp_contention(eligible_orders, ilp_world)

    output = write_report(orders, eligibility, pipeline_result, naive_result, ilp_result, "docs/report.md")
    print(f"Wrote {output.resolve()}")


if __name__ == "__main__":
    main()
