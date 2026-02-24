"""Binary sensor platform for Smart Battery Charging."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartBatteryCoordinator


@dataclass(frozen=True, kw_only=True)
class SmartBatteryBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Smart Battery Charging binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSOR_DESCRIPTIONS: tuple[SmartBatteryBinarySensorDescription, ...] = (
    SmartBatteryBinarySensorDescription(
        key="charging_active",
        translation_key="charging_active",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: d.get("charging_active", False),
    ),
    SmartBatteryBinarySensorDescription(
        key="charging_recommended",
        translation_key="charging_recommended",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda d: d.get("charging_recommended", False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Battery Charging binary sensors."""
    coordinator: SmartBatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SmartBatteryBinarySensor(coordinator, description, entry)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class SmartBatteryBinarySensor(
    CoordinatorEntity[SmartBatteryCoordinator], BinarySensorEntity
):
    """A Smart Battery Charging binary sensor."""

    entity_description: SmartBatteryBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartBatteryCoordinator,
        description: SmartBatteryBinarySensorDescription,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Smart Battery Charging",
            "model": "Virtual",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        if self.coordinator.data is None:
            return False
        return self.entity_description.value_fn(self.coordinator.data)
