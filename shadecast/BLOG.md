# The heat warning that doesn't fire on the dangerous day

*Written for AWS Builder Center · Environmental Hacks, Bharat Builds Tour 2026*

> Fill in the bracketed bits before publishing — the specifics are what make a build log
> worth reading, and they are the only part I cannot write for you.

---

We started this weekend intending to build a heat dashboard. We are glad we did not, and
the reason is a number that took about forty minutes to find and completely changed the
project.

## The formula was wrong

Our first WBGT implementation used the Bureau of Meteorology approximation, which is the
one you land on first when you search for how to compute wet bulb globe temperature from
ordinary weather data:

```
WBGT = 0.567·Ta + 0.393·e + 3.94
```

It ran. It produced numbers. The numbers said that on a May day in Bhopal, all work must
stop by eight in the morning.

That is obviously false. People work at eight in the morning in Bhopal in May. When your
model disagrees with the street outside your window, the model is wrong.

The problem is that the BoM formula carries no radiation term and is not the shaded WBGT we
assumed it was, so the solar correction we had bolted on top was double-counting. We were
reading about seven degrees too hot. Our fix was to stop looking for one formula and
implement the actual definition:

```
WBGT = 0.7·Tnwb + 0.2·Tg + 0.1·Ta
```

Wet bulb from [Stull (2011)](https://journals.ametsoc.org/view/journals/apme/50/11/jamc-d-11-0143.1.xml),
accurate to about a third of a degree. Black globe from the linearised solution in the
[NWS Tulsa WBGT paper](https://www.weather.gov/media/tsa/pdf/WBGTpaper2.pdf), where wind
and solar radiation genuinely enter the physics. Then the standard weighting.

Look at those weights. **Air temperature is the least important term.** Humidity carries
seven times the weight.

## Which is the whole problem

India's Heat Action Plans trigger on air temperature. A Centre for Policy Research review
of thirty-seven of them found most never establish a local threshold at all.

Once the model was right, we ran two Bhopal days through it:

| | Peak air | Peak WBGT | Work hours lost, 09–18 |
|---|---|---|---|
| Mid May, dry | 42.0 °C | 30.0 °C | 6.0 of 9 |
| Late July, humid | 31.5 °C | 30.1 °C | 6.0 of 9 |

The July day is **ten and a half degrees cooler** and puts the same strain on a working
body. No heat warning fires anywhere in India on that day. The humidity does the damage,
and humidity is the variable the warning system does not look at.

That became the project.

## The product decision that mattered

Our first scheduler output a red/amber/green warning. We killed it, because on a Bhopal May
afternoon almost every hour is red, and a tool that says "stop work" to a crew paid by the
day gets switched off by lunchtime on the day you install it. The workers end up no safer
and you have burned your one chance with that site.

So Shadecast never outputs a bare stop. It outputs a plan: the same work, moved to the hours
that cost the least. The research says [about 30% of heat-lost heavy labour is recoverable
by shifting hours](https://www.downtoearth.org.in/news/climate-change/labour-losses-due-to-rising-heat-india-among-the-most-affected-finds-study-80787).
That recovery is the product. Safety and wages point the same direction, which is the only
reason a contractor will ever adopt it.

For a crew of twelve laying brick: the normal nine-to-six gives 2.8 productive hours once
the standard's rest ratios are applied. Moving to 05:00–11:00 plus 17:00–20:00 gives 6.2,
and removes five hours of severe exposure. Same work, same pay, different clock.

## Where AWS actually fits

We picked this problem partly because of where the data lives. NOAA's Global Forecast System
is published to `s3://noaa-gfs-bdp-pds` on the Registry of Open Data, CC0, new cycle every
six hours, and readable with no AWS account:

```bash
aws s3 ls --no-sign-request s3://noaa-gfs-bdp-pds/
```

Two things we would tell anyone building on it.

**Use the `.idx` files.** A full GFS file for one forecast hour is about 500 MB and we need
five fields. Every object has a sibling `.idx` listing each record's byte offset, so you read
the index, compute byte ranges, and issue ranged GETs. 500 MB becomes about 2 MB. That is the
difference between fitting in a Lambda timeout and not.

**Split your functions by dependency, not by feature.** Reading GRIB2 needs ecCodes, a native
library, and building it into a Lambda layer by hand is how you lose four hours. Computing
heat strain needs `math`. So ingest carries the GRIB stack, and the planner — the function
that must never fail — has no hard dependencies at all.

**Make the backend a flag, not a fork.** We expected to write the project twice: once
locally, once for AWS. Instead the handlers ask a tiny `store.py` for a table, and one
environment variable decides whether that table is a JSON file or DynamoDB. The function we
demo on a laptop is byte-for-byte the function that deploys. [Say here which path you ran
for the demo and why — the honest version of that choice is more interesting than either
option on its own.]

That refactor also caught a bug that would have survived to production: the plans table is
keyed on date, and we were writing the *human* label — "Fri 15 May 2026" — into the key
while the API queried for "2026-05-15". Every stored plan was unfetchable. It only surfaced
because running the API handler locally is one command.

## The bug that was not a bug

Our optimiser worked. It recommended 05:00–11:00 plus 17:00–20:00 for a Bhopal May day:
6.2 productive hours, zero hours of severe heat exposure, against 2.8 productive hours
for the normal nine-to-six. We were pleased with it.

Then one of us added up the span. Five in the morning to eight at night. **Fifteen hours
of day for nine hours of work.** These workers commute — often an hour each way, often on
foot. The six-hour gap is too short to go home and worth less than the fare, so they
would spend it at the site, in the heat we had just told them to avoid. We had optimised
a safety metric into a worse day, and labelled it *wages protected*.

Every number in that output was correct. The objective function was wrong.

So the plan is now constrained to an eleven-hour span by default, which yields
05:00–14:00: 5.5 productive hours and three severe-risk hours. Worse on exposure, much
better on the worker's day. Neither plan dominates, so we surface both and make the
deciding input explicit — `workers_commute` — because whether the split shift is humane
depends on where the crew sleeps, which is a fact no weather model contains.

If you are building for people whose time you are not spending, check what your optimiser
is quietly taking from them.

## The policy layer we nearly faked

Cedar silently returned `NoDecision` for every request for about twenty minutes, and we
did not notice, because our Python fallback was generating plausible-looking denial
reasons. The demo looked perfect and the policy engine was doing nothing at all.

The cause: Cedar has no floating-point type, and we had put `wbgt: 23.4` into an entity
attribute. Entity deserialization failed, every decision came back `NoDecision`, and
because we were only reading the denials it looked exactly like a working policy layer.

Now a `NoDecision` raises. **If your authorization layer can fail open and look identical
to success, you do not have an authorization layer.**

## What it still gets wrong

Real WBGT needs a black globe thermometer on the site. We estimate it from a 28 km forecast
grid, so we resolve nothing about asphalt versus tree cover, and our use of the psychrometric
wet bulb in place of the natural wet bulb runs slightly low in strong sun. A ₹2,000 globe
thermometer on one pilot site would calibrate the whole model, and that is the next thing
we would build.

We say all of this on the page itself. A tool that tells a supervisor when to send people
home has to be honest about its own error bars, or it does not deserve to be followed.

---

**Built by [names], [college], over four days.** Code: [repo link]. Live: [URL].
Track: Heat and Water.

Forecast data: NOAA GFS via the Registry of Open Data on AWS, CC0. NOAA does not endorse
this project.
