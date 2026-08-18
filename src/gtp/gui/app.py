"""Tkinter desktop GUI for the GTP tool.

Calls the same functions the CLI uses (report.py, validate.py,
balance.py, chart.py, store.py) directly -- no reimplemented business
logic here, only widgets and glue. One App holding widget references
and the shared DB connection; each tab is built by its own method.
"""

import calendar
import sqlite3
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from gtp.balance import compute_period_balance
from gtp.chart import build_trend_chart
from gtp.db import DEFAULT_DB_PATH, init_db
from gtp.gui.format import (
    BALANCE_COLUMNS,
    READINGS_COLUMNS,
    SNAPSHOTS_COLUMNS,
    format_balance_row,
    format_finding,
    format_reading_row,
    format_snapshot_row,
    format_status_lines,
    parse_kl,
    parse_optional_kl,
)
from gtp.models import BalanceSnapshot, DailyReading
from gtp.report import build_status_view, write_alert_log
from gtp.store import save_balance_snapshot, save_daily_reading
from gtp.validate import (
    BLOCK,
    load_validation_config_from_toml,
    recent_balance_snapshots,
    recent_daily_readings,
    validate_balance_snapshot,
    validate_daily_reading,
)

# The GUI renders its own fitted-to-window copy of the chart, leaving
# out/trend.png as the CLI's full-resolution artifact.
GUI_CHART_PATH = Path("out/trend_gui.png")


def _today() -> str:
    return datetime.now().date().isoformat()


class App:
    def __init__(self, root: tk.Tk, conn: sqlite3.Connection):
        self.root = root
        self.conn = conn
        self.chart_image: tk.PhotoImage | None = None  # kept alive to avoid GC

        root.title("GTP Monitoring Tool")
        root.geometry("1100x740")

        self._chart_dpi: int | None = None
        self._chart_resize_job: str | None = None

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        self._build_status_tab(notebook)
        self._build_add_reading_tab(notebook)
        self._build_snapshot_tab(notebook)
        self._build_data_tab(notebook)
        self._build_chart_tab(notebook)

        self.refresh_status()

    # --- Status tab ---------------------------------------------------

    def _build_status_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Status")

        self.status_text = tk.Text(frame, height=22, wrap="word", state="disabled")
        self.status_text.pack(fill="both", expand=True)

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="Refresh", command=self.refresh_status).pack(side="left")
        ttk.Button(
            button_row, text="Recompute balance…", command=self.open_recompute_dialog
        ).pack(side="left", padx=(8, 0))

    def refresh_status(self) -> None:
        view = build_status_view(self.conn, today=_today())
        lines = format_status_lines(view)

        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.insert("1.0", "\n".join(lines))
        self.status_text.configure(state="disabled")

        if view.has_balance:
            write_alert_log(view, logged_at=datetime.now().isoformat(timespec="seconds"))

    def open_recompute_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Recompute balance")
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Month (YYYY-MM):").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        month_var = tk.StringVar(value=_today()[:7])
        ttk.Entry(dialog, textvariable=month_var, width=15).grid(row=0, column=1, padx=8, pady=8)

        def do_compute() -> None:
            month = month_var.get().strip()
            try:
                year, mon = (int(x) for x in month.split("-"))
            except ValueError:
                messagebox.showerror("Invalid month", f"Month must be YYYY-MM, got {month!r}.", parent=dialog)
                return
            start_date = f"{year:04d}-{mon:02d}-01"
            end_date = f"{year:04d}-{mon:02d}-{calendar.monthrange(year, mon)[1]:02d}"
            try:
                compute_period_balance(self.conn, start_date, end_date)
            except ValueError as e:
                messagebox.showerror("Cannot compute balance", str(e), parent=dialog)
                return
            dialog.destroy()
            self.refresh_status()
            self.refresh_data()
            self.refresh_chart()

        ttk.Button(dialog, text="Compute", command=do_compute).grid(row=1, column=0, columnspan=2, pady=(0, 8))

    # --- Add reading tab --------------------------------------------------

    def _build_add_reading_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Add reading")

        v = self.add_vars = {}

        def field(row: int, key: str, label: str, default: str = "") -> None:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=32).grid(row=row, column=1, sticky="w", pady=4)
            v[key] = var

        field(0, "date", "Date (YYYY-MM-DD):", _today())
        field(1, "fit0101_reading", "FIT0101 reading (kL):")
        field(2, "fit0101_error", "FIT0101 error (kL):", "0")
        field(3, "fit0501", "FIT0501 (kL):")
        field(4, "op_fraction", "Op fraction (0-1):")
        field(5, "bore_status", "Bore status:")
        field(6, "comment", "Comment:")

        v["estimated"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Estimated (back-filled, not measured)", variable=v["estimated"]
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=4)
        field(8, "reason", "Reason (required if estimated):")

        ttk.Button(frame, text="Save reading", command=self._save_reading).grid(
            row=9, column=0, columnspan=2, pady=12
        )

    def _save_reading(self) -> None:
        v = self.add_vars
        date = v["date"].get().strip()
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Invalid date", f"Date must be YYYY-MM-DD, got {date!r}.", parent=self.root)
            return

        try:
            fit0101_reading = parse_kl(v["fit0101_reading"].get(), "FIT0101 reading")
            fit0101_error = parse_kl(v["fit0101_error"].get() or "0", "FIT0101 error")
            fit0501 = parse_kl(v["fit0501"].get(), "FIT0501")
            op_fraction = parse_kl(v["op_fraction"].get(), "Op fraction")
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self.root)
            return

        estimated = v["estimated"].get()
        reason = v["reason"].get().strip() or None
        if estimated and not reason:
            messagebox.showerror(
                "Missing reason", 'A reason is required when "Estimated" is checked.', parent=self.root
            )
            return

        existing = self.conn.execute("SELECT 1 FROM daily_reading WHERE date = ?", (date,)).fetchone()
        if existing:
            messagebox.showerror(
                "Duplicate date", f"A reading for {date} already exists. Refusing to overwrite.", parent=self.root
            )
            return

        candidate = DailyReading(
            date=date, fit0101_reading=fit0101_reading, fit0101_error=fit0101_error,
            fit0501=fit0501, op_fraction=op_fraction,
            bore_status=v["bore_status"].get().strip() or None,
            comment=v["comment"].get().strip() or None,
            is_estimated=int(estimated), estimate_reason=reason,
        )
        config = load_validation_config_from_toml()
        recent = recent_daily_readings(self.conn, date)
        findings = validate_daily_reading(candidate, recent, config, today=_today())

        override_reason = self._resolve_findings(findings, BLOCK)
        if override_reason is False:
            return

        save_daily_reading(self.conn, candidate, override_reason)
        messagebox.showinfo("Saved", f"Added reading for {date}.", parent=self.root)
        for key in ("fit0101_reading", "fit0501", "op_fraction", "bore_status", "comment", "reason"):
            v[key].set("")
        v["fit0101_error"].set("0")
        v["estimated"].set(False)
        v["date"].set(_today())
        self.refresh_status()
        self.refresh_data()

    # --- Snapshot tab -------------------------------------------------------

    def _build_snapshot_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Snapshot")

        v = self.snapshot_vars = {}

        def field(row: int, key: str, label: str, default: str = "") -> None:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=32).grid(row=row, column=1, sticky="w", pady=4)
            v[key] = var

        field(0, "taken_at", "Taken at (ISO datetime):", datetime.now().isoformat(timespec="seconds"))
        field(1, "t02", "T02 (kL):")
        field(2, "t31", "T31 (kL):")
        field(3, "t32", "T32 (kL):")
        field(4, "t5", "T5 (kL):")
        field(5, "mains_meter", "Mains meter (kL):")
        field(6, "f4s", "F4s (kL):", "0")
        field(7, "other_adjustments", "Other adjustments (kL):", "0")
        field(8, "other_note", "Other note:")
        field(9, "note", "Note:")

        ttk.Button(frame, text="Save snapshot", command=self._save_snapshot).grid(
            row=10, column=0, columnspan=2, pady=12
        )

    def _save_snapshot(self) -> None:
        v = self.snapshot_vars
        taken_at = v["taken_at"].get().strip()
        try:
            datetime.fromisoformat(taken_at)
        except ValueError:
            messagebox.showerror(
                "Invalid datetime", f"Taken-at must be an ISO datetime, got {taken_at!r}.", parent=self.root
            )
            return

        try:
            t02 = parse_kl(v["t02"].get(), "T02")
            t31 = parse_kl(v["t31"].get(), "T31")
            t32 = parse_kl(v["t32"].get(), "T32")
            t5 = parse_kl(v["t5"].get(), "T5")
            mains_meter = parse_kl(v["mains_meter"].get(), "Mains meter")
            f4s = parse_kl(v["f4s"].get() or "0", "F4s")
            other_adjustments = parse_kl(v["other_adjustments"].get() or "0", "Other adjustments")
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self.root)
            return

        existing = self.conn.execute(
            "SELECT 1 FROM balance_snapshot WHERE taken_at = ?", (taken_at,)
        ).fetchone()
        if existing:
            messagebox.showerror(
                "Duplicate snapshot", f"A snapshot at {taken_at} already exists. Refusing to overwrite.",
                parent=self.root,
            )
            return

        candidate = BalanceSnapshot(
            taken_at=taken_at, t02=t02, t31=t31, t32=t32, t5=t5, f4s=f4s,
            mains_meter=mains_meter, other_adjustments=other_adjustments,
            other_note=v["other_note"].get().strip() or None,
            note=v["note"].get().strip() or None,
        )
        config = load_validation_config_from_toml()
        recent = recent_balance_snapshots(self.conn, taken_at, limit=config.mains_median_window + 1)
        findings = validate_balance_snapshot(candidate, recent, config, today=_today())

        override_reason = self._resolve_findings(findings, BLOCK)
        if override_reason is False:
            return

        save_balance_snapshot(self.conn, candidate, override_reason)
        messagebox.showinfo("Saved", f"Recorded snapshot at {taken_at}.", parent=self.root)
        for key in ("t02", "t31", "t32", "t5", "mains_meter", "other_note", "note"):
            v[key].set("")
        v["f4s"].set("0")
        v["other_adjustments"].set("0")
        v["taken_at"].set(datetime.now().isoformat(timespec="seconds"))
        self.refresh_status()
        self.refresh_data()

    # --- Data tab (read-only spreadsheet view) ----------------------------

    def _build_data_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Data")

        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.data_choice = tk.StringVar(value="Daily readings")
        picker = ttk.Combobox(
            top, textvariable=self.data_choice, state="readonly", width=18,
            values=("Daily readings", "Snapshots", "Balance results"),
        )
        picker.pack(side="left", padx=(6, 0))
        picker.bind("<<ComboboxSelected>>", lambda _e: self.refresh_data())
        ttk.Button(top, text="Refresh", command=self.refresh_data).pack(side="left", padx=(8, 0))

        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.data_table = ttk.Treeview(table_frame, show="headings")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.data_table.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.data_table.xview)
        self.data_table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.data_table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.refresh_data()

    def _fetch_dataset(self, name: str) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
        """Returns (column labels, display-formatted rows) for the picked
        dataset. Readings skip placeholder rows (both flow columns NULL,
        "not yet entered") -- the same criterion run_check uses.
        """
        if name == "Snapshots":
            rows = self.conn.execute(
                "SELECT taken_at, t02, t31, t32, t5, f4s, mains_meter, "
                "other_adjustments, note FROM balance_snapshot ORDER BY taken_at DESC"
            ).fetchall()
            return SNAPSHOTS_COLUMNS, [format_snapshot_row(r) for r in rows]
        if name == "Balance results":
            rows = self.conn.execute(
                "SELECT period_start, period_end, total_in, total_out, mains_used, "
                "chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates "
                "FROM balance_result ORDER BY period_start"
            ).fetchall()
            return BALANCE_COLUMNS, [format_balance_row(r) for r in rows]
        rows = self.conn.execute(
            "SELECT date, fit0101_reading, fit0101_error, fit0501, op_fraction, "
            "is_estimated, estimate_reason, override_reason, comment FROM daily_reading "
            "WHERE fit0101_reading IS NOT NULL OR fit0501 IS NOT NULL ORDER BY date DESC"
        ).fetchall()
        return READINGS_COLUMNS, [format_reading_row(r) for r in rows]

    def refresh_data(self) -> None:
        columns, rows = self._fetch_dataset(self.data_choice.get())
        table = self.data_table
        table.delete(*table.get_children())
        ids = [f"c{i}" for i in range(len(columns))]
        table.configure(columns=ids)
        for i, (cid, label) in enumerate(zip(ids, columns)):
            table.heading(cid, text=label)
            table.column(cid, width=150 if i == 0 else 110, anchor="w", stretch=True)
        for row in rows:
            table.insert("", "end", values=row)

    # --- Chart tab ------------------------------------------------------

    def _build_chart_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="Chart")

        # Button packed first (bottom) so the expanding label can't push
        # it out of the window.
        ttk.Button(frame, text="Refresh chart", command=self.refresh_chart).pack(
            side="bottom", pady=(8, 0)
        )
        self.chart_label = ttk.Label(frame, anchor="center")
        self.chart_label.pack(fill="both", expand=True)

        # Re-render on resize (and on first display -- mapping the tab
        # fires <Configure> too). Debounced: a drag-resize fires a burst
        # of events; only render once things settle.
        frame.bind("<Configure>", self._on_chart_resize)

    def _on_chart_resize(self, _event: object) -> None:
        if self._chart_resize_job is not None:
            self.root.after_cancel(self._chart_resize_job)
        self._chart_resize_job = self.root.after(250, lambda: self.refresh_chart(force=False))

    def refresh_chart(self, force: bool = True) -> None:
        self._chart_resize_job = None
        width = self.chart_label.winfo_width()
        height = self.chart_label.winfo_height()
        if width < 50 or height < 50:
            return  # not laid out yet (unmapped widgets report 1x1)
        # The figure is 10x5.5 inches; pick the dpi that fits the label.
        dpi = max(60, int(min(width / 10, height / 5.5)))
        if not force and dpi == self._chart_dpi:
            return  # same size as the last render -- nothing to do
        self._chart_dpi = dpi
        build_trend_chart(self.conn, GUI_CHART_PATH, dpi=dpi)
        self.chart_image = tk.PhotoImage(file=str(GUI_CHART_PATH))
        self.chart_label.configure(image=self.chart_image)

    # --- shared validation-flow helper (used by later tabs) --------------

    def _resolve_findings(self, findings, block_level: str) -> str | None | bool:
        """Mirrors the CLI's WARN/BLOCK/override flow: WARNs are shown and
        can be dismissed; BLOCKs require a typed reason to proceed.
        Returns the override reason to save (None if not needed), or
        False if the caller should not save.
        """
        blocks = [f for f in findings if f.level == block_level]
        warns = [f for f in findings if f.level != block_level]

        if blocks:
            lines = ["This entry has BLOCK-level issues:", ""]
            lines += [format_finding(f) for f in blocks]
            if warns:
                lines += ["", "WARN-level issues:"] + [format_finding(f) for f in warns]
            lines += ["", "Enter a reason to save anyway, or Cancel to fix the values."]
            reason = simpledialog.askstring("Validation BLOCK", "\n".join(lines), parent=self.root)
            if not reason:
                return False
            return reason

        if warns:
            lines = ["This entry has WARN-level issues:", ""]
            lines += [format_finding(f) for f in warns]
            lines += ["", "Save anyway?"]
            if not messagebox.askyesno("Validation warning", "\n".join(lines), parent=self.root):
                return False

        return None


def main() -> None:
    conn = init_db(DEFAULT_DB_PATH)
    root = tk.Tk()
    App(root, conn)
    root.mainloop()


if __name__ == "__main__":
    main()
