#!/usr/bin/env python3
"""
Run the whole Shadecast pipeline locally: register a site, ingest, plan, serve.

No AWS account. No credentials. No Docker. These are the real Lambda handlers from
aws/, invoked in order, against local JSON storage.

    python3 scripts/run_pipeline.py                 # live NOAA GFS from S3
    python3 scripts/run_pipeline.py --offline       # representative May day
    python3 scripts/run_pipeline.py --offline --day monsoon_break
    python3 scripts/run_pipeline.py --workload heavy --crew 20

What it proves, in order:
    1. the open GFS bucket is readable with no account
    2. the ingest handler stores forecast rows
    3. the planner handler reads them, computes WBGT, and produces a shift
    4. the API handler serves that plan as JSON, exactly as API Gateway would
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IST = timezone(timedelta(hours=5, minutes=30))

RULE = "─" * 68


def step(n: int, title: str) -> None:
    print(f"\n{RULE}\n  {n}. {title}\n{RULE}")


def main() -> int:
    p = argparse.ArgumentParser(description="Run the Shadecast pipeline end to end, locally")
    p.add_argument("--offline", action="store_true",
                   help="use a representative profile instead of fetching GFS")
    p.add_argument("--day", default="pre_monsoon",
                   choices=["pre_monsoon", "monsoon_break", "october"])
    p.add_argument("--workload", default="moderate",
                   choices=["light", "moderate", "heavy"])
    p.add_argument("--crew", type=int, default=12)
    p.add_argument("--wage", type=float, default=450.0)
    p.add_argument("--required", type=float, default=6.0)
    p.add_argument("--unacclimatised", action="store_true")
    p.add_argument("--site", default="Kolar Road site")
    p.add_argument("--reset", action="store_true", help="clear local data first")
    args = p.parse_args()

    os.environ.setdefault("SHADECAST_BACKEND", "local")

    from shadecast import store
    from shadecast.forecast import BHOPAL

    if args.reset and store.LOCAL_DIR.exists():
        for f in store.LOCAL_DIR.glob("*"):
            f.unlink()
        print(f"cleared {store.LOCAL_DIR}/")

    print(f"\n  SHADECAST pipeline · storage: {store.backend_name()}")

    # ---------------------------------------------------------------- 1
    step(1, "Register the worksite")
    site = {
        "site_id": "kolar-road",
        "name": args.site,
        "lat": BHOPAL["lat"],
        "lon": BHOPAL["lon"],
        "grid": f"{round(BHOPAL['lat'] * 4) / 4:.2f},{round(BHOPAL['lon'] * 4) / 4:.2f}",
        "crew_size": args.crew,
        "daily_wage": args.wage,
        "required_hours": args.required,
        "workload": args.workload,
        "acclimatised": not args.unacclimatised,
        "shade_fraction": 0,
    }
    store.table("sites").put_item(Item=site)
    print(f"  {site['name']} · crew of {site['crew_size']} · {site['workload']} work")
    print(f"  GFS grid cell {site['grid']}")

    # ---------------------------------------------------------------- 2
    from aws.lambda_ingest import handler as ingest_handler
    from aws.lambda_planner import api_handler, scheduled_handler

    step(2, "Ingest the forecast  ·  aws/lambda_ingest.handler")
    if not args.offline:
        print("  fetching from s3://noaa-gfs-bdp-pds (no AWS account required)…")
    result = ingest_handler(
        {"offline": args.offline, "demo_day": args.day, "verbose": not args.offline},
        None,
    )
    print(f"  source       {result['source']}")
    print(f"  cycle        {result['cycle']}")
    print(f"  rows written {result['rows_written']}")
    if result["rows_written"] == 0:
        print("\n  No forecast rows retrieved.")
        print("  NOAA's bucket may be slow or a cycle may still be publishing.")
        print("  Run the same pipeline on a stored profile instead:")
        print("      python3 scripts/run_pipeline.py --offline")
        return 1

    # Which local day did we actually get full coverage for?
    rows = store.table("weather").scan()["Items"]
    days: dict[str, int] = {}
    for r in rows:
        d = str(r["hour_ist"])[:10]
        if 5 <= int(str(r["hour_ist"])[11:13]) <= 20:
            days[d] = days.get(d, 0) + 1
    target = max(sorted(days), key=lambda d: (days[d], d)) if days else None
    print(f"  coverage     " + ", ".join(f"{d}: {n}/16 working hours"
                                          for d, n in sorted(days.items())))

    # ---------------------------------------------------------------- 3
    step(3, "Plan the shift  ·  aws/lambda_planner.scheduled_handler")
    planned = scheduled_handler({"for_date": target}, None)
    if not planned["planned"]:
        got = planned["skipped_no_forecast"]
        print(f"  not enough forecast coverage to plan safely: {got}")
        print("  The planner needs 12 of the 16 working hours. Either re-run to pick")
        print("  up the hours that dropped, or use --offline for a complete profile.")
        return 1
    row = planned["planned"][0]
    print(f"  planning for {planned['for_date']}")
    print(f"  shift        {'  +  '.join(row['shift'])}")
    print(f"  rescued      {row['hours_rescued']:+.1f} productive hours")
    print(f"  severe       {row['severe_removed']:.0f} hours of severe exposure removed")
    print(f"  notify       {row['notify']}")

    # ---------------------------------------------------------------- 4
    step(4, "Serve it  ·  aws/lambda_planner.api_handler")
    resp = api_handler(
        {"queryStringParameters": {"site_id": "kolar-road", "date": planned["for_date"]}},
        None,
    )
    print(f"  HTTP {resp['statusCode']}  ·  {len(resp['body'])} bytes of JSON")
    plan = json.loads(resp["body"])

    # ---------------------------------------------------------------- the output
    from shadecast.report import shift_sheet
    from shadecast.schedule import HourSlot, recommend
    from shadecast.wbgt import wbgt_outdoor

    print()
    print(RULE)
    print("  WHAT THE SUPERVISOR GETS")
    print(RULE)
    for h in plan["hours"]:
        if not (5 <= h["hour"] <= 20):
            continue
        mark = ">" if h["hour"] in plan["worked_hours"] else " "
        ratio = ("continuous" if h["rest_minutes"] == 0
                 else "STOP WORK" if h["band"] == "stop"
                 else f"{60 - h['rest_minutes']} min / {h['rest_minutes']} min")
        print(f"  {mark} {h['label']}   WBGT {h['wbgt']:5.1f}   air {h['air_c']:5.1f}"
              f"   RH {h['rh']:3.0f}%   {h['band']:<8} {ratio}")
    print(RULE)
    print(f"  On site            {plan['span_hours']} h"
          + (f" including a {plan['gap_hours']} h stand-down"
             if plan['gap_hours'] else " straight through"))
    print(f"  Productive hours   {plan['baseline_productive_hours']:.1f}  ->  "
          f"{plan['productive_hours']:.1f}   ({plan['hours_rescued']:+.1f})")
    print(f"  Wages protected    approx Rs {plan['wages_protected_inr']:,.0f} (estimate)")
    print(f"  Drinking water     {plan['water_litres_crew']:.0f} L for the crew")
    if not plan["meets_requirement"]:
        print(f"  !! short by {plan['shortfall_hours']:.1f} productive hours — "
              f"cut scope, do not push the crew")

    alt = plan.get("alternative")
    if alt:
        print()
        print(RULE)
        print("  THE OTHER OPTION, IF YOUR CREW SLEEPS ON SITE")
        print(RULE)
        print(f"  {'  +  '.join(alt['blocks'])}")
        print(f"  {alt['span_hours']} h on site ({alt['gap_hours']} h stand-down)  ·  "
              f"{alt['productive_hours']:.1f} productive  ·  "
              f"{alt['severe_hours_faced']:.0f} severe-risk hours")
        print(f"  Recommended plan: {plan['span_hours']} h on site  ·  "
              f"{plan['productive_hours']:.1f} productive  ·  "
              f"{plan['severe_hours_faced']:.0f} severe-risk hours")
        print()
        print("  Neither is strictly better. The split shift removes all severe")
        print("  exposure but keeps people on site for "
              f"{alt['span_hours']} hours. We default to the")
        print("  shorter day because most informal workers commute; set")
        print("  workers_commute=false on the site if yours sleep there.")

    pol = plan.get("policy")
    if pol and pol.get("policy_available"):
        print()
        print(RULE)
        print("  WHO MAY WORK IT  ·  Cedar policy, shadecast/policies/heat_safety.cedar")
        print(RULE)
        print(f"  {len(pol['fully_cleared'])} of {plan['crew_size']} cleared for every hour")
        shown = set()
        for e in pol["exclusions"]:
            if e["worker"] in shown:
                continue
            shown.add(e["worker"])
            n = sum(1 for x in pol["exclusions"] if x["worker"] == e["worker"])
            print(f"    {e['worker']:<10} held from {n} hour(s) — {e['reason']}")

    print()
    print(RULE)
    print("  WHAT EVERY WORKER GETS")
    print(RULE)
    for line in plan["sms_hi"].splitlines():
        print(f"  │ {line}")
    print(RULE)
    print(f"\n  Stored under {store.LOCAL_DIR}/ — set SHADECAST_BACKEND=dynamodb")
    print("  and the same handlers write to DynamoDB instead.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
