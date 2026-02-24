"""Switch platform for Smart Battery Charging."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartBatteryCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Battery Charging switch."""
    coordinator: SmartBatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SmartBatterySwitch(coordinator, entry)])


class SmartBatterySwitch(SwitchEntity):
    """Master enable/disable switch for Smart Battery Charging."""

    _attr_has_entity_name = True
    _attr_translation_key = "enabled"
    _attr_icon = "mdi:power"

    def __init__(
        self,
        coordinator: SmartBatteryCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Smart Battery Charging",
            "model": "Virtual",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the switch."""
        self.coordinator.enabled = True
        if self.coordinator.state_machine is not None:
            await self.coordinator.state_machine.async_on_enable()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the switch."""
        self.coordinator.enabled = False
        if self.coordinator.state_machine is not None:
            await self.coordinator.state_machine.async_on_disable()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
