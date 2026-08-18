# IMPORTER_NOTES.md — reading a real-world operational spreadsheet

The importer (`src/gtp/importer.py`) exists because years of operational
history lived in a hand-maintained Excel workbook — one sheet per month,
laid out for humans, not for parsers. The demo workbook
(`demo/Demo Operational Record.xlsx`) reproduces every quirk the importer
was built to survive, so the code you're reading is exercised for real:

- **The daily block starts on a different row on every sheet.** The
  importer scans column B for the first day-1 cell (`find_day1_row`)
  instead of hardcoding offsets — the demo sheets vary the start row
  between 8 and 14 on purpose.
- **Some numbers are formula strings, not numbers.** The monthly mains
  usage is stored as text like `=1655.1-1650.0+(0/30*5.3)`. Trusting
  Excel's cached value would mean trusting a file nobody recalculated, so
  the importer parses and evaluates the arithmetic itself with a small
  AST-based evaluator (`safe_eval_arithmetic`) — no `eval()`.
- **Timestamps are free text.** Snapshot times arrive as `"30 Jun @
  0915am"`, `"31 Oct @ midnight"` — including a recurring `"midnght"`
  typo. Parsing is best-effort: on failure the raw string is kept, the
  time defaults to midnight, and the row is flagged rather than guessed.
- **Adjacent sheets share a boundary snapshot.** Each month's closing
  stocktake is the next month's opening one; the importer upserts on the
  parsed timestamp so both sheets land on a single database row.
- **Some cells are simply wrong in interesting ways.** Anything the
  importer can't interpret (the demo has an `"OFFLINE"` note where a
  service date should be) becomes a warning in the import summary — never
  a silent skip, never an invented value.
- **PLC-freeze days are flagged, not fixed.** A day whose comment records
  a PLC freeze is imported as-is but marked `is_estimated = 1` with the
  reason preserved, and every balance covering it reports
  `contains_estimates`.

Re-running `gtp import` is idempotent: daily rows and snapshots upsert on
their natural keys, and the fully-derived tables (`service_event`,
`legacy_balance_note`) are rebuilt from scratch each run.
