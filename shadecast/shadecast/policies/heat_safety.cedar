// Heat-safety assignment rules, as policy rather than as if-statements.
//
// WHY CEDAR AND NOT PYTHON
// ------------------------
// These are not engineering rules, they are labour-safety rules. They come from ACGIH,
// from India's Occupational Safety, Health and Working Conditions Code, and from
// whatever a particular contractor or city has committed to. They change without the
// code changing, different sites run under different ones, and somebody who is not a
// programmer has to be able to read them and say "yes, that is our policy".
//
// Buried in Python they are invisible and unauditable. In Cedar they are a file a
// safety officer can read, a labour inspector can audit, and a site can override
// without touching the scheduler. Cedar is AWS's open-source authorization language;
// deciding who may be assigned to which hour is exactly an authorization question.
//
// Entities:
//   Worker   -- acclimatised, age_band, has_heat_risk_condition
//   Hour     -- band ("safe" | "caution" | "high" | "severe" | "stop"), wbgt
//   action   -- Action::"assign"

// -- the permit -------------------------------------------------------------
// Assignment is allowed by default; every rule below takes it away. Written this way
// round because a scheduler that silently assigns nobody is a scheduler nobody uses,
// whereas a forbid that fires is visible and explainable.
permit (
  principal,
  action == Action::"assign",
  resource
);

// -- nobody works an hour above the threshold limit values ------------------
// ACGIH defines no safe work ratio for continued exposure above the 25/75 ceiling.
forbid (
  principal,
  action == Action::"assign",
  resource
)
when { resource.band == "stop" };

// -- unacclimatised workers ------------------------------------------------
// First week on site, or first hot spell of the season. ACGIH publishes a separate,
// stricter action limit for them; we hold them out of the severe band entirely.
forbid (
  principal,
  action == Action::"assign",
  resource
)
when { principal.acclimatised == false && resource.band == "severe" };

// -- young workers ---------------------------------------------------------
// Thermoregulation is less developed and the legal protections are stronger.
forbid (
  principal,
  action == Action::"assign",
  resource
)
when { principal.age_band == "under_18" && (resource.band == "severe" || resource.band == "high") };

// -- workers with a heat-aggravated condition ------------------------------
// Chronic kidney disease is the one the Indian occupational-health literature keeps
// finding in outdoor workers, alongside cardiac conditions and pregnancy. The ACGIH
// limits are not calibrated for any of them, so we do not pretend they are.
forbid (
  principal,
  action == Action::"assign",
  resource
)
when { principal.has_heat_risk_condition == true && resource.band != "safe" && resource.band != "caution" };

// -- older workers in the severe band --------------------------------------
forbid (
  principal,
  action == Action::"assign",
  resource
)
when { principal.age_band == "over_55" && resource.band == "severe" };
