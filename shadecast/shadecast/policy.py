"""
Who on the crew may work which hour — decided by Cedar, not by if-statements.

The scheduler in schedule.py answers "when is it safe to work at all". This module
answers the second question a supervisor actually has: "given the plan, who on my crew
can be on it". The two are different, and the second one is an authorization question,
which is what Cedar is for.

The rules live in policies/heat_safety.cedar as readable policy text. A safety officer
can read that file, a labour inspector can audit it, and a site can override it without
anyone touching Python. If those rules were buried in this module as conditionals they
would be invisible and unauditable, which is exactly the failure the CPR review found in
India's Heat Action Plans: the vulnerable groups are named in the document and absent
from what actually gets done.

Cedar is AWS's open-source authorization language. It runs locally, needs no account,
and the policy file is the artifact, not the code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

POLICY_FILE = Path(__file__).parent / "policies" / "heat_safety.cedar"

AgeBand = Literal["under_18", "adult", "over_55"]


@dataclass
class Worker:
    """One member of the crew. Only the attributes the policy reads."""

    worker_id: str
    name: str = ""
    acclimatised: bool = True
    age_band: AgeBand = "adult"
    has_heat_risk_condition: bool = False

    def as_entity(self) -> dict:
        return {
            "uid": {"type": "Worker", "id": self.worker_id},
            "attrs": {
                "acclimatised": self.acclimatised,
                "age_band": self.age_band,
                "has_heat_risk_condition": self.has_heat_risk_condition,
            },
            "parents": [],
        }


@dataclass
class HourResource:
    hour: int
    band: str
    wbgt: float

    @property
    def uid(self) -> str:
        return f"{self.hour:02d}:00"

    def as_entity(self) -> dict:
        # Cedar has no floating-point type. A bare 23.4 here fails entity
        # deserialization and every request silently returns NoDecision -- which looks
        # exactly like a working policy layer if you are only reading the denials. WBGT
        # is carried as tenths of a degree (an integer) and the policy never needs it
        # anyway; it reasons about the band.
        return {
            "uid": {"type": "Hour", "id": self.uid},
            "attrs": {
                "band": self.band,
                "wbgt_tenths": int(round(self.wbgt * 10)),
                "hour": self.hour,
            },
            "parents": [],
        }


@dataclass
class Exclusion:
    worker_id: str
    worker_name: str
    hour: int
    band: str
    reason: str


@dataclass
class CrewClearance:
    """The result: who may work which hours, and why anyone was held back."""

    cleared: dict[str, list[int]] = field(default_factory=dict)
    exclusions: list[Exclusion] = field(default_factory=list)
    policy_available: bool = True

    @property
    def fully_cleared(self) -> list[str]:
        held = {e.worker_id for e in self.exclusions}
        return [w for w in self.cleared if w not in held]

    def as_dict(self) -> dict:
        return {
            "policy_available": self.policy_available,
            "fully_cleared": self.fully_cleared,
            "cleared_hours": self.cleared,
            "exclusions": [
                {
                    "worker": e.worker_name or e.worker_id,
                    "hour": f"{e.hour:02d}:00",
                    "band": e.band,
                    "reason": e.reason,
                }
                for e in self.exclusions
            ],
        }


# Human-readable reasons. Cedar tells us a forbid fired and which policy id; this maps
# that to something a supervisor can act on. Kept beside the policy file deliberately so
# the two are edited together.
REASONS = {
    "stop": "above all ACGIH threshold limit values — nobody works this hour",
    "unacclimatised": "new to the site, not yet acclimatised to this heat",
    "under_18": "under 18 — not permitted in high or severe heat",
    "condition": "has a heat-aggravated health condition",
    "over_55": "over 55 — not permitted in the severe band",
}


def _reason_for(worker: Worker, band: str) -> str:
    if band == "stop":
        return REASONS["stop"]
    if not worker.acclimatised and band == "severe":
        return REASONS["unacclimatised"]
    if worker.age_band == "under_18" and band in ("severe", "high"):
        return REASONS["under_18"]
    if worker.has_heat_risk_condition and band not in ("safe", "caution"):
        return REASONS["condition"]
    if worker.age_band == "over_55" and band == "severe":
        return REASONS["over_55"]
    return "held back by heat-safety policy"


def clear_crew(
    workers: list[Worker],
    hours: list[HourResource],
    policy_file: Path = POLICY_FILE,
) -> CrewClearance:
    """Ask Cedar, for every worker and every planned hour, whether assignment is allowed.

    If cedarpy is not installed the clearance is returned empty with
    policy_available=False, and callers fall back to the unfiltered plan. Policy is an
    additional safeguard on top of the schedule, never a prerequisite for producing one.
    """
    result = CrewClearance()
    try:
        from cedarpy import is_authorized, Decision  # noqa: PLC0415
    except ImportError:
        result.policy_available = False
        return result

    policy_text = policy_file.read_text(encoding="utf-8")
    entities = [w.as_entity() for w in workers] + [h.as_entity() for h in hours]
    entities_json = json.dumps(entities)

    for worker in workers:
        allowed: list[int] = []
        for hour in hours:
            request = {
                "principal": f'Worker::"{worker.worker_id}"',
                "action": 'Action::"assign"',
                "resource": f'Hour::"{hour.uid}"',
                "context": {},
            }
            decision = is_authorized(request, policy_text, entities_json)
            if decision.decision == Decision.NoDecision:
                # Cedar could not evaluate: a malformed policy or entity. Never treat
                # this as a denial -- a silent NoDecision masquerading as a working
                # policy is worse than no policy at all.
                raise RuntimeError(
                    "Cedar could not evaluate the request: "
                    f"{getattr(decision.diagnostics, 'errors', None)}"
                )
            if decision.decision == Decision.Allow:
                allowed.append(hour.hour)
            else:
                result.exclusions.append(
                    Exclusion(
                        worker_id=worker.worker_id,
                        worker_name=worker.name,
                        hour=hour.hour,
                        band=hour.band,
                        reason=_reason_for(worker, hour.band),
                    )
                )
        result.cleared[worker.worker_id] = allowed

    return result


def sample_crew(size: int = 12) -> list[Worker]:
    """A representative crew, for the demo.

    Proportions are illustrative, not survey data: one worker in their first week, one
    under 18, one older, one with a heat-aggravated condition. Every real crew has some
    of these people, and the point of the policy layer is that the plan must account for
    them rather than averaging them away.
    """
    crew = [Worker(f"w{i:02d}", f"Worker {i}") for i in range(1, size + 1)]
    if size >= 4:
        crew[1] = Worker("w02", "Worker 2", acclimatised=False)
        crew[2] = Worker("w03", "Worker 3", age_band="under_18")
        crew[3] = Worker("w04", "Worker 4", has_heat_risk_condition=True)
    if size >= 8:
        crew[7] = Worker("w08", "Worker 8", age_band="over_55")
    return crew
