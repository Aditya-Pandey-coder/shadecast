#!/usr/bin/env python3
"""Register one worksite. Run once, before the first planner run."""
from decimal import Decimal
import boto3

site = {
    "site_id": "kolar-road",
    "name": "Kolar Road site",
    "lat": Decimal("23.26"),
    "lon": Decimal("77.41"),
    "grid": "23.25,77.50",       # the GFS cell this site falls in
    "crew_size": 12,
    "daily_wage": Decimal("450"),
    "required_hours": Decimal("6"),
    "workload": "moderate",       # light | moderate | heavy
    "acclimatised": True,
    "shade_fraction": Decimal("0"),
}
boto3.resource("dynamodb").Table("shadecast-sites").put_item(Item=site)
print(f"registered {site['site_id']} in grid cell {site['grid']}")
