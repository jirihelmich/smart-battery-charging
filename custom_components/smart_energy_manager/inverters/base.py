"""Base inverter controller — ABC for all inverter implementations.

All inverter interactions (mode changes, charge commands, SOC limits) go through
subclasses of this base, making them easy to mock and test.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import MODBUS_CALL_TIMEOUT

_LOGGER = logging.getLogger(__name__)

MODBUS_SETTLE_DELAY = 5  # seconds between Modbus writes


class InverterCommandError(Exception):
    """Raised when an inverter Modbus command fails."""


class BaseInverterController(ABC):
    """Abstract gateway for all Modbus commands via HA service calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
    ) -> None:
        self._hass = hass
        self._config = config

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

    # --- Public API (abstract) ---

    @abstractmethod
    async def async_start_charging(self, target_soc: float) -> bool:
        """Start force-charging the battery. Returns True on success."""

    @abstractmethod
    async def async_stop_charging(self, min_soc: float) -> bool:
        """Stop charging and restore normal mode. Returns True on success."""

    @abstractmethod
    async def async_get_current_mode(self) -> str:
        """Read the current inverter mode."""

    @abstractmethod
    def is_manual_mode(self, mode_str: str) -> bool:
        """Check if the given mode string matches the charging mode."""
