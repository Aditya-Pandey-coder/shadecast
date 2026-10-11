#!/usr/bin/env python3
"""Emit the hourly heat data the supervisor page needs, and inline it into the page.

The browser runs the shift search itself (web/optimiser.js, held identical to the
Python optimiser by tests/test_js_parity.py), so what ships here is the PHYSICS, not
precomputed answers: one hourly array per day, workload and acclimatisation state.
That is what lets someone put their own crew size, required hours and span limit in
and watch the plan move, instead of clicking between canned scenarios.
"""

from __future__ import annotations

import json

from shadecast.forecast import (
    BHOPAL,
    DEMO_DAYS,
    day_label,
    day_note,
    day_source,
    is_real,
    synthetic_day,
)
from shadecast.policy import HourResource, clear_crew, sample_crew
from shadecast.schedule import (
    DEFAULT_MAX_SPAN_HOURS,
    SPAN_IF_COMMUTING,
    SPAN_IF_ON_SITE,
    HourSlot,
)
from shadecast.wbgt import TLV_TABLE, WORKLOAD_DESCRIPTIONS, wbgt_outdoor

CREW_BASE = 12
WAGE_BASE = 450.0

out = {
    "site": "Kolar Road site, Bhopal",
    "crew_base": CREW_BASE,
    "wage_base": WAGE_BASE,
    "span_commuting": SPAN_IF_COMMUTING,
    "span_on_site": SPAN_IF_ON_SITE,
    "default_span": DEFAULT_MAX_SPAN_HOURS,
    "workload_descriptions": WORKLOAD_DESCRIPTIONS,
    # The ACGIH ceilings, so the chart can draw the lines the curve has to stay under.
    "tlv": {
        load: [{"wbgt": c, "fraction": f} for c, f in rows]
        for load, rows in TLV_TABLE.items()
    },
    "days": {
        k: {
            "label": day_label(k),
            "note": day_note(k),
            "source": day_source(k),
            "observed": is_real(k),
        }
        for k in DEMO_DAYS
    },
    "hours": {},
    "policy": {},
}

for day_key in DEMO_DAYS:
    forecast = synthetic_day(day_key)
    for workload in ("light", "moderate", "heavy"):
        for acc in (True, False):
            slots = []
            for fh in forecast:
                b = wbgt_outdoor(
                    fh.temp_c, fh.rh_pct, fh.shortwave_wm2, fh.wind_ms,
                    fh.time_utc, BHOPAL["lat"], BHOPAL["lon"],
                )
                slots.append(HourSlot(fh.time_ist, b, workload, acc))
            key = f"{day_key}|{workload}|{'acc' if acc else 'unacc'}"
            out["hours"][key] = [s.as_dict() for s in slots]

            # Crew clearance depends on the bands present in the day, not on which
            # hours get chosen, so it is computed per scenario over every hour and
            # the page filters it to the planned hours.
            try:
                out["policy"][key] = clear_crew(
                    sample_crew(CREW_BASE),
                    [HourResource(s.local_time.hour, s.band, round(s.wbgt.wbgt_c, 1))
                     for s in slots],
                ).as_dict()
            except Exception as exc:
                out["policy"][key] = {"policy_available": False, "error": str(exc)}

with open("web/scenarios.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
print(f"wrote web/scenarios.json ({len(out['hours'])} hourly datasets)")

observed = [k for k, v in out["days"].items() if v["observed"]]
print("observed days:", ", ".join(observed) if observed else "none — using generated profiles")

with open("web/index.template.html", encoding="utf-8") as fh:
    tpl = fh.read()
with open("web/scenarios.json", encoding="utf-8") as fh:
    blob = fh.read()
with open("web/optimiser.js", encoding="utf-8") as fh:
    opt = fh.read()

page = tpl.replace("__SCENARIOS__", blob).replace("/*__OPTIMISER__*/", opt)
with open("web/index.html", "w", encoding="utf-8") as fh:
    fh.write(page)
print(f"wrote web/index.html ({len(page)//1024} KB)")
