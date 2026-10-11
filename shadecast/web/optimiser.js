/*
 * Shadecast shift optimiser, browser build.
 *
 * WHY THIS EXISTS AND WHY IT IS A RISK WORTH TAKING
 * -------------------------------------------------
 * The heat physics stays in Python. WBGT is the part that has to be right, it is
 * validated against published methods, and re-implementing it twice would double the
 * surface area for it to drift and be wrong in only one language.
 *
 * The SEARCH is different. Given a day's hourly work-fractions, choosing where to put
 * the shift is arithmetic over a few hundred candidates. Running it in the browser is
 * what turns this page from a brochure showing canned answers into a tool a supervisor
 * can put their own numbers into and watch the plan move.
 *
 * The honesty requirement that comes with that: tests/test_js_parity.py runs this file
 * under Node against the Python optimiser across the full grid of days, workloads,
 * acclimatisation, required hours and span limits, and asserts the two produce
 * identical plans. If they ever diverge, the build fails. A second implementation you
 * have not pinned to the first is a second set of bugs.
 *
 * Mirrors shadecast/schedule.py. Any change here needs the same change there.
 */

(function (root) {
  "use strict";

  var EARLIEST_START = 5;
  var LATEST_END = 20;
  var MIN_CLOCK_HOURS = 3;
  var MAX_CLOCK_HOURS = 9;
  var BASELINE_START = 9;
  var BASELINE_BREAK = 13;
  var BASELINE_END = 18;
  var EPS = 1e-9;

  // Severe ceiling = the 25/75 threshold limit value for the workload, in C WBGT.
  var SEVERE_CEILING = { light: 32.2, moderate: 31.1, heavy: 30.0 };

  function blockHours(start, end) {
    var out = [];
    for (var h = start; h < end; h++) out.push(h);
    return out;
  }

  /* A candidate plan, scored against the day. `byHour` maps hour -> {fraction, wbgt}. */
  function makePlan(blocks, byHour, workload) {
    var worked = [];
    blocks.forEach(function (b) {
      worked = worked.concat(blockHours(b[0], b[1]));
    });
    worked.sort(function (a, b) { return a - b; });

    var productive = 0, severe = 0, exposure = 0, water = 0, peak = 0;
    var ceiling = SEVERE_CEILING[workload];
    worked.forEach(function (h) {
      var s = byHour[h];
      if (!s) return;
      productive += s.fraction;
      if (s.fraction <= 0.25) severe += 1;
      if (s.wbgt > ceiling) exposure += s.wbgt - ceiling;
      water += s.water_litres;
      if (s.wbgt > peak) peak = s.wbgt;
    });

    var span = worked.length ? worked[worked.length - 1] + 1 - worked[0] : 0;
    return {
      blocks: blocks,
      workedHours: worked,
      clockHours: worked.length,
      spanHours: span,
      gapHours: span - worked.length,
      productiveHours: productive,
      severeHours: severe,
      exposureIndex: exposure,
      waterLitres: water,
      peakWbgt: peak,
      startHour: worked.length ? worked[0] : 99,
      label: blocks.map(function (b) {
        return pad(b[0]) + ":00-" + pad(b[1]) + ":00";
      }).join("  +  ")
    };
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function candidatePlans(byHour, clockHours) {
    var out = [];
    var available = Object.keys(byHour)
      .map(Number)
      .filter(function (h) { return h >= EARLIEST_START && h < LATEST_END; })
      .sort(function (a, b) { return a - b; });
    if (!available.length) return out;

    function allPresent(hours) {
      return hours.every(function (h) { return byHour[h] !== undefined; });
    }

    // One continuous block.
    available.forEach(function (start) {
      var end = start + clockHours;
      if (end > LATEST_END) return;
      if (allPresent(blockHours(start, end))) out.push([[start, end]]);
    });

    // Split shift: a morning block and an evening block, gap of at least two hours.
    for (var mLen = 2; mLen < clockHours - 1; mLen++) {
      var eLen = clockHours - mLen;
      for (var i = 0; i < available.length; i++) {
        var mStart = available[i];
        var mEnd = mStart + mLen;
        if (mEnd > LATEST_END) continue;
        for (var eStart = mEnd + 2; eStart < LATEST_END; eStart++) {
          var eEnd = eStart + eLen;
          if (eEnd > LATEST_END) continue;
          var hours = blockHours(mStart, mEnd).concat(blockHours(eStart, eEnd));
          if (allPresent(hours)) out.push([[mStart, mEnd], [eStart, eEnd]]);
        }
      }
    }
    return out;
  }

  /* Lexicographic comparison over an array of numbers. */
  function cmpKey(a, b) {
    for (var i = 0; i < a.length; i++) {
      if (a[i] < b[i]) return -1;
      if (a[i] > b[i]) return 1;
    }
    return 0;
  }

  function minBy(list, keyFn) {
    var best = list[0], bestKey = keyFn(best);
    for (var i = 1; i < list.length; i++) {
      var k = keyFn(list[i]);
      if (cmpKey(k, bestKey) < 0) { best = list[i]; bestKey = k; }
    }
    return best;
  }

  function maxBy(list, keyFn) {
    var best = list[0], bestKey = keyFn(best);
    for (var i = 1; i < list.length; i++) {
      var k = keyFn(list[i]);
      if (cmpKey(k, bestKey) > 0) { best = list[i]; bestKey = k; }
    }
    return best;
  }

  /*
   * hours: array of {hour, fraction, wbgt, band, rest_minutes, water_litres, air_c, rh}
   * opts:  {workload, requiredHours, maxSpanHours, crewSize, dailyWage}
   */
  function plan(hours, opts) {
    var workload = opts.workload || "moderate";
    var required = opts.requiredHours === undefined ? 6 : opts.requiredHours;
    var maxSpan = opts.maxSpanHours === undefined ? 11 : opts.maxSpanHours;
    var crew = opts.crewSize === undefined ? 12 : opts.crewSize;
    var wage = opts.dailyWage === undefined ? 450 : opts.dailyWage;

    var byHour = {};
    hours.forEach(function (h) { byHour[h.hour] = h; });

    var candidates = [];
    for (var ch = MIN_CLOCK_HOURS; ch <= MAX_CLOCK_HOURS; ch++) {
      candidatePlans(byHour, ch).forEach(function (blocks) {
        candidates.push(makePlan(blocks, byHour, workload));
      });
    }
    if (!candidates.length) return null;

    // Best plan ignoring the span limit, kept so the cost of the limit is reportable.
    var unconstrained = minBy(candidates, function (p) {
      return [-p.productiveHours, p.severeHours, p.exposureIndex];
    });

    var withinSpan = candidates.filter(function (p) { return p.spanHours <= maxSpan; });
    if (!withinSpan.length) {
      var minSpan = Math.min.apply(null, candidates.map(function (p) { return p.spanHours; }));
      withinSpan = candidates.filter(function (p) { return p.spanHours === minSpan; });
    }

    var feasible = withinSpan.filter(function (p) {
      return p.productiveHours + EPS >= required;
    });
    var pool = feasible.length ? feasible : withinSpan;

    var best = feasible.length
      ? minBy(pool, function (p) {
          return [p.severeHours, p.exposureIndex, p.clockHours, p.startHour];
        })
      : maxBy(pool, function (p) {
          return [p.productiveHours, -p.severeHours, -p.exposureIndex];
        });

    var baseline = makePlan(
      [[BASELINE_START, BASELINE_BREAK], [BASELINE_BREAK + 1, BASELINE_END]],
      byHour, workload
    );

    var rescued = best.productiveHours - baseline.productiveHours;
    var spanCost = Math.max(0, unconstrained.productiveHours - best.productiveHours);
    var differs = unconstrained.workedHours.join(",") !== best.workedHours.join(",");

    var stopHour = null;
    hours.slice().sort(function (a, b) { return a.hour - b.hour; }).forEach(function (h) {
      if (stopHour === null && h.fraction === 0 && h.hour >= BASELINE_START) {
        stopHour = h.hour;
      }
    });

    return {
      blocks: best.blocks.map(function (b) {
        return pad(b[0]) + ":00-" + pad(b[1]) + ":00";
      }),
      workedHours: best.workedHours,
      baselineWorkedHours: baseline.workedHours,
      spanHours: best.spanHours,
      gapHours: best.gapHours,
      clockHours: best.clockHours,
      productiveHours: round2(best.productiveHours),
      baselineProductiveHours: round2(baseline.productiveHours),
      hoursRescued: round2(rescued),
      severeHoursFaced: round1(best.severeHours),
      severeHoursRemoved: round1(baseline.severeHours - best.severeHours),
      peakWbgt: round1(best.peakWbgt),
      baselinePeakWbgt: round1(baseline.peakWbgt),
      waterPerWorker: round1(best.waterLitres),
      waterCrew: round1(best.waterLitres * crew),
      wagesProtected: Math.round(Math.max(0, rescued) / 8 * wage * crew),
      meetsRequirement: best.productiveHours + EPS >= required,
      shortfallHours: round2(Math.max(0, required - best.productiveHours)),
      spanCostHours: round2(spanCost),
      stopWorkHour: stopHour,
      alternative: differs ? {
        blocks: unconstrained.blocks.map(function (b) {
          return pad(b[0]) + ":00-" + pad(b[1]) + ":00";
        }),
        spanHours: unconstrained.spanHours,
        gapHours: unconstrained.gapHours,
        productiveHours: round2(unconstrained.productiveHours),
        severeHoursFaced: round1(unconstrained.severeHours)
      } : null
    };
  }

  function round1(x) { return Math.round(x * 10) / 10; }
  function round2(x) { return Math.round(x * 100) / 100; }

  var api = { plan: plan, SEVERE_CEILING: SEVERE_CEILING,
              EARLIEST_START: EARLIEST_START, LATEST_END: LATEST_END };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShadecastPlanner = api;
})(typeof self !== "undefined" ? self : this);
