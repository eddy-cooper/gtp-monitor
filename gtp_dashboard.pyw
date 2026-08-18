"""Double-click launcher for the GTP web dashboard.

Same working-directory rule as gtp_gui.pyw: chdir to this file's folder
first, because config.toml, data/gtp.db and out/ are cwd-relative
everywhere in the gtp package. Under pythonw.exe there is no console, so
failures surface via a Tk message box instead of vanishing silently.

If the dashboard is already running (this file was double-clicked
twice), run() detects the busy port and just opens another browser tab.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)


def _fail(message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("GTP Dashboard", message)
    sys.exit(1)


if not (PROJECT_ROOT / "config.toml").exists():
    _fail(
        f"config.toml not found in {PROJECT_ROOT}.\n\n"
        "Refusing to start against the wrong folder."
    )

try:
    from gtp.web.app import run
except ImportError as e:
    _fail(
        f"Could not import the dashboard:\n{e}\n\n"
        "Is the virtual environment set up (pip install -e .)?"
    )

run()
