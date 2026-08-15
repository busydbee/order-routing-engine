"""Shipping-zone distance proxy. Every FC and every order carries an
integer zone number (like a real carrier zone chart); the zone distance
is just their difference. Zone 0-1 apart is the rate as filed. Each zone
beyond that adds a linear surcharge and transit penalty -- a simple proxy
for how real zone charts scale, not real carrier data."""
from __future__ import annotations

from dataclasses import replace

from routing.models import CarrierRate, ServiceLevel

SURCHARGE_PER_EXTRA_ZONE = 3.5  # $/unit, per zone beyond the adjacent one
DAYS_PER_EXTRA_ZONE = 1  # extra business days, per zone beyond the adjacent one


def zone_distance(fc_zone: int, destination_zone: int) -> int:
    return abs(fc_zone - destination_zone)


def zone_adjusted_rate(rate: CarrierRate, distance: int) -> CarrierRate:
    extra_zones = max(0, distance - 1)
    if extra_zones == 0:
        return rate
    return replace(
        rate,
        per_unit_fee=rate.per_unit_fee + SURCHARGE_PER_EXTRA_ZONE * extra_zones,
        transit_business_days=rate.transit_business_days + extra_zones,
    )


def zone_adjusted_rates_for_fc(
    carrier_rates_at_fc: dict[ServiceLevel, CarrierRate],
    fc_zone: int,
    destination_zone: int,
) -> dict[ServiceLevel, CarrierRate]:
    distance = zone_distance(fc_zone, destination_zone)
    return {level: zone_adjusted_rate(rate, distance) for level, rate in carrier_rates_at_fc.items()}
