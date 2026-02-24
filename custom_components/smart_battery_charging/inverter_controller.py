"""Inverter controller — the ONLY class that calls HA services for Modbus commands.

All inverter interactions (mode changes, charge commands, SOC limits) go through
this single gateway, making them easy to mock and test.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHARGE_FORCE,
    CONF_CHARGE_STOP,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
)

_LOGGER = logging.getLogger(__name__)

MODBUS_SETTLE_DELAY = 5  # seconds between Modbus writes


class InverterController:
    """Single gateway for all Modbus commands via HA service calls."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self._hass = hass
        self._config = config

    # --- Config accessors ---

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

    # --- Service call helpers ---

    async def _set_select(self, entity_id: str, option: str) -> None:
        """Call select.select_option service."""
        _LOGGER.debug("Setting %s to %s", entity_id, option)
        await self._hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )

    async def _set_number(self, entity_id: str, value: float) -> None:
        """Call number.set_value service."""
        _LOGGER.debug("Setting %s to %s", entity_id, value)
        await self._hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=True,
        )

    # --- Public API ---

    async def async_start_charging(self, target_soc: float) -> None:
        """Start force-charging the battery.

        Sequence: Set SOC limit → Manual Mode → 5s delay → Force Charge
        """
        _LOGGER.info("Starting charge, target SOC: %.0f%%", target_soc)

        # Set charge SOC limit
        await self._set_number(self.soc_limit_entity, target_soc)

        # Switch to Manual Mode
        await self._set_select(self.mode_select_entity, self.mode_manual)

        # Wait for Modbus to settle
        await asyncio.sleep(MODBUS_SETTLE_DELAY)

        # Issue Force Charge command
        await self._set_select(self.charge_command_entity, self.charge_force)

        _LOGGER.info("Charge started successfully")

    async def async_stop_charging(self, min_soc: float) -> None:
        """Stop charging and restore Self Use mode.

        Sequence: Stop Charge → 5s delay → Reset SOC limit → Self Use Mode
                  → (optional) set discharge min SOC
        """
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
            await self._set_number(self.discharge_min_soc_entity, min_soc)

        _LOGGER.info("Self Use mode restored")

    async def async_get_current_mode(self) -> str:
        """Read the current inverter mode from the select entity state."""
        state = self._hass.states.get(self.mode_select_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return ""
        return str(state.state)

    def is_manual_mode(self, mode_str: str) -> bool:
        """Check if the given mode string matches Manual Mode."""
        return mode_str == self.mode_manual
