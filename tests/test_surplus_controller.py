"""Tests for the Surplus Load Controller."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Mock HA modules before importing the package
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

from smart_battery_charging.models import SurplusLoadConfig, SurplusLoadState
from smart_battery_charging.surplus_controller import (
    SurplusLoadController,
    _load_configs_from_options,
)


def _make_coordinator(
    soc: float = 99.0,
    surplus_loads: list | None = None,
    grid_export_sensor: str = "sensor.grid_export_power",
) -> MagicMock:
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.current_soc = soc
    coord._opt = MagicMock(side_effect=lambda key, default: {
        "surplus_loads": surplus_loads or [],
        "grid_export_power_sensor": grid_export_sensor,
        "notify_surplus_load": True,
    }.get(key, default))
    coord.store = MagicMock()
    coord.store.surplus_runtime_history = []
    coord.async_record_surplus_runtime = AsyncMock()
    return coord


def _make_hass(
    switch_states: dict[str, str] | None = None,
    grid_export_kw: float = 5.0,
    grid_uom: str = "kW",
) -> MagicMock:
    """Create a mock hass with entity states."""
    switch_states = switch_states or {}
    hass = MagicMock()

    def get_state(entity_id):
        if entity_id == "sensor.grid_export_power":
            s = MagicMock()
            s.state = str(grid_export_kw)
            s.attributes = {"unit_of_measurement": grid_uom}
            return s
        if entity_id in switch_states:
            s = MagicMock()
            s.state = switch_states[entity_id]
            return s
        return None

    hass.states.get = get_state
    hass.services.async_call = AsyncMock()
    return hass


WATER_HEATER_LOAD = {
    "name": "Water Heater",
    "switch_entity": "switch.water_heater",
    "power_kw": 2.3,
    "priority": 1,
    "battery_on_threshold": 98.0,
    "battery_off_threshold": 95.0,
    "margin_on_kw": 0.3,
    "margin_off_kw": 0.5,
    "min_switch_interval": 300,
}

FLOOR_HEATING_LOAD = {
    "name": "Floor Heating",
    "switch_entity": "switch.floor_heating",
    "power_kw": 1.5,
    "priority": 2,
    "battery_on_threshold": 98.0,
    "battery_off_threshold": 95.0,
    "margin_on_kw": 0.3,
    "margin_off_kw": 0.5,
    "min_switch_interval": 300,
}


class TestLoadConfigs:
    """Test config parsing."""

    def test_parse_single_load(self):
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 1
        assert configs[0].name == "Water Heater"
        assert configs[0].power_kw == 2.3
        assert configs[0].priority == 1

    def test_parse_multiple_loads_sorted_by_priority(self):
        coord = _make_coordinator(surplus_loads=[FLOOR_HEATING_LOAD, WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 2
        assert configs[0].name == "Water Heater"  # priority 1
        assert configs[1].name == "Floor Heating"  # priority 2

    def test_parse_empty(self):
        coord = _make_coordinator(surplus_loads=[])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 0

    def test_parse_invalid_item_skipped(self):
        coord = _make_coordinator(surplus_loads=[{"invalid": True}, WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 1


class TestTrueSurplus:
    """Test true surplus calculation."""

    def test_no_loads_running(self):
        hass = _make_hass(grid_export_kw=3.0)
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        assert ctrl._compute_true_surplus(3.0) == 3.0

    def test_load_running_adds_power(self):
        hass = _make_hass(grid_export_kw=1.0, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()
        # true_surplus = grid_export (1.0) + running load (2.3) = 3.3
        assert ctrl._compute_true_surplus(1.0) == pytest.approx(3.3)

    def test_multiple_loads_running(self):
        hass = _make_hass(
            grid_export_kw=0.5,
            switch_states={"switch.water_heater": "on", "switch.floor_heating": "on"},
        )
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()
        # true_surplus = 0.5 + 2.3 + 1.5 = 4.3
        assert ctrl._compute_true_surplus(0.5) == pytest.approx(4.3)


class TestSurplusTick:
    """Test the main tick logic."""

    @pytest.mark.asyncio
    async def test_turn_on_when_surplus(self):
        """Turn on load when SOC high and surplus exceeds power + margin."""
        hass = _make_hass(grid_export_kw=3.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_called_once_with(
            "switch", "turn_on", {"entity_id": "switch.water_heater"}
        )

    @pytest.mark.asyncio
    async def test_no_turn_on_when_soc_low(self):
        """Don't turn on when SOC below threshold."""
        hass = _make_hass(grid_export_kw=5.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=90.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_turn_on_when_surplus_insufficient(self):
        """Don't turn on when surplus doesn't cover power + margin."""
        hass = _make_hass(grid_export_kw=2.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # 2.0 < 2.3 + 0.3 = 2.6 -> no turn on
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_when_soc_drops(self):
        """Turn off when SOC drops below off threshold."""
        hass = _make_hass(grid_export_kw=0.5, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=93.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_called_once_with(
            "switch", "turn_off", {"entity_id": "switch.water_heater"}
        )

    @pytest.mark.asyncio
    async def test_stay_on_when_true_surplus_ok(self):
        """Stay on when true surplus (accounting for own consumption) is sufficient."""
        # Grid export is 0.5 kW, but load is running (2.3 kW)
        # True surplus = 0.5 + 2.3 = 2.8 kW
        # Stay on: true_surplus (2.8) >= power_kw (2.3) - margin_off (0.5) = 1.8
        hass = _make_hass(grid_export_kw=0.5, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_when_true_surplus_low(self):
        """Turn off when true surplus drops below threshold."""
        # Grid export is -1.5 kW (importing), load is running (2.3 kW)
        # True surplus = -1.5 + 2.3 = 0.8 kW
        # Turn off: true_surplus (0.8) < power_kw (2.3) - margin_off (0.5) = 1.8
        hass = _make_hass(grid_export_kw=-1.5, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_called_once_with(
            "switch", "turn_off", {"entity_id": "switch.water_heater"}
        )

    @pytest.mark.asyncio
    async def test_anti_flap_blocks_switch(self):
        """Anti-flap prevents switching within min_switch_interval."""
        hass = _make_hass(grid_export_kw=3.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        # Set last switch time to just now
        ctrl._states["switch.water_heater"].last_switch_time = time.monotonic()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Higher priority load gets surplus first."""
        # 3.0 kW surplus, water heater needs 2.3+0.3=2.6, floor heating needs 1.5+0.3=1.8
        # After water heater: remaining = 3.0 - 2.3 = 0.7 (not enough for floor heating)
        hass = _make_hass(
            grid_export_kw=3.0,
            switch_states={"switch.water_heater": "off", "switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # Only water heater should turn on
        calls = hass.services.async_call.call_args_list
        assert len(calls) == 1
        assert calls[0][0] == ("switch", "turn_on", {"entity_id": "switch.water_heater"})

    @pytest.mark.asyncio
    async def test_both_loads_when_enough_surplus(self):
        """Both loads turn on when surplus covers both."""
        # 5.0 kW surplus, water heater needs 2.6, remaining 2.7, floor heating needs 1.8
        hass = _make_hass(
            grid_export_kw=5.0,
            switch_states={"switch.water_heater": "off", "switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_grid_export_sensor_unavailable(self):
        """Skip tick when grid export sensor is unavailable."""
        hass = MagicMock()
        hass.states.get = MagicMock(return_value=None)
        hass.services.async_call = AsyncMock()
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_watts_conversion(self):
        """Grid export in W is converted to kW."""
        hass = _make_hass(grid_export_kw=3000, grid_uom="W", switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # 3000 W = 3.0 kW, enough for 2.3 + 0.3 = 2.6
        hass.services.async_call.assert_called_once()


class TestRestoreState:
    """Test state restoration."""

    def test_restore_states(self):
        hass = _make_hass(switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        ctrl.restore_states({
            "switch.water_heater": {
                "last_switch_time": 1000.0,
                "daily_runtime_seconds": 3600.0,
            }
        })

        st = ctrl.states["switch.water_heater"]
        assert st.last_switch_time == 1000.0
        assert st.daily_runtime_seconds == 3600.0

    def test_restore_unknown_entity_ignored(self):
        hass = _make_hass(switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        # Should not raise
        ctrl.restore_states({"switch.unknown": {"last_switch_time": 1000.0}})


class TestSensorData:
    """Test sensor data generation."""

    def test_no_loads(self):
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        data = ctrl.get_sensor_data()
        assert data["surplus_active_loads"] == 0

    def test_with_running_load(self):
        hass = _make_hass(grid_export_kw=1.0, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()

        data = ctrl.get_sensor_data()
        assert data["surplus_active_loads"] == 1
        assert data["surplus_active_load_names"] == "Water Heater"
        assert data["surplus_total_power_kw"] == 2.3
        assert data["surplus_true_surplus_kw"] == pytest.approx(3.3)


class TestMidnight:
    """Test midnight reset."""

    @pytest.mark.asyncio
    async def test_midnight_records_runtime(self):
        hass = _make_hass(switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["switch.water_heater"].daily_runtime_seconds = 7200.0

        await ctrl.async_on_midnight()

        coord.async_record_surplus_runtime.assert_called_once_with(
            {"Water Heater": 2.0}  # 7200s = 2h
        )
        # Runtime reset
        assert ctrl._states["switch.water_heater"].daily_runtime_seconds == 0.0
