"""(FC, SKU) -> qty on hand, restock_date, and unit_price fixtures."""
from datetime import date

ON_HAND: dict[tuple[str, str], int] = {
    ("FC-A", "WIDGET-Z"): 1,
    ("FC-A", "GADGET-1"): 5,
    ("FC-A", "GADGET-2"): 5,
    ("FC-B", "GADGET-1"): 5,
    ("FC-B", "GADGET-2"): 5,
    ("FC-B", "GADGET-3"): 5,
    ("FC-A", "ITEM-CAP-A"): 5,
    ("FC-C", "ITEM-CAP-A"): 5,
    ("FC-A", "ITEM-CAP-B"): 5,
    ("FC-C", "ITEM-CAP-B"): 5,
    ("FC-A", "ITEM-CAP-C"): 5,
    ("FC-B", "ITEM-CAP-C"): 5,
    ("FC-C", "SKU-UNC1"): 2,
    ("FC-B", "SKU-UNC2"): 2,
    ("FC-D", "SKU-O8"): 4,
    ("FC-C", "SKU-O8"): 4,
    ("FC-D", "SKU-O9"): 3,
    ("FC-C", "LASTUNIT-2"): 1,
    ("FC-A", "ITEM-DRAIN"): 20,
    ("FC-B", "ITEM-ZONE"): 10,
    ("FC-E", "ITEM-ZONE"): 10,
    ("FC-E", "ITEM-WAVE"): 10,
}

RESTOCK_DATES: dict[tuple[str, str], date] = {
    ("FC-A", "WIDGET-Z"): date(2026, 8, 20),
    ("FC-C", "LASTUNIT-2"): date(2026, 8, 20),
}

UNIT_PRICES: dict[str, float] = {
    "WIDGET-Z": 50.0,
    "GADGET-1": 20.0,
    "GADGET-2": 20.0,
    "GADGET-3": 40.0,
    "ITEM-CAP-A": 15.0,
    "ITEM-CAP-B": 18.0,
    "ITEM-CAP-C": 22.0,
    "SKU-UNC1": 25.0,
    "SKU-UNC2": 25.0,
    "SKU-O8": 30.0,
    "SKU-O9": 30.0,
    "LASTUNIT-2": 40.0,
    "ITEM-DRAIN": 10.0,
    "ITEM-ZONE": 25.0,
    "ITEM-WAVE": 12.0,
}
