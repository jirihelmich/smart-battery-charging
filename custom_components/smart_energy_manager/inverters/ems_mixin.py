"""EMS power-based inverter control mixin.

Used by Wattsonic GEN2 and similar inverters that use number entities
for EMS battery power control via Modbus registers.
"""

from __future__ import annotations

import asyncio
import logging

from ..const import (
    CONF_EMS_CHARGE_MODE_VALUE,
    CONF_EMS_NORMAL_MODE_VALUE,
    CONF_INVERTER_AC_LOWER_LIMIT_NUMBER,
    CONF_INVERTER_BATTERY_DOD_NUMBER,
    CONF_INVERTER_BATTERY_POWER_NUMBER,
    CONF_INVERTER_WORKING_MODE_NUMBER,
    CONF_MAX_CHARGE_POWER,
)
from .base import MODBUS_SETTLE_DELAY, InverterCommandError

_LOGGER = logging.getLogger(__name__)


class EmsInverterMixin:
    """Mixin providing EMS power-based inverter control."""

    # --- Config accessors ---

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

    # --- Public API implementations ---

    async def async_start_charging(self, target_soc: float) -> bool:
        """Start force-charging via EMS power control. Returns True on success."""
        try:
            return await self._ems_start_charging(target_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during start_charging")
            return False

    async def async_stop_charging(self, min_soc: float) -> bool:
        """Stop charging via EMS control. Returns True on success."""
        try:
            return await self._ems_stop_charging(min_soc)
        except InverterCommandError:
            _LOGGER.error("Inverter command failed during stop_charging")
            return False

    async def async_get_current_mode(self) -> str:
        """Read the current working mode as a string."""
        val = self._get_number_state(self.working_mode_entity)
        return str(int(val)) if val is not None else ""

    def is_manual_mode(self, mode_str: str) -> bool:
        """Check if the given mode string matches the EMS charge mode."""
        try:
            return int(float(mode_str)) == self.ems_charge_mode_value
        except (ValueError, TypeError):
            return False

    # --- Internal implementation ---

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
