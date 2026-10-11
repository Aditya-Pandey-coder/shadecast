"""The physics. If these are wrong, everything downstream is confidently wrong."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from shadecast.wbgt import (
    TLV_TABLE,
    globe_temperature,
    solar_cos_zenith,
    wbgt_outdoor,
    wet_bulb_stull,
    work_fraction,
)

IST = timezone(timedelta(hours=5, minutes=30))
BHOPAL = (23.26, 77.41)


def at(hour: int, month: int = 5, day: int = 15) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=IST).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Wet bulb
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "temp_c,rh,expected",
    [
        # Reference values from psychrometric tables, tolerance covers Stull's
        # stated +/-0.3 C plus table rounding.
        (30.0, 50.0, 22.0),
        (20.0, 50.0, 13.7),
        (40.0, 20.0, 23.1),
        (35.0, 80.0, 31.6),
    ],
)
def test_wet_bulb_matches_psychrometric_tables(temp_c, rh, expected):
    assert wet_bulb_stull(temp_c, rh) == pytest.approx(expected, abs=0.6)


def test_wet_bulb_equals_dry_bulb_at_saturation():
    for t in (10.0, 25.0, 40.0):
        assert wet_bulb_stull(t, 100.0) == pytest.approx(t, abs=0.5)


def test_wet_bulb_never_exceeds_dry_bulb():
    for t in range(0, 50, 5):
        for rh in range(5, 101, 5):
            assert wet_bulb_stull(float(t), float(rh)) <= t + 1e-9


def test_wet_bulb_falls_as_air_dries():
    vals = [wet_bulb_stull(38.0, rh) for rh in (90, 70, 50, 30, 10)]
    assert vals == sorted(vals, reverse=True)


# ---------------------------------------------------------------------------
# Globe temperature
# ---------------------------------------------------------------------------

def test_globe_equals_air_without_sun():
    """At night the black globe reads air temperature. This is the sanity check that
    caught the original formula, which read seven degrees too hot in the dark."""
    assert globe_temperature(30.0, 0.0, 2.0, cos_zenith=0.0) == pytest.approx(30.0, abs=0.3)


def test_globe_exceeds_air_in_sun():
    tg = globe_temperature(38.0, 850.0, 2.0, cos_zenith=0.9)
    assert tg > 38.0
    # Field globes run roughly 5-20 C above air in strong sun; anything outside that
    # means the radiation term is mis-scaled.
    assert 5.0 < tg - 38.0 < 25.0


def test_wind_cools_the_globe():
    still = globe_temperature(38.0, 850.0, 0.5, cos_zenith=0.9)
    breezy = globe_temperature(38.0, 850.0, 8.0, cos_zenith=0.9)
    assert breezy < still


def test_shade_reduces_globe_toward_air():
    exposed = globe_temperature(38.0, 850.0, 2.0, 0.9, shade_fraction=0.0)
    shaded = globe_temperature(38.0, 850.0, 2.0, 0.9, shade_fraction=1.0)
    assert shaded < exposed
    assert shaded == pytest.approx(38.0, abs=0.3)


# ---------------------------------------------------------------------------
# Solar geometry
# ---------------------------------------------------------------------------

def test_sun_is_up_at_noon_and_down_at_midnight():
    assert solar_cos_zenith(*BHOPAL, at(12)) > 0.7
    assert solar_cos_zenith(*BHOPAL, at(0)) < 0.0


def test_sun_higher_in_may_than_december_in_bhopal():
    may = solar_cos_zenith(*BHOPAL, at(12, month=5, day=15))
    dec = solar_cos_zenith(*BHOPAL, datetime(2026, 12, 15, 12, tzinfo=IST).astimezone(timezone.utc))
    assert may > dec


# ---------------------------------------------------------------------------
# WBGT composition -- the claim the whole project rests on
# ---------------------------------------------------------------------------

def test_humid_cool_day_is_as_dangerous_as_hot_dry_day():
    """Shadecast's headline, as a test.

    A 31 C day at 80% humidity and a 42 C day at 18% humidity land within a degree of
    each other in WBGT. India's heat warnings trigger on air temperature, so only one
    of these two days produces a warning. If this test ever fails, the headline is no
    longer true and the pitch has to change.
    """
    hot_dry = wbgt_outdoor(42.0, 18.0, 760.0, 3.0, at(15), *BHOPAL)
    cool_humid = wbgt_outdoor(31.0, 80.0, 400.0, 2.0, at(13, month=7, day=20), *BHOPAL)

    assert abs(hot_dry.wbgt_c - cool_humid.wbgt_c) < 1.5
    assert hot_dry.air_c - cool_humid.air_c > 10.0


def test_humidity_dominates_the_index():
    """Air temperature carries a weight of 0.1 and the wet bulb 0.7. The whole argument
    against temperature-triggered warnings is this ratio."""
    b = wbgt_outdoor(31.0, 85.0, 400.0, 2.0, at(13, month=7, day=20), *BHOPAL)
    assert b.humidity_contribution_c > 4 * b.air_contribution_c


def test_components_sum_to_the_index():
    b = wbgt_outdoor(36.0, 40.0, 700.0, 2.5, at(14), *BHOPAL)
    total = b.humidity_contribution_c + b.radiant_contribution_c + b.air_contribution_c
    assert total == pytest.approx(b.wbgt_c, abs=1e-9)


def test_wbgt_rises_with_humidity_at_fixed_temperature():
    vals = [
        wbgt_outdoor(35.0, rh, 600.0, 2.0, at(13), *BHOPAL).wbgt_c
        for rh in (20, 40, 60, 80)
    ]
    assert vals == sorted(vals)


# ---------------------------------------------------------------------------
# ACGIH thresholds
# ---------------------------------------------------------------------------

def test_tlv_table_matches_published_fahrenheit_values():
    """ACGIH TLVs, acclimatised, normal clothing. Hard-coded from the published table
    so a typo in the conversion cannot pass silently."""
    assert [c for c, _ in TLV_TABLE["heavy"]] == [25.0, 25.9, 27.9, 30.0]
    assert [c for c, _ in TLV_TABLE["moderate"]] == [26.7, 28.0, 29.4, 31.1]
    assert [c for c, _ in TLV_TABLE["light"]] == [30.0, 30.6, 31.4, 32.2]


def test_thresholds_are_stricter_for_heavier_work():
    for i in range(4):
        assert TLV_TABLE["heavy"][i][0] < TLV_TABLE["moderate"][i][0]
        assert TLV_TABLE["moderate"][i][0] < TLV_TABLE["light"][i][0]


@pytest.mark.parametrize(
    "wbgt,workload,expected",
    [
        (24.0, "heavy", 1.00),
        (25.5, "heavy", 0.75),
        (27.0, "heavy", 0.50),
        (29.0, "heavy", 0.25),
        (31.0, "heavy", 0.00),
        (26.0, "moderate", 1.00),
        (33.0, "light", 0.00),
    ],
)
def test_work_fraction_lands_in_the_right_band(wbgt, workload, expected):
    assert work_fraction(wbgt, workload) == expected


def test_unacclimatised_workers_get_a_stricter_answer():
    """Never more permissive. A bug in the wrong direction here sends someone who is
    not heat-adapted into an hour that the standard says is unsafe for them."""
    for wbgt in [20.0 + 0.5 * i for i in range(30)]:
        for load in ("light", "moderate", "heavy"):
            assert work_fraction(wbgt, load, acclimatised=False) <= work_fraction(
                wbgt, load, acclimatised=True
            )


def test_work_fraction_never_rises_with_heat():
    for load in ("light", "moderate", "heavy"):
        vals = [work_fraction(20.0 + 0.5 * i, load) for i in range(40)]
        assert vals == sorted(vals, reverse=True)


def test_above_all_thresholds_means_stop():
    assert work_fraction(45.0, "light") == 0.0
    assert work_fraction(45.0, "heavy") == 0.0
