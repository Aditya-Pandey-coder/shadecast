# Security and data handling

Shadecast schedules work for people whose health depends on getting it right, and the
message it sends is about where a named crew will be and when. That is worth taking
seriously even in a hackathon build, so here is the actual threat model rather than a
list of reassuring words.

## What data exists, and what we chose not to collect

| Data | Where it lives | Why |
|---|---|---|
| Weather forecast | NOAA GFS, public, CC0 | Not personal data at all |
| Site: lat/lon, crew size, day rate, workload | `shadecast-sites` | The minimum the optimiser needs |
| Worker attributes | acclimatised / age band / has a heat-aggravated condition | See below |
| Worker names | **not stored** | Not needed to compute a plan |
| Worker phone numbers | **not stored by Shadecast** | SNS holds the subscription, we publish to a topic |
| Location traces | **never collected** | A shift planner does not need to track anyone |

The worker attributes are health-adjacent and the most sensitive thing here. They are
carried as three coarse flags, never as a diagnosis, never with a name attached, and the
demo crew is synthetic (`sample_crew()`). A real deployment would hold them under
whatever the site's own consent and retention rules are; the schema is deliberately thin
so there is little to leak.

## Authorization is a policy file, not an `if` statement

Which worker may be assigned which hour is decided by Cedar against
`shadecast/policies/heat_safety.cedar`. Two reasons that matters beyond tidiness:

- **Auditable.** A labour inspector or safety officer can read the rules without reading
  Python. Rules buried in conditionals are rules nobody outside the team can check.
- **Fails closed, loudly.** Cedar once returned `NoDecision` for every request because an
  entity attribute held a float and Cedar has no float type. Every denial still looked
  plausible, because the explanation text came from Python. A `NoDecision` now raises
  rather than being read as a deny. An authorization layer that can fail open while
  looking identical to success is not an authorization layer.

## The web page

- **No third-party code.** The optimiser and all page logic are inlined. The only
  external requests are Google Fonts stylesheets and font files, which a CSP restricts
  to exactly those two origins.
- **CSP** set via meta tag: `default-src 'none'`, no `connect-src`, so the page cannot
  make network requests even if something injected tried to. `form-action 'none'`,
  `frame-ancestors 'none'`, `base-uri 'none'`.
- **Nothing is sent anywhere.** The plan is computed locally from data embedded at build
  time. No analytics, no telemetry, no backend call from the page.
- **Nothing is stored.** No cookies, no `localStorage`, no `sessionStorage`. Reload and
  the page is identical.
- **No user input reaches HTML as markup.** Values interpolated into the DOM go through
  an `esc()` helper or `textContent`. The inputs are sliders and fixed segmented
  controls, so there is no free text path, but the escaping is there because the next
  version will have one.

## The AWS side

- Every Lambda gets a scoped policy: the planner can read sites and forecasts and write
  plans, and nothing else. No wildcard resources.
- The forecast table carries a 7-day TTL. Stale forecasts are not useful and not kept.
- The S3 read is anonymous (`--no-sign-request`) against a public CC0 bucket — no
  credentials involved in the one network dependency the project actually has.
- The web bucket is public-read for the static site and holds no personal data.

## What this build does not do

Stated plainly, because a hackathon project claiming to be production-secure would be
lying:

- **No authentication.** Anyone with the URL sees the demo site. A real deployment needs
  Cognito or equivalent in front of anything site-specific.
- **No encryption beyond AWS defaults.** DynamoDB's at-rest encryption, TLS in transit.
  No per-field encryption of the worker health flags, which is what a real deployment
  holding real ones would want.
- **No audit log.** Who changed a site's configuration, and when, is not recorded.
- **No rate limiting** on the API.
- **The SMS path is unverified end to end.** SNS delivery to Indian numbers requires TRAI
  DLT registration, which we could not complete in a weekend.
