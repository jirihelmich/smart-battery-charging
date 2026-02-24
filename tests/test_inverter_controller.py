"""Tests for InverterController — the Modbus service call gateway."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

# Import as package so relative imports work
_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_battery_charging.inverter_controller import (
    InverterController,
    MODBUS_SETTLE_DELAY,
)


@pytest.fixture
def config() -> dict:
    return {
        "inverter_mode_select": "select.solax_inverter_mode",
        "inverter_charge_command_select": "select.solax_charger_use_mode",
        "inverter_charge_soc_limit": "number.solax_charge_soc_limit",
        "inverter_discharge_min_soc": "number.solax_discharge_min_soc",
        "mode_self_use": "Self Use Mode",
        "mode_manual": "Manual Mode",
        "charge_force": "Force Charge",
        "charge_stop": "Stop Charge and Discharge",
    }


@pytest.fixture
def hass() -> MagicMock:
    """Create a mock hass object."""
    mock_hass = MagicMock()
    mock_hass.services.async_call = AsyncMock()
    return mock_hass


@pytest.fixture
def controller(hass, config) -> InverterController:
    return InverterController(hass, config)


class TestConfigAccessors:
    """Test that config keys are read correctly."""

    def test_entity_ids(self, controller):
        assert controller.mode_select_entity == "select.solax_inverter_mode"
        assert controller.charge_command_entity == "select.solax_charger_use_mode"
        assert controller.soc_limit_entity == "number.solax_charge_soc_limit"
        assert controller.discharge_min_soc_entity == "number.solax_discharge_min_soc"

    def test_mode_strings(self, controller):
        assert controller.mode_self_use == "Self Use Mode"
        assert controller.mode_manual == "Manual Mode"
        assert controller.charge_force == "Force Charge"
        assert controller.charge_stop == "Stop Charge and Discharge"

    def test_defaults_when_missing(self, hass):
        ctrl = InverterController(hass, {})
        assert ctrl.mode_select_entity == ""
        assert ctrl.mode_self_use == "Self Use Mode"
        assert ctrl.mode_manual == "Manual Mode"


class TestStartCharging:
    """Test the start-charging sequence."""

    @pytest.mark.asyncio
    async def test_start_charging_sequence(self, controller, hass):
        """Verify correct order: SOC limit → Manual Mode → delay → Force Charge."""
        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await controller.async_start_charging(90.0)

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 3

        # 1. Set SOC limit
        args, kwargs = calls[0]
        assert args == ("number", "set_value", {"entity_id": "number.solax_charge_soc_limit", "value": 90.0})
        assert kwargs == {"blocking": True}

        # 2. Manual Mode
        args, kwargs = calls[1]
        assert args == ("select", "select_option", {"entity_id": "select.solax_inverter_mode", "option": "Manual Mode"})
        assert kwargs == {"blocking": True}

        # 3. Force Charge
        args, kwargs = calls[2]
        assert args == ("select", "select_option", {"entity_id": "select.solax_charger_use_mode", "option": "Force Charge"})
        assert kwargs == {"blocking": True}

    @pytest.mark.asyncio
    async def test_start_charging_has_delay(self, controller, hass):
        """Verify 5s delay between mode switch and charge command."""
        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await controller.async_start_charging(85.0)
            mock_sleep.assert_called_once_with(MODBUS_SETTLE_DELAY)


class TestStopCharging:
    """Test the stop-charging sequence."""

    @pytest.mark.asyncio
    async def test_stop_charging_sequence(self, controller, hass):
        """Verify correct order: Stop → delay → Reset SOC → Self Use → discharge min."""
        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await controller.async_stop_charging(20.0)

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 4

        # 1. Stop Charge
        args, kwargs = calls[0]
        assert args == ("select", "select_option", {"entity_id": "select.solax_charger_use_mode", "option": "Stop Charge and Discharge"})
        assert kwargs == {"blocking": True}

        # 2. Reset SOC limit to 100
        args, kwargs = calls[1]
        assert args == ("number", "set_value", {"entity_id": "number.solax_charge_soc_limit", "value": 100})
        assert kwargs == {"blocking": True}

        # 3. Self Use Mode
        args, kwargs = calls[2]
        assert args == ("select", "select_option", {"entity_id": "select.solax_inverter_mode", "option": "Self Use Mode"})
        assert kwargs == {"blocking": True}

        # 4. Discharge min SOC
        args, kwargs = calls[3]
        assert args == ("number", "set_value", {"entity_id": "number.solax_discharge_min_soc", "value": 20.0})
        assert kwargs == {"blocking": True}

    @pytest.mark.asyncio
    async def test_stop_charging_no_discharge_entity(self, hass):
        """When no discharge min SOC entity configured, skip that step."""
        config_no_discharge = {
            "inverter_mode_select": "select.solax_inverter_mode",
            "inverter_charge_command_select": "select.solax_charger_use_mode",
            "inverter_charge_soc_limit": "number.solax_charge_soc_limit",
            "mode_self_use": "Self Use Mode",
            "mode_manual": "Manual Mode",
            "charge_force": "Force Charge",
            "charge_stop": "Stop Charge and Discharge",
        }
        ctrl = InverterController(hass, config_no_discharge)
        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await ctrl.async_stop_charging(20.0)

        # Should be 3 calls (no discharge min SOC)
        assert hass.services.async_call.call_count == 3

    @pytest.mark.asyncio
    async def test_stop_charging_has_delay(self, controller, hass):
        """Verify 5s delay between stop command and mode restore."""
        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await controller.async_stop_charging(20.0)
            mock_sleep.assert_called_once_with(MODBUS_SETTLE_DELAY)


class TestGetCurrentMode:
    """Test reading current inverter mode."""

    @pytest.mark.asyncio
    async def test_returns_state(self, controller, hass):
        state_obj = MagicMock()
        state_obj.state = "Self Use Mode"
        hass.states.get.return_value = state_obj

        mode = await controller.async_get_current_mode()
        assert mode == "Self Use Mode"

    @pytest.mark.asyncio
    async def test_returns_empty_when_unavailable(self, controller, hass):
        state_obj = MagicMock()
        state_obj.state = "unavailable"
        hass.states.get.return_value = state_obj

        mode = await controller.async_get_current_mode()
        assert mode == ""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_state(self, controller, hass):
        hass.states.get.return_value = None

        mode = await controller.async_get_current_mode()
        assert mode == ""


class TestIsManualMode:
    """Test manual mode detection."""

    def test_matches_manual(self, controller):
        assert controller.is_manual_mode("Manual Mode") is True

    def test_no_match(self, controller):
        assert controller.is_manual_mode("Self Use Mode") is False

    def test_empty_string(self, controller):
        assert controller.is_manual_mode("") is False
