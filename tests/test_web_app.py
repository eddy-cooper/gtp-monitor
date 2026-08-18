"""Tests for the Flask dashboard routes (web/app.py) via Flask's test
client -- no real server, no browser. The /shutdown timer is
monkeypatched out; actually killing the process is not something a test
should do.
"""

import pytest

import gtp.web.app as web_app
from gtp.db import init_db
from gtp.web.app import create_app
from gtp.web.viewmodel import load_site_config_from_toml


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = tmp_path / "web.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0101_error, fit0501, op_fraction) "
        "VALUES ('2026-07-30', 40.0, 0.0, 38.0, 1.0)"
    )
    conn.execute(
        "INSERT INTO daily_reading (date, fit0101_reading, fit0101_error, fit0501, op_fraction) "
        "VALUES ('2026-07-31', 41.0, 0.0, 39.0, 1.0)"
    )
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, total_in, total_out, "
        "mains_used, chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates) "
        "VALUES ('2026-06-01', '2026-06-30', 1000.0, 996.0, 5.0, 0.5, 3.0, 0.003, 0)"
    )
    conn.execute(
        "INSERT INTO balance_result (period_start, period_end, total_in, total_out, "
        "mains_used, chemicals_used, fit0101_under_kl, fit0101_under_pct, contains_estimates) "
        "VALUES ('2026-07-01', '2026-07-31', 1000.0, 993.0, 5.0, 0.5, 6.0, 0.006, 0)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_index_renders_dashboard(seeded_db):
    client = create_app(seeded_db).test_client()
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # site name comes from config.toml, never hardcoded
    assert load_site_config_from_toml().display_name in page
    assert "FIT0101 under-reading trend" in page
    # first payload is inlined so the page renders without a fetch
    assert "window.__INITIAL__" in page


def test_api_dashboard_payload(seeded_db):
    client = create_app(seeded_db).test_client()
    data = client.get("/api/dashboard").get_json()

    assert data["status"]["has_balance"] is True
    assert data["status"]["under_pct"] == "0.600%"
    assert data["status"]["badge"]["level"] == "WATCH"  # 0.6% is above the 0.5% watch line
    assert data["status"]["trend"]["direction"] == "rising"

    assert data["trend"]["labels"] == ["Jun 2026", "Jul 2026"]
    assert data["trend"]["values"] == [pytest.approx(0.3), pytest.approx(0.6)]

    assert len(data["tables"]["readings"]["rows"]) == 2
    assert len(data["tables"]["balances"]["rows"]) == 2


def test_empty_database_renders_without_error(tmp_path):
    db_path = tmp_path / "empty.db"
    init_db(db_path).close()
    client = create_app(db_path).test_client()

    response = client.get("/")
    assert response.status_code == 200

    data = client.get("/api/dashboard").get_json()
    assert data["status"]["has_balance"] is False
    assert data["trend"]["labels"] == []


def test_shutdown_route_schedules_shutdown(seeded_db, monkeypatch):
    calls = []
    monkeypatch.setattr(web_app, "_schedule_shutdown", lambda: calls.append(True))
    client = create_app(seeded_db).test_client()
    response = client.post("/shutdown")
    assert response.status_code == 200
    assert calls == [True]


def test_shutdown_requires_post(seeded_db):
    client = create_app(seeded_db).test_client()
    assert client.get("/shutdown").status_code == 405
