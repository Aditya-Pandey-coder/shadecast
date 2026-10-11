"""
The ingest handler: NOAA GFS out of the open S3 bucket, into storage.

Like the planner, this runs unchanged locally or in Lambda; SHADECAST_BACKEND decides
whether rows land in JSON files or DynamoDB. Reading the bucket itself needs no AWS
account in either case -- the data is CC0 on the Registry of Open Data, and
shadecast/forecast.py fetches it over plain HTTPS with ranged GETs.

This is the only part of Shadecast with a native dependency (ecCodes, via cfgrib), which
is why it is kept away from the planner. If the GRIB stack ever fails to build, this
function stops producing tomorrow's forecast; it can never stop the planner producing
today's shift sheet from rows already stored.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from shadecast import store
from shadecast.forecast import BHOPAL, fetch_gfs, latest_cycle, synthetic_day

IST = timezone(timedelta(hours=5, minutes=30))

# Forecast rows expire after a week. A forecast has no value once it is history, and
# the TTL keeps the DynamoDB table inside the free tier indefinitely.
TTL_DAYS = 7

# 30 hours from any 6-hourly cycle always covers the whole of tomorrow in IST,
# which is what the planner needs. 48 just doubled the download for nothing.
FORECAST_HOURS = int(os.environ.get("SHADECAST_FORECAST_HOURS", "30"))


def grid_key(lat: float, lon: float) -> str:
    """GFS is a 0.25 degree grid, so snap to it. Every site in the same ~28 km cell
    shares one fetch and one set of rows."""
    return f"{round(lat * 4) / 4:.2f},{round(lon * 4) / 4:.2f}"


def handler(event, context):
    event = event or {}
    offline = bool(event.get("offline"))
    verbose = bool(event.get("verbose", False))
    demo_day = event.get("demo_day", "pre_monsoon")

    sites = store.table("sites").scan().get("Items", [])
    cells: dict[str, tuple[float, float]] = {}
    for s in sites:
        lat, lon = float(s["lat"]), float(s["lon"])
        cells.setdefault(grid_key(lat, lon), (lat, lon))
    if not cells:  # nothing registered yet: seed Bhopal so a first run still works
        cells = {grid_key(BHOPAL["lat"], BHOPAL["lon"]): (BHOPAL["lat"], BHOPAL["lon"])}

    expires = int((datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)).timestamp())
    table = store.table("weather")
    written = 0
    cycle_label = "offline-" + demo_day

    for key, (lat, lon) in cells.items():
        if offline:
            # Representative profile, for demos and for when the network is not there.
            hours = synthetic_day(demo_day)
        else:
            date, cycle = latest_cycle()
            cycle_label = f"{date}{cycle}"
            hours = fetch_gfs(lat=lat, lon=lon, hours=FORECAST_HOURS, date=date,
                              cycle=cycle, verbose=verbose)

        written += table.batch_put(
            {
                "grid": key,
                "hour_ist": fh.time_ist.isoformat(),
                "temp_c": round(fh.temp_c, 2),
                "rh_pct": round(fh.rh_pct, 1),
                "wind_ms": round(fh.wind_ms, 2),
                "shortwave_wm2": round(fh.shortwave_wm2, 1),
                "cycle": cycle_label,
                "expires_at": expires,
            }
            for fh in hours
        )

    return {
        "backend": store.backend_name(),
        "source": "offline profile" if offline else "s3://noaa-gfs-bdp-pds",
        "cycle": cycle_label,
        "cells": list(cells),
        "rows_written": written,
    }
