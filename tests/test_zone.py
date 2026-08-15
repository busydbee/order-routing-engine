"""Tests for zone.py."""
from routing.models import CarrierRate, ServiceLevel
from routing.zone import zone_adjusted_rate, zone_adjusted_rates_for_fc, zone_distance


def rate(per_unit_fee=2.5, transit_business_days=2, service_level=ServiceLevel.GROUND):
    return CarrierRate("FC-X", service_level, base_fee=8.0, per_unit_fee=per_unit_fee, transit_business_days=transit_business_days)


def test_zone_distance_is_the_absolute_difference():
    assert zone_distance(1, 1) == 0
    assert zone_distance(1, 3) == 2
    assert zone_distance(5, 2) == 3


def test_same_or_adjacent_zone_is_unchanged():
    r = rate()
    assert zone_adjusted_rate(r, distance=0) == r
    assert zone_adjusted_rate(r, distance=1) == r


def test_each_zone_past_the_first_adds_linear_surcharge_and_transit_days():
    r = rate(per_unit_fee=1.0, transit_business_days=2)

    one_extra = zone_adjusted_rate(r, distance=2)
    assert one_extra.per_unit_fee == 1.0 + 3.5
    assert one_extra.transit_business_days == 2 + 1

    two_extra = zone_adjusted_rate(r, distance=3)
    assert two_extra.per_unit_fee == 1.0 + 3.5 * 2
    assert two_extra.transit_business_days == 2 + 2


def test_base_fee_is_untouched():
    r = rate()
    adjusted = zone_adjusted_rate(r, distance=4)
    assert adjusted.base_fee == r.base_fee
    assert adjusted.fc_id == r.fc_id
    assert adjusted.service_level == r.service_level


def test_zone_adjusted_rates_for_fc_applies_same_distance_to_every_service_level():
    rates = {
        ServiceLevel.GROUND: rate(per_unit_fee=1.0, service_level=ServiceLevel.GROUND),
        ServiceLevel.EXPEDITED: rate(per_unit_fee=2.0, transit_business_days=1, service_level=ServiceLevel.EXPEDITED),
    }
    adjusted = zone_adjusted_rates_for_fc(rates, fc_zone=1, destination_zone=3)
    assert adjusted[ServiceLevel.GROUND].per_unit_fee == 1.0 + 3.5
    assert adjusted[ServiceLevel.EXPEDITED].per_unit_fee == 2.0 + 3.5
