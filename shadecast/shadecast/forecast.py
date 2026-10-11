"""
Forecast ingest: NOAA GFS out of the open AWS S3 bucket, plus offline demo profiles.

WHERE THE DATA COMES FROM
-------------------------
    s3://noaa-gfs-bdp-pds      Registry of Open Data on AWS
    CC0 licensed, no AWS account required, new cycle every 6 hours, global 0.25 degree.

This is the whole reason the project sits on AWS. The forecast model we need is
published as objects in S3 and nowhere else that is free; AWS is not decoration bolted
onto the project, it is the only supply line for the input.

THE BYTE-RANGE TRICK
--------------------
A full GFS GRIB2 file for one forecast hour is roughly 500 MB. We need five fields out
of it. Every GFS object has a sibling `.idx` text file listing each record's byte
offset, so we read the index, work out the byte ranges for just our five fields, and
issue ranged GETs. That turns 500 MB into about 2 MB per forecast hour, which is the
difference between a pipeline that runs inside a Lambda's timeout and one that does not.

FIELDS WE PULL
--------------
    TMP:2 m above ground     air temperature          -> all three WBGT components
    RH:2 m above ground      relative humidity        -> wet bulb
    UGRD/VGRD:10 m above ground  wind vector          -> globe temperature (convection)
    DSWRF:surface            downward shortwave flux  -> globe temperature (radiation)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Bhopal, Madhya Pradesh. GFS is a 0.25 degree grid, so the nearest grid point is
# 23.25 N, 77.50 E -- about 10 km from the city centre.
BHOPAL = {"name": "Bhopal", "lat": 23.26, "lon": 77.41}

GFS_BUCKET = "noaa-gfs-bdp-pds"
GFS_HTTPS = f"https://{GFS_BUCKET}.s3.amazonaws.com"

# Concurrent forecast-hour downloads. Keep this modest: it is NOAA's bandwidth.
DOWNLOAD_WORKERS = int(os.environ.get("SHADECAST_DOWNLOAD_WORKERS", "8"))

WANTED = (
    ":TMP:2 m above ground:",
    ":RH:2 m above ground:",
    ":UGRD:10 m above ground:",
    ":VGRD:10 m above ground:",
    ":DSWRF:surface:",
)


@dataclass
class ForecastHour:
    """One hour of weather at one point."""

    time_utc: datetime
    temp_c: float
    rh_pct: float
    wind_ms: float
    shortwave_wm2: float

    @property
    def time_ist(self) -> datetime:
        return self.time_utc.astimezone(IST)


# ---------------------------------------------------------------------------
# Live GFS ingest
# ---------------------------------------------------------------------------

def latest_cycle(now_utc: datetime | None = None, lag_hours: int = 5) -> tuple[str, str]:
    """Most recent GFS cycle that is likely to be fully published.

    Cycles run at 00/06/12/18Z and take roughly 3.5-5 hours to land, so we step back
    `lag_hours` before rounding down.
    """
    now = now_utc or datetime.now(timezone.utc)
    t = now - timedelta(hours=lag_hours)
    cycle_hour = (t.hour // 6) * 6
    return t.strftime("%Y%m%d"), f"{cycle_hour:02d}"


def _object_key(date: str, cycle: str, fhour: int) -> str:
    return f"gfs.{date}/{cycle}/atmos/gfs.t{cycle}z.pgrb2.0p25.f{fhour:03d}"


def _parse_idx(idx_text: str) -> list[tuple[int, int | None, str]]:
    """Return (start_byte, end_byte, description) for the records we want."""
    lines = [ln for ln in idx_text.splitlines() if ln.strip()]
    starts: list[int] = []
    for ln in lines:
        parts = ln.split(":")
        starts.append(int(parts[1]))

    ranges: list[tuple[int, int | None, str]] = []
    for i, ln in enumerate(lines):
        if not any(w in ln for w in WANTED):
            continue
        start = starts[i]
        end = starts[i + 1] - 1 if i + 1 < len(starts) else None
        ranges.append((start, end, ln))
    return ranges


def _make_session():
    """A requests Session that survives S3 hanging up on us.

    Pulling a day of forecast is a few hundred small ranged GETs against the same host.
    S3 will close a keep-alive connection partway through that, and the bare error --
    "Remote end closed connection without response" -- aborts the whole run. urllib3's
    Retry handles reconnecting, with backoff so a transient 5xx does not turn into a
    hammering loop.
    """
    import requests  # noqa: PLC0415
    from requests.adapters import HTTPAdapter  # noqa: PLC0415
    from urllib3.util.retry import Retry  # noqa: PLC0415

    common = dict(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.6,  # 0.6s, 1.2s, 2.4s, 4.8s, 9.6s
        status_forcelist=(429, 500, 502, 503, 504),
        raise_on_status=False,
    )
    try:
        retry = Retry(allowed_methods=frozenset(["GET"]), **common)
    except TypeError:
        # urllib3 < 1.26 spells it method_whitelist
        retry = Retry(method_whitelist=frozenset(["GET"]), **common)
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session.mount("https://", adapter)
    session.headers["User-Agent"] = "shadecast/1.0 (hackathon project; NOAA open data)"
    return session


def _download_subset(session, key: str, dest_path: str) -> bool:
    """Fetch only the wanted GRIB records for one forecast hour. True on success.

    Writes to a temporary path and renames on success, so a half-written file from a
    dropped connection is never handed to the GRIB decoder.
    """
    import requests  # noqa: PLC0415

    try:
        idx = session.get(f"{GFS_HTTPS}/{key}.idx", timeout=(10, 60))
        if not idx.ok:
            return False
        ranges = _parse_idx(idx.text)
        if not ranges:
            return False

        partial = dest_path + ".part"
        with open(partial, "wb") as fh:
            for start, end, _desc in ranges:
                header = f"bytes={start}-{end if end is not None else ''}"
                resp = session.get(
                    f"{GFS_HTTPS}/{key}", headers={"Range": header}, timeout=(10, 120)
                )
                if resp.status_code not in (200, 206):
                    return False
                fh.write(resp.content)
        os.replace(partial, dest_path)
        return True
    except (requests.RequestException, OSError):
        # Retries inside the adapter are already exhausted at this point. One forecast
        # hour missing is survivable -- the planner needs 12 of 16 working hours, not
        # all of them -- so report the failure and let the caller carry on.
        return False


def fetch_gfs(
    lat: float = BHOPAL["lat"],
    lon: float = BHOPAL["lon"],
    hours: int = 30,
    date: str | None = None,
    cycle: str | None = None,
    verbose: bool = True,
) -> list[ForecastHour]:
    """Pull hourly forecast for one point from the open GFS bucket on S3.

    Requires `requests`, `cfgrib` and `xarray`. cfgrib needs the ecCodes binary, which
    on most systems arrives with `pip install eccodes cfgrib`; if the import fails,
    `conda install -c conda-forge cfgrib` is the reliable fallback.
    """
    import xarray as xr  # noqa: PLC0415

    if date is None or cycle is None:
        date, cycle = latest_cycle()
    if verbose:
        print(f"[gfs] cycle {date} {cycle}Z  point {lat:.2f}N {lon:.2f}E")

    workdir = tempfile.mkdtemp(prefix="shadecast-gfs-")
    wanted_hours = list(range(0, hours + 1))

    # Each forecast hour costs one index read plus five ranged GETs, so fetching them
    # one after another means ~300 sequential round trips to S3 and a multi-minute wait
    # with nothing on screen. The downloads are independent, so run a small pool of them
    # at once -- small deliberately, because this is NOAA's bandwidth, not ours.
    def _grab(fhour: int) -> tuple[int, str | None]:
        session = _make_session()  # one per thread; Session is not thread-safe
        local = os.path.join(workdir, f"f{fhour:03d}.grib2")
        ok = _download_subset(session, _object_key(date, cycle, fhour), local)
        return fhour, (local if ok else None)

    downloaded: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(_grab, h): h for h in wanted_hours}
        done = 0
        for fut in as_completed(futures):
            fhour, path = fut.result()
            done += 1
            if path:
                downloaded[fhour] = path
            if verbose:
                print(f"\r[gfs] downloading {done}/{len(wanted_hours)} forecast hours",
                      end="", flush=True)
    if verbose:
        print(f"\r[gfs] downloaded {len(downloaded)}/{len(wanted_hours)} forecast hours"
              f"{' ' * 12}")

    out: list[ForecastHour] = []
    for fhour in sorted(downloaded):
        local = downloaded[fhour]
        record: dict[str, float] = {}
        # Several filters are tried per file. Some legitimately match nothing -- at
        # forecast hour 000 there is no accumulated radiation field yet, so the
        # surface/avg filter opens an EMPTY dataset rather than failing. Selecting a
        # latitude on an empty dataset raises KeyError, so every filter is guarded
        # both for the open and for the presence of real coordinates.
        for filters in (
            {"typeOfLevel": "heightAboveGround", "level": 2},
            {"typeOfLevel": "heightAboveGround", "level": 10},
            {"typeOfLevel": "surface", "stepType": "avg"},
            {"typeOfLevel": "surface", "stepType": "instant"},
        ):
            try:
                ds = xr.open_dataset(
                    local,
                    engine="cfgrib",
                    backend_kwargs={"filter_by_keys": filters, "indexpath": ""},
                )
            except Exception:
                continue

            try:
                if "latitude" not in ds.coords or "longitude" not in ds.coords:
                    continue  # empty match for this filter; nothing to read
                point = ds.sel(latitude=lat, longitude=lon % 360, method="nearest")
                for name, var in (
                    ("temp_c", "t2m"),
                    ("rh_pct", "r2"),
                    ("u", "u10"),
                    ("v", "v10"),
                    ("shortwave_wm2", "dswrf"),
                ):
                    if var in point and name not in record:
                        record[name] = float(point[var].values)
            except (KeyError, ValueError):
                continue
            finally:
                ds.close()

        if "temp_c" not in record or "rh_pct" not in record:
            continue

        u = record.get("u", 0.0)
        v = record.get("v", 0.0)
        wind = (u * u + v * v) ** 0.5

        valid = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H").replace(
            tzinfo=timezone.utc
        ) + timedelta(hours=fhour)

        out.append(
            ForecastHour(
                time_utc=valid,
                temp_c=record["temp_c"] - 273.15,  # GFS gives kelvin
                rh_pct=record["rh_pct"],
                wind_ms=wind,
                shortwave_wm2=max(0.0, record.get("shortwave_wm2", 0.0)),
            )
        )
        if verbose:
            print(
                f"[gfs] f{fhour:03d} {valid:%Y-%m-%d %H}Z  "
                f"{out[-1].temp_c:5.1f}C  {out[-1].rh_pct:3.0f}%  "
                f"{out[-1].shortwave_wm2:4.0f} W/m2"
            )

    return out


# ---------------------------------------------------------------------------
# Offline demo profiles
# ---------------------------------------------------------------------------
#
# Used when there is no network, and for the demo video so the recording does not
# depend on whichever day the judges happen to watch it. These are climatologically
# representative Bhopal days, NOT measurements, and the UI labels them as such.

def _diurnal(
    hour: int,
    t_min: float,
    t_max: float,
    rh_min: float,
    rh_max: float,
    cloud: float,
) -> tuple[float, float, float, float]:
    """Smooth diurnal curves for temperature, humidity, wind and radiation."""
    import math

    # Temperature: minimum near 05:00, maximum near 15:00.
    phase = math.cos((hour - 15) / 24.0 * 2 * math.pi)
    temp = (t_max + t_min) / 2 + (t_max - t_min) / 2 * phase

    # Humidity runs inversely to temperature.
    rh = (rh_max + rh_min) / 2 - (rh_max - rh_min) / 2 * phase

    # Clear-sky radiation, a sine between 06:00 and 18:00, damped by cloud.
    if 6 <= hour <= 18:
        sw = 950.0 * math.sin((hour - 6) / 12.0 * math.pi) * (1.0 - cloud)
    else:
        sw = 0.0

    # Wind picks up through the afternoon.
    wind = 1.2 + 2.0 * max(0.0, math.sin((hour - 8) / 14.0 * math.pi))

    return temp, max(5.0, min(99.0, rh)), max(0.0, sw), wind


REAL_DAYS_FILE = os.path.join(os.path.dirname(__file__), "real_days.json")


def _load_real_days() -> dict:
    """Observed GFS days, if scripts/fetch_real_days.py has been run.

    Real data is preferred over the generated profiles wherever it exists. Nothing
    silently substitutes: `is_real()` tells the UI which it is looking at, and the page
    and the shift sheet label them differently.
    """
    try:
        with open(REAL_DAYS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


REAL_DAYS = _load_real_days()


def is_real(key: str) -> bool:
    return key in REAL_DAYS


def day_label(key: str) -> str:
    if key in REAL_DAYS:
        return REAL_DAYS[key]["label"]
    return DEMO_DAYS[key]["label"]


def day_note(key: str) -> str:
    if key in REAL_DAYS:
        return REAL_DAYS[key]["note"]
    return DEMO_DAYS[key]["note"]


def day_source(key: str) -> str:
    if key in REAL_DAYS:
        return REAL_DAYS[key].get("source", "NOAA GFS, observed")
    return "generated profile, climatologically representative — not a measurement"


DEMO_DAYS = {
    "pre_monsoon": {
        "label": "Bhopal, dry pre-monsoon day (mid May)",
        "date": datetime(2026, 5, 15, tzinfo=IST),
        "t_min": 27.0,
        "t_max": 42.0,
        "rh_min": 17.0,
        "rh_max": 58.0,
        "cloud": 0.05,
        "note": "The day everyone expects to be dangerous. 42 C, but very dry.",
    },
    "monsoon_break": {
        "label": "Bhopal, monsoon-break day (late July)",
        "date": datetime(2026, 7, 20, tzinfo=IST),
        "t_min": 25.0,
        "t_max": 31.5,
        "rh_min": 74.0,
        "rh_max": 94.0,
        "cloud": 0.45,
        "note": "11 C cooler. No heat warning fires anywhere in India on this day.",
    },
    "october": {
        "label": "Bhopal, post-monsoon day (October)",
        "date": datetime(2026, 10, 9, tzinfo=IST),
        "t_min": 20.0,
        "t_max": 33.0,
        "rh_min": 32.0,
        "rh_max": 72.0,
        "cloud": 0.15,
        "note": "Today. Shoulder season: the plan should barely need to move.",
    },
}


def synthetic_day(key: str = "pre_monsoon") -> list[ForecastHour]:
    """A full day of Bhopal weather without touching the network.

    Returns the OBSERVED day from real_days.json when it is available, and the
    generated profile otherwise. Callers that need to tell the user which they got
    should ask `is_real(key)`.
    """
    if key in REAL_DAYS:
        entry = REAL_DAYS[key]
        base = datetime.strptime(entry["local_date"], "%Y-%m-%d").replace(tzinfo=IST)
        out: list[ForecastHour] = []
        for row in entry["hours"]:
            local = base.replace(hour=int(row["hour"]))
            out.append(
                ForecastHour(
                    time_utc=local.astimezone(timezone.utc),
                    temp_c=float(row["temp_c"]),
                    rh_pct=float(row["rh_pct"]),
                    wind_ms=float(row["wind_ms"]),
                    shortwave_wm2=float(row["shortwave_wm2"]),
                )
            )
        return sorted(out, key=lambda r: r.time_ist.hour)

    spec = DEMO_DAYS[key]
    base: datetime = spec["date"]
    out: list[ForecastHour] = []
    for hour in range(24):
        temp, rh, sw, wind = _diurnal(
            hour,
            spec["t_min"],
            spec["t_max"],
            spec["rh_min"],
            spec["rh_max"],
            spec["cloud"],
        )
        local = base.replace(hour=hour, minute=0, second=0, microsecond=0)
        out.append(
            ForecastHour(
                time_utc=local.astimezone(timezone.utc),
                temp_c=round(temp, 1),
                rh_pct=round(rh, 1),
                wind_ms=round(wind, 1),
                shortwave_wm2=round(sw, 1),
            )
        )
    return out
