"""Double-click launcher for the GTP desktop GUI.

Sets the working directory to this file's own folder first --
config.toml, data/gtp.db, and out/ are all relative paths in the gtp
package, so launching from a Desktop shortcut (a different working
directory) would otherwise silently create an empty database next to
wherever the shortcut started.
"""

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

if not (PROJECT_ROOT / "config.toml").exists():
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "GTP Monitoring Tool",
        f"config.toml not found in {PROJECT_ROOT}.\n\n"
        "Refusing to start against the wrong folder.",
    )
    sys.exit(1)

from gtp.gui.app import main

main()
