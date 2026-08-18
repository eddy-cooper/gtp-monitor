# CLAUDE.md — GTP Monitoring Tool

This project was built by a first-time programmer pairing with Claude Code,
replacing a manual Excel workbook that monitors a groundwater treatment
plant. This file is the standing brief the AI works from; the interesting
part is the guardrails.

## What this is

A local tool that stores daily water volumes, calculates a water balance,
validates entries, and alerts when the plant's flowmeters drift out of
agreement. Single user, runs locally. This public copy runs entirely on a
synthetic demo dataset — see `tools/make_demo_data.py`.

## Stack

Python 3.11+ (stdlib first), SQLite, `openpyxl`/`pandas`/`matplotlib`,
`typer` CLI, `pytest`. Flask + vendored Chart.js for the local read-only
dashboard. Nothing else without an explicit decision.

## Non-negotiables

- **Units are kL throughout.** Never mix in litres or m³ without an explicit conversion at the boundary.
- **Never invent or interpolate a reading.** Missing data stays missing and is flagged. Estimated values are stored with `is_estimated = 1` and a reason.
- **The source workbook is read-only.** The importer never writes to it.
- Every calculation that produces a balance result stores its inputs alongside the result, so any figure can be reproduced later.
- Dates are ISO `YYYY-MM-DD`; floats are volumes in kL, rounded to 2 dp for display only, never for storage.
- Config (thresholds, chemical parameters, batch volume) lives in `config.toml`, never hardcoded.

## How we work

Plan before code; test-first for balance and validation logic; commit at
phase boundaries; one clear function over a class hierarchy. Decisions and
gotchas get written down the day they happen, not remembered.
