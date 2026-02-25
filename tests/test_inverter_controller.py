"""Tests for InverterController — the Modbus service call gateway."""

from __future__ import annotations

import asyncio
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
    "homeassistant.util",
    "homeassistant.util.dt",
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

# Import as package so relative imports work
_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_battery_charging.inverter_controller import (
    InverterCommandError,
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
def ems_config() -> dict:
    return {
        "inverter_working_mode_number": "number.wattsonic_working_mode",
        "inverter_battery_power_number": "number.wattsonic_battery_power",
        "inverter_ac_lower_limit_number": "number.wattsonic_ac_lower_limit",
        "inverter_battery_dod_number": "number.wattsonic_battery_dod",
        "ems_charge_mode_value": 771,
        "ems_normal_mode_value": 257,
        "max_charge_power": 5.0,
    }


@pytest.fixture
def hass() -> MagicMock:
    """Create a mock hass object."""
    mock_hass = MagicMock()
    mock_hass.services.async_call = AsyncMock()
    return mock_hass


@pytest.fixture
def controller(hass, config) -> InverterController:
    return InverterController(hass, config, control_type="select")


@pytest.fixture
def ems_controller(hass, ems_config) -> InverterController:
    return InverterController(hass, ems_config, control_type="ems_power")


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

    def test_ems_config_accessors(self, ems_controller):
        assert ems_controller.working_mode_entity == "number.wattsonic_working_mode"
        assert ems_controller.battery_power_entity == "number.wattsonic_battery_power"
        assert ems_controller.ac_lower_limit_entity == "number.wattsonic_ac_lower_limit"
        assert ems_controller.battery_dod_entity == "number.wattsonic_battery_dod"
        assert ems_controller.ems_charge_mode_value == 771
        assert ems_controller.ems_normal_mode_value == 257


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

        # 2. Manual Mode
        args, kwargs = calls[1]
        assert args == ("select", "select_option", {"entity_id": "select.solax_inverter_mode", "option": "Manual Mode"})

        # 3. Force Charge
        args, kwargs = calls[2]
        assert args == ("select", "select_option", {"entity_id": "select.solax_charger_use_mode", "option": "Force Charge"})

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

        # 2. Reset SOC limit to 100
        args, kwargs = calls[1]
        assert args == ("number", "set_value", {"entity_id": "number.solax_charge_soc_limit", "value": 100})

        # 3. Self Use Mode
        args, kwargs = calls[2]
        assert args == ("select", "select_option", {"entity_id": "select.solax_inverter_mode", "option": "Self Use Mode"})

        # 4. Discharge min SOC
        args, kwargs = calls[3]
        assert args == ("number", "set_value", {"entity_id": "number.solax_discharge_min_soc", "value": 20.0})

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

    def test_ems_matches_charge_mode(self, ems_controller):
        assert ems_controller.is_manual_mode("771") is True

    def test_ems_no_match(self, ems_controller):
        assert ems_controller.is_manual_mode("257") is False


class TestCommandVerification:
    """Test that commands verify the result (Fix 4)."""

    @pytest.mark.asyncio
    async def test_start_charging_returns_true_on_success(self, controller, hass):
        """Returns True when mode confirms manual."""
        state_obj = MagicMock()
        state_obj.state = "Manual Mode"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await controller.async_start_charging(90.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_start_charging_returns_false_on_failure(self, controller, hass):
        """Returns False when mode is not manual after command."""
        state_obj = MagicMock()
        state_obj.state = "Self Use Mode"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await controller.async_start_charging(90.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_charging_returns_true_on_success(self, controller, hass):
        """Returns True when mode confirms self-use after stop."""
        state_obj = MagicMock()
        state_obj.state = "Self Use Mode"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await controller.async_stop_charging(20.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_stop_charging_returns_false_if_still_manual(self, controller, hass):
        """Returns False when still in manual mode after stop command."""
        state_obj = MagicMock()
        state_obj.state = "Manual Mode"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await controller.async_stop_charging(20.0)

        assert result is False


class TestModbusTimeout:
    """Test Modbus call timeout handling (C2)."""

    @pytest.mark.asyncio
    async def test_timeout_on_service_call_returns_false(self, controller, hass):
        """Service call timeout → InverterCommandError → returns False."""
        hass.services.async_call = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await controller.async_start_charging(90.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_on_stop_returns_false(self, controller, hass):
        """Stop charging timeout → returns False."""
        hass.services.async_call = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await controller.async_stop_charging(20.0)

        assert result is False


class TestEMSControl:
    """Test EMS power-based control (Wattsonic)."""

    @pytest.mark.asyncio
    async def test_ems_start_charging(self, ems_controller, hass):
        """EMS start: set working mode, battery power, AC limit."""
        # Mock state to confirm mode set
        state_obj = MagicMock()
        state_obj.state = "771"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await ems_controller.async_start_charging(80.0)

        assert result is True
        calls = hass.services.async_call.call_args_list
        # Should have: set working mode, set battery power, set AC lower limit
        assert len(calls) == 3

        # 1. Working mode = 771
        args, _ = calls[0]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_working_mode", "value": 771})

        # 2. Battery power = -5000 (5kW charge, negative)
        args, _ = calls[1]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_battery_power", "value": -5000.0})

        # 3. AC lower limit = -5000
        args, _ = calls[2]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_ac_lower_limit", "value": -5000.0})

    @pytest.mark.asyncio
    async def test_ems_stop_charging(self, ems_controller, hass):
        """EMS stop: set power=0, restore general mode, set DOD."""
        # Mock state to confirm mode restored
        state_obj = MagicMock()
        state_obj.state = "257"
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await ems_controller.async_stop_charging(20.0)

        assert result is True
        calls = hass.services.async_call.call_args_list
        # Should have: set power=0, set working mode=257, set DOD
        assert len(calls) == 3

        # 1. Battery power = 0
        args, _ = calls[0]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_battery_power", "value": 0})

        # 2. Working mode = 257 (General Mode)
        args, _ = calls[1]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_working_mode", "value": 257})

        # 3. DOD = 80% (100 - 20% min_soc)
        args, _ = calls[2]
        assert args == ("number", "set_value", {"entity_id": "number.wattsonic_battery_dod", "value": 80.0})

    @pytest.mark.asyncio
    async def test_ems_get_current_mode(self, ems_controller, hass):
        """EMS get_current_mode reads number state as int string."""
        state_obj = MagicMock()
        state_obj.state = "771.0"
        hass.states.get.return_value = state_obj

        mode = await ems_controller.async_get_current_mode()
        assert mode == "771"

    @pytest.mark.asyncio
    async def test_ems_start_returns_false_on_wrong_mode(self, ems_controller, hass):
        """EMS start returns False when mode doesn't confirm."""
        state_obj = MagicMock()
        state_obj.state = "257"  # Still in General Mode
        hass.states.get.return_value = state_obj

        with patch(
            "smart_battery_charging.inverter_controller.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await ems_controller.async_start_charging(80.0)

        assert result is False
