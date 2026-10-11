"""
The planner: weather in, shift plan out, notification to the crew.

RUNS IN TWO PLACES, UNCHANGED
-----------------------------
This is the real handler whether it runs under `sam local invoke` on a laptop with no
AWS account, or in Lambda. The only difference is SHADECAST_BACKEND: unset it and storage
is JSON files, set it to `dynamodb` and storage is DynamoDB. Nothing else changes, so
the function demonstrated locally is the function that would deploy.

WHY THIS FUNCTION HAS NO HARD DEPENDENCIES
------------------------------------------
Everything in shadecast/wbgt.py and shadecast/schedule.py is stdlib `math` and `dataclasses`,
and boto3 is imported lazily by shadecast/store.py only when the DynamoDB backend is asked
for. Reading GRIB needs the native ecCodes library; computing heat strain does not. The
GRIB work lives in lambda_ingest.py so that this -- the function that must not fail --
has nothing in it that can fail to build.

TRIGGERS
--------
  EventBridge, every 6 hours   -> replan every site, notify when it matters
  API Gateway  GET /plan       -> the web UI asks for one site's current plan
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shadecast import store
from shadecast.report import sms_english, sms_hindi
from shadecast.policy import HourResource, clear_crew, sample_crew
from shadecast.schedule import (
    SPAN_IF_COMMUTING,
    SPAN_IF_ON_SITE,
    HourSlot,
    recommend,
)
from shadecast.wbgt import wbgt_outdoor

IST = timezone(timedelta(hours=5, minutes=30))
ALERT_TOPIC = os.environ.get("ALERT_TOPIC_ARN", "")


def _f(v) -> float:
    return float(v) if not isinstance(v, Decimal) else float(v)


def load_sites() -> list[dict]:
    return store.table("sites").scan().get("Items", [])


def load_weather(grid_key: str, day: str) -> list[dict]:
    """Hourly weather for one GFS grid cell on one local day."""
    return store.table("weather").query_prefix(grid_key, day)


def plan_for_site(site: dict, rows: list[dict]) -> dict:
    slots: list[HourSlot] = []
    for row in rows:
        local = datetime.fromisoformat(row["hour_ist"])
        if local.tzinfo is None:
            local = local.replace(tzinfo=IST)
        breakdown = wbgt_outdoor(
            temp_c=_f(row["temp_c"]),
            rh_pct=_f(row["rh_pct"]),
            shortwave_wm2=_f(row.get("shortwave_wm2", 0)),
            wind_ms=_f(row.get("wind_ms", 1.0)),
            when_utc=local.astimezone(timezone.utc),
            lat_deg=_f(site["lat"]),
            lon_deg=_f(site["lon"]),
            shade_fraction=_f(site.get("shade_fraction", 0)),
        )
        slots.append(
            HourSlot(
                local_time=local,
                wbgt=breakdown,
                workload=site.get("workload", "moderate"),
                acclimatised=bool(site.get("acclimatised", True)),
            )
        )

    # Where the crew sleeps decides how long a day we are willing to recommend.
    commute = bool(site.get("workers_commute", True))
    rec = recommend(
        slots,
        crew_size=int(site.get("crew_size", 12)),
        daily_wage=_f(site.get("daily_wage", 450)),
        required_hours=_f(site.get("required_hours", 6)),
        max_span_hours=SPAN_IF_COMMUTING if commute else SPAN_IF_ON_SITE,
    )
    payload = rec.as_dict()
    payload["workers_commute"] = commute

    # Second question, separate from "when is it safe at all": who on this crew may
    # work the hours we just chose. Cedar answers it from a readable policy file.
    worked = set(rec.plan.worked_hours)
    try:
        clearance = clear_crew(
            sample_crew(int(site.get("crew_size", 12))),
            [
                HourResource(s.local_time.hour, s.band, round(s.wbgt.wbgt_c, 1))
                for s in slots
                if s.local_time.hour in worked
            ],
        )
        payload["policy"] = clearance.as_dict()
    except Exception as exc:  # policy is a safeguard, never a prerequisite
        payload["policy"] = {"policy_available": False, "error": str(exc)}
    payload["site_id"] = site["site_id"]
    payload["site_name"] = site.get("name", site["site_id"])
    payload["sms_hi"] = sms_hindi(rec, site.get("name", "साइट"))
    payload["sms_en"] = sms_english(rec, site.get("name", "Site"))
    return payload


def store_plan(payload: dict) -> None:
    store.table("plans").put_item(Item=json.loads(json.dumps(payload)))


def notify(site: dict, payload: dict) -> str:
    """Send the plan to the crew, but only when there is something worth their attention.

    An alert that fires every six hours whether or not anything changed is an alert
    people mute within a week, and a muted alert protects nobody. So: send only when the
    day contains hours that must stop, when the plan removed severe exposure, or when
    the required work cannot be delivered safely at all.
    """
    worth_sending = (
        payload["stop_work_hour"] is not None
        or payload["severe_hours_removed"] > 0
        or not payload["meets_requirement"]
    )
    if not worth_sending:
        return "quiet"

    if not ALERT_TOPIC:
        # Local run: no SNS. Write the outbox so the message is inspectable and the
        # demo can show exactly what each worker would receive.
        outbox = store.LOCAL_DIR / "outbox.txt"
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with outbox.open("a", encoding="utf-8") as fh:
            fh.write(
                f"--- {payload['site_name']} · {payload['date']} ---\n"
                f"{payload['sms_hi']}\n\n"
            )
        return f"written to {outbox}"

    import boto3  # noqa: PLC0415

    boto3.client("sns").publish(
        TopicArn=ALERT_TOPIC,
        Subject=f"Shadecast plan: {payload['site_name']}",
        Message=payload["sms_hi"],
        MessageAttributes={
            "site_id": {"DataType": "String", "StringValue": site["site_id"]}
        },
    )
    return "published to SNS"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def scheduled_handler(event, context):
    """EventBridge, every 6 hours, once a new GFS cycle has been ingested."""
    target = (event or {}).get("for_date") or (
        datetime.now(IST) + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    done, skipped, sent = [], [], []
    for site in load_sites():
        rows = load_weather(site["grid"], target)
        if len(rows) < 12:
            skipped.append({"site_id": site["site_id"], "hours_found": len(rows)})
            continue
        payload = plan_for_site(site, rows)
        store_plan(payload)
        outcome = notify(site, payload)
        done.append(
            {
                "site_id": site["site_id"],
                "shift": payload["recommended_blocks"],
                "hours_rescued": payload["hours_rescued"],
                "severe_removed": payload["severe_hours_removed"],
                "notify": outcome,
            }
        )
        if outcome != "quiet":
            sent.append(site["site_id"])

    return {
        "backend": store.backend_name(),
        "for_date": target,
        "planned": done,
        "skipped_no_forecast": skipped,
        "notified": sent,
    }


def api_handler(event, context):
    """GET /plan?site_id=...&date=YYYY-MM-DD"""
    params = (event or {}).get("queryStringParameters") or {}
    site_id = params.get("site_id")
    date = params.get("date") or datetime.now(IST).strftime("%Y-%m-%d")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "max-age=900",
    }
    if not site_id:
        return {
            "statusCode": 400,
            "headers": headers,
            "body": json.dumps({"error": "site_id is required"}),
        }

    item = store.table("plans").get_item(Key={"site_id": site_id, "date": date}).get("Item")
    if not item:
        return {
            "statusCode": 404,
            "headers": headers,
            "body": json.dumps({"error": f"no plan stored for {site_id} on {date}"}),
        }
    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps(item, default=float, ensure_ascii=False),
    }
