# Shadecast — two-minute script

**Read by Kunal Jha, team Cryptix.** Roughly 300 spoken words, about 2:00 at a calm pace.
Record the screen first, then the voice over it. Do not try to narrate live while clicking.

Every claim below is one a judge can check. Nothing here needs embellishing — the GFS
pull is real, Cedar really runs, the handlers are real Lambda code. If you are asked
"is it deployed?", the answer is: *"No. The rules offer two ways to build and score them
the same, and we chose the one where the whole thing runs without an account."*

---

## 0:00 – 0:18 · The number

**On screen:** your Bhopal worksite footage. If you have none, the terminal running
`python3 run_demo.py --compare`, large font.

> Heat costs Indian workers about seventy-eight billion dollars a year in lost wages.
> Two percent of GDP. A construction worker loses around twenty-four days of work to it —
> close to a tenth of everything they earn.
>
> About a third of that is recoverable. Not with air conditioning. By moving the work to a
> different hour of the day.

## 0:18 – 0:42 · The bug in the warning system

**On screen:** the `--compare` output, both days side by side. Let the two numbers sit.

> India's answer is Heat Action Plans. A review of thirty-seven of them found most never
> set a local threshold, and all of them trigger on air temperature.
>
> Here are two days in Bhopal. May, forty-two degrees. July, thirty-one and a half.
> Eleven degrees cooler — and the same strain on a working body, because it is eighty
> percent humidity.
>
> On the more dangerous day, no warning fires anywhere in India. Temperature is the one
> thing they measure, and in the international standard it carries the smallest weight of
> the three.

## 0:42 – 1:05 · What it shows you

**On screen:** the Shadecast page. Hover slowly across the chart once.

> This is Shadecast. The background is the safety standard itself — five bands, each one
> a work-rest ratio from ISO 7243. The line is wet bulb globe temperature through the day.
>
> You can see the morning climb out of "work continuously" and into "fifteen minutes in
> sixty". And the shift we recommend is the window where the line stays low.

## 1:05 – 1:35 · The mistake we made

**On screen:** drag the span slider from 11 to 15. Let the split happen. Then drag it back.

> Our first version put this crew on five to eleven and five to eight. Six point two
> productive hours, zero severe exposure. We were pleased with it for about a day.
>
> Then we added up the span. On site from five in the morning until eight at night.
> Fifteen hours of day for nine hours of work. These workers commute, often an hour each
> way, and the six-hour gap is too short to go home — so they would spend it sitting at
> the site, in the heat we had just told them to avoid. And we were calling it
> wages protected.
>
> So the optimiser scores the worker's day now, not just their exposure. Five to two,
> straight through. It costs three quarters of an hour of work, and it is the right trade.
> Neither plan wins outright, so we show both — because which one is right depends on
> where the crew sleeps, and no weather model knows that.

## 1:35 – 1:52 · Where AWS actually fits

**On screen:** `web/architecture.html`, then the terminal running
`python3 scripts/run_pipeline.py`.

> The forecast this runs on is NOAA's Global Forecast System, published as open data in
> S3 — no account, no key, a new cycle every six hours. It is the only place that model
> exists for free, which is why this project is on AWS rather than decorated with it. We
> read the GRIB index and pull two megabytes instead of five hundred.
>
> These are Lambda handlers, and you are watching them run on a laptop. Storage is one
> environment variable: JSON files here, DynamoDB when deployed.
>
> And who on the crew may work which hour is not in our code. It is a Cedar policy file —
> AWS's open-source policy language — that a safety officer can read and a labour
> inspector can audit. Four of these twelve are held back: one not yet acclimatised, one
> under eighteen, one over fifty-five, one with a heat-aggravated condition.

## 1:52 – 2:05 · The honest close

**On screen:** the Hindi SMS in the phone frame.

> Every worker gets this. In Hindi, by SMS, on a thousand-rupee phone. No app.
>
> One thing we cannot do: measure. Wet bulb globe temperature properly needs a black
> globe thermometer on the site. We estimate it from the forecast using published methods,
> and in strong sun our estimate runs slightly low. A two-thousand-rupee thermometer on
> one pilot site fixes that, and it is the next thing we would build.
>
> Bhopal has thousands of outdoor crews. The forecast is free. The standard is eighty
> years old. The only missing piece was somebody doing the arithmetic and sending the SMS.

**Last frame, three seconds:** Shadecast · Move the work, not the worker · team Cryptix ·
the repo link.

---

## If a judge asks

**"Is it deployed?"** No. The rules offer Build It and Ship It and score them the same.
We took Build It: the forecast comes from an open S3 bucket that needs no account, the
policy layer is Cedar running locally, and the SAM template is in the repo for whoever
deploys it. Everything in the demo is running, not mocked.

**"Did you validate the model?"** No, and we say so on the page. Every method is
published — Stull for wet bulb, the NWS linearised solution for black globe, ISO 7243 to
combine them — but we have never put a globe thermometer next to our own output. That is
the first thing a pilot would do.

**"Is that real weather?"** The page labels each day. Run `scripts/fetch_real_days.py` and
it pulls observed GFS and relabels them "observed"; until then they are generated profiles
and the page says so.

**"How do you know the browser maths matches the backend?"** 325 tests, and 270 of them
run the browser optimiser under Node against the Python one across every combination of
day, workload, acclimatisation, required hours and span limit. They have been
mutation-checked: break either implementation and the suite fails.
