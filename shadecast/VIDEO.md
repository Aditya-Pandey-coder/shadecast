# The three-minute video

> A tighter two-minute version, with the spoken lines written out, is in `SCRIPT.md`.

The judges wrote: *"There is no live demo, so the video is what the judges see."* Treat this
as the deliverable, not the afterthought. Budget four hours. Record the screen captures
first, write the voiceover against what you actually captured, then record audio separately
and lay it over. Do not try to narrate live while clicking.

**Three rules.** No slide that is just your logo. No "we used AWS services like S3 and
Lambda" list — show one diagram and name the data. Say the limitation out loud; judges
punish overclaiming far harder than they punish an honest gap.

---

## 0:00–0:22 · The number, then the face

**On screen:** a worksite at 1 PM. If you can shoot ten seconds of real footage in Bhopal —
a crew on Kolar Road, Hoshangabad Road, anywhere — shoot it on a phone. Real footage beats
stock, and a judge can tell within two seconds which one they are watching.

> Heat costs Indian workers about seventy-eight billion dollars a year in lost wages. Two
> percent of GDP. A construction worker loses twenty-four days of work to it — a tenth of
> everything they earn.
>
> About thirty percent of that is recoverable. Not with air conditioning. By moving the work
> to a different hour of the day.

## 0:22–0:52 · The bug nobody has fixed

**On screen:** the `--compare` output in a terminal, large font. Let the two numbers sit.

> India's answer is Heat Action Plans. A review of thirty-seven of them found most never
> set a local threshold, and all of them trigger on air temperature.
>
> Here are two days in Bhopal. A May day at forty-two degrees. A July day at thirty-one and
> a half. Eleven degrees cooler — and the same strain on a working body, because it is
> eighty-five percent humidity.
>
> On the more dangerous day, no warning fires anywhere in India. Temperature is the one
> thing they measure, and it is the least important of the three.

## 0:52–2:10 · The product, and the bug we found in it

**On screen:** the supervisor page. Click slowly, no narrating the UI:

1. Land on the May day. Let the ribbon render.
2. The two shift bars: dotted is the normal nine-to-six, solid is Shadecast's plan.
3. The stat row: hours rescued, severe hours removed, wages protected.
4. **Scroll to "There is a second plan".** Let both options sit on screen.
5. Flip "Where does the crew sleep?" from *They commute* to *On site*. The whole plan
   changes.
6. Scroll to "Who on the crew can work it" — 8 of 12, with the four reasons.
7. The Hindi message.

> A crew of twelve laying brick in Bhopal. The normal shift gives them two point eight
> hours of real work, because the standard forces a fifteen-minute-in-sixty rest ratio
> through the afternoon.
>
> Our first version moved them to five in the morning and five in the evening. Six point
> two productive hours, zero severe exposure. We were pleased with it for about a day.
>
> Then we worked out what we had actually recommended: on site from five AM to eight PM.
> Fifteen hours of day for nine hours of work. Most of these workers commute an hour each
> way, and the six-hour gap is too short to go home and costs more in fare than it is
> worth — so they would spend it sitting at the site, in the heat we just told them to
> avoid. And we were calling it wages protected.
>
> So the optimiser now scores the worker's day, not just their exposure. Five to two,
> straight through. That costs three quarters of an hour of work and it is the right
> trade. Neither plan wins outright, so we show both — because which one is right depends
> on where the crew sleeps, and no weather model knows that.
>
> And then the second question: who on this crew can actually work it. Four people cannot
> — one is new and not yet acclimatised, one is under eighteen, one is over fifty-five,
> one has a heat-aggravated condition. That is not in our code. It is a Cedar policy file
> a safety officer can read and a labour inspector can audit.
>
> Every worker gets this. In Hindi, by SMS, on a thousand-rupee phone. No app.

**The span bug is your strongest 30 seconds.** Judges have sat through a lot of demos
where nothing ever went wrong. A team that found the flaw in its own output, named it
plainly, and paid real productivity to fix it reads as a team that would be safe to
deploy something.

## 2:10–2:38 · Where AWS fits

**On screen:** `web/architecture.html` full-screen, then cut to the terminal running
`python3 scripts/run_pipeline.py`.

> The forecast model this runs on is NOAA's Global Forecast System. It is published as
> open data in S3 — no account, no key, free, new cycle every six hours. That is the only
> place it exists for free, which is why this project is on AWS rather than decorated
> with it.
>
> The ingest function reads the GRIB index and pulls two megabytes instead of five
> hundred. The planner computes wet bulb globe temperature, applies the ISO 7243
> work-rest standard, and sends the shift to the crew.
>
> These are Lambda handlers, and you are watching them run on a laptop with no AWS
> account. Storage is one environment variable: JSON files here, DynamoDB when deployed.
> The function we are demonstrating is the function that would deploy — there is no
> local version to drift out of sync.
>
> The planner carries no dependencies at all. Reading GRIB needs a native library.
> Computing heat strain needs arithmetic. We kept them apart so the function that
> matters has nothing in it that can break. The safety rules sit outside both, in Cedar,
> where someone who is not a programmer can read them.

**Why say the local part out loud:** the rules offer two ways to build and score them the
same. Running the real handlers locally is the Build It path, not a shortfall — but only
if you name it confidently instead of hoping nobody notices there is no URL.

## 2:38–3:00 · The honest close

> One thing this does not do: measure. Wet bulb globe temperature properly needs a black
> globe thermometer on the site. We estimate it from the forecast, using published methods,
> and in strong sun our estimate runs slightly low. A two-thousand-rupee globe thermometer
> on one pilot site fixes that, and it is the next thing we would build.
>
> Bhopal has thousands of outdoor crews. The forecast is free. The standard is eighty years
> old. The only missing piece was somebody doing the arithmetic and sending the SMS.

**Last frame:** the URL, the repo, the team. Three seconds. No music sting.

---

## Shot checklist

- [ ] Ten seconds of real Bhopal worksite footage, shot on a phone
- [ ] Terminal: `python3 run_demo.py --compare`, font size 18+
- [ ] Terminal: `python3 scripts/run_pipeline.py --offline` — four handlers, the two
      plans, and the Cedar clearance, all in one screen
- [ ] The commute/on-site toggle flipping the plan on camera
- [ ] `shadecast/policies/heat_safety.cedar` on screen for three seconds while you say
      "a safety officer can read this"
- [ ] Screen recording of the supervisor page at 1280×800, cursor movements slow
- [ ] Your phone receiving the Hindi SMS, filmed on a second phone
- [ ] `web/architecture.html` full-screen
- [ ] Audio recorded separately, in a room with soft furnishings
- [ ] Captions burned in — judges may watch muted
- [ ] Under 3:00. Check twice.
