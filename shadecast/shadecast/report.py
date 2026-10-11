"""Turn a Recommendation into the three things a human receives: a shift sheet,
an SMS in Hindi, and a JSON blob for the web UI."""

from __future__ import annotations

import json

from .schedule import HourSlot, Recommendation
from .wbgt import BAND_LABELS_HI, WORKLOAD_DESCRIPTIONS

def _worst_worked_hour(rec: Recommendation, payload: dict) -> dict | None:
    """The hardest hour the crew actually faces UNDER THE PLAN.

    Not the worst hour of the day. Quoting the day's worst hour would tell a crew
    that is working 05:00-11:00 to take 45-minute breaks they do not need, and a
    rule that is visibly wrong once is a rule nobody follows again.
    """
    worked: set[int] = set()
    for block in rec.plan.blocks:
        worked.update(block.hours)
    candidates = [h for h in payload["hours"] if h["hour"] in worked]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h["wbgt"])


BAND_GLYPH = {
    "safe": "###",
    "caution": "## ",
    "high": "#  ",
    "severe": "!  ",
    "stop": "XXX",
}


def shift_sheet(rec: Recommendation, site_name: str = "Site") -> str:
    """The supervisor's printable plan. Deliberately plain text: it gets printed on
    a ten-year-old inkjet and taped to the site office wall."""
    r = rec.as_dict()
    lines: list[str] = []
    w = 64

    lines.append("=" * w)
    lines.append(f"  SHADECAST  |  {site_name}  |  {r['date_label']}")
    lines.append(f"  Crew of {r['crew_size']}  |  {r['workload'].title()} work")
    lines.append(f"  {WORKLOAD_DESCRIPTIONS[r['workload']]}")
    lines.append("=" * w)
    lines.append("")

    lines.append(f"  TODAY'S SHIFT   {'  +  '.join(r['recommended_blocks'])}")
    lines.append(f"  ON SITE         {r['span_hours']} hours"
                 + (f", including a {r['gap_hours']} hour stand-down"
                    if r['gap_hours'] else ", straight through"))
    if r["stop_work_hour"] is not None:
        lines.append(f"  HARD STOP       {r['stop_work_hour']:02d}:00  (no work past this hour)")
    lines.append(f"  DRINKING WATER  {r['water_litres_crew']:.0f} litres for the crew")
    lines.append("")

    lines.append("-" * w)
    lines.append("  HOUR    WBGT   AIR    RH    RISK       WORK / REST")
    lines.append("-" * w)
    worked = set()
    for b in rec.plan.blocks:
        worked.update(b.hours)

    for h in r["hours"]:
        if not (5 <= h["hour"] <= 20):
            continue
        marker = ">" if h["hour"] in worked else " "
        if h["rest_minutes"] == 0:
            ratio = "continuous"
        elif h["band"] == "stop":
            ratio = "STOP WORK"
        else:
            ratio = f"{60 - h['rest_minutes']} min / {h['rest_minutes']} min"
        lines.append(
            f" {marker}{h['label']}  {h['wbgt']:5.1f}  {h['air_c']:4.1f}  {h['rh']:3.0f}%  "
            f"{BAND_GLYPH[h['band']]}  {h['band']:<8}  {ratio}"
        )
    lines.append("-" * w)
    lines.append("  > = hours the crew is on site under this plan")
    lines.append("")

    lines.append("  WHAT THIS PLAN CHANGES, AGAINST A NORMAL 09:00-18:00 SHIFT")
    lines.append(f"    Productive hours   {r['baseline_productive_hours']:.1f}  ->  "
                 f"{r['productive_hours']:.1f}   ({r['hours_rescued']:+.1f})")
    lines.append(f"    Peak WBGT faced    {r['baseline_peak_wbgt']:.1f}  ->  "
                 f"{r['peak_wbgt']:.1f} C")
    lines.append(f"    Severe-risk hours  removed: {r['severe_hours_removed']:.0f}")
    lines.append(f"    Wages protected    approx Rs {r['wages_protected_inr']:,.0f} "
                 f"for the crew (estimate)")
    if not r["meets_requirement"]:
        lines.append("")
        lines.append(f"  !! NO PLAN DELIVERS THE REQUESTED WORK SAFELY TODAY.")
        lines.append(f"     Short by {r['shortfall_hours']:.1f} productive hours.")
        lines.append(f"     This is a day to reduce scope, not to push the crew.")
    if r.get("alternative"):
        a = r["alternative"]
        lines.append("")
        lines.append("  IF YOUR CREW SLEEPS ON SITE, THIS IS BETTER")
        lines.append(f"    {'  +  '.join(a['blocks'])}  "
                     f"({a['span_hours']} h on site, {a['gap_hours']} h stand-down)")
        lines.append(f"    {a['productive_hours']:.1f} productive hours and "
                     f"{a['severe_hours_faced']:.0f} severe-risk hours, against "
                     f"{r['productive_hours']:.1f} and {r['severe_hours_faced']:.0f}.")
        lines.append(f"    We do not recommend it by default: a {a['span_hours']} hour "
                     f"span is punishing for")
        lines.append("    anyone who commutes. Change it on the site if they do not.")

    if r.get("policy"):
        pol = r["policy"]
        lines.append("")
        lines.append("  WHO ON THE CREW CAN WORK THIS PLAN  (Cedar policy)")
        lines.append(f"    {len(pol['fully_cleared'])} of {r['crew_size']} cleared "
                     f"for every hour")
        shown = set()
        for e in pol["exclusions"]:
            if e["worker"] in shown:
                continue
            shown.add(e["worker"])
            n = sum(1 for x in pol["exclusions"] if x["worker"] == e["worker"])
            lines.append(f"    {e['worker']}: held from {n} hour(s) — {e['reason']}")

    lines.append("")
    lines.append("  WBGT is an estimate from forecast data, not a site measurement.")
    lines.append("  Thresholds: ACGIH / ISO 7243, acclimatised workers.")
    lines.append("=" * w)
    return "\n".join(lines)


def sms_hindi(rec: Recommendation, site_name: str = "साइट") -> str:
    """The worker-facing message. Constraints that shaped every character of it:

    - It has to land on a 1,000 rupee feature phone, so: SMS, not an app.
    - Many readers have low text literacy, so: short lines, numbers, no paragraphs.
    - A 70-character Unicode SMS segment is the budget before it splits into two.
    """
    r = rec.as_dict()
    blocks = " + ".join(r["recommended_blocks"])
    stop = (
        f"\n{r['stop_work_hour']:02d}:00 के बाद काम बंद।"
        if r["stop_work_hour"] is not None
        else ""
    )
    peak_band = _worst_worked_hour(rec, r)
    band_hi = BAND_LABELS_HI[peak_band["band"]] if peak_band else ""
    water = r["water_litres_per_worker"]

    return (
        f"छाँव | {site_name}\n"
        f"काम का समय: {blocks}{stop}\n"
        f"गर्मी: {band_hi}\n"
        f"हर घंटे {60 - (peak_band['rest_minutes'] if peak_band else 0)} मिनट काम, "
        f"{peak_band['rest_minutes'] if peak_band else 0} मिनट छाँव में आराम।\n"
        f"पानी: {water:.0f} लीटर प्रति व्यक्ति।\n"
        f"चक्कर या उल्टी हो तो तुरंत रुकें।"
    )


def sms_english(rec: Recommendation, site_name: str = "Site") -> str:
    r = rec.as_dict()
    blocks = " + ".join(r["recommended_blocks"])
    stop = (
        f"\nNo work after {r['stop_work_hour']:02d}:00."
        if r["stop_work_hour"] is not None
        else ""
    )
    peak = _worst_worked_hour(rec, r)
    return (
        f"SHADECAST | {site_name}\n"
        f"Work hours: {blocks}{stop}\n"
        f"Heat: {peak['band'] if peak else '-'}\n"
        f"Each hour: {60 - (peak['rest_minutes'] if peak else 0)} min work, "
        f"{peak['rest_minutes'] if peak else 0} min rest in shade.\n"
        f"Water: {r['water_litres_per_worker']:.0f} L per person.\n"
        f"Stop at once if dizzy or vomiting."
    )


def to_json(rec: Recommendation, site_name: str = "Site") -> str:
    payload = rec.as_dict()
    payload["site"] = site_name
    payload["sms_hi"] = sms_hindi(rec, site_name)
    payload["sms_en"] = sms_english(rec, site_name)
    return json.dumps(payload, indent=2, ensure_ascii=False)
