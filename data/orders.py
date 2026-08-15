"""The 16 fabricated orders: 14 exercise routing mechanics (see the plan's
"Fabricated scenario" section for what each order or pair proves), and 2
(O15/O16) exercise Stage 1's real eligibility gate instead."""
from datetime import date

from routing.models import LineItem, Order, ServiceLevel

ORDERS: list[Order] = [
    Order("O1", date(2026, 8, 18), ServiceLevel.STANDARD, (LineItem("WIDGET-Z", 1),), 0, destination_zone=2),
    Order("O2", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("WIDGET-Z", 1),), 1),
    Order(
        "O3", date(2026, 8, 20), ServiceLevel.STANDARD,
        (LineItem("GADGET-1", 3), LineItem("GADGET-2", 3), LineItem("GADGET-3", 1)), 2,
    ),
    Order("O4", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("ITEM-CAP-A", 3),), 3),
    Order("O5", date(2026, 8, 17), ServiceLevel.EXPEDITED, (LineItem("ITEM-CAP-B", 3),), 4),
    Order("O6", date(2026, 8, 18), ServiceLevel.STANDARD, (LineItem("SKU-UNC1", 2),), 5, destination_zone=3),
    Order("O7", date(2026, 8, 19), ServiceLevel.EXPEDITED, (LineItem("SKU-UNC2", 2),), 6, destination_zone=2),
    Order("O8", date(2026, 8, 17), ServiceLevel.STANDARD, (LineItem("SKU-O8", 4),), 7),
    Order("O9", date(2026, 8, 14), ServiceLevel.EXPEDITED, (LineItem("SKU-O9", 3),), 8),
    Order("O10", date(2026, 8, 20), ServiceLevel.STANDARD, (LineItem("ITEM-CAP-C", 2),), 9),
    Order("O11", date(2026, 8, 12), ServiceLevel.EXPEDITED, (LineItem("LASTUNIT-2", 1),), 10),
    Order("O12", date(2026, 8, 14), ServiceLevel.EXPEDITED, (LineItem("LASTUNIT-2", 1),), 11),
    Order("O13", date(2026, 8, 16), ServiceLevel.EXPEDITED, (LineItem("ITEM-DRAIN", 8),), 12),
    Order("O14", date(2026, 8, 25), ServiceLevel.STANDARD, (LineItem("ITEM-ZONE", 3),), 13, destination_zone=1),
    # O15/O16 exercise Stage 1's real eligibility gate (routing/stage1_eligibility.py),
    # not routing itself -- destination_zone=4 and ITEM-WAVE (stocked only at
    # FC-E, no other order touches it) keep them isolated from O1-O14's outcomes.
    Order("O15", date(2026, 8, 27), ServiceLevel.STANDARD, (LineItem("ITEM-WAVE", 2),), 14, destination_zone=4),
    Order("O16", date(2026, 8, 13), ServiceLevel.STANDARD, (LineItem("ITEM-WAVE", 2),), 15, destination_zone=4),
]
