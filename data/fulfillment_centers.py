"""The 4 fabricated FCs."""
from datetime import time

from routing.models import FC

FULFILLMENT_CENTERS: dict[str, FC] = {
    "FC-A": FC(fc_id="FC-A", cutoff_time=time(12, 0), handling_days=0, capacity_units_per_day=10),
    "FC-B": FC(fc_id="FC-B", cutoff_time=time(23, 0), handling_days=0, capacity_units_per_day=50),
    "FC-C": FC(fc_id="FC-C", cutoff_time=time(23, 0), handling_days=0, capacity_units_per_day=50),
    "FC-D": FC(fc_id="FC-D", cutoff_time=time(23, 0), handling_days=0, capacity_units_per_day=6),
    "FC-E": FC(fc_id="FC-E", cutoff_time=time(23, 0), handling_days=0, capacity_units_per_day=50, zone=3),
}
