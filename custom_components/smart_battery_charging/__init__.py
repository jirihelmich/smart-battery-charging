"""Smart Battery Charging integration for Home Assistant.

Phase 1: Read-only sensors, no inverter control.
Computes and displays charging-related values using data from
user-configured inverter, price, solar, and consumption sensors.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import SmartBatteryCoordinator
from .storage import SmartBatteryStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Charging from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Initialize storage
    store = SmartBatteryStore(hass, entry.entry_id)
    await store.async_load()

    # Create coordinator
    coordinator = SmartBatteryCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Smart Battery Charging setup complete for %s", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry — clean up storage."""
    store = SmartBatteryStore(hass, entry.entry_id)
    await store.async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
