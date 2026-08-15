"""Render a static report.md comparing optimization methods."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from routing.baselines import BaselineResult
from routing.models import Assignment, Option, Order
from routing.optimization_ilp import ILPResult
from routing.pipeline import PipelineResult
from routing.stage1_eligibility import EligibilityDecision

from data.inventory import UNIT_PRICES

ARM_WITH_CONTENTION = "Routing-with-Contention"
ARM_NAIVE = "Routing-Naive"
ARM_ILP = "Routing-with-ILP-Contention"
COL_CONTENTION = "contention"
COL_NAIVE = "naive"
COL_ILP = "ilp"
COL_DELTA = "delta (contention − naive)"
COL_DELTA_ILP = "delta (ilp − contention)"
LATE_REFUND_LABEL = "late refund"

# Tags group orders that share a scarce resource or sort-order interaction.
SCENARIO_TAGS: dict[str, str] = {
    "O1": "inventory",
    "O2": "inventory",
    "O3": "split",
    "O4": "tiebreak",
    "O5": "fc-a-capacity",
    "O6": "zone",
    "O7": "fastpath",
    "O8": "fc-d-capacity",
    "O9": "fc-d-capacity",
    "O10": "tiebreak",
    "O11": "inventory",
    "O12": "inventory",
    "O13": "fc-a-capacity",
    "O14": "zone",
    "O15": "eligibility",
    "O16": "eligibility",
}

SCENARIO_NOTES: dict[str, str] = {
    "O1": "Last-unit inventory contention with O2 for WIDGET-Z at FC-A.",
    "O2": "Same last unit as O1; loses the race, falls back to a late restock path.",
    "O3": "Three-item order where a stockout at FC-A makes Split cheaper than Consolidated.",
    "O4": "FC-A capacity is tight; survives the one-day slip, stays Safe with FC-C as backup.",
    "O5": "FC-A capacity slip breaks its on-time path; rerouted to FC-C.",
    "O6": "Uncontested single-item order; customer is 2 zones from FC-C (zone surcharge applies).",
    "O7": "Uncontested single-item order; adjacent zone, no surcharge.",
    "O8": "Shares FC-D daily capacity with O9 but has FC-C as a second on-time option (Safe).",
    "O9": "At-risk for FC-D capacity; contention processes it before O8 to protect the tighter promise.",
    "O10": "Shares promise date with O4; higher regret cost puts it first among Safe orders.",
    "O11": "Hopeless — promise already unreachable; escalated instead of auto-routing late.",
    "O12": "At-risk for the last LASTUNIT-2 at FC-C; claims before hopeless O11.",
    "O13": "Drains most of FC-A's daily capacity ahead of O5.",
    "O14": "Zone-distance demo — FC-E looks cheapest on list rate but FC-B wins after zone surcharge.",
    "O15": "Slack promise, no candidate FC cutoff passed yet — deferred from this pass's routing.",
    "O16": "Same zone/slack profile as O15, but promise date is inside the urgency deadline — admitted immediately.",
}

IMPACT_NOTES: dict[str, str] = {
    "O1": "Claimed before O2 on the contention method; won the last WIDGET-Z at FC-A. No change vs naive.",
    "O2": "Lost the last-unit race to O1 on the contention method. No change vs naive.",
    "O3": "No impact — same outcome on both methods.",
    "O4": "No impact — same outcome on both methods.",
    "O5": "No impact — same outcome on both methods.",
    "O6": "No impact — uncontested; same outcome on both methods.",
    "O7": "No impact — uncontested; same outcome on both methods.",
    "O8": "O9 processed first on the contention method; O9 claims FC-D capacity before O8. No change vs naive.",
    "O9": "Processed before O8 to protect this tighter promise; saved $13.00 vs naive.",
    "O10": "Processed before O4 on regret tiebreak. No change vs naive.",
    "O11": "O11 demoted to save O12; escalating O11 costs $2.20 more than auto-routing it late (vs naive) but frees the unit O12 needed.",
    "O12": "Saved O12's ETA by demoting O11 in the queue; the salvageable order claimed the last unit first (+$2.20 vs naive).",
    "O13": "Processed before O5 on the contention method; paid on-time freight to protect this promise (+$26.00 vs naive).",
    "O14": "No impact — uncontested; same outcome on both methods.",
    "O16": "No impact — uncontested; same outcome on both methods.",
}


def _impact_note(order_id: str) -> str:
    return IMPACT_NOTES.get(order_id, "")


@dataclass(frozen=True)
class OrderCostRow:
    order_id: str
    pipeline_shipping: float | None
    pipeline_penalty: float | None
    pipeline_refund: float | None
    pipeline_effective: float | None
    pipeline_on_time: bool | None
    pipeline_fcs: str
    pipeline_escalated: bool
    naive_shipping: float | None
    naive_penalty: float | None
    naive_refund: float | None
    naive_effective: float | None
    naive_on_time: bool | None
    naive_fcs: str
    naive_escalated: bool
    effective_delta: float | None
    ilp_shipping: float | None
    ilp_penalty: float | None
    ilp_refund: float | None
    ilp_effective: float | None
    ilp_on_time: bool | None
    ilp_fcs: str
    ilp_escalated: bool
    ilp_delta: float | None  # ilp_effective - pipeline_effective


def _service_levels_label(assignment: Assignment) -> str:
    if assignment.chosen_option is None:
        return "—"
    return ", ".join(sorted({leg.service_level.value for leg in assignment.chosen_option.legs}))


def _decision_label(assignment: Assignment) -> str:
    """Generic across methods -- only reads chosen_option/strategy/FC/service,
    none of which are pipeline-specific."""
    if assignment.chosen_option is None:
        return "ESCALATE"
    opt = assignment.chosen_option
    return f"{opt.strategy.value}, {_fcs_label(assignment)}, {_service_levels_label(assignment)}"


def _pipeline_rationale(order: Order, assignment: Assignment) -> str:
    if assignment.escalated or assignment.chosen_option is None:
        rejected = assignment.escalated_option
        if rejected is None:
            return (
                "No surviving option within the 7-day lateness bound after live re-check; "
                "flagged ESCALATE for human review instead of auto-assigning a late route."
            )
        return (
            f"No option within the 7-day lateness bound after live re-check; cheapest rejected option "
            f"would cost ${rejected.effective_cost:.2f} effective cost (shipping ${rejected.shipping_cost:.2f} "
            f"+ penalty ${rejected.penalty_cost:.2f} + {LATE_REFUND_LABEL} ${rejected.late_refund:.2f}), "
            f"{rejected.days_late}d late — flagged ESCALATE for human review instead of auto-assigning it."
        )

    opt = assignment.chosen_option
    routing = (
        "Uncontested — assigned immediately on the fast path."
        if not assignment.contested
        else f"Contested ({assignment.category.value if assignment.category else 'unknown'}) — "
        f"the risk-first sort claims it before lower-priority orders."
    )
    if opt.on_time:
        cost = f"Cheapest on-time option still available at claim time: ${opt.shipping_cost:.2f} shipping, ETA {opt.eta} (promise {order.promise_date})."
    else:
        cost = (
            f"No on-time option left; chose minimum effective cost ${opt.effective_cost:.2f} "
            f"(shipping ${opt.shipping_cost:.2f} + penalty ${opt.penalty_cost:.2f} + {LATE_REFUND_LABEL} ${opt.late_refund:.2f}), "
            f"ETA {opt.eta} ({opt.days_late}d late)."
        )
    return f"{routing} {cost}"


def _pipeline_rationale_dataframe(
    orders: list[Order],
    pipeline: PipelineResult,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order": order.order_id,
                "tag": SCENARIO_TAGS.get(order.order_id, ""),
                "scenario": SCENARIO_NOTES.get(order.order_id, ""),
                f"{COL_CONTENTION} decision": _decision_label(pipeline.assignments[order.order_id]),
                "why this FC / service / cost": _pipeline_rationale(order, pipeline.assignments[order.order_id]),
                "impact": _impact_note(order.order_id),
            }
            for order in sorted(orders, key=lambda o: o.arrival_index)
        ]
    )


def _ilp_rationale_dataframe(orders: list[Order], ilp: ILPResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order": order.order_id,
                "tag": SCENARIO_TAGS.get(order.order_id, ""),
                "scenario": SCENARIO_NOTES.get(order.order_id, ""),
                f"{COL_ILP} decision": _decision_label(ilp.assignments[order.order_id]),
                "why this FC / service / cost": ilp.assignments[order.order_id].trade_off,
            }
            for order in sorted(orders, key=lambda o: o.arrival_index)
        ]
    )


def _display_option(assignment: Assignment) -> Option | None:
    """chosen_option is None whenever escalated. escalated_option is the cheapest option the pipeline
    priced and rejected for being past the lateness bound: real, already-
    known cost (the order is already this late) even though ops, not the
    router, decides what happens next. The report prices it the same as
    any other order's cost."""
    return assignment.chosen_option or assignment.escalated_option


def _fcs_label(assignment: Assignment) -> str:
    opt = _display_option(assignment)
    if opt is None:
        return "—"
    return ", ".join(sorted({leg.fc_id for leg in opt.legs}))


def _cost_fields(assignment: Assignment) -> tuple[float | None, float | None, float | None, float | None, bool | None]:
    opt = _display_option(assignment)
    if opt is None:
        return None, None, None, None, None
    return opt.shipping_cost, opt.penalty_cost, opt.late_refund, opt.effective_cost, opt.on_time


def build_order_cost_rows(
    orders: list[Order],
    pipeline: PipelineResult,
    naive: BaselineResult,
    ilp: ILPResult,
) -> list[OrderCostRow]:
    rows: list[OrderCostRow] = []
    for order in sorted(orders, key=lambda o: o.arrival_index):
        p = pipeline.assignments[order.order_id]
        n = naive.assignments[order.order_id]
        i = ilp.assignments[order.order_id]
        p_ship, p_pen, p_ref, p_eff, p_on = _cost_fields(p)
        n_ship, n_pen, n_ref, n_eff, n_on = _cost_fields(n)
        i_ship, i_pen, i_ref, i_eff, i_on = _cost_fields(i)
        delta = None
        if p_eff is not None and n_eff is not None:
            delta = round(p_eff - n_eff, 2)
        ilp_delta = None
        if i_eff is not None and p_eff is not None:
            ilp_delta = round(i_eff - p_eff, 2)
        rows.append(
            OrderCostRow(
                order_id=order.order_id,
                pipeline_shipping=p_ship,
                pipeline_penalty=p_pen,
                pipeline_refund=p_ref,
                pipeline_effective=p_eff,
                pipeline_on_time=p_on,
                pipeline_fcs=_fcs_label(p),
                pipeline_escalated=p.escalated,
                naive_shipping=n_ship,
                naive_penalty=n_pen,
                naive_refund=n_ref,
                naive_effective=n_eff,
                naive_on_time=n_on,
                naive_fcs=_fcs_label(n),
                naive_escalated=n.escalated,
                effective_delta=delta,
                ilp_shipping=i_ship,
                ilp_penalty=i_pen,
                ilp_refund=i_ref,
                ilp_effective=i_eff,
                ilp_on_time=i_on,
                ilp_fcs=_fcs_label(i),
                ilp_escalated=i.escalated,
                ilp_delta=ilp_delta,
            )
        )
    return rows


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:.2f}"


def _on_time_label(value: bool | None, escalated: bool) -> str:
    if escalated:
        return "ESCALATE"
    if value is None:
        return "—"
    return "yes" if value else "no"


def _order_value(order: Order, unit_prices: dict[str, float] = UNIT_PRICES) -> float:
    return sum(unit_prices[item.sku] * item.qty for item in order.line_items)


def _pct_label(part: float, total: float) -> str:
    if total == 0:
        return "—"
    return f"{100 * part / total:.1f}%"


def _on_time_metrics(
    rows: list[OrderCostRow],
    orders: list[Order],
    on_time_getter,
    unit_prices: dict[str, float] = UNIT_PRICES,
) -> tuple[int, str, str]:
    order_by_id = {order.order_id: order for order in orders}
    total_orders = len(rows)
    total_value = sum(_order_value(order_by_id[row.order_id], unit_prices) for row in rows)
    on_time_count = sum(1 for row in rows if on_time_getter(row) is True)
    on_time_value = sum(
        _order_value(order_by_id[row.order_id], unit_prices)
        for row in rows
        if on_time_getter(row) is True
    )
    return (
        on_time_count,
        _pct_label(on_time_count, total_orders),
        _pct_label(on_time_value, total_value),
    )


def _total_order_value(orders: list[Order], unit_prices: dict[str, float] = UNIT_PRICES) -> float:
    return sum(_order_value(order, unit_prices) for order in orders)


def _cost_per_order_value_label(cost: float | None, total_value: float) -> str:
    if cost is None or total_value == 0:
        return "—"
    return _pct_label(cost, total_value)


def _pct_point_delta(pipeline_pct: str, naive_pct: str) -> str:
    if pipeline_pct == "—" or naive_pct == "—":
        return "—"
    pipeline_value = float(pipeline_pct.rstrip("%"))
    naive_value = float(naive_pct.rstrip("%"))
    return f"{pipeline_value - naive_value:+.1f} pp"


def _comparison_dataframe(rows: list[OrderCostRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order": row.order_id,
                f"{COL_CONTENTION} FC(s)": row.pipeline_fcs,
                f"{COL_CONTENTION} shipping": _money(row.pipeline_shipping),
                f"{COL_CONTENTION} penalty": _money(row.pipeline_penalty),
                f"{COL_CONTENTION} {LATE_REFUND_LABEL}": _money(row.pipeline_refund),
                f"{COL_CONTENTION} effective": _money(row.pipeline_effective),
                f"{COL_CONTENTION} on time": _on_time_label(row.pipeline_on_time, row.pipeline_escalated),
                f"{COL_NAIVE} FC(s)": row.naive_fcs,
                f"{COL_NAIVE} shipping": _money(row.naive_shipping),
                f"{COL_NAIVE} penalty": _money(row.naive_penalty),
                f"{COL_NAIVE} {LATE_REFUND_LABEL}": _money(row.naive_refund),
                f"{COL_NAIVE} effective": _money(row.naive_effective),
                f"{COL_NAIVE} on time": _on_time_label(row.naive_on_time, row.naive_escalated),
                COL_DELTA: "—" if row.effective_delta is None else f"${row.effective_delta:.2f}",
                f"{COL_ILP} FC(s)": row.ilp_fcs,
                f"{COL_ILP} shipping": _money(row.ilp_shipping),
                f"{COL_ILP} penalty": _money(row.ilp_penalty),
                f"{COL_ILP} {LATE_REFUND_LABEL}": _money(row.ilp_refund),
                f"{COL_ILP} effective": _money(row.ilp_effective),
                f"{COL_ILP} on time": _on_time_label(row.ilp_on_time, row.ilp_escalated),
                COL_DELTA_ILP: "—" if row.ilp_delta is None else f"${row.ilp_delta:.2f}",
            }
            for row in rows
        ]
    )


def _summary_dataframe(rows: list[OrderCostRow], orders: list[Order]) -> pd.DataFrame:
    def total_effective(getter):
        values = [getter(r) for r in rows if getter(r) is not None]
        return round(sum(values), 2) if values else None

    pipeline_total = total_effective(lambda r: r.pipeline_effective)
    naive_total = total_effective(lambda r: r.naive_effective)
    ilp_total = total_effective(lambda r: r.ilp_effective)
    delta = None
    if pipeline_total is not None and naive_total is not None:
        delta = round(pipeline_total - naive_total, 2)
    ilp_delta_total = None
    if ilp_total is not None and pipeline_total is not None:
        ilp_delta_total = round(ilp_total - pipeline_total, 2)

    pipeline_on_time, pipeline_orders_pct, pipeline_value_pct = _on_time_metrics(
        rows, orders, lambda r: r.pipeline_on_time
    )
    naive_on_time, naive_orders_pct, naive_value_pct = _on_time_metrics(
        rows, orders, lambda r: r.naive_on_time
    )
    ilp_on_time, ilp_orders_pct, ilp_value_pct = _on_time_metrics(
        rows, orders, lambda r: r.ilp_on_time
    )
    total_order_value = _total_order_value(orders)
    pipeline_cost_per_value = _cost_per_order_value_label(pipeline_total, total_order_value)
    naive_cost_per_value = _cost_per_order_value_label(naive_total, total_order_value)
    ilp_cost_per_value = _cost_per_order_value_label(ilp_total, total_order_value)

    return pd.DataFrame(
        [
            {
                "method": COL_CONTENTION,
                "orders on time": pipeline_on_time,
                "% orders on time": pipeline_orders_pct,
                "% order value on time": pipeline_value_pct,
                "orders escalated": sum(1 for r in rows if r.pipeline_escalated),
                "total effective cost": _money(pipeline_total),
                "cost per order value": pipeline_cost_per_value,
            },
            {
                "method": COL_NAIVE,
                "orders on time": naive_on_time,
                "% orders on time": naive_orders_pct,
                "% order value on time": naive_value_pct,
                "orders escalated": sum(1 for r in rows if r.naive_escalated),
                "total effective cost": _money(naive_total),
                "cost per order value": naive_cost_per_value,
            },
            {
                "method": COL_DELTA,
                "orders on time": pipeline_on_time - naive_on_time,
                "% orders on time": _pct_point_delta(pipeline_orders_pct, naive_orders_pct),
                "% order value on time": _pct_point_delta(pipeline_value_pct, naive_value_pct),
                "orders escalated": sum(1 for r in rows if r.pipeline_escalated) - sum(1 for r in rows if r.naive_escalated),
                "total effective cost": "—" if delta is None else f"${delta:.2f}",
                "cost per order value": _pct_point_delta(pipeline_cost_per_value, naive_cost_per_value),
            },
            {
                "method": COL_ILP,
                "orders on time": ilp_on_time,
                "% orders on time": ilp_orders_pct,
                "% order value on time": ilp_value_pct,
                "orders escalated": sum(1 for r in rows if r.ilp_escalated),
                "total effective cost": _money(ilp_total),
                "cost per order value": ilp_cost_per_value,
            },
            {
                "method": COL_DELTA_ILP,
                "orders on time": ilp_on_time - pipeline_on_time,
                "% orders on time": _pct_point_delta(ilp_orders_pct, pipeline_orders_pct),
                "% order value on time": _pct_point_delta(ilp_value_pct, pipeline_value_pct),
                "orders escalated": sum(1 for r in rows if r.ilp_escalated) - sum(1 for r in rows if r.pipeline_escalated),
                "total effective cost": "—" if ilp_delta_total is None else f"${ilp_delta_total:.2f}",
                "cost per order value": _pct_point_delta(ilp_cost_per_value, pipeline_cost_per_value),
            },
        ]
    )


def _processing_order_dataframe(pipeline: PipelineResult, naive: BaselineResult) -> pd.DataFrame:
    max_len = max(len(pipeline.processing_order), len(naive.processing_order))
    return pd.DataFrame(
        {
            "step": list(range(1, max_len + 1)),
            COL_CONTENTION: pipeline.processing_order + [""] * (max_len - len(pipeline.processing_order)),
            COL_NAIVE: naive.processing_order + [""] * (max_len - len(naive.processing_order)),
        }
    )


def _buffer_days_label(buffer_days: int | None) -> str:
    if buffer_days is None:
        return "no path"
    return f"{buffer_days:+d}"


def _time_to_promise_dataframe(pipeline: PipelineResult, orders: list[Order]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order": order.order_id,
                "service level (classification)": (
                    f"{order.service_level_requested.value} ({pipeline.service_level_categories[order.order_id].value})"
                ),
                "promise date": str(order.promise_date),
                "buffer days": _buffer_days_label(pipeline.time_to_promise_snapshots[order.order_id].buffer_days),
                "Stage 2 on-time options": pipeline.time_to_promise_snapshots[order.order_id].on_time_options,
                "contested": pipeline.contention.is_contested(order.order_id),
            }
            for order in sorted(orders, key=lambda o: o.arrival_index)
        ]
    )


def _eligibility_dataframe(orders: list[Order], eligibility: dict[str, EligibilityDecision]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order": order.order_id,
                "promise date": str(order.promise_date),
                "destination zone": order.destination_zone,
                "eligible this pass": "yes" if eligibility[order.order_id].eligible else "no — deferred",
                "reason": eligibility[order.order_id].reason,
            }
            for order in sorted(orders, key=lambda o: o.arrival_index)
        ]
    )


def _dataframe_to_markdown(df: pd.DataFrame) -> str:
    return df.to_markdown(index=False)


def _render_section(
    title: str,
    tables: list[tuple[str | None, pd.DataFrame]],
    note: str | None = None,
) -> str:
    parts = [f"## {title}", ""]
    if note:
        parts.extend([note, ""])
    for subtitle, table in tables:
        if subtitle:
            parts.extend([f"### {subtitle}", ""])
        parts.append(_dataframe_to_markdown(table))
        parts.append("")
    return "\n".join(parts).rstrip()


REPORT_INTRO = (
    "Three methods route the same batch, compared below:\n\n"
    f"- **{ARM_WITH_CONTENTION}** — The proposed pipeline stages 1–4. Capture service level, filter feasible FC options, and "
    "scan for contested inventory/capacity. Sort contested orders by delivery risk (At-risk → Safe → "
    "Hopeless) to protect the EDD (estimated delivery date). Claim sequentially with live re-checks.\n"
    f"- **{ARM_NAIVE}** — Same claim logic, but raw arrival order: no contention scan, no risk-first sort. "
    "When resources are scarce, sequencing decides who wins, changing cost and on-time outcomes.\n"
    f"- **{ARM_ILP}** — Replaces Stage 3's sort and Stage 4's sequential claim for the contested subset. "
    "One joint mixed-integer program (`pulp`/CBC) solves it against the same shared inventory/capacity "
    "limits, instead of one order claiming at a time. Uncontested orders still take the same fast path "
    "as the other two methods.\n\n"
    "**What each method optimizes.** Per order, all three use the same rule: cheapest on-time option if "
    f"one exists, otherwise minimum effective cost (shipping + penalty + {LATE_REFUND_LABEL}). They "
    "differ in batch-level priority:\n\n"
    f"- {ARM_ILP} uses one weighted objective instead of a sequential rule: a bigger "
    "penalty on escalation than on a late option keeps on-time dominant.\n"
    f"- {ARM_WITH_CONTENTION} and {ARM_ILP} both protect promise dates on contested orders. They "
    "maximize **% orders on time** and **% order value on time**, even at higher **total effective "
    "cost** / **cost per order value**.\n"
    f"- {ARM_NAIVE} skips those service metrics. \n"
    f"- {COL_DELTA_ILP} Compares solving jointly with sequential sort on this contested subset."
)


def render_report_markdown(
    orders: list[Order],
    eligibility: dict[str, EligibilityDecision],
    pipeline: PipelineResult,
    naive: BaselineResult,
    ilp: ILPResult,
) -> str:
    """`orders` is the full batch, including any order Stage 1 deferred.
    `pipeline`/`naive`/`ilp` only see the eligible subset (run.py filters
    before calling them), so every table below except eligibility itself
    covers only that subset."""
    eligible_orders = [o for o in orders if eligibility[o.order_id].eligible]
    deferred_count = len(orders) - len(eligible_orders)
    rows = build_order_cost_rows(eligible_orders, pipeline, naive, ilp)
    sections = [
        _render_section(
            "Batch summary",
            [(None, _summary_dataframe(rows, eligible_orders))],
        ),
        _render_section(
            "Stage 1 — eligibility (wave release)",
            [(None, _eligibility_dataframe(orders, eligibility))],
            note=(
                "An order is eligible once *either* a candidate FC (geography-only lookup, zone distance "
                "<= MAX_CANDIDATE_ZONE_DISTANCE) has already hit its pack cutoff today, *or* its own "
                "promise date is within URGENT_DEADLINE_DAYS — whichever comes first. Deferred orders "
                "skip Stage 2, the contention scan, and every method below this pass; they stay queued "
                "for later."
            ),
        ),
        _render_section(
            f"Per-order cost: {ARM_WITH_CONTENTION} vs {ARM_NAIVE} vs {ARM_ILP}",
            [
                (None, _comparison_dataframe(rows)),
                (f"{ARM_WITH_CONTENTION} scenario and rationale", _pipeline_rationale_dataframe(eligible_orders, pipeline)),
                (f"{ARM_ILP} scenario and rationale", _ilp_rationale_dataframe(eligible_orders, ilp)),
            ],
            note=(
                f"Fabricated {len(orders)}-order batch:\n\n"
                "- 14 orders cover inventory contention, FC capacity limits, split vs consolidated "
                "routing, zone-distance pricing, and risk-first sequencing.\n"
                "- 2 orders exercise Stage 1's eligibility gate above "
                f"({deferred_count} deferred, {len(eligible_orders)} reach the tables below).\n"
                "- The cost table compares all three methods; the rationale tables explain what each order "
                "tests and each method's FC/service/cost choice."
            ),
        ),
        _render_section(
            "Processing order",
            [(None, _processing_order_dataframe(pipeline, naive))],
            note=(
                "Claim sequence when inventory or FC capacity is scarce. "
                "**step** = who goes first, second, third. "
                f"**{ARM_WITH_CONTENTION}** = uncontested orders first, then contested orders sorted by risk. "
                f"**{ARM_NAIVE}** = raw arrival order. "
                "When the two columns differ, sequencing changed who won a shared resource — see the per-order cost table above. "
                f"**{ARM_ILP}** isn't shown here: its contested subset is solved jointly in one shot. "
            ),
        ),
        _render_section(
            "Stage 1 & 2 — time to promise and feasibility",
            [(None, _time_to_promise_dataframe(pipeline, eligible_orders))],
            note=(
                "- **service level (classification)** — Stage 1's coarse urgency category (High/Low, based "
                "on requested service level).\n"
                "- **buffer days** — promise date minus the fastest ETA any FC could hit if this order had "
                "the batch's starting inventory/capacity to itself.\n"
                "- **Stage 2 on-time options** — how many of Stage 2's feasible options actually clear that "
                "promise.\n\n"
                "This snapshot runs before any order claims anything, so negative buffer days or 0 on-time "
                "options mean the promise was already tight before contention. Deferred orders never reach "
                "this snapshot."
            ),
        ),
    ]
    body = "\n\n".join(sections)
    return f"""# Order routing report

{REPORT_INTRO}

Effective cost = shipping + late penalty + {LATE_REFUND_LABEL}.

{body}
"""


def write_report(
    orders: list[Order],
    eligibility: dict[str, EligibilityDecision],
    pipeline: PipelineResult,
    naive: BaselineResult,
    ilp: ILPResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.write_text(render_report_markdown(orders, eligibility, pipeline, naive, ilp), encoding="utf-8")
    return path
