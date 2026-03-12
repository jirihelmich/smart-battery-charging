"""Select-based inverter control mixin.

Shared by Solax, SolarEdge, Huawei, and Custom inverters that use
HA select entities for mode switching and charge commands.
"""

from __future__ import annotations

import asyncio
import logging

from ..const import (
    CONF_CHARGE_FORCE,
    CONF_CHARGE_STOP,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
)
from .base import MODBUS_SETTLE_DELAY, InverterCommandError

_LOGGER = logging.getLogger(__name__)


class SelectInverterMixin:
    """Mixin providing select-based inverter control."""

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

    # --- Public API implementations ---

    async def async_start_charging(self, target_soc: float) -> bool:
        """Start force-charging via select entities. Returns True on success."""
        try:
            return await self._select_start_charging(target_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during start_charging")
            return False

    async def async_stop_charging(self, min_soc: float) -> bool:
        """Stop charging via select entities. Returns True on success."""
        try:
            return await self._select_stop_charging(min_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during stop_charging")
            return False

    async def async_get_current_mode(self) -> str:
        """Read the current inverter mode from the select entity."""
        state = self._hass.states.get(self.mode_select_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return ""
        return str(state.state)

    def is_manual_mode(self, mode_str: str) -> bool:
        """Check if the given mode string matches the manual/charging mode."""
        return mode_str == self.mode_manual

    # --- Internal implementation ---

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
