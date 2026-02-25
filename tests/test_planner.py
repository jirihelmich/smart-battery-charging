"""Tests for ChargingPlanner — pure orchestration logic."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# Mock HA modules before importing
for mod_name in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.selector",
    "homeassistant.helpers.event",
    "homeassistant.components",
    "homeassistant.components.switch",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.number",
    "homeassistant.data_entry_flow",
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_battery_charging.consumption_tracker import ConsumptionTracker
from smart_battery_charging.forecast_corrector import ForecastCorrector
from smart_battery_charging.models import EnergyDeficit, OvernightNeed
from smart_battery_charging.planner import ChargingPlanner
from smart_battery_charging.price_analyzer import PriceAnalyzer, PriceSlot, PriceWindow


def _make_coordinator(
    enabled=True,
    battery_capacity=15.0,
    max_charge_level=90.0,
    min_soc=20.0,
    max_charge_power=10.0,
    max_charge_price=4.0,
    solar_forecast_tomorrow=5.0,
    consumption_history=None,
    forecast_error_history=None,
    price_attributes=None,
    current_soc=50.0,
    solar_forecast_tomorrow_hourly=None,
    sunrise_hour_tomorrow=6.5,
    solar_forecast_today=10.0,
    actual_solar_today=5.0,
):
    """Create a mock coordinator with controlled values."""
    coord = MagicMock()
    coord.enabled = enabled
    coord.battery_capacity = battery_capacity
    coord.max_charge_level = max_charge_level
    coord.min_soc = min_soc
    coord.max_charge_power = max_charge_power
    coord.max_charge_price = max_charge_price
    coord.solar_forecast_tomorrow = solar_forecast_tomorrow
    coord.current_soc = current_soc
    coord.solar_forecast_today = solar_forecast_today
    coord.actual_solar_today = actual_solar_today
    coord._last_overnight = None

    # Overnight-related properties
    type(coord).solar_forecast_tomorrow_hourly = PropertyMock(
        return_value=solar_forecast_tomorrow_hourly or {}
    )
    type(coord).sunrise_hour_tomorrow = PropertyMock(return_value=sunrise_hour_tomorrow)

    # Real sub-components for correct logic
    coord.consumption_tracker = ConsumptionTracker(window_days=7, fallback_kwh=20.0)
    coord.forecast_corrector = ForecastCorrector(window_days=7)
    coord.price_analyzer = PriceAnalyzer(window_start_hour=22, window_end_hour=6)

    # Store data
    coord.store = MagicMock()
    coord.store.consumption_history = [16.0, 17.0, 16.5] if consumption_history is None else consumption_history
    coord.store.forecast_error_history = [] if forecast_error_history is None else forecast_error_history

    # Price attributes — default: realistic night prices for today/tomorrow
    if price_attributes is None:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        price_attributes = {
            f"{today}T22:00:00+01:00": 1.8,
            f"{today}T23:00:00+01:00": 1.5,
            f"{tomorrow}T00:00:00+01:00": 1.2,
            f"{tomorrow}T01:00:00+01:00": 1.0,
            f"{tomorrow}T02:00:00+01:00": 1.3,
            f"{tomorrow}T03:00:00+01:00": 1.6,
            f"{tomorrow}T04:00:00+01:00": 2.0,
            f"{tomorrow}T05:00:00+01:00": 2.5,
        }
    coord.price_attributes = price_attributes

    return coord


class TestComputeEnergyDeficit:
    """Test energy deficit calculation."""

    def test_deficit_when_solar_less_than_consumption(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()

        assert deficit.consumption == 16.5  # average of 16, 17, 16.5
        assert deficit.solar_raw == 5.0
        assert deficit.solar_adjusted == 5.0  # no error history
        assert deficit.deficit == 11.5  # 16.5 - 5.0
        assert deficit.charge_needed == 10.5  # clamped to usable capacity
        assert deficit.usable_capacity == 10.5  # 15 * (90-20)/100

    def test_no_deficit_when_solar_covers_consumption(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=20.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()

        assert deficit.deficit == 0.0
        assert deficit.charge_needed == 0.0

    def test_deficit_clamped_to_usable_capacity(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=0.0,
            consumption_history=[30.0],  # way more than usable capacity
            battery_capacity=15.0,
            max_charge_level=90.0,
            min_soc=20.0,
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()

        # Usable capacity = 15 * (90-20)/100 = 10.5
        assert deficit.charge_needed == 10.5

    def test_forecast_error_adjustment(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            # 40% average overestimate → adjusted = 10 * (1 - 0.4) = 6.0
            forecast_error_history=[0.4, 0.4, 0.4],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()

        assert deficit.solar_raw == 10.0
        assert deficit.solar_adjusted == 6.0
        assert deficit.deficit == 10.0  # 16.0 - 6.0
        assert deficit.forecast_error_pct == 40.0

    def test_uses_fallback_consumption_when_no_history(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()

        assert deficit.consumption == 20.0  # fallback


class TestHasTomorrowPrices:
    """Test tomorrow's price availability check."""

    def test_prices_available(self):
        coord = _make_coordinator()  # default has tomorrow's prices
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices() is True

    def test_prices_not_available(self):
        coord = _make_coordinator(price_attributes={
            "2020-01-01T00:00:00+01:00": 1.0,
        })
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices() is False

    def test_empty_prices(self):
        coord = _make_coordinator(price_attributes={})
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices() is False


class TestComputeTargetSoc:
    """Test target SOC calculation."""

    def test_basic_target(self):
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit()
        target = planner.compute_target_soc(deficit)

        # charge_needed / capacity * 100 + min_soc
        # with default data: deficit exists, target should be between min and max
        assert 20.0 <= target <= 90.0

    def test_target_clamped_to_max(self):
        coord = _make_coordinator(
            battery_capacity=10.0,
            min_soc=20.0,
            max_charge_level=90.0,
        )
        planner = ChargingPlanner(coord)
        # Create a deficit that would push target past max
        from smart_battery_charging.models import EnergyDeficit
        deficit = EnergyDeficit(
            consumption=20.0, solar_raw=0.0, solar_adjusted=0.0,
            forecast_error_pct=0.0, deficit=20.0, charge_needed=10.0,
            usable_capacity=7.0,
        )
        target = planner.compute_target_soc(deficit)
        # 20 + (10/10*100) = 120 → clamped to 90
        assert target == 90.0

    def test_no_charge_returns_min_soc(self):
        coord = _make_coordinator(min_soc=20.0)
        planner = ChargingPlanner(coord)
        from smart_battery_charging.models import EnergyDeficit
        deficit = EnergyDeficit(
            consumption=10.0, solar_raw=15.0, solar_adjusted=15.0,
            forecast_error_pct=0.0, deficit=0.0, charge_needed=0.0,
            usable_capacity=10.5,
        )
        target = planner.compute_target_soc(deficit)
        assert target == 20.0


class TestPlanCharging:
    """Test the full planning pipeline."""

    def test_creates_schedule_when_deficit(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is not None
        assert schedule.required_kwh > 0
        assert schedule.target_soc > coord.min_soc
        assert schedule.avg_price <= coord.max_charge_price
        assert schedule.window_hours >= 1

    @patch("smart_battery_charging.planner.datetime")
    def test_returns_none_when_solar_covers(self, mock_dt):
        # Fix time to 20:00 — only 2h of pre-window drain
        now = datetime(2026, 2, 25, 20, 0, 0)
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")
        prices = {
            f"{today}T22:00:00+01:00": 1.8,
            f"{today}T23:00:00+01:00": 1.5,
            f"{tomorrow}T00:00:00+01:00": 1.2,
            f"{tomorrow}T01:00:00+01:00": 1.0,
            f"{tomorrow}T02:00:00+01:00": 1.3,
            f"{tomorrow}T03:00:00+01:00": 1.6,
            f"{tomorrow}T04:00:00+01:00": 2.0,
            f"{tomorrow}T05:00:00+01:00": 2.5,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=25.0,
            consumption_history=[16.0, 17.0, 16.5],
            current_soc=85.0,  # high SOC so battery covers overnight too
            price_attributes=prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is None

    def test_returns_none_when_disabled(self):
        coord = _make_coordinator(enabled=False)
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is None

    def test_returns_none_when_no_prices(self):
        coord = _make_coordinator(price_attributes={})
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is None

    def test_returns_none_when_price_too_high(self):
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        expensive_prices = {
            f"{today}T22:00:00+01:00": 10.0,
            f"{today}T23:00:00+01:00": 10.0,
            f"{tomorrow}T00:00:00+01:00": 10.0,
            f"{tomorrow}T01:00:00+01:00": 10.0,
            f"{tomorrow}T02:00:00+01:00": 10.0,
            f"{tomorrow}T03:00:00+01:00": 10.0,
            f"{tomorrow}T04:00:00+01:00": 10.0,
            f"{tomorrow}T05:00:00+01:00": 10.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_price=4.0,
            price_attributes=expensive_prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is None

    def test_schedule_picks_cheapest_window(self):
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        prices = {
            f"{today}T22:00:00+01:00": 3.0,
            f"{today}T23:00:00+01:00": 3.0,
            f"{tomorrow}T00:00:00+01:00": 1.0,
            f"{tomorrow}T01:00:00+01:00": 0.8,
            f"{tomorrow}T02:00:00+01:00": 1.2,
            f"{tomorrow}T03:00:00+01:00": 2.0,
            f"{tomorrow}T04:00:00+01:00": 2.5,
            f"{tomorrow}T05:00:00+01:00": 3.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_power=10.0,  # need 1 hour for ~10 kWh
            price_attributes=prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is not None
        # Cheapest contiguous window should include hours 0-1 (1.0, 0.8)
        assert schedule.avg_price <= 2.0

    def test_schedule_has_created_at(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is not None
        assert schedule.created_at is not None

    def test_overnight_triggers_charging_when_daily_deficit_zero(self):
        """Solar > consumption but low battery can't bridge the night."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=25.0,  # plenty of solar
            consumption_history=[16.0, 17.0, 16.5],
            current_soc=30.0,  # low battery — ~1.5 kWh usable
            solar_forecast_today=10.0,
            actual_solar_today=10.0,  # no remaining solar today
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        # Daily deficit is 0 but overnight survival triggers charging
        assert schedule is not None
        assert schedule.required_kwh > 0
        assert planner.last_overnight_need is not None
        assert planner.last_overnight_need.charge_needed > 0

    def test_overnight_increases_charge_above_daily_deficit(self):
        """Overnight need exceeds daily deficit → uses overnight value."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=14.0,  # small deficit
            consumption_history=[16.0, 17.0, 16.5],
            current_soc=30.0,  # low battery
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging()

        assert schedule is not None
        overnight = planner.last_overnight_need
        assert overnight is not None
        # If overnight need > daily deficit, required_kwh should reflect that
        if overnight.charge_needed > 2.5:
            assert schedule.required_kwh >= overnight.charge_needed


class TestComputeOvernightNeed:
    """Test overnight survival calculation."""

    def test_overnight_shortfall_low_battery(self):
        """Battery at 30% cannot cover overnight consumption."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=30.0,  # only 1.5 kWh usable (30-20=10% of 15)
            consumption_history=[16.0, 17.0, 16.5],  # ~16.5 avg = 0.6875/h
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,  # no remaining solar
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        # ~8.5 dark hours (22:00 to 06:30+2h=08:30), ~5.8 kWh consumed
        # Battery usable ~1.5 kWh → shortfall > 0
        assert overnight.charge_needed > 0
        assert overnight.dark_hours > 0
        assert overnight.source in ("sun_entity", "fallback")

    @patch("smart_battery_charging.planner.datetime")
    def test_overnight_no_shortfall_full_battery(self, mock_dt):
        """Battery at 85% easily covers overnight (planning at 20:00)."""
        now = datetime(2026, 2, 25, 20, 0, 0)
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=85.0,  # 9.75 kWh usable
            consumption_history=[16.0, 17.0, 16.5],
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        # At 20:00 with 85% SOC: 9.75 kWh usable
        # 2h pre-window drain: 2 * 0.6875 = 1.375, no remaining solar → drain = 1.375
        # Battery at start: 9.75 - 1.375 = 8.375
        # ~10.5h dark (22:00 to 08:30), consumption ~7.22 kWh
        # 8.375 > 7.22 → no shortfall
        assert overnight.charge_needed == 0

    def test_overnight_with_hourly_solar_data(self):
        """Hour-by-hour simulation with forecast_solar data."""
        hourly = {
            7: 0.3,   # sunrise ramp
            8: 1.0,   # not enough (consumption ~0.69)
            9: 2.0,   # covers consumption
            10: 3.0,
        }
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=35.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_tomorrow_hourly=hourly,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        assert overnight.source == "forecast_solar"
        # Solar covers consumption starting at hour 8 (1.0 > 0.6875)
        assert overnight.solar_start_hour == 8.0
        assert overnight.dark_hours > 0

    def test_overnight_fallback_no_sun_entity(self):
        """When sun.sun is not available, uses fallback."""
        coord = _make_coordinator(
            current_soc=40.0,
            consumption_history=[16.0],
            sunrise_hour_tomorrow=None,  # sun entity unavailable
            solar_forecast_today=5.0,
            actual_solar_today=5.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        # Fallback: window_end + 3 = 6 + 3 = 9
        assert overnight.source == "fallback"
        assert overnight.solar_start_hour == 9.0

    def test_overnight_clamped_to_usable_capacity(self):
        """Charge needed cannot exceed usable capacity."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=20.0,  # exactly at min SOC → 0 usable
            consumption_history=[16.0, 17.0],
            sunrise_hour_tomorrow=8.0,  # late sunrise → long dark period
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        usable_capacity = 15.0 * (90.0 - 20.0) / 100  # 10.5
        assert overnight.charge_needed <= usable_capacity

    def test_overnight_accounts_for_pre_window_discharge(self):
        """Planning during daytime accounts for evening battery drain."""
        # Simulate planning at ~14:00 — 8 hours until 22:00 window start
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=60.0,  # 6 kWh usable now
            consumption_history=[16.0, 17.0, 16.5],  # 0.6875/h
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=5.0,  # 5 kWh remaining solar today
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need()

        # Battery at window start should be less than current usable
        assert overnight.battery_at_window_start < 6.0
