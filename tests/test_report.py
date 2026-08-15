"""Tests for report/report.py."""
from pathlib import Path

from data.fulfillment_centers import FULFILLMENT_CENTERS
from data.orders import ORDERS
from data.world import build_world
from report.report import (
    ARM_ILP,
    ARM_NAIVE,
    ARM_WITH_CONTENTION,
    COL_DELTA,
    COL_DELTA_ILP,
    COL_NAIVE,
    build_order_cost_rows,
    write_report,
)
from routing.baselines import run_naive_baseline
from routing.optimization_ilp import run_ilp_contention
from routing.pipeline import run_pipeline
from routing.shipping_calendar import NOW
from routing.stage1_eligibility import decide_eligibility_for_orders

ELIGIBILITY = decide_eligibility_for_orders(ORDERS, FULFILLMENT_CENTERS, NOW)
ELIGIBLE_ORDERS = [o for o in ORDERS if ELIGIBILITY[o.order_id].eligible]


def _run_all_arms():
    pipeline = run_pipeline(ELIGIBLE_ORDERS, build_world())
    naive = run_naive_baseline(ELIGIBLE_ORDERS, build_world())
    ilp = run_ilp_contention(ELIGIBLE_ORDERS, build_world())
    return pipeline, naive, ilp


def test_build_order_cost_rows_includes_per_order_delta():
    pipeline, naive, ilp = _run_all_arms()
    rows = build_order_cost_rows(ELIGIBLE_ORDERS, pipeline, naive, ilp)

    assert len(rows) == len(ELIGIBLE_ORDERS)
    o12 = next(row for row in rows if row.order_id == "O12")
    assert o12.pipeline_on_time is True
    assert o12.naive_on_time is False
    assert o12.effective_delta is not None
    assert o12.effective_delta != 0
    assert o12.ilp_on_time is not None
    assert o12.ilp_effective is not None
    assert o12.ilp_delta is not None


def test_escalated_order_cost_is_shown_not_dashed_out():
    """O11 escalates on both the contention and ILP arms, but the cost of
    its cheapest rejected option is already known (it's late regardless of
    what human review decides) -- it should be reported and counted like
    any other order's cost, not hidden behind a dash just because nothing
    was committed to the ledger."""
    pipeline, naive, ilp = _run_all_arms()
    rows = build_order_cost_rows(ELIGIBLE_ORDERS, pipeline, naive, ilp)
    o11 = next(row for row in rows if row.order_id == "O11")

    assert o11.pipeline_escalated is True
    assert o11.pipeline_effective is not None
    assert o11.pipeline_effective > 0
    assert o11.pipeline_on_time is False  # it's late, not "no decision"

    assert o11.ilp_escalated is True
    assert o11.ilp_effective is not None
    assert o11.ilp_effective > 0


def test_write_report_creates_markdown_with_per_order_cost_table(tmp_path: Path):
    pipeline, naive, ilp = _run_all_arms()
    output = write_report(ORDERS, ELIGIBILITY, pipeline, naive, ilp, tmp_path / "report.md")

    md = output.read_text(encoding="utf-8")
    assert "# Order routing report" in md
    assert md.find("Effective cost = shipping") < md.find("## Batch summary")
    assert "## Stage 1 — eligibility (wave release)" in md
    assert "O15" in md
    assert "no — deferred" in md
    assert "## Batch summary" in md
    assert f"## Per-order cost: {ARM_WITH_CONTENTION} vs {ARM_NAIVE} vs {ARM_ILP}" in md
    assert "Fabricated 16-order batch" in md
    assert f"### {ARM_WITH_CONTENTION} scenario and rationale" in md
    assert f"### {ARM_ILP} scenario and rationale" in md
    assert "| impact" in md
    assert "Saved O12's ETA by demoting O11" in md
    assert "O11 demoted to save O12" in md
    assert "Capture service level" in md
    assert "What each method optimizes" in md
    assert "why this FC / service / cost" in md
    assert "Last-unit inventory contention with O2" in md
    assert "Claim sequence when inventory or FC capacity is scarce" in md
    assert "solved jointly" in md
    assert "O12" in md
    assert COL_DELTA in md
    assert COL_DELTA_ILP in md
    assert "% orders on time" in md
    assert "% order value on time" in md
    assert "cost per order value" in md
    assert ARM_WITH_CONTENTION in md
    assert ARM_NAIVE in md
    assert ARM_ILP in md


def test_write_report_excludes_deferred_order_from_downstream_tables(tmp_path: Path):
    pipeline, naive, ilp = _run_all_arms()
    output = write_report(ORDERS, ELIGIBILITY, pipeline, naive, ilp, tmp_path / "report.md")

    md = output.read_text(encoding="utf-8")
    # O15 is deferred -- it has its own row only in the eligibility table,
    # never in a table scoped to the routed subset (batch summary / per-order
    # cost). O16's own scenario note mentions "O15" by name in prose, so
    # check for a table row (leading "| O15 |"), not the bare substring.
    before_eligibility, rest = md.split("## Stage 1 — eligibility (wave release)")
    eligibility_section, downstream = rest.split("## Per-order cost", 1)
    assert "| O15 " not in before_eligibility
    assert "| O15 " in eligibility_section
    assert "| O15 " not in downstream
