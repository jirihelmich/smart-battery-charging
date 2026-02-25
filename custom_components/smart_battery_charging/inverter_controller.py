"""Inverter controller — the ONLY class that calls HA services for Modbus commands.

All inverter interactions (mode changes, charge commands, SOC limits) go through
this single gateway, making them easy to mock and test.

Supports two control types:
- "select": Traditional select-based control (Solax, SolarEdge, Huawei)
- "ems_power": EMS battery power control via number entities (Wattsonic)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHARGE_FORCE,
    CONF_CHARGE_STOP,
    CONF_EMS_CHARGE_MODE_VALUE,
    CONF_EMS_NORMAL_MODE_VALUE,
    CONF_INVERTER_AC_LOWER_LIMIT_NUMBER,
    CONF_INVERTER_BATTERY_DOD_NUMBER,
    CONF_INVERTER_BATTERY_POWER_NUMBER,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_INVERTER_WORKING_MODE_NUMBER,
    CONF_MAX_CHARGE_POWER,
    CONF_MIN_SOC,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
    CONTROL_TYPE_EMS_POWER,
    CONTROL_TYPE_SELECT,
    MODBUS_CALL_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

MODBUS_SETTLE_DELAY = 5  # seconds between Modbus writes


class InverterCommandError(Exception):
    """Raised when an inverter Modbus command fails."""


class InverterController:
    """Single gateway for all Modbus commands via HA service calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        control_type: str = CONTROL_TYPE_SELECT,
    ) -> None:
        self._hass = hass
        self._config = config
        self._control_type = control_type

    # --- Config accessors (select-based) ---

    @property
    def mode_select_entity(self) -> str:
        return self._config.get(CONF_INVERTER_MODE_SELECT, "")

    @property
    def charge_command_entity(self) -> str:
        return self._config.get(CONF_INVERTER_CHARGE_COMMAND_SELECT, "")

    @property
    def soc_limit_entity(self) -> str:
        return self._config.get(CONF_INVERTER_CHARGE_SOC_LIMIT, "")

    @property
    def discharge_min_soc_entity(self) -> str:
        return self._config.get(CONF_INVERTER_DISCHARGE_MIN_SOC, "")

    @property
    def mode_self_use(self) -> str:
        return self._config.get(CONF_MODE_SELF_USE, "Self Use Mode")

    @property
    def mode_manual(self) -> str:
        return self._config.get(CONF_MODE_MANUAL, "Manual Mode")

    @property
    def charge_force(self) -> str:
        return self._config.get(CONF_CHARGE_FORCE, "Force Charge")

    @property
    def charge_stop(self) -> str:
        return self._config.get(CONF_CHARGE_STOP, "Stop Charge and Discharge")

    # --- Config accessors (EMS power-based / Wattsonic) ---

    @property
    def working_mode_entity(self) -> str:
        return self._config.get(CONF_INVERTER_WORKING_MODE_NUMBER, "")

    @property
    def battery_power_entity(self) -> str:
        return self._config.get(CONF_INVERTER_BATTERY_POWER_NUMBER, "")

    @property
    def ac_lower_limit_entity(self) -> str:
        return self._config.get(CONF_INVERTER_AC_LOWER_LIMIT_NUMBER, "")

    @property
    def battery_dod_entity(self) -> str:
        return self._config.get(CONF_INVERTER_BATTERY_DOD_NUMBER, "")

    @property
    def ems_charge_mode_value(self) -> int:
        return int(self._config.get(CONF_EMS_CHARGE_MODE_VALUE, 771))

    @property
    def ems_normal_mode_value(self) -> int:
        return int(self._config.get(CONF_EMS_NORMAL_MODE_VALUE, 257))

    # --- Service call helpers with timeout (C2) ---

    async def _set_select(self, entity_id: str, option: str) -> None:
        """Call select.select_option service with timeout."""
        _LOGGER.debug("Setting %s to %s", entity_id, option)
        try:
            await asyncio.wait_for(
                self._hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": entity_id, "option": option},
                    blocking=True,
                ),
                timeout=MODBUS_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout setting %s to %s after %ds", entity_id, option, MODBUS_CALL_TIMEOUT)
            raise InverterCommandError(f"Timeout setting {entity_id}") from None

    async def _set_number(self, entity_id: str, value: float) -> None:
        """Call number.set_value service with timeout."""
        _LOGGER.debug("Setting %s to %s", entity_id, value)
        try:
            await asyncio.wait_for(
                self._hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                ),
                timeout=MODBUS_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout setting %s to %s after %ds", entity_id, value, MODBUS_CALL_TIMEOUT)
            raise InverterCommandError(f"Timeout setting {entity_id}") from None

    def _get_number_state(self, entity_id: str) -> float | None:
        """Read the current numeric state of an entity."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    # --- Public API ---

    async def async_start_charging(self, target_soc: float) -> bool:
        """Start force-charging the battery. Returns True on success."""
        try:
            if self._control_type == CONTROL_TYPE_EMS_POWER:
                return await self._ems_start_charging(target_soc)
            return await self._select_start_charging(target_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during start_charging")
            return False

    async def async_stop_charging(self, min_soc: float) -> bool:
        """Stop charging and restore normal mode. Returns True on success."""
        try:
            if self._control_type == CONTROL_TYPE_EMS_POWER:
                return await self._ems_stop_charging(min_soc)
            return await self._select_stop_charging(min_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during stop_charging")
            return False

    async def async_get_current_mode(self) -> str:
        """Read the current inverter mode."""
        if self._control_type == CONTROL_TYPE_EMS_POWER:
            val = self._get_number_state(self.working_mode_entity)
            return str(int(val)) if val is not None else ""
        state = self._hass.states.get(self.mode_select_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return ""
        return str(state.state)

    def is_manual_mode(self, mode_str: str) -> bool:
        """Check if the given mode string matches the charging mode."""
        if self._control_type == CONTROL_TYPE_EMS_POWER:
            try:
                return int(float(mode_str)) == self.ems_charge_mode_value
            except (ValueError, TypeError):
                return False
        return mode_str == self.mode_manual

    # --- Select-based control (Solax/SolarEdge/Huawei) ---

    async def _select_start_charging(self, target_soc: float) -> bool:
        """Start charging via select entities."""
        _LOGGER.info("Starting charge, target SOC: %.0f%%", target_soc)

        # Set charge SOC limit
        await self._set_number(self.soc_limit_entity, target_soc)

        # Switch to Manual Mode
        await self._set_select(self.mode_select_entity, self.mode_manual)

        # Wait for Modbus to settle
        await asyncio.sleep(MODBUS_SETTLE_DELAY)

        # Issue Force Charge command
        await self._set_select(self.charge_command_entity, self.charge_force)

        # Verify mode switch
        current_mode = await self.async_get_current_mode()
        if not self.is_manual_mode(current_mode):
            _LOGGER.warning(
                "Charge command sent but inverter mode is '%s', expected '%s'",
                current_mode, self.mode_manual,
            )
            return False

        _LOGGER.info("Charge started successfully")
        return True

    async def _select_stop_charging(self, min_soc: float) -> bool:
        """Stop charging via select entities."""
        _LOGGER.info("Stopping charge, restoring Self Use mode")

        # Stop charging
        await self._set_select(self.charge_command_entity, self.charge_stop)

        # Wait for Modbus to settle
        await asyncio.sleep(MODBUS_SETTLE_DELAY)

        # Reset SOC limit to 100% (allow full charge from solar)
        await self._set_number(self.soc_limit_entity, 100)

        # Restore Self Use Mode
        await self._set_select(self.mode_select_entity, self.mode_self_use)

        # Optionally set discharge min SOC to prevent deep discharge
        if self.discharge_min_soc_entity:
            _LOGGER.info(
                "Setting discharge min SOC to %.0f%% on %s",
                min_soc, self.discharge_min_soc_entity,
            )
            await self._set_number(self.discharge_min_soc_entity, min_soc)
            # Verify discharge min SOC was set
            state = self._hass.states.get(self.discharge_min_soc_entity)
            if state is not None and state.state not in ("unknown", "unavailable"):
                try:
                    actual = float(state.state)
                    if abs(actual - min_soc) > 1.0:
                        _LOGGER.warning(
                            "Discharge min SOC read-back mismatch: expected %.0f%%, got %.0f%%",
                            min_soc, actual,
                        )
                except (ValueError, TypeError):
                    pass

        # Verify mode restored
        current_mode = await self.async_get_current_mode()
        if self.is_manual_mode(current_mode):
            _LOGGER.warning(
                "Stop command sent but inverter still in '%s', expected '%s'",
                current_mode, self.mode_self_use,
            )
            return False

        _LOGGER.info("Self Use mode restored")
        return True

    # --- EMS power-based control (Wattsonic) ---

    async def _ems_start_charging(self, target_soc: float) -> bool:
        """Start charging via EMS battery power control (Wattsonic)."""
        _LOGGER.info("EMS: Starting charge, target SOC: %.0f%%", target_soc)

        # Set working mode to EMS Battery Control
        await self._set_number(self.working_mode_entity, self.ems_charge_mode_value)

        await asyncio.sleep(MODBUS_SETTLE_DELAY)

        # Set battery power to charge (negative = charge in Wattsonic convention)
        charge_power_kw = float(self._config.get(CONF_MAX_CHARGE_POWER, 5.0))
        charge_power_w = charge_power_kw * -1000
        await self._set_number(self.battery_power_entity, charge_power_w)

        # Set AC lower limit to allow grid purchasing
        await self._set_number(self.ac_lower_limit_entity, charge_power_w)

        # Verify working mode
        actual = self._get_number_state(self.working_mode_entity)
        if actual is None or int(actual) != self.ems_charge_mode_value:
            _LOGGER.warning(
                "EMS: Mode is %s, expected %s", actual, self.ems_charge_mode_value,
            )
            return False

        _LOGGER.info("EMS: Charge started successfully")
        return True

    async def _ems_stop_charging(self, min_soc: float) -> bool:
        """Stop charging via EMS control and restore General Mode (Wattsonic)."""
        _LOGGER.info("EMS: Stopping charge, restoring General Mode")

        # Set battery power to 0 (stop force charge)
        await self._set_number(self.battery_power_entity, 0)

        await asyncio.sleep(MODBUS_SETTLE_DELAY)

        # Restore General Mode
        await self._set_number(self.working_mode_entity, self.ems_normal_mode_value)

        # Set battery DOD to match min_soc (Wattsonic uses DOD% = 100 - min_soc)
        if self.battery_dod_entity:
            dod_pct = 100.0 - min_soc
            _LOGGER.info("EMS: Setting battery DOD to %.0f%% on %s", dod_pct, self.battery_dod_entity)
            await self._set_number(self.battery_dod_entity, dod_pct)

        # Verify mode restored
        actual = self._get_number_state(self.working_mode_entity)
        if actual is not None and int(actual) == self.ems_charge_mode_value:
            _LOGGER.warning(
                "EMS: Still in charge mode %s, expected %s",
                actual, self.ems_normal_mode_value,
            )
            return False

        _LOGGER.info("EMS: General Mode restored")
        return True
