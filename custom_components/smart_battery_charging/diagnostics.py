"""Diagnostics support for Smart Battery Charging."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SmartBatteryCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SmartBatteryCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Redact entity IDs partially for privacy
    config_data = dict(entry.data)
    for key in list(config_data.keys()):
        if "sensor" in key or "select" in key or "number" in key:
            val = config_data[key]
            if isinstance(val, str):
                config_data[key] = f"***{val[-20:]}" if len(val) > 20 else val

    return {
        "config": config_data,
        "options": dict(entry.options),
        "coordinator_data": coordinator.data if coordinator.data else {},
        "store": {
            "consumption_history": coordinator.store.consumption_history,
            "charge_history": coordinator.store.charge_history,
            "forecast_error_history": coordinator.store.forecast_error_history,
            "last_session": (
                {
                    "start_soc": coordinator.store.last_session.start_soc,
                    "end_soc": coordinator.store.last_session.end_soc,
                    "result": coordinator.store.last_session.result,
                }
                if coordinator.store.last_session
                else None
            ),
        },
    }
