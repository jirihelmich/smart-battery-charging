"""JSON-based persistent storage for Smart Battery Charging.

Replaces the comma-separated input_text hack from the YAML version with
proper structured JSON stored in HA's .storage directory.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CHARGE_HISTORY_DAYS, CONSUMPTION_WINDOW_DAYS, DOMAIN, FORECAST_ERROR_WINDOW_DAYS
from .models import ChargingSession

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN


def _default_data() -> dict[str, Any]:
    """Return default storage data."""
    return {
        "consumption_history": [],
        "charge_history": [],
        "forecast_error_history": [],
        "last_session": None,
        "enabled": True,
        "charging_state": "idle",
        "current_schedule": None,
    }


class SmartBatteryStore:
    """Manages persistent storage for the integration."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._data: dict[str, Any] = _default_data()

    async def async_load(self) -> None:
        """Load data from storage."""
        stored = await self._store.async_load()
        if stored:
            self._data = {**_default_data(), **stored}
        else:
            self._data = _default_data()
        _LOGGER.debug("Loaded storage data: %s entries", len(self._data))

    async def async_save(self) -> None:
        """Save data to storage."""
        await self._store.async_save(self._data)

    # --- Consumption History ---

    @property
    def consumption_history(self) -> list[float]:
        """Return daily consumption history (most recent first)."""
        return list(self._data.get("consumption_history", []))

    async def async_set_consumption_history(self, history: list[float]) -> None:
        """Set the full consumption history and persist."""
        self._data["consumption_history"] = history[:CONSUMPTION_WINDOW_DAYS]
        await self.async_save()

    # --- Charge History ---

    @property
    def charge_history(self) -> list[float]:
        """Return daily charge history in kWh (most recent first)."""
        return list(self._data.get("charge_history", []))

    async def async_set_charge_history(self, history: list[float]) -> None:
        """Set the full charge history and persist."""
        self._data["charge_history"] = history[:CHARGE_HISTORY_DAYS]
        await self.async_save()

    # --- Forecast Error History ---

    @property
    def forecast_error_history(self) -> list[float]:
        """Return forecast error history (most recent first)."""
        return list(self._data.get("forecast_error_history", []))

    async def async_set_forecast_error_history(self, history: list[float]) -> None:
        """Set the full forecast error history and persist."""
        self._data["forecast_error_history"] = history[:FORECAST_ERROR_WINDOW_DAYS]
        await self.async_save()

    # --- Last Session ---

    @property
    def last_session(self) -> ChargingSession | None:
        """Return the last charging session."""
        data = self._data.get("last_session")
        if not data:
            return None
        return ChargingSession(
            start_soc=data.get("start_soc", 0.0),
            end_soc=data.get("end_soc", 0.0),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            avg_price=data.get("avg_price", 0.0),
            result=data.get("result", ""),
        )

    async def async_set_last_session(self, session: ChargingSession) -> None:
        """Set the last charging session and persist."""
        self._data["last_session"] = {
            "start_soc": session.start_soc,
            "end_soc": session.end_soc,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "avg_price": session.avg_price,
            "result": session.result,
        }
        await self.async_save()

    # --- Enabled State ---

    @property
    def enabled(self) -> bool:
        """Return whether charging is enabled."""
        return bool(self._data.get("enabled", True))

    async def async_set_enabled(self, value: bool) -> None:
        """Set the enabled state and persist."""
        self._data["enabled"] = value
        await self.async_save()

    # --- Charging State (C1) ---

    @property
    def charging_state(self) -> str:
        """Return the persisted charging state string."""
        return str(self._data.get("charging_state", "idle"))

    async def async_set_charging_state(self, state: str) -> None:
        """Set the charging state and persist."""
        self._data["charging_state"] = state
        await self.async_save()

    # --- Current Schedule (C1) ---

    @property
    def current_schedule(self) -> dict[str, Any] | None:
        """Return the persisted schedule dict (or None)."""
        return self._data.get("current_schedule")

    async def async_set_current_schedule(self, schedule_dict: dict[str, Any] | None) -> None:
        """Set the current schedule dict and persist."""
        self._data["current_schedule"] = schedule_dict
        await self.async_save()

    async def async_remove(self) -> None:
        """Remove the storage file."""
        await self._store.async_remove()
