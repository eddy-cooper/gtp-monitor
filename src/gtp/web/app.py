"""Flask routes for the local web dashboard.

Read-only by design: it renders what report.py / chart.py / gui/format.py
compute and never writes to the database or the alert log -- entries are
made in the desktop GUI or CLI, which own the validation/override flow.
Serves 127.0.0.1 only; nothing is exposed to the network.
"""

import os
import socket
import sqlite3
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, g, jsonify, render_template

from gtp.alerts import load_alert_config_from_toml
from gtp.chart import fetch_trend_points
from gtp.db import DEFAULT_DB_PATH, init_db
from gtp.report import build_status_view
from gtp.web import viewmodel

DEFAULT_PORT = 8742


def _schedule_shutdown() -> None:
    # os._exit rather than sys.exit: the server's worker threads would
    # keep the process alive through a normal exit. Blunt but deliberate
    # for a single-user local tool -- this app never writes, so there is
    # nothing that needs graceful teardown. The delay lets the HTTP
    # response reach the browser first.
    threading.Timer(0.3, os._exit, args=(0,)).start()


def create_app(db_path: str | Path = DEFAULT_DB_PATH) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = Path(db_path)

    def get_conn() -> sqlite3.Connection:
        # One connection per request (flask.g): sqlite3 connections can't
        # be shared across the threaded server's request threads.
        if "conn" not in g:
            g.conn = init_db(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc: BaseException | None) -> None:
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    def full_payload() -> dict:
        conn = get_conn()
        today = datetime.now().date().isoformat()
        site = viewmodel.load_site_config_from_toml()
        alert_config = load_alert_config_from_toml()
        view = build_status_view(conn, today=today)
        return {
            "site_name": site.display_name,
            "today": today,
            "status": viewmodel.status_payload(view, site.stale_entry_days),
            "trend": viewmodel.trend_payload(
                fetch_trend_points(conn), alert_config.action_pct, alert_config.watch_pct
            ),
            "tables": viewmodel.tables_payload(conn),
        }

    @app.get("/")
    def index() -> str:
        # The first payload is inlined into the page, so the dashboard
        # renders even if a later /api fetch fails.
        return render_template("dashboard.html", payload=full_payload())

    @app.get("/api/dashboard")
    def api_dashboard():
        return jsonify(full_payload())

    @app.post("/shutdown")
    def shutdown() -> str:
        _schedule_shutdown()
        return "Server stopped. You can close this tab."

    return app


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """Start the dashboard server and open the default browser.

    If the port is already serving (the launcher was double-clicked
    twice), just open another browser tab at the running instance.
    """
    url = f"http://127.0.0.1:{port}/"
    if _port_in_use(port):
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app = create_app()
    # use_reloader=False is mandatory: the reloader re-executes the main
    # module, which breaks under pythonw.exe (no console to restart in).
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
