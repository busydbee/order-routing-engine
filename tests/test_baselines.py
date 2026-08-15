"""Tests for routing/baselines.py."""
from data.orders import ORDERS
from data.world import build_world
from routing.baselines import run_naive_baseline
from routing.pipeline import run_pipeline


def test_naive_baseline_processes_orders_in_arrival_order():
    world = build_world()
    result = run_naive_baseline(ORDERS, world)
    assert result.processing_order == [o.order_id for o in sorted(ORDERS, key=lambda o: o.arrival_index)]


def test_naive_baseline_differs_from_pipeline_on_at_least_one_order():
    pipeline = run_pipeline(ORDERS, build_world())
    naive = run_naive_baseline(ORDERS, build_world())

    differences = []
    for order in ORDERS:
        p = pipeline.assignments[order.order_id]
        n = naive.assignments[order.order_id]
        if p.escalated != n.escalated:
            differences.append(order.order_id)
            continue
        if p.chosen_option is None or n.chosen_option is None:
            if p.chosen_option is not n.chosen_option:
                differences.append(order.order_id)
            continue
        if p.chosen_option.on_time != n.chosen_option.on_time:
            differences.append(order.order_id)
            continue
        if p.chosen_option.effective_cost != n.chosen_option.effective_cost:
            differences.append(order.order_id)

    assert differences, "expected naive arrival-order processing to change at least one outcome"
