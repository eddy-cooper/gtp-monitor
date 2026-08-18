# DOMAIN.md — what the plant does and why the numbers matter

*(This public copy describes a fictional site, "Fairview Road", running on
synthetic data. The engineering problem is real; the numbers are not.)*

## The plant in one paragraph

A groundwater treatment plant (GTP) pumps contaminated groundwater from a
ring of extraction wells (EW01–EW12), treats it in batches with a
four-reagent chemical process, and discharges the treated water to sewer
under a trade-waste agreement. Two flowmeters bracket the process:
**FIT0101** totals the raw water coming in from the wells, and **FIT0501**
totals the treated water going out to sewer. Water sits in four buffer
tanks (T02, T31, T32, T5) between the two.

## The water balance

Over a month, what came in must equal what went out, adjusted for what
changed in the tanks and what the process itself added (mains top-up water,
chemical dosing volume, occasional tanker deliveries). When the books
don't balance, the leftover — the **unaccounted volume** — is evidence
that a meter is misreading.

The tool computes, per period:

```
under = (end tanks − expected end tanks) − mains used − chemicals added − other adjustments
under % = under ÷ total out
```

A small, steady under-reading is normal metering noise. A **growing**
under-reading is the signature of a wearing inflow meter (impeller wear
and air entrainment both read low) — and that matters because the metered
inflow volume is what feeds regulatory reporting, so accuracy has to be
defensible.

## The drift signal

The monthly under-reading percentage is the tool's headline number:

- above **0.5 %** → WATCH: keep an eye on it, schedule a meter check
- above **1.0 %** → ACTION: book a meter service/verification
- more than **2× the recent average** → ACCELERATING: the wear is speeding up
- strongly negative → INVESTIGATE: almost certainly a data error, not physics

The demo dataset tells exactly this story: eight quiet months, then a
drift that crosses the watch line and accelerates — the state the
dashboard's hero screenshot shows.

## Why data entry is deliberately manual

The plant's control system sits behind a VPN with no data export. The
operator reads totals off the SCADA screen daily and types them in. That
is why the tool leans so hard on validation (13 rules at entry time) and
on honesty about estimates: when the PLC freezes and a day's total has to
be back-filled, it is stored flagged as estimated, with a reason, and
every balance that includes it says so.
