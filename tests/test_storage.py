"""Tests for storage-related data models — pure logic, no HA deps.

The actual SmartBatteryStore class requires HA framework (homeassistant.helpers.storage).
These tests verify the ChargingSession model used by storage.
Full storage integration tests will be added in Phase 2 with the HA test framework.
"""

from __future__ import annotations

import pytest

from models import ChargingSession


class TestChargingSession:
    """Test ChargingSession data model."""

    def test_kwh_charged_basic(self):
        session = ChargingSession(start_soc=20.0, end_soc=80.0)
        # 60% of 15 kWh battery = 9.0 kWh
        assert session.kwh_charged(15.0) == 9.0

    def test_kwh_charged_zero_delta(self):
        session = ChargingSession(start_soc=50.0, end_soc=50.0)
        assert session.kwh_charged(15.0) == 0.0

    def test_kwh_charged_negative_delta(self):
        """If end < start (e.g. data error), return 0."""
        session = ChargingSession(start_soc=80.0, end_soc=20.0)
        assert session.kwh_charged(15.0) == 0.0

    def test_total_cost(self):
        session = ChargingSession(
            start_soc=20.0, end_soc=80.0, avg_price=1.5
        )
        # 9.0 kWh * 1.5 Kč/kWh = 13.5 Kč
        assert session.total_cost(15.0) == 13.5

    def test_total_cost_no_charge(self):
        session = ChargingSession(start_soc=50.0, end_soc=50.0, avg_price=2.0)
        assert session.total_cost(15.0) == 0.0

    def test_real_world_session(self):
        """Simulate a real charging session from Feb 13 data."""
        session = ChargingSession(
            start_soc=22.0,
            end_soc=78.0,
            start_time="2026-02-13T01:00:00+01:00",
            end_time="2026-02-13T04:12:00+01:00",
            avg_price=1.23,
            result="Target reached",
        )
        capacity = 17.28  # BMS reports 17280 Wh
        kwh = session.kwh_charged(capacity)
        assert kwh == pytest.approx(9.68, abs=0.01)
        cost = session.total_cost(capacity)
        assert cost == pytest.approx(11.9, abs=0.1)

    def test_default_values(self):
        session = ChargingSession()
        assert session.start_soc == 0.0
        assert session.end_soc == 0.0
        assert session.start_time == ""
        assert session.end_time == ""
        assert session.avg_price == 0.0
        assert session.result == ""

    def test_no_charged_kwh_property(self):
        """M5/L3: charged_kwh property has been removed (was always 0.0)."""
        session = ChargingSession(start_soc=20.0, end_soc=80.0)
        assert not hasattr(session, "charged_kwh") or not isinstance(
            getattr(type(session), "charged_kwh", None), property
        )


class TestDefaultData:
    """Test storage default data includes new fields."""

    def test_default_data_has_all_fields(self):
        expected_keys = {
            "consumption_history", "charge_history", "forecast_error_history",
            "last_session", "enabled", "charging_state", "current_schedule",
        }
        data = {
            "consumption_history": [],
            "charge_history": [],
            "forecast_error_history": [],
            "last_session": None,
            "enabled": True,
            "charging_state": "idle",
            "current_schedule": None,
        }
        assert set(data.keys()) == expected_keys
        assert data["enabled"] is True
        assert data["charging_state"] == "idle"
        assert data["current_schedule"] is None
