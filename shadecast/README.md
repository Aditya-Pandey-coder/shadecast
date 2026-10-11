# Shadecast

**Move the work, not the worker.**

**A shift planner for outdoor work crews. It does not tell you to stop working. It tells you which hours cost the least.**

Heat-aware shift planning for outdoor crews.
Built for Environmental Hacks, Bharat Builds Tour 2026 · Track: Heat and Water · Bhopal

*Shadecast to the supervisor; छाँव — "shade" — on the worker's phone, because that is
the word they would use for it.*

---

## The problem

Heat-related productivity loss and kidney disease cost Indian workers around **$78 billion in
lost wages, about 2% of GDP**. Workers in construction and road laying lose roughly **24 days
of work a year** to heat — close to a tenth of their annual earnings. India loses more labour
hours to heat than any other country.

Around **30% of that loss is recoverable right now**, simply by moving work out of the
hottest hours. Nobody is doing the moving.

India's answer is Heat Action Plans. A Centre for Policy Research review of 37 of them found
they are poor at identifying and targeting vulnerable groups, and that **only 10 of 37 set
locally defined temperature thresholds**. They trigger on air temperature, and they ignore
humidity entirely.

That omission is the bug. Run `python3 run_demo.py --compare`:

```
Bhopal, dry pre-monsoon day (mid May)      peak air 42.0 C    peak WBGT 30.0 C
Bhopal, monsoon-break day (late July)      peak air 31.5 C    peak WBGT 30.1 C
```

A day **10.5 °C cooler** puts the same strain on a working body and costs the same six lost
work hours. On that day, no heat warning fires anywhere in India.

## What Shadecast does

1. Pulls the hourly forecast for a worksite from **NOAA GFS**, published as open data in S3.
2. Estimates **Wet Bulb Globe Temperature** — the index ISO 7243 and the ACGIH work-rest
   limits are actually written against, which accounts for humidity, radiant sun and wind.
3. Searches every legal shift placement, including split shifts, and picks the one that
   delivers the required work with the fewest hours of severe exposure.
4. Sends the supervisor a shift sheet and every worker **an SMS in Hindi** that lands on a
   ₹1,000 feature phone. No app, no data connection, no English.

```
TODAY'S SHIFT   05:00-11:00  +  17:00-20:00

 >05:00   23.4  28.0   55%  ###  safe      continuous
 >08:00   27.5  32.6   43%  ##   caution   45 min / 15 min
  13:00   30.0  41.0   20%  !    severe    15 min / 45 min
 >17:00   29.3  41.0   20%  #    high      30 min / 30 min

  Productive hours   2.8  ->  6.2   (+3.5)
  Severe-risk hours  removed: 5
  Wages protected    approx Rs 2,362 for the crew (estimate)
```

## Where AWS fits

The NOAA Global Forecast System is published to `s3://noaa-gfs-bdp-pds` under CC0, a new
cycle every six hours, readable with **no AWS account at all**:

```bash
aws s3 ls --no-sign-request s3://noaa-gfs-bdp-pds/
```

AWS is not bolted onto this project. It is the only free supply line for the single input
the product cannot exist without.

### One codebase, both ways to build

The hackathon offers two paths: locally with AWS open-source tools, or deployed on AWS
services. Most projects pick one and the other becomes dead code. Shadecast makes it a
runtime flag.

`aws/lambda_planner.py` and `aws/lambda_ingest.py` are the real handlers either way. They
never import boto3 directly — they ask `shadecast/store.py` for a table:

```bash
python3 scripts/run_pipeline.py              # local JSON storage, no account
SHADECAST_BACKEND=dynamodb sam deploy --guided  # same handlers, DynamoDB + SNS
```

The function demonstrated on a laptop is byte-for-byte the function that would run in
Lambda. There is no "local version" to drift out of sync.

| Piece | Build It (local) | Ship It (deployed) |
|---|---|---|
| Forecast data | `s3://noaa-gfs-bdp-pds`, anonymous | same bucket |
| GRIB ingest | `aws/lambda_ingest.py` direct, or SAM CLI | Lambda (container) + EventBridge |
| WBGT + shift search | `aws/lambda_planner.py` direct, or SAM CLI | Lambda (plain zip) |
| Sites, forecasts, plans | JSON under `.shadecast-data/` | DynamoDB, 7-day TTL on forecasts |
| Message to the crew | `.shadecast-data/outbox.txt` | SNS → SMS |
| Supervisor page | `web/index.html` over `file://` | S3 static site + API Gateway |

One deliberate split in both modes: **the planner has no hard dependencies.** Reading
GRIB2 needs the native ecCodes library; computing heat strain needs `math`. So GRIB lives
in its own function, and the function that must never fail has nothing in it to break.

### Downloading 2 MB instead of 500 MB

A full GFS file for one forecast hour is about 500 MB, and we need five fields. Every GFS
object has a sibling `.idx` listing each record's byte offset, so `shadecast/forecast.py` reads
the index, computes byte ranges, and issues ranged GETs. That is the difference between a
pipeline that finishes inside a Lambda timeout and one that does not.

## The constraint that changed the product

Our first optimiser recommended this for a Bhopal May day:

```
05:00-11:00  +  17:00-20:00     6.2 productive hours, zero severe-risk hours
```

Technically excellent and quietly brutal. That is a crew on site from 5 AM to 8 PM —
**fifteen hours of day for nine hours of work.** Most informal workers in an Indian city
commute, often an hour each way, so that is a seventeen-hour day, and the six-hour gap is
too short to go home and worth less than the fare. They spend it sitting at the site, in
the heat we just told them to avoid. And the tool reported it as *wages protected*.

So the optimiser now scores the worker's day, not just their exposure:

```
05:00-14:00                      5.5 productive hours, three severe-risk hours
                                 9 hours on site, straight through
```

**Neither plan wins outright.** The split shift removes all severe exposure; the straight
shift gives people their evening back. Which is right depends on where the crew sleeps —
a fact no weather model contains. So it is an input (`workers_commute`), the default is
the shorter day, and the page shows both options with what each one costs.

## Policy, not if-statements

Who on the crew may work which hour is a separate question from when it is safe to work
at all, and it is an authorization question. It lives in
[`shadecast/policies/heat_safety.cedar`](shadecast/policies/heat_safety.cedar), evaluated by
**Cedar**, AWS's open-source policy language:

```cedar
forbid (principal, action == Action::"assign", resource)
when { principal.acclimatised == false && resource.band == "severe" };
```

Unacclimatised workers, workers under 18, workers over 55, and workers with a
heat-aggravated condition are held out of the bands they should not be in. On the May
plan that clears 8 of 12 and names the other four with a reason.

These are labour-safety rules, not engineering rules: they come from ACGIH and India's
OSH Code, they differ by site, and a safety officer who does not write Python has to be
able to read them and an inspector has to be able to audit them. Buried in `if`
statements they are invisible — which is precisely the failure the CPR review found in
India's Heat Action Plans, where vulnerable groups are named in the document and absent
from what actually gets done.

## Two optimisers, held identical

The supervisor page recomputes the plan as you drag a slider, which means the shift
search runs in the browser as well as in Lambda. Two implementations of the same logic is
two sets of bugs unless they are pinned together, so
[`tests/test_js_parity.py`](tests/test_js_parity.py) runs `web/optimiser.js` under Node
against the Python optimiser across every combination of day, workload, acclimatisation,
required hours and span limit — 270 cases — and asserts the plans are identical.

The physics does **not** run twice. WBGT is computed once, in Python, and the hourly
result is what the browser is given. Re-implementing the part that has to be right would
double the surface area for it to drift and be wrong in only one language; re-implementing
a search over a few hundred candidates, with a test pinning it, is what turns the page
from a brochure into a tool.

## The model, and what is wrong with it

`WBGT = 0.7·Tnwb + 0.2·Tg + 0.1·Ta`. Note the weights: **air temperature, the only thing
Indian heat warnings look at, is the least important of the three terms.**

| Term | Method | Source |
|---|---|---|
| Wet bulb | Stull (2011), ±0.3 °C | J. Applied Meteorology and Climatology |
| Black globe | Linearised `Tg⁴ + C·Tg = B + C·Ta`, wind in `C`, solar in `B` | [NWS Tulsa WBGT paper](https://www.weather.gov/media/tsa/pdf/WBGTpaper2.pdf) |
| Work-rest ratios | ACGIH TLVs, acclimatised, normal clothing | ISO 7243 |

**Known limitations, stated on the page and in the video:**

- We substitute the psychrometric wet bulb for the natural wet bulb. Outdoors in strong sun
  the natural wet bulb runs slightly warmer, so this is mildly non-conservative. A ₹2,000
  globe thermometer on one pilot site would calibrate it.
- The GFS grid is ~28 km. Asphalt-versus-tree-cover variation within a city is not resolved.
  `shade_fraction` on each site is the hook where a site-level correction belongs.
- ACGIH TLVs assume healthy acclimatised adults. They are not calibrated for pregnancy,
  cardiac or kidney conditions.
- "Wages protected" is an estimate: rescued productive hours priced at the day rate.

## Run it

```bash
pip install -r requirements.txt
```

**The whole pipeline, no AWS account:**

```bash
python3 scripts/run_pipeline.py --offline   # stored day, no network
python3 scripts/run_pipeline.py             # live NOAA GFS from S3
```

That registers a site, runs the ingest handler, runs the planner handler, and serves the
result through the API handler — the real Lambda functions, in order.

**Use real observed weather instead of generated profiles:**

```bash
python3 scripts/fetch_real_days.py
python3 build_scenarios.py
```

Pulls the actual GFS analysis for three Bhopal dates out of the open bucket and writes
`shadecast/real_days.json`. Everything downstream prefers it automatically, and the UI
labels each day **observed** or **generated profile** so the two are never confused. If
the archive does not hold a date, the generated profile stays and says so.

**The tests:**

```bash
python3 -m pytest
```

325 of them. The engine against published psychrometric values, the ACGIH table against
the published Fahrenheit figures, the span constraint against the exact fifteen-hour plan
that started it, Cedar actually denying rather than failing open, and 270 cases holding
the browser optimiser identical to the Python one. The suite has been mutation-checked:
disabling the span constraint or altering the JS search makes it fail.

**The supervisor page:**

```bash
python3 build_scenarios.py && open web/index.html
```

**The engine on its own:**

```bash
python3 run_demo.py --compare                # the dry-vs-humid headline
python3 run_demo.py --workload heavy --unacclimatised
```

Only the live GFS paths need the network. If `cfgrib` fights you, `pip install eccodes
cfgrib` usually suffices; on Python 3.9 there may be no wheel, and 3.12 is the easy fix.

**With SAM CLI, if you have Docker:**

```bash
sam build --template aws/template.yaml
sam local invoke PlannerFunction --event events/scheduled.json
```

## Deploy (optional)

Nothing above requires an AWS account. To run the same handlers on AWS instead:

```bash
sam build --template aws/template.yaml
sam deploy --guided --stack-name shadecast --capabilities CAPABILITY_IAM

python3 scripts/seed_site.py
aws lambda invoke --function-name shadecast-ingest  /dev/stdout
aws lambda invoke --function-name shadecast-planner /dev/stdout
aws s3 sync web/ s3://$(aws cloudformation describe-stacks --stack-name shadecast \
  --query "Stacks[0].Outputs[?OutputKey=='WebBucketName'].OutputValue" --output text)/
```

Set `SHADECAST_BACKEND=dynamodb` in the function environment (the SAM template does this)
and the handlers switch storage. Tear down with `sam delete --stack-name shadecast`.

## Layout

```
shadecast/wbgt.py          WBGT estimation + ACGIH thresholds   (stdlib only)
shadecast/schedule.py      the shift optimiser                   (stdlib only)
shadecast/report.py        shift sheet, Hindi SMS, JSON          (stdlib only)
shadecast/forecast.py      NOAA GFS from S3 + offline profiles
shadecast/store.py         storage: local JSON or DynamoDB
shadecast/policy.py        Cedar crew clearance
shadecast/policies/        the heat-safety policy, as readable Cedar
aws/lambda_planner.py   EventBridge + API Gateway handlers
aws/lambda_ingest.py    GRIB ingest
aws/template.yaml       the whole stack
events/                 sample events for sam local invoke
scripts/run_pipeline.py the whole pipeline locally, no account
web/index.html          the supervisor screen
web/architecture.html   the architecture figure
web/optimiser.js        the browser optimiser, pinned to the Python one
tests/                  325 tests, mutation-checked
SECURITY.md             threat model, and what this build does not do
```

## Data and licences

NOAA GFS via the Registry of Open Data on AWS, CC0 (`s3://noaa-gfs-bdp-pds`). NOAA requests
attribution and does not endorse this project. Demo days are climatologically representative
profiles for Bhopal, generated in `shadecast/forecast.py` — they are **not measurements**, and
the UI says so.
