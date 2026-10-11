#!/usr/bin/env python3
"""
Shadecast demo runner.

    python3 run_demo.py                      # offline, representative Bhopal May day
    python3 run_demo.py --day monsoon_break  # the cooler, more dangerous day
    python3 run_demo.py --live               # real NOAA GFS forecast from S3
    python3 run_demo.py --compare            # the headline: dry day vs humid day
    python3 run_demo.py --json               # machine-readable, for the web UI
"""

from __future__ import annotations

import argparse
import sys

from shadecast.forecast import BHOPAL, DEMO_DAYS, fetch_gfs, synthetic_day
from shadecast.report import shift_sheet, sms_hindi, to_json
from shadecast.schedule import HourSlot, recommend
from shadecast.wbgt import wbgt_outdoor


def build_slots(hours, workload, lat, lon, shade_fraction=0.0, acclimatised=True):
    slots = []
    for fh in hours:
        breakdown = wbgt_outdoor(
            temp_c=fh.temp_c,
            rh_pct=fh.rh_pct,
            shortwave_wm2=fh.shortwave_wm2,
            wind_ms=fh.wind_ms,
            when_utc=fh.time_utc,
            lat_deg=lat,
            lon_deg=lon,
            shade_fraction=shade_fraction,
        )
        slots.append(
            HourSlot(
                local_time=fh.time_ist,
                wbgt=breakdown,
                workload=workload,
                acclimatised=acclimatised,
            )
        )
    return slots


def main() -> int:
    p = argparse.ArgumentParser(description="Shadecast shift planner")
    p.add_argument("--day", default="pre_monsoon", choices=sorted(DEMO_DAYS))
    p.add_argument("--live", action="store_true", help="fetch real GFS data from S3")
    p.add_argument("--compare", action="store_true", help="dry day vs humid day")
    p.add_argument("--json", action="store_true")
    p.add_argument("--workload", default="moderate",
                   choices=["light", "moderate", "heavy"])
    p.add_argument("--crew", type=int, default=12)
    p.add_argument("--wage", type=float, default=450.0)
    p.add_argument("--required", type=float, default=6.0)
    p.add_argument("--site", default="Kolar Road site")
    p.add_argument("--unacclimatised", action="store_true")
    args = p.parse_args()

    lat, lon = BHOPAL["lat"], BHOPAL["lon"]

    if args.compare:
        print("\nTHE CASE AGAINST TEMPERATURE-BASED HEAT WARNINGS")
        print("=" * 64)
        summary = {}
        for key in ("pre_monsoon", "monsoon_break"):
            spec = DEMO_DAYS[key]
            slots = build_slots(synthetic_day(key), args.workload, lat, lon,
                                acclimatised=not args.unacclimatised)
            rec = recommend(slots, args.crew, args.wage, args.required)
            day = [s for s in slots if 9 <= s.local_time.hour <= 17]
            peak_air = max(s.wbgt.air_c for s in day)
            peak_wbgt = max(s.wbgt.wbgt_c for s in day)
            lost = sum(1 - s.fraction for s in day)
            print(f"\n  {spec['label']}")
            print(f"    {spec['note']}")
            print(f"    Peak air temperature   {peak_air:5.1f} C")
            print(f"    Peak WBGT              {peak_wbgt:5.1f} C")
            print(f"    Work hours lost 09-18  {lost:5.1f} of 9")
            print(f"    Best plan              {rec.plan.label()}")
            summary[key] = (peak_air, peak_wbgt, lost)

        dry_air, dry_wbgt, dry_lost = summary["pre_monsoon"]
        wet_air, wet_wbgt, wet_lost = summary["monsoon_break"]
        print("\n" + "=" * 64)
        print(f"  The July day is {dry_air - wet_air:.1f} C COOLER in air temperature,")
        print(f"  and {wet_wbgt - dry_wbgt:+.1f} C in WBGT: the same heat strain on the body,")
        print(f"  costing the same {wet_lost:.0f} lost work hours out of 9.")
        print()
        print("  India's heat warnings trigger on air temperature. At 31.5 C,")
        print("  nothing fires. The humidity is what does the damage, and it is")
        print("  the one variable the warning system does not look at.")
        print("=" * 64 + "\n")
        return 0

    if args.live:
        hours = fetch_gfs(lat=lat, lon=lon, hours=30)
        if not hours:
            print("No GFS data retrieved. Falling back to the offline profile.",
                  file=sys.stderr)
            hours = synthetic_day(args.day)
        else:
            # A 12Z cycle begins at 17:30 IST, so a 30-hour fetch straddles two
            # local dates and neither is complete. Plan for whichever local day
            # actually has the working window covered, preferring the later one:
            # a supervisor reads this in the evening to plan tomorrow.
            by_date = {}
            for h in hours:
                by_date.setdefault(h.time_ist.date(), []).append(h)
            def coverage(rows):
                return len({r.time_ist.hour for r in rows if 5 <= r.time_ist.hour <= 20})
            best = max(sorted(by_date), key=lambda d: (coverage(by_date[d]), d))
            if coverage(by_date[best]) >= 12:
                hours = by_date[best]
                print(f"[gfs] planning for {best} "
                      f"({coverage(by_date[best])} of 16 working hours covered)")
    else:
        hours = synthetic_day(args.day)

    slots = build_slots(hours, args.workload, lat, lon,
                        acclimatised=not args.unacclimatised)
    rec = recommend(slots, args.crew, args.wage, args.required)

    if args.json:
        print(to_json(rec, args.site))
    else:
        print()
        print(shift_sheet(rec, args.site))
        print()
        print("  SMS TO EVERY WORKER ON THE CREW")
        print("  " + "-" * 44)
        for line in sms_hindi(rec, args.site).splitlines():
            print(f"  | {line}")
        print("  " + "-" * 44)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
