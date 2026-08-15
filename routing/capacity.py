"""Per-FC, per-day pick-pack capacity ledger, one shared pool per (FC, date)."""
from __future__ import annotations

from datetime import date

from routing.models import FC


class CapacityLedger:
    def __init__(self, fcs: dict[str, FC]):
        self._fcs = fcs
        self._used: dict[tuple[str, date], int] = {}

    def remaining(self, fc_id: str, d: date) -> int:
        cap = self._fcs[fc_id].capacity_units_per_day
        used = self._used.get((fc_id, d), 0)
        return cap - used

    def commit(self, fc_id: str, d: date, units: int) -> None:
        self._used[(fc_id, d)] = self._used.get((fc_id, d), 0) + units
