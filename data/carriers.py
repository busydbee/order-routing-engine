"""(FC, service level) -> rate."""
from routing.models import CarrierRate, ServiceLevel

CARRIER_RATES: dict[str, dict[ServiceLevel, CarrierRate]] = {
    "FC-A": {
        ServiceLevel.GROUND: CarrierRate("FC-A", ServiceLevel.GROUND, base_fee=5.0, per_unit_fee=1.0, transit_business_days=2),
        ServiceLevel.EXPEDITED: CarrierRate("FC-A", ServiceLevel.EXPEDITED, base_fee=15.0, per_unit_fee=2.0, transit_business_days=1),
    },
    "FC-B": {
        ServiceLevel.GROUND: CarrierRate("FC-B", ServiceLevel.GROUND, base_fee=8.0, per_unit_fee=2.5, transit_business_days=2),
        ServiceLevel.EXPEDITED: CarrierRate("FC-B", ServiceLevel.EXPEDITED, base_fee=20.0, per_unit_fee=3.5, transit_business_days=1),
    },
    "FC-C": {
        ServiceLevel.GROUND: CarrierRate("FC-C", ServiceLevel.GROUND, base_fee=6.0, per_unit_fee=1.2, transit_business_days=2),
        ServiceLevel.EXPEDITED: CarrierRate("FC-C", ServiceLevel.EXPEDITED, base_fee=16.0, per_unit_fee=2.2, transit_business_days=1),
    },
    "FC-D": {
        ServiceLevel.GROUND: CarrierRate("FC-D", ServiceLevel.GROUND, base_fee=4.0, per_unit_fee=0.8, transit_business_days=2),
        ServiceLevel.EXPEDITED: CarrierRate("FC-D", ServiceLevel.EXPEDITED, base_fee=14.0, per_unit_fee=1.8, transit_business_days=1),
    },
    "FC-E": {
        ServiceLevel.GROUND: CarrierRate("FC-E", ServiceLevel.GROUND, base_fee=3.0, per_unit_fee=1.0, transit_business_days=2),
        ServiceLevel.EXPEDITED: CarrierRate("FC-E", ServiceLevel.EXPEDITED, base_fee=13.0, per_unit_fee=1.5, transit_business_days=1),
    },
}
