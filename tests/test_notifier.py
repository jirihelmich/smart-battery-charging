"""Tests for ChargingNotifier — notification gateway."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock HA modules before importing
_DAYTIME = datetime(2026, 2, 26, 15, 0, 0)  # 15:00 — daytime for tests

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
    "homeassistant.util",
    "homeassistant.util.dt",
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_battery_charging.models import (
    ChargingSchedule,
    ChargingSession,
    EnergyDeficit,
)
from smart_battery_charging import notifier as _notifier_mod
from smart_battery_charging.notifier import ChargingNotifier


@pytest.fixture(autouse=True)
def _patch_dt_util_now():
    """Ensure dt_util.now() returns daytime for all tests by default."""
    with patch.object(_notifier_mod.dt_util, "now", return_value=_DAYTIME):
        yield


def _make_deficit(
    consumption=16.0,
    solar_raw=8.0,
    solar_adjusted=6.0,
    forecast_error_pct=25.0,
    deficit=10.0,
    charge_needed=10.0,
    usable_capacity=10.5,
):
    return EnergyDeficit(
        consumption=consumption,
        solar_raw=solar_raw,
        solar_adjusted=solar_adjusted,
        forecast_error_pct=forecast_error_pct,
        deficit=deficit,
        charge_needed=charge_needed,
        usable_capacity=usable_capacity,
    )


def _make_schedule(
    start_hour=1, end_hour=4, window_hours=3, avg_price=1.5, required_kwh=5.0, target_soc=80.0
):
    return ChargingSchedule(
        start_hour=start_hour,
        end_hour=end_hour,
        window_hours=window_hours,
        avg_price=avg_price,
        required_kwh=required_kwh,
        target_soc=target_soc,
    )


def _make_session(
    start_soc=30.0,
    end_soc=80.0,
    start_time="2026-02-15T01:00:00",
    end_time="2026-02-15T03:30:00",
    avg_price=1.5,
    result="Target reached",
):
    return ChargingSession(
        start_soc=start_soc,
        end_soc=end_soc,
        start_time=start_time,
        end_time=end_time,
        avg_price=avg_price,
        result=result,
    )


def _make_coordinator(
    notification_service="mobile_app_phone",
    notify_planning=True,
    notify_charging_start=True,
    notify_charging_complete=True,
    notify_morning_safety=True,
    current_soc=50.0,
    max_charge_price=4.0,
    currency="Kč/kWh",
):
    coord = MagicMock()
    opts = {
        "notification_service": notification_service,
        "notify_planning": notify_planning,
        "notify_charging_start": notify_charging_start,
        "notify_charging_complete": notify_charging_complete,
        "notify_morning_safety": notify_morning_safety,
    }
    coord._opt = MagicMock(side_effect=lambda key, default: opts.get(key, default))
    coord.current_soc = current_soc
    coord.max_charge_price = max_charge_price
    coord.currency = currency
    return coord


def _make_hass():
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


class TestPlanNotification:
    """Test planning notification variants."""

    @pytest.mark.asyncio
    async def test_plan_scheduled_sends_notification(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        schedule = _make_schedule()
        deficit = _make_deficit()

        await notifier.async_notify_plan(schedule, deficit)

        hass.services.async_call.assert_called_once()
        args = hass.services.async_call.call_args
        assert args[0][0] == "notify"
        assert args[0][1] == "mobile_app_phone"
        data = args[0][2]
        assert "Charging Scheduled" in data["title"]
        assert "01:00" in data["message"]
        assert "04:00" in data["message"]
        assert "5.0 kWh" in data["message"]
        assert "80%" in data["message"]

    @pytest.mark.asyncio
    async def test_plan_not_needed_sends_solar_message(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        deficit = _make_deficit(charge_needed=0.0, deficit=0.0)

        await notifier.async_notify_plan(None, deficit)

        hass.services.async_call.assert_called_once()
        data = hass.services.async_call.call_args[0][2]
        assert "No Charging Needed" in data["title"]
        assert "Solar forecast covers" in data["message"]

    @pytest.mark.asyncio
    async def test_plan_not_scheduled_price_too_high(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        deficit = _make_deficit(charge_needed=5.0)

        await notifier.async_notify_plan(None, deficit)

        hass.services.async_call.assert_called_once()
        data = hass.services.async_call.call_args[0][2]
        assert "Not Scheduled" in data["title"]
        assert "5.0 kWh" in data["message"]
        assert "Price threshold" in data["message"]

    @pytest.mark.asyncio
    async def test_plan_includes_solar_and_consumption(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        deficit = _make_deficit(
            consumption=16.5, solar_raw=8.0, solar_adjusted=6.0
        )
        schedule = _make_schedule()

        await notifier.async_notify_plan(schedule, deficit)

        data = hass.services.async_call.call_args[0][2]
        assert "8.0 kWh" in data["message"]  # solar raw
        assert "6.0 kWh" in data["message"]  # solar adjusted
        assert "16.5 kWh" in data["message"]  # consumption


class TestChargingStartedNotification:
    """Test charging started notification."""

    @pytest.mark.asyncio
    async def test_sends_notification(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_charging_started(30.0, 80.0, 5.0)

        hass.services.async_call.assert_called_once()
        data = hass.services.async_call.call_args[0][2]
        assert "Charging Started" in data["title"]
        assert "30%" in data["message"]
        assert "80%" in data["message"]
        assert "5.0 kWh" in data["message"]


class TestChargingCompleteNotification:
    """Test charging complete notification."""

    @pytest.mark.asyncio
    async def test_target_reached(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        session = _make_session(result="Target reached")

        await notifier.async_notify_charging_complete(session, 80.0)

        hass.services.async_call.assert_called_once()
        data = hass.services.async_call.call_args[0][2]
        assert "Charging Complete" in data["title"]
        assert "Target reached" in data["message"]
        assert "30%" in data["message"]  # start_soc
        assert "80%" in data["message"]  # end_soc / target

    @pytest.mark.asyncio
    async def test_window_ended(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        session = _make_session(result="Window ended", end_soc=65.0)

        await notifier.async_notify_charging_complete(session, 80.0)

        data = hass.services.async_call.call_args[0][2]
        assert "Window ended" in data["message"]
        assert "65%" in data["message"]

    @pytest.mark.asyncio
    async def test_includes_duration(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        session = _make_session()

        await notifier.async_notify_charging_complete(session, 80.0)

        data = hass.services.async_call.call_args[0][2]
        assert "01:00" in data["message"]
        assert "03:30" in data["message"]


class TestMorningSafetyNotification:
    """Test morning safety notification."""

    @pytest.mark.asyncio
    async def test_sends_notification(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_morning_safety(70.0)

        hass.services.async_call.assert_called_once()
        data = hass.services.async_call.call_args[0][2]
        assert "Morning" in data["title"]
        assert "70%" in data["message"]
        assert "Self Use" in data["message"]


class TestServiceNotConfigured:
    """Test that no calls are made when service is not configured."""

    @pytest.mark.asyncio
    async def test_empty_service_no_call(self):
        hass = _make_hass()
        coord = _make_coordinator(notification_service="")
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_plan(_make_schedule(), _make_deficit())
        await notifier.async_notify_charging_started(30.0, 80.0, 5.0)
        await notifier.async_notify_charging_complete(_make_session(), 80.0)
        await notifier.async_notify_morning_safety(70.0)

        hass.services.async_call.assert_not_called()


class TestToggles:
    """Test that individual toggles disable their notification type."""

    @pytest.mark.asyncio
    async def test_planning_toggle_off(self):
        hass = _make_hass()
        coord = _make_coordinator(notify_planning=False)
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_plan(_make_schedule(), _make_deficit())
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_charging_start_toggle_off(self):
        hass = _make_hass()
        coord = _make_coordinator(notify_charging_start=False)
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_charging_started(30.0, 80.0, 5.0)
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_charging_complete_toggle_off(self):
        hass = _make_hass()
        coord = _make_coordinator(notify_charging_complete=False)
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_charging_complete(_make_session(), 80.0)
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_morning_safety_toggle_off(self):
        hass = _make_hass()
        coord = _make_coordinator(notify_morning_safety=False)
        notifier = ChargingNotifier(hass, coord)

        await notifier.async_notify_morning_safety(70.0)
        hass.services.async_call.assert_not_called()


class TestDeduplication:
    """Test planning notification deduplication."""

    @pytest.mark.asyncio
    async def test_same_schedule_twice_only_one_call(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        schedule = _make_schedule()
        deficit = _make_deficit()

        await notifier.async_notify_plan(schedule, deficit)
        await notifier.async_notify_plan(schedule, deficit)

        assert hass.services.async_call.call_count == 1

    @pytest.mark.asyncio
    async def test_different_schedule_sends_again(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        deficit = _make_deficit()

        schedule1 = _make_schedule(start_hour=1, end_hour=3)
        schedule2 = _make_schedule(start_hour=2, end_hour=5)

        await notifier.async_notify_plan(schedule1, deficit)
        await notifier.async_notify_plan(schedule2, deficit)

        assert hass.services.async_call.call_count == 2

    @pytest.mark.asyncio
    async def test_new_day_resets_dedup(self):
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        schedule = _make_schedule()
        deficit = _make_deficit()

        await notifier.async_notify_plan(schedule, deficit)

        # Simulate next day by changing internal state
        notifier._last_plan_date = date(2020, 1, 1)

        await notifier.async_notify_plan(schedule, deficit)

        assert hass.services.async_call.call_count == 2

    @pytest.mark.asyncio
    async def test_no_schedule_dedup_works(self):
        """Same no-schedule deficit twice → only one notification."""
        hass = _make_hass()
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        deficit = _make_deficit(charge_needed=5.0)

        await notifier.async_notify_plan(None, deficit)
        await notifier.async_notify_plan(None, deficit)

        assert hass.services.async_call.call_count == 1


class TestOvernightSuppression:
    """Test that plan notifications are suppressed during overnight hours."""

    @pytest.mark.asyncio
    async def test_suppressed_at_2am(self, _patch_dt_util_now):
        hass = _make_hass()
        coord = _make_coordinator()
        n = ChargingNotifier(hass, coord)

        with patch.object(_notifier_mod.dt_util, "now", return_value=datetime(2026, 2, 26, 2, 0, 0)):
            await n.async_notify_plan(_make_schedule(), _make_deficit())
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_suppressed_at_23pm(self, _patch_dt_util_now):
        hass = _make_hass()
        coord = _make_coordinator()
        n = ChargingNotifier(hass, coord)

        with patch.object(_notifier_mod.dt_util, "now", return_value=datetime(2026, 2, 26, 23, 0, 0)):
            await n.async_notify_plan(_make_schedule(), _make_deficit())
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_suppressed_at_15pm(self):
        hass = _make_hass()
        coord = _make_coordinator()
        n = ChargingNotifier(hass, coord)

        await n.async_notify_plan(_make_schedule(), _make_deficit())
        hass.services.async_call.assert_called_once()


class TestServiceError:
    """Test that service call errors are handled gracefully."""

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        hass = _make_hass()
        hass.services.async_call = AsyncMock(side_effect=Exception("Service unavailable"))
        coord = _make_coordinator()
        notifier = ChargingNotifier(hass, coord)

        # Should not raise
        await notifier.async_notify_morning_safety(70.0)
