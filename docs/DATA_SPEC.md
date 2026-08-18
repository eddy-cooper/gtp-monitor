# DATA_SPEC.md — schema, balance maths, validation rules

*(Public copy. All example figures come from the synthetic demo dataset —
regenerate them any time with `python tools/make_demo_data.py`.)*

## 1. Schema (SQLite, `data/gtp.db`)

Dates are ISO `YYYY-MM-DD` TEXT; every REAL is a volume in kL, stored
unrounded (rounding is display-only).

| Table | One row per | Key columns |
|---|---|---|
| `daily_reading` | calendar day | `fit0101_reading`, `fit0101_error`, `fit0501`, `op_fraction`, `bore_status`, `comment`, `is_estimated` + `estimate_reason`, `override_reason` |
| `balance_snapshot` | manual tank stocktake | `taken_at` (ISO datetime), `t02 t31 t32 t5 f4s`, `mains_meter` (cumulative), `other_adjustments` + note fields |
| `balance_result` | computed period | totals **plus every input used** (`start/end_volume`, `mains_used`, `chemicals_used`, `batch_volume_kl`, `chem_litres_per_batch`, `computed_at`, `contains_estimates`) so any figure is reproducible later |
| `config_history` | config era | chemical factors + batch volume in force from `effective_from`, written at import/compute time |
| `service_event` | maintenance tick | date, item (+ running-hours columns for hour-based servicing) |
| `legacy_balance_note` | workbook reference row | non-reproducible hand figures kept for cross-checking, never used in maths |

## 2. Balance calculation (`balance.py`)

For a period bounded by two snapshots:

```
total_in   = Σ (fit0101_reading − fit0101_error)          # per day
total_out  = Σ fit0501
start/end  = t02 + t31 + t32 + t5 + f4s                    # at each snapshot
discrepancy = end_volume − (start_volume + total_in − total_out)

chem_litres_per_batch = base × (acid + ferrous + h2o2 + caustic)
chemicals_used = (total_out ÷ batch_volume_kl) × chem_litres_per_batch ÷ 1000

under_kl  = discrepancy − mains_used − chemicals_used − other_adjustments
under_pct = under_kl ÷ total_out          (None when total_out = 0)
```

Config values are resolved from `config_history` as of the period end —
recomputing an old month always uses the factors that were in force then,
so past results never shift when `config.toml` is re-estimated.

Worked example (demo October 2025, from `demo/expected_figures.json`):
the pipeline reproduces every period's `total_in`, `total_out`,
`mains_used`, `chemicals_used`, `under_kl`, and `under_pct` to within
float precision — `tests/test_demo_data.py::test_balances_match_expected_figures`
asserts exactly that.

## 3. Validation rules (`validate.py`)

WARN = show and confirm; BLOCK = refuse unless an override reason is
recorded (stored on the row, permanently visible in `gtp check`).

| Rule | Level | Fires when |
|---|---|---|
| V1 | BLOCK | negative daily flow |
| V2 | BLOCK | daily flow above `max_daily_kl` (plant physical limit) |
| V3 | WARN | daily flow above the typical band while below the limit |
| V4 | WARN | daily flow below the typical band while `op_fraction` = 1.0 |
| V5 | WARN | flow recorded while marked not operating (or zero flow while fully operating) |
| V6 | WARN | identical FIT0101/FIT0501 pairs N days running — possible PLC freeze |
| V7 | WARN | date gap since the previous entry |
| V8 | BLOCK | mains meter lower than the previous snapshot (it's cumulative) |
| V9 | BLOCK | mains meter jumped more than `mains_increase_factor` × the median recent interval |
| V10 | BLOCK | negative tank level (capacity upper bound reserved for when capacities are confirmed) |
| V11 | BLOCK | `is_estimated` set without a reason |
| V12 | WARN | \|in − out\| beyond `daily_imbalance_kl` in a single day |
| V13 | BLOCK | date in the future |

## 4. Demo reference figures

The generator writes `demo/expected_figures.json` alongside the workbook:
per-period totals and under-reading figures computed by independent
arithmetic. The demo story: baseline months at 0.12–0.24 %, an offline
February (no percentage — the chart shows a genuine gap), estimated
PLC-freeze days in May and August (hollow chart markers), the watch
threshold crossed in July (0.52 %), and a final accelerating partial
August at 0.93 % — WATCH, not yet ACTION.

## 5. Alerts and trend

`gtp status` / the dashboard classify the latest period: OK / WATCH
(> 0.5 %) / ACTION (> 1.0 %) / INVESTIGATE (< −0.5 %) / NO_DATA, plus an
ACCELERATING flag when the latest percentage exceeds
`accelerating_factor` × the mean of the previous `accelerating_window`
periods. Alert-worthy states append one line to `out/alerts.log`.
The trend chart draws one point per computed period, hollow markers for
periods containing estimates, and dashed watch/action threshold lines.
