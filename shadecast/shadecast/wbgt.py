"""
Wet Bulb Globe Temperature (WBGT) estimation and ACGIH / ISO 7243 work-rest mapping.

WHY WBGT AND NOT TEMPERATURE
----------------------------
India's Heat Action Plans trigger on dry-bulb air temperature. A Centre for Policy
Research review of 37 state/district/city HAPs found they do not account for humidity,
hot nights, or heat duration. That omission is not academic: this module will show you
a 31 C monsoon day in Bhopal that is MORE dangerous to a working body than a 42 C
pre-monsoon day, because the first one is at 85% humidity and the second is at 18%.
A temperature-triggered warning system issues nothing on the dangerous day.

WBGT is the index that ISO 7243 and the ACGIH work-rest limits are actually written
against, and it is composed of three measurements:

    WBGT_outdoor = 0.7 * Tnwb  +  0.2 * Tg  +  0.1 * Ta

    Tnwb = natural wet bulb  -> carries humidity (the body's ability to cool by sweating)
    Tg   = black globe       -> carries radiant load (standing in the sun)
    Ta   = dry bulb          -> plain air temperature, weighted least

Note the weightings. Air temperature, the only thing Indian HAPs look at, is the
LEAST important of the three inputs.

HOW WE ESTIMATE IT WITHOUT INSTRUMENTS
--------------------------------------
True WBGT needs three instruments on the worksite, including a 150mm black globe
thermometer. We have a weather forecast instead. Each of the three components is
estimated with a published method:

  Tnwb  Stull (2011) psychrometric wet bulb from temperature and relative humidity.
        Accurate to about +/-0.3 C over the range that matters.

  Tg    Linearised black-globe equation from the NWS Tulsa WBGT paper:
            Tg = (B + C*Ta + 7,680,000) / (C + 256,000)
        where C carries wind speed and B carries solar radiation. The authors report
        agreement with measured globe temperature within about 1 F in field tests.

  Ta    Straight from the forecast.

Solar geometry (zenith angle) is computed from latitude, longitude and timestamp, and
the direct/diffuse split of incoming radiation uses the Erbs correlation.

KNOWN LIMITATIONS -- STATE THESE, DO NOT HIDE THEM
--------------------------------------------------
1. We substitute the PSYCHROMETRIC wet bulb for the NATURAL wet bulb. Outdoors in
   strong sun the natural wet bulb runs slightly warmer, so this estimate is mildly
   conservative in the non-conservative direction. A globe thermometer on one pilot
   site would calibrate it.
2. The forecast grid is ~28 km. Within-city variation (asphalt vs tree cover) is not
   resolved. SHADE_FRACTION is the hook where a site-level correction belongs.
3. ACGIH TLVs assume acclimatised, healthy, normally-clothed adults. They are not
   calibrated for pregnancy, cardiac or kidney conditions, or for the 45+ C core
   exposures common on Indian brick kilns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Workload = Literal["light", "moderate", "heavy"]

STEFAN_BOLTZMANN = 5.67e-8
SOLAR_CONSTANT = 1367.0  # W/m2


# ---------------------------------------------------------------------------
# Component 1: natural wet bulb  (Stull 2011)
# ---------------------------------------------------------------------------

def wet_bulb_stull(temp_c: float, rh_pct: float) -> float:
    """Psychrometric wet bulb temperature (C) from air temperature and RH.

    Stull, R. (2011), "Wet-Bulb Temperature from Relative Humidity and Air
    Temperature", Journal of Applied Meteorology and Climatology.

    Valid roughly -20..50 C and 5..99% RH, accurate to about +/-0.3 C.
    """
    rh = min(100.0, max(1.0, rh_pct))
    t = temp_c
    tw = (
        t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * (rh ** 1.5) * math.atan(0.023101 * rh)
        - 4.686035
    )
    # Physical constraint: the wet bulb can never exceed the dry bulb.
    return min(tw, t)


# ---------------------------------------------------------------------------
# Solar geometry and the direct/diffuse split
# ---------------------------------------------------------------------------

def solar_cos_zenith(lat_deg: float, lon_deg: float, when_utc: datetime) -> float:
    """Cosine of the solar zenith angle. 1.0 = sun overhead, <=0 = sun below horizon."""
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    day_of_year = when_utc.timetuple().tm_yday
    utc_hours = when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0

    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1 + (utc_hours - 12.0) / 24.0)

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    time_offset = eqtime + 4.0 * lon_deg
    true_solar_time = utc_hours * 60.0 + time_offset
    hour_angle = math.radians(true_solar_time / 4.0 - 180.0)

    lat = math.radians(lat_deg)
    cos_z = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(
        hour_angle
    )
    return max(-1.0, min(1.0, cos_z))


def diffuse_fraction(shortwave_wm2: float, cos_zenith: float) -> float:
    """Fraction of global horizontal irradiance that is diffuse (Erbs correlation)."""
    if cos_zenith <= 0.01 or shortwave_wm2 <= 0:
        return 1.0
    extraterrestrial = SOLAR_CONSTANT * cos_zenith
    kt = shortwave_wm2 / extraterrestrial  # clearness index
    kt = max(0.0, min(1.0, kt))
    if kt <= 0.22:
        fd = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        fd = (
            0.9511
            - 0.1604 * kt
            + 4.388 * kt**2
            - 16.638 * kt**3
            + 12.336 * kt**4
        )
    else:
        fd = 0.165
    return max(0.0, min(1.0, fd))


# ---------------------------------------------------------------------------
# Component 2: black globe temperature  (NWS Tulsa linearised solution)
# ---------------------------------------------------------------------------

CONVECTION_H = 0.315  # the NWS paper notes 0.437 fitted better in their field tests


def globe_temperature(
    temp_c: float,
    shortwave_wm2: float,
    wind_ms: float,
    cos_zenith: float,
    shade_fraction: float = 0.0,
) -> float:
    """Estimated black globe temperature (C).

    Linearised solution of  Tg^4 + C*Tg = B + C*Ta  about Tg = 40:

        Tg = (B + C*Ta + 7,680,000) / (C + 256,000)

    C carries wind (wind speed in metres per HOUR), B carries solar load. With no
    solar load the expression collapses to Tg = Ta, which is the correct physical
    behaviour in shade and at night.
    """
    wind_mh = max(0.1, wind_ms) * 3600.0
    c_term = CONVECTION_H * (wind_mh**0.58) / 5.3865e-8

    effective_sw = max(0.0, shortwave_wm2) * max(0.0, 1.0 - shade_fraction)
    if effective_sw <= 0 or cos_zenith <= 0.01:
        b_term = 0.0
    else:
        fdif = diffuse_fraction(shortwave_wm2, cos_zenith)
        fdb = 1.0 - fdif
        direct = fdb / (4.0 * STEFAN_BOLTZMANN * max(0.05, cos_zenith))
        diffuse = 1.2 / STEFAN_BOLTZMANN * fdif
        b_term = effective_sw * (direct + diffuse)

    return (b_term + c_term * temp_c + 7_680_000.0) / (c_term + 256_000.0)


# ---------------------------------------------------------------------------
# WBGT
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WbgtBreakdown:
    """Every component kept, so the UI can show the user WHY a day is dangerous."""

    wbgt_c: float
    wet_bulb_c: float
    globe_c: float
    air_c: float
    rh_pct: float
    shortwave_wm2: float
    wind_ms: float

    @property
    def humidity_contribution_c(self) -> float:
        return 0.7 * self.wet_bulb_c

    @property
    def radiant_contribution_c(self) -> float:
        return 0.2 * self.globe_c

    @property
    def air_contribution_c(self) -> float:
        return 0.1 * self.air_c


def wbgt_outdoor(
    temp_c: float,
    rh_pct: float,
    shortwave_wm2: float,
    wind_ms: float,
    when_utc: datetime,
    lat_deg: float,
    lon_deg: float,
    shade_fraction: float = 0.0,
) -> WbgtBreakdown:
    """Estimated outdoor WBGT (C) with its three components kept separate."""
    cos_z = solar_cos_zenith(lat_deg, lon_deg, when_utc)
    tnwb = wet_bulb_stull(temp_c, rh_pct)
    tg = globe_temperature(temp_c, shortwave_wm2, wind_ms, cos_z, shade_fraction)
    wbgt = 0.7 * tnwb + 0.2 * tg + 0.1 * temp_c
    return WbgtBreakdown(
        wbgt_c=wbgt,
        wet_bulb_c=tnwb,
        globe_c=tg,
        air_c=temp_c,
        rh_pct=rh_pct,
        shortwave_wm2=shortwave_wm2,
        wind_ms=wind_ms,
    )


# ---------------------------------------------------------------------------
# Component 3: ACGIH / ISO 7243 work-rest thresholds
# ---------------------------------------------------------------------------
#
# Threshold Limit Values, acclimatised workers, normal work clothing.
# Source: ACGIH TLV table, converted from Fahrenheit.
#
#   Heavy    continuous 77.0F  75/25 78.6F  50/50 82.2F  25/75 86.0F
#   Moderate continuous 80.1F  75/25 82.4F  50/50 84.9F  25/75 88.0F
#   Light    continuous 86.0F  75/25 87.1F  50/50 88.5F  25/75 90.0F
#
# Above the 25/75 ceiling the standard defines no safe ratio for continued exposure,
# so we return 0.0 -- stop work.

def _f_to_c(f: float) -> float:
    return round((f - 32.0) * 5.0 / 9.0, 1)


TLV_TABLE: dict[Workload, list[tuple[float, float]]] = {
    "heavy": [
        (_f_to_c(77.0), 1.00),
        (_f_to_c(78.6), 0.75),
        (_f_to_c(82.2), 0.50),
        (_f_to_c(86.0), 0.25),
    ],
    "moderate": [
        (_f_to_c(80.1), 1.00),
        (_f_to_c(82.4), 0.75),
        (_f_to_c(84.9), 0.50),
        (_f_to_c(88.0), 0.25),
    ],
    "light": [
        (_f_to_c(86.0), 1.00),
        (_f_to_c(87.1), 0.75),
        (_f_to_c(88.5), 0.50),
        (_f_to_c(90.0), 0.25),
    ],
}

# Workers new to the site or in the first hot week of the season are unacclimatised
# and carry a lower tolerance. ACGIH publishes a separate stricter action-limit table;
# we apply a conservative shift rather than a second full table.
UNACCLIMATISED_SHIFT_C = 1.5

WORKLOAD_DESCRIPTIONS = {
    "light": "Supervising, light assembly, walking on level ground",
    "moderate": "Brick laying, plastering, pushing a loaded wheelbarrow",
    "heavy": "Digging, carrying over 20 kg, head-loading, breaking concrete",
}


def work_fraction(
    wbgt_c: float, workload: Workload = "moderate", acclimatised: bool = True
) -> float:
    """Fraction of each hour that may be worked: 1.0, 0.75, 0.5, 0.25 or 0.0."""
    effective = wbgt_c + (0.0 if acclimatised else UNACCLIMATISED_SHIFT_C)
    for ceiling, fraction in TLV_TABLE[workload]:
        if effective <= ceiling:
            return fraction
    return 0.0


def rest_minutes_per_hour(fraction: float) -> int:
    """Minutes of rest required inside each worked hour."""
    return int(round((1.0 - fraction) * 60))


BAND_NAMES = {1.00: "safe", 0.75: "caution", 0.50: "high", 0.25: "severe", 0.00: "stop"}
BAND_LABELS_HI = {
    "safe": "सुरक्षित",
    "caution": "सावधान",
    "high": "ख़तरा",
    "severe": "भारी ख़तरा",
    "stop": "काम बंद",
}


def risk_band(fraction: float) -> str:
    return BAND_NAMES[fraction]


# Drinking water per worker per worked hour. NIOSH guidance for work in heat is
# roughly one cup (0.24 L) every 15-20 minutes, which is about 1 L/hour at the hot end.
WATER_LITRES_PER_HOUR = {
    "safe": 0.5,
    "caution": 0.75,
    "high": 1.0,
    "severe": 1.0,
    "stop": 0.0,
}
