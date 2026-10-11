"""The optimiser and the policy layer.

These are the tests that would have caught the two bugs that mattered most: a plan
that kept people on site for fifteen hours, and a Cedar layer that silently decided
nothing at all.
"""

from __future__ import annotations

import pytest

from shadecast.forecast import BHOPAL, synthetic_day
from shadecast.policy import HourResource, Worker, clear_crew, sample_crew
from shadecast.report import shift_sheet, sms_hindi, to_json
from shadecast.schedule import (
    DEFAULT_MAX_SPAN_HOURS,
    EARLIEST_START,
    LATEST_END,
    SPAN_IF_COMMUTING,
    SPAN_IF_ON_SITE,
    HourSlot,
    recommend,
)
from shadecast.wbgt import wbgt_outdoor


def slots(day="pre_monsoon", workload="moderate", acclimatised=True):
    out = []
    for fh in synthetic_day(day):
        b = wbgt_outdoor(
            fh.temp_c, fh.rh_pct, fh.shortwave_wm2, fh.wind_ms,
            fh.time_utc, BHOPAL["lat"], BHOPAL["lon"],
        )
        out.append(HourSlot(fh.time_ist, b, workload, acclimatised))
    return out


# ---------------------------------------------------------------------------
# The span constraint -- the bug that nearly shipped
# ---------------------------------------------------------------------------

# The humane-day limit, written as a literal on purpose. Asserting against
# DEFAULT_MAX_SPAN_HOURS would make the test self-referential: raising the constant
# would raise the bar too, and the test would pass with the constraint switched off.
# It did exactly that until a mutation run caught it.
HUMANE_SPAN_LIMIT = 11


def test_the_default_span_limit_is_eleven_hours():
    assert DEFAULT_MAX_SPAN_HOURS == HUMANE_SPAN_LIMIT
    assert SPAN_IF_COMMUTING == HUMANE_SPAN_LIMIT


def test_default_plan_never_strands_a_commuting_worker():
    """Our first optimiser recommended 05:00-11:00 plus 17:00-20:00: nine hours of work
    across a fifteen-hour span. For a worker who commutes an hour each way that is a
    seventeen-hour day, and the gap is too short to go home. The default must not do
    that again."""
    for day in ("pre_monsoon", "monsoon_break", "october"):
        for load in ("light", "moderate", "heavy"):
            for acc in (True, False):
                rec = recommend(slots(day, load, acc))
                assert rec.plan.span_hours <= HUMANE_SPAN_LIMIT, (
                    day, load, acc, rec.plan.label(), rec.plan.span_hours
                )


def test_a_fifteen_hour_day_is_never_the_default_answer():
    """The specific plan that started all this: 05:00-11:00 + 17:00-20:00."""
    for day in ("pre_monsoon", "monsoon_break", "october"):
        blocks = recommend(slots(day)).as_dict()["recommended_blocks"]
        assert blocks != ["05:00-11:00", "17:00-20:00"]


def test_relaxing_the_span_is_what_unlocks_the_split_shift():
    tight = recommend(slots(), max_span_hours=SPAN_IF_COMMUTING)
    loose = recommend(slots(), max_span_hours=SPAN_IF_ON_SITE)
    assert loose.plan.span_hours >= tight.plan.span_hours
    assert loose.plan.productive_hours >= tight.plan.productive_hours


def test_span_cost_is_reported_not_hidden():
    rec = recommend(slots(), max_span_hours=SPAN_IF_COMMUTING)
    if rec.has_alternative:
        assert rec.span_cost_hours > 0
        d = rec.as_dict()
        assert d["alternative"] is not None
        assert d["span_cost_hours"] == pytest.approx(rec.span_cost_hours, abs=0.01)


def test_span_and_gap_are_arithmetically_consistent():
    for day in ("pre_monsoon", "monsoon_break", "october"):
        p = recommend(slots(day)).plan
        assert p.span_hours == p.clock_hours + p.gap_hours


# ---------------------------------------------------------------------------
# The optimiser
# ---------------------------------------------------------------------------

def test_plan_beats_the_conventional_shift_on_a_hot_day():
    rec = recommend(slots("pre_monsoon"))
    assert rec.plan.productive_hours > rec.baseline.productive_hours
    assert rec.plan.severe_hours <= rec.baseline.severe_hours


def test_plan_stays_inside_legal_hours():
    for day in ("pre_monsoon", "monsoon_break", "october"):
        for h in recommend(slots(day)).plan.worked_hours:
            assert EARLIEST_START <= h < LATEST_END


def test_no_hour_is_scheduled_twice():
    for day in ("pre_monsoon", "monsoon_break", "october"):
        hours = recommend(slots(day)).plan.worked_hours
        assert len(hours) == len(set(hours))


def test_productive_hours_never_exceed_clock_hours():
    p = recommend(slots()).plan
    assert p.productive_hours <= p.clock_hours + 1e-9


def test_shortfall_is_admitted_rather_than_papered_over():
    """On a day where the required work cannot be delivered safely, the honest output
    is a shortfall, not a plan that quietly overruns the thresholds."""
    rec = recommend(slots("pre_monsoon", "heavy", acclimatised=False), required_hours=8.0)
    if not rec.meets_requirement:
        assert rec.shortfall_hours > 0
        assert rec.as_dict()["meets_requirement"] is False


def test_wages_are_never_negative():
    for day in ("pre_monsoon", "monsoon_break", "october"):
        assert recommend(slots(day)).wages_protected >= 0


def test_date_key_is_iso_not_a_human_label():
    """The plans table is keyed on this. Writing 'Fri 15 May 2026' here once made every
    stored plan unfetchable through the API."""
    d = recommend(slots()).as_dict()
    assert len(d["date"]) == 10 and d["date"][4] == "-" and d["date"][7] == "-"
    assert d["date_label"] != d["date"]


# ---------------------------------------------------------------------------
# Cedar policy
# ---------------------------------------------------------------------------

cedar = pytest.importorskip("cedarpy", reason="cedarpy not installed")

SEVERE = [HourResource(13, "severe", 30.0)]
HIGH = [HourResource(11, "high", 28.5)]
SAFE = [HourResource(6, "safe", 23.0)]
STOP = [HourResource(14, "stop", 34.0)]


def test_policy_actually_evaluates():
    """Cedar returned NoDecision for every request once, because an entity attribute
    held a float and Cedar has no float type. Every denial still looked plausible
    because the reason text came from Python. A policy layer that can fail open and
    look identical to success is not a policy layer."""
    c = clear_crew([Worker("w1", "A")], SAFE)
    assert c.policy_available
    assert not c.exclusions
    assert c.cleared["w1"] == [6]


def test_healthy_adult_is_allowed_in_severe_but_not_stop():
    assert not clear_crew([Worker("w1", "A")], SEVERE).exclusions
    assert clear_crew([Worker("w1", "A")], STOP).exclusions


@pytest.mark.parametrize(
    "worker,hours",
    [
        (Worker("w2", "B", acclimatised=False), SEVERE),
        (Worker("w3", "C", age_band="under_18"), SEVERE),
        (Worker("w3", "C", age_band="under_18"), HIGH),
        (Worker("w4", "D", has_heat_risk_condition=True), HIGH),
        (Worker("w8", "E", age_band="over_55"), SEVERE),
    ],
)
def test_vulnerable_workers_are_held_back(worker, hours):
    c = clear_crew([worker], hours)
    assert c.policy_available
    assert c.exclusions, f"{worker} should not be cleared for {hours[0].band}"
    assert c.exclusions[0].reason


def test_everyone_is_barred_from_a_stop_hour():
    crew = sample_crew(12)
    c = clear_crew(crew, STOP)
    assert len(c.exclusions) == len(crew)


def test_policy_degrades_without_raising_if_cedar_is_missing(monkeypatch):
    """Policy is a safeguard on top of the schedule, never a prerequisite for one."""
    import builtins

    real_import = builtins.__import__

    def no_cedar(name, *a, **k):
        if name == "cedarpy":
            raise ImportError("simulated: cedarpy not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_cedar)
    c = clear_crew([Worker("w1", "A")], SEVERE)
    assert c.policy_available is False
    assert c.as_dict()["policy_available"] is False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def test_sms_quotes_the_worst_hour_the_crew_actually_works():
    """Not the worst hour of the day. Telling a crew working 05:00-11:00 to take
    45-minute breaks they do not need is a rule nobody follows twice."""
    rec = recommend(slots())
    worked = set(rec.plan.worked_hours)
    worst_worked = max(
        (h for h in rec.as_dict()["hours"] if h["hour"] in worked),
        key=lambda h: h["wbgt"],
    )
    rest = worst_worked["rest_minutes"]
    assert f"{60 - rest} मिनट काम" in sms_hindi(rec, "Test")


def test_sms_is_devanagari_and_short_enough_to_send():
    body = sms_hindi(recommend(slots()), "Kolar Road")
    assert any("ऀ" <= ch <= "ॿ" for ch in body)
    assert len(body) < 320  # a few Unicode SMS segments, not a novel


def test_shift_sheet_renders_every_demo_day():
    for day in ("pre_monsoon", "monsoon_break", "october"):
        sheet = shift_sheet(recommend(slots(day)), "Kolar Road")
        assert "SHADECAST" in sheet
        assert "ON SITE" in sheet


def test_json_payload_is_serialisable_for_api_gateway():
    import json

    json.loads(to_json(recommend(slots()), "Kolar Road"))
