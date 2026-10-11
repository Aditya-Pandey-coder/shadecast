"""
The shift optimiser: turn an hourly WBGT forecast into a work plan a supervisor can act on.

THE DESIGN DECISION THAT MATTERS
--------------------------------
The obvious product here is a red/amber/green heat warning. That product fails, for a
reason that is visible in the data: on a Bhopal May afternoon almost every hour is red.
A tool that says "stop work" to a crew paid by the day will be switched off by lunchtime
on the day it is installed, and the workers will be no safer.

So Shadecast never outputs a bare stop. It outputs a PLAN: the same work, moved to the
hours that cost the least. The Duke/Parsons labour-loss work found that roughly 30% of
heavy labour currently lost to heat is recoverable simply by shifting it into cooler
hours. That recovery is the product. The safety gain and the wage gain point the same
way, which is the only reason a contractor will ever adopt this.

Three numbers come out, and they are the three a supervisor actually cares about:

    1. SEVERE HOURS REMOVED  -- exposure above the ACGIH 25/75 ceiling that is designed
       out of the day. This is the safety number.
    2. PRODUCTIVE HOURS RESCUED -- work delivered under the optimised plan minus work
       delivered under the conventional 9-6 shift. This is the money number.
    3. WAGES PROTECTED -- the rescued hours priced at the crew's day rate. An estimate,
       and labelled as one everywhere it is displayed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from .wbgt import (
    WATER_LITRES_PER_HOUR,
    WbgtBreakdown,
    Workload,
    rest_minutes_per_hour,
    risk_band,
    work_fraction,
)

# A conventional Indian construction shift: on site 09:00, off 18:00, one hour
# unpaid break at 13:00. This is the baseline every gain is measured against.
BASELINE_START = 9
BASELINE_END = 18
BASELINE_BREAK = 13

# Legal/practical bounds on how far a shift may be moved.
EARLIEST_START = 5
LATEST_END = 20

# The furthest apart a worker's first arrival and last departure may be, including any
# mid-day stand-down. Eleven hours leaves room for a split shift with a two to three
# hour gap without turning the day into a sixteen-hour ordeal for someone who commutes.
# This is a humane-day constraint, not a legal one, and the user can move it.
DEFAULT_MAX_SPAN_HOURS = 11

# Workers who sleep in a labour camp on the site can take a long mid-day stand-down,
# because the gap costs them nothing but time they would spend there anyway. Workers who
# commute cannot: a six-hour gap is too short to go home and worth less than the fare.
# So the same forecast yields two different right answers, and which one applies depends
# on where the crew sleeps -- a fact no weather model contains.
SPAN_IF_COMMUTING = 11
SPAN_IF_ON_SITE = 15


@dataclass
class HourSlot:
    """One forecast hour, scored."""

    local_time: datetime
    wbgt: WbgtBreakdown
    workload: Workload
    acclimatised: bool = True

    @property
    def fraction(self) -> float:
        return work_fraction(self.wbgt.wbgt_c, self.workload, self.acclimatised)

    @property
    def band(self) -> str:
        return risk_band(self.fraction)

    @property
    def rest_minutes(self) -> int:
        return rest_minutes_per_hour(self.fraction)

    @property
    def water_litres(self) -> float:
        return WATER_LITRES_PER_HOUR[self.band]

    @property
    def is_severe(self) -> bool:
        """At or beyond the 25/75 ceiling: the hours that put people in hospital."""
        return self.fraction <= 0.25

    def as_dict(self) -> dict:
        return {
            "hour": self.local_time.hour,
            "label": self.local_time.strftime("%H:%M"),
            "wbgt": round(self.wbgt.wbgt_c, 1),
            "air_c": round(self.wbgt.air_c, 1),
            "rh": round(self.wbgt.rh_pct),
            "wet_bulb": round(self.wbgt.wet_bulb_c, 1),
            "globe": round(self.wbgt.globe_c, 1),
            "wind": round(self.wbgt.wind_ms, 1),
            "fraction": self.fraction,
            "band": self.band,
            "rest_minutes": self.rest_minutes,
            "water_litres": self.water_litres,
        }


@dataclass
class Block:
    """A contiguous run of worked hours."""

    start_hour: int
    end_hour: int  # exclusive

    @property
    def hours(self) -> list[int]:
        return list(range(self.start_hour, self.end_hour))

    def label(self) -> str:
        return f"{self.start_hour:02d}:00-{self.end_hour:02d}:00"


@dataclass
class Plan:
    """A candidate work plan and everything it costs."""

    blocks: list[Block]
    slots_by_hour: dict[int, HourSlot] = field(repr=False, default_factory=dict)

    @property
    def worked_hours(self) -> list[int]:
        out: list[int] = []
        for b in self.blocks:
            out.extend(b.hours)
        return sorted(out)

    @property
    def clock_hours(self) -> int:
        return len(self.worked_hours)

    @property
    def span_hours(self) -> int:
        """First arrival to last departure, INCLUDING any mid-day stand-down.

        This is the number that matters to the worker and the one our first version
        ignored. A plan of 05:00-11:00 plus 17:00-20:00 delivers nine hours of work
        across a fifteen-hour span. For someone who commutes an hour each way -- which
        describes most informal workers in an Indian city -- that is a seventeen-hour
        day, and the six-hour gap is not long enough to go home and worth less than the
        fare, so they spend it sitting at the site in the heat we just told them to
        avoid. A plan like that can be worse for the worker than the one it replaces,
        while the tool reports it as wages protected.
        """
        hours = self.worked_hours
        return (max(hours) + 1 - min(hours)) if hours else 0

    @property
    def gap_hours(self) -> int:
        """Hours of unpaid stand-down inside the span."""
        return self.span_hours - self.clock_hours

    @property
    def productive_hours(self) -> float:
        """Hours of actual work delivered, after mandated rest is deducted."""
        return sum(
            self.slots_by_hour[h].fraction
            for h in self.worked_hours
            if h in self.slots_by_hour
        )

    @property
    def severe_hours(self) -> float:
        return sum(
            1.0
            for h in self.worked_hours
            if h in self.slots_by_hour and self.slots_by_hour[h].is_severe
        )

    @property
    def peak_wbgt(self) -> float:
        vals = [
            self.slots_by_hour[h].wbgt.wbgt_c
            for h in self.worked_hours
            if h in self.slots_by_hour
        ]
        return max(vals) if vals else 0.0

    @property
    def exposure_index(self) -> float:
        """Cumulative WBGT-degree-hours above the 25/75 ceiling.

        A single scalar for 'how much punishment did this plan hand out'. Lower is
        better. Used to break ties between plans that deliver the same work.
        """
        total = 0.0
        for h in self.worked_hours:
            slot = self.slots_by_hour.get(h)
            if slot is None:
                continue
            ceiling = _severe_ceiling(slot)
            if slot.wbgt.wbgt_c > ceiling:
                total += slot.wbgt.wbgt_c - ceiling
        return total

    @property
    def water_litres_per_worker(self) -> float:
        return sum(
            self.slots_by_hour[h].water_litres
            for h in self.worked_hours
            if h in self.slots_by_hour
        )

    @property
    def start_hour(self) -> int:
        return self.blocks[0].start_hour if self.blocks else 99

    def label(self) -> str:
        return " + ".join(b.label() for b in self.blocks)


def _severe_ceiling(slot: HourSlot) -> float:
    """The 25/75 WBGT ceiling for this slot's workload."""
    from .wbgt import TLV_TABLE

    return TLV_TABLE[slot.workload][-1][0]


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def _candidate_plans(
    slots_by_hour: dict[int, HourSlot], clock_hours: int
) -> Iterable[Plan]:
    """Every sensible way to place `clock_hours` of work inside the legal window.

    Two shapes:
      - a single continuous block
      - a split shift: a morning block and an evening block with a mid-day stand-down,
        which is how Indian sites have historically handled summer anyway
    """
    available = sorted(h for h in slots_by_hour if EARLIEST_START <= h < LATEST_END)
    if not available:
        return

    # Single continuous block.
    for start in available:
        end = start + clock_hours
        if end > LATEST_END:
            continue
        if all(h in slots_by_hour for h in range(start, end)):
            yield Plan([Block(start, end)], slots_by_hour)

    # Split shift: morning block + evening block, with a gap of at least 2 hours.
    for morning_len in range(2, clock_hours - 1):
        evening_len = clock_hours - morning_len
        for m_start in available:
            m_end = m_start + morning_len
            if m_end > LATEST_END:
                continue
            for e_start in range(m_end + 2, LATEST_END):
                e_end = e_start + evening_len
                if e_end > LATEST_END:
                    continue
                hours = list(range(m_start, m_end)) + list(range(e_start, e_end))
                if all(h in slots_by_hour for h in hours):
                    yield Plan(
                        [Block(m_start, m_end), Block(e_start, e_end)], slots_by_hour
                    )


def baseline_plan(slots_by_hour: dict[int, HourSlot]) -> Plan:
    """The conventional 09:00-18:00 shift with a 13:00 break."""
    blocks = [Block(BASELINE_START, BASELINE_BREAK), Block(BASELINE_BREAK + 1, BASELINE_END)]
    return Plan(blocks, slots_by_hour)


# ---------------------------------------------------------------------------
# The optimiser
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    plan: Plan
    baseline: Plan
    slots: list[HourSlot]
    crew_size: int
    daily_wage: float
    required_hours: float
    workload: Workload
    date_label: str
    iso_date: str
    max_span_hours: int = DEFAULT_MAX_SPAN_HOURS
    unconstrained: "Plan | None" = None

    @property
    def span_cost_hours(self) -> float:
        """Productive hours given up to keep the worker's day within the span limit.

        Reported, not hidden. If the honest answer is "this costs an hour of work",
        a supervisor deserves to see that and decide.
        """
        if self.unconstrained is None:
            return 0.0
        return max(0.0, self.unconstrained.productive_hours - self.plan.productive_hours)

    @property
    def has_alternative(self) -> bool:
        """True when relaxing the span would give a materially different plan."""
        return (
            self.unconstrained is not None
            and self.unconstrained.worked_hours != self.plan.worked_hours
        )

    @property
    def hours_rescued(self) -> float:
        return self.plan.productive_hours - self.baseline.productive_hours

    @property
    def severe_hours_removed(self) -> float:
        return self.baseline.severe_hours - self.plan.severe_hours

    @property
    def wages_protected(self) -> float:
        """Rescued hours priced at the crew day rate. An ESTIMATE.

        Assumes an 8-hour day rate and that output scales with productive hours.
        Labelled as an estimate everywhere it is shown.
        """
        return max(0.0, self.hours_rescued) / 8.0 * self.daily_wage * self.crew_size

    @property
    def meets_requirement(self) -> bool:
        return self.plan.productive_hours + 1e-9 >= self.required_hours

    @property
    def shortfall_hours(self) -> float:
        return max(0.0, self.required_hours - self.plan.productive_hours)

    @property
    def stop_work_hour(self) -> int | None:
        """First hour of the day at which all work must cease, if any."""
        for slot in sorted(self.slots, key=lambda s: s.local_time.hour):
            if slot.fraction == 0.0 and slot.local_time.hour >= BASELINE_START:
                return slot.local_time.hour
        return None

    def as_dict(self) -> dict:
        return {
            # ISO date is the storage key (DynamoDB sort key, API query param);
            # the human label is for printing. Conflating the two put
            # "Fri 15 May 2026" in the key and made every plan unfetchable.
            "date": self.iso_date,
            "date_label": self.date_label,
            "workload": self.workload,
            "crew_size": self.crew_size,
            "recommended_blocks": [b.label() for b in self.plan.blocks],
            "worked_hours": self.plan.worked_hours,
            "baseline_worked_hours": self.baseline.worked_hours,
            "span_hours": self.plan.span_hours,
            "gap_hours": self.plan.gap_hours,
            "max_span_hours": self.max_span_hours,
            "span_cost_hours": round(self.span_cost_hours, 2),
            "severe_hours_faced": round(self.plan.severe_hours, 1),
            "alternative": (
                {
                    "blocks": [b.label() for b in self.unconstrained.blocks],
                    "span_hours": self.unconstrained.span_hours,
                    "gap_hours": self.unconstrained.gap_hours,
                    "productive_hours": round(self.unconstrained.productive_hours, 2),
                    "severe_hours_faced": round(self.unconstrained.severe_hours, 1),
                }
                if self.has_alternative
                else None
            ),
            "baseline_blocks": [b.label() for b in self.baseline.blocks],
            "productive_hours": round(self.plan.productive_hours, 2),
            "baseline_productive_hours": round(self.baseline.productive_hours, 2),
            "hours_rescued": round(self.hours_rescued, 2),
            "severe_hours_removed": round(self.severe_hours_removed, 1),
            "wages_protected_inr": round(self.wages_protected),
            "peak_wbgt": round(self.plan.peak_wbgt, 1),
            "baseline_peak_wbgt": round(self.baseline.peak_wbgt, 1),
            "water_litres_per_worker": round(self.plan.water_litres_per_worker, 1),
            "water_litres_crew": round(
                self.plan.water_litres_per_worker * self.crew_size, 1
            ),
            "meets_requirement": self.meets_requirement,
            "shortfall_hours": round(self.shortfall_hours, 2),
            "stop_work_hour": self.stop_work_hour,
            "hours": [s.as_dict() for s in sorted(self.slots, key=lambda s: s.local_time.hour)],
        }


def recommend(
    slots: Sequence[HourSlot],
    crew_size: int = 12,
    daily_wage: float = 450.0,
    required_hours: float = 6.0,
    max_clock_hours: int = 9,
    max_span_hours: int = DEFAULT_MAX_SPAN_HOURS,
) -> Recommendation:
    """Choose the work plan that delivers the required work at the lowest heat cost.

    Selection rule, in order:
      1. Prefer plans that deliver the required productive hours.
      2. Among those, fewest severe-exposure hours.
      3. Then lowest cumulative exposure index.
      4. Then the shortest time on site.

    If nothing can deliver the required hours, return the plan with the most
    productive hours and report the shortfall honestly rather than pretending.
    """
    if not slots:
        raise ValueError("no forecast slots supplied")

    slots_by_hour = {s.local_time.hour: s for s in slots}
    workload = slots[0].workload
    date_label = slots[0].local_time.strftime("%a %d %b %Y")
    iso_date = slots[0].local_time.strftime("%Y-%m-%d")

    candidates: list[Plan] = []
    for clock_hours in range(3, max_clock_hours + 1):
        candidates.extend(_candidate_plans(slots_by_hour, clock_hours))

    if not candidates:
        raise ValueError("no feasible plan inside the legal working window")

    # The span filter comes BEFORE the productivity filter on purpose. A plan that keeps
    # someone at the site for fifteen hours is not a better plan that costs more; it is
    # a plan we are not willing to recommend. Keeping the unconstrained best lets us
    # report honestly what the constraint cost.
    unconstrained = min(
        candidates,
        key=lambda p: (-p.productive_hours, p.severe_hours, p.exposure_index),
    )
    within_span = [p for p in candidates if p.span_hours <= max_span_hours]
    if not within_span:
        within_span = [
            p for p in candidates
            if p.span_hours == min(c.span_hours for c in candidates)
        ]

    feasible = [p for p in within_span if p.productive_hours + 1e-9 >= required_hours]
    pool = feasible if feasible else within_span

    if feasible:
        best = min(
            pool,
            key=lambda p: (p.severe_hours, p.exposure_index, p.clock_hours, p.start_hour),
        )
    else:
        best = max(
            pool,
            key=lambda p: (p.productive_hours, -p.severe_hours, -p.exposure_index),
        )

    return Recommendation(
        plan=best,
        baseline=baseline_plan(slots_by_hour),
        slots=list(slots),
        crew_size=crew_size,
        daily_wage=daily_wage,
        required_hours=required_hours,
        workload=workload,
        date_label=date_label,
        iso_date=iso_date,
        max_span_hours=max_span_hours,
        unconstrained=unconstrained,
    )
