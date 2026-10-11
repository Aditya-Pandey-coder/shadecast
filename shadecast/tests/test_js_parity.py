"""The browser optimiser must agree with the Python one. Exactly.

Shadecast runs the shift search twice: in Python for the Lambda handlers, and in
JavaScript so the supervisor page can recompute live as someone drags a slider. Two
implementations of the same logic is two sets of bugs unless they are pinned to each
other, so this test runs web/optimiser.js under Node across the full grid of inputs and
asserts the plans are identical.

Skipped, loudly, if Node is not installed. Never silently passed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shadecast.forecast import BHOPAL, DEMO_DAYS, synthetic_day
from shadecast.schedule import HourSlot, recommend
from shadecast.wbgt import wbgt_outdoor

ROOT = Path(__file__).resolve().parent.parent
OPTIMISER_JS = ROOT / "web" / "optimiser.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def hour_rows(day: str, workload: str, acclimatised: bool):
    """The hourly input both implementations are given."""
    rows = []
    for fh in synthetic_day(day):
        b = wbgt_outdoor(
            fh.temp_c, fh.rh_pct, fh.shortwave_wm2, fh.wind_ms,
            fh.time_utc, BHOPAL["lat"], BHOPAL["lon"],
        )
        slot = HourSlot(fh.time_ist, b, workload, acclimatised)
        rows.append(slot.as_dict())
    return rows


def run_js(rows, opts) -> dict:
    script = f"""
const P = require({json.dumps(str(OPTIMISER_JS))});
const rows = {json.dumps(rows)};
const opts = {json.dumps(opts)};
process.stdout.write(JSON.stringify(P.plan(rows, opts)));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run(
            [node, path], capture_output=True, text=True, timeout=60, check=True
        )
        return json.loads(out.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


GRID = [
    (day, load, acc, required, span)
    for day in DEMO_DAYS
    for load in ("light", "moderate", "heavy")
    for acc in (True, False)
    for required in (4.0, 6.0, 8.0)
    for span in (9, 11, 13, 15, 24)
]


@pytest.mark.parametrize("day,load,acc,required,span", GRID)
def test_js_matches_python(day, load, acc, required, span):
    rows = hour_rows(day, load, acc)
    py = recommend(
        [
            HourSlot(fh.time_ist, wbgt_outdoor(
                fh.temp_c, fh.rh_pct, fh.shortwave_wm2, fh.wind_ms,
                fh.time_utc, BHOPAL["lat"], BHOPAL["lon"]), load, acc)
            for fh in synthetic_day(day)
        ],
        crew_size=12, daily_wage=450.0,
        required_hours=required, max_span_hours=span,
    ).as_dict()

    js = run_js(rows, {
        "workload": load, "requiredHours": required, "maxSpanHours": span,
        "crewSize": 12, "dailyWage": 450.0,
    })

    ctx = f"{day}/{load}/acc={acc}/req={required}/span={span}"
    assert js["blocks"] == py["recommended_blocks"], ctx
    assert js["workedHours"] == py["worked_hours"], ctx
    assert js["spanHours"] == py["span_hours"], ctx
    assert js["gapHours"] == py["gap_hours"], ctx
    assert js["productiveHours"] == pytest.approx(py["productive_hours"], abs=0.01), ctx
    assert js["hoursRescued"] == pytest.approx(py["hours_rescued"], abs=0.01), ctx
    assert js["severeHoursFaced"] == pytest.approx(py["severe_hours_faced"], abs=0.05), ctx
    assert js["meetsRequirement"] == py["meets_requirement"], ctx
    assert js["stopWorkHour"] == py["stop_work_hour"], ctx
    assert js["wagesProtected"] == pytest.approx(py["wages_protected_inr"], abs=1), ctx

    if py["alternative"] is None:
        assert js["alternative"] is None, ctx
    else:
        assert js["alternative"] is not None, ctx
        assert js["alternative"]["blocks"] == py["alternative"]["blocks"], ctx
        assert js["alternative"]["spanHours"] == py["alternative"]["span_hours"], ctx
