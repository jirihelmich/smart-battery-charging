"""Number platform for Smart Battery Charging.

Each number entity is both a live setting and synced with the config entry options,
so changes from the dashboard/automations are persisted and used by the coordinator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_FALLBACK_CONSUMPTION,
    CONF_MAX_CHARGE_LEVEL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_CHARGE_PRICE,
    CONF_MIN_SOC,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_MAX_CHARGE_LEVEL,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_CHARGE_PRICE,
    DEFAULT_MIN_SOC,
    DOMAIN,
)
from .coordinator import SmartBatteryCoordinator


@dataclass(frozen=True, kw_only=True)
class SmartBatteryNumberDescription(NumberEntityDescription):
    """Describe a Smart Battery Charging number entity."""

    config_key: str
    default_value: float
    getter: Callable[[SmartBatteryCoordinator], float]
    setter: Callable[[SmartBatteryCoordinator, float], None]


NUMBER_DESCRIPTIONS: tuple[SmartBatteryNumberDescription, ...] = (
    SmartBatteryNumberDescription(
        key="battery_capacity",
        translation_key="battery_capacity",
        icon="mdi:battery",
        native_min_value=5.0,
        native_max_value=50.0,
        native_step=0.5,
        native_unit_of_measurement="kWh",
        mode=NumberMode.BOX,
        config_key=CONF_BATTERY_CAPACITY,
        default_value=DEFAULT_BATTERY_CAPACITY,
        getter=lambda c: c.battery_capacity,
        setter=lambda c, v: setattr(c, "battery_capacity", v),
    ),
    SmartBatteryNumberDescription(
        key="max_charge_level",
        translation_key="max_charge_level",
        icon="mdi:battery-90",
        native_min_value=50.0,
        native_max_value=100.0,
        native_step=1.0,
        native_unit_of_measurement="%",
        mode=NumberMode.SLIDER,
        config_key=CONF_MAX_CHARGE_LEVEL,
        default_value=DEFAULT_MAX_CHARGE_LEVEL,
        getter=lambda c: c.max_charge_level,
        setter=lambda c, v: setattr(c, "max_charge_level", v),
    ),
    SmartBatteryNumberDescription(
        key="min_soc",
        translation_key="min_soc",
        icon="mdi:battery-20",
        native_min_value=0.0,
        native_max_value=50.0,
        native_step=1.0,
        native_unit_of_measurement="%",
        mode=NumberMode.SLIDER,
        config_key=CONF_MIN_SOC,
        default_value=DEFAULT_MIN_SOC,
        getter=lambda c: c.min_soc,
        setter=lambda c, v: setattr(c, "min_soc", v),
    ),
    SmartBatteryNumberDescription(
        key="max_charge_power",
        translation_key="max_charge_power",
        icon="mdi:lightning-bolt",
        native_min_value=1.0,
        native_max_value=20.0,
        native_step=0.5,
        native_unit_of_measurement="kW",
        mode=NumberMode.BOX,
        config_key=CONF_MAX_CHARGE_POWER,
        default_value=DEFAULT_MAX_CHARGE_POWER,
        getter=lambda c: c.max_charge_power,
        setter=lambda c, v: setattr(c, "max_charge_power", v),
    ),
    SmartBatteryNumberDescription(
        key="max_charge_price",
        translation_key="max_charge_price",
        icon="mdi:cash",
        native_min_value=0.0,
        native_max_value=20.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        config_key=CONF_MAX_CHARGE_PRICE,
        default_value=DEFAULT_MAX_CHARGE_PRICE,
        getter=lambda c: c.max_charge_price,
        setter=lambda c, v: setattr(c, "max_charge_price", v),
    ),
    SmartBatteryNumberDescription(
        key="fallback_consumption",
        translation_key="fallback_consumption",
        icon="mdi:home-lightning-bolt",
        native_min_value=5.0,
        native_max_value=50.0,
        native_step=0.5,
        native_unit_of_measurement="kWh",
        mode=NumberMode.BOX,
        config_key=CONF_FALLBACK_CONSUMPTION,
        default_value=DEFAULT_FALLBACK_CONSUMPTION,
        getter=lambda c: c.fallback_consumption,
        setter=lambda c, v: setattr(c, "fallback_consumption", v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Battery Charging number entities."""
    coordinator: SmartBatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SmartBatteryNumber(coordinator, description, entry)
        for description in NUMBER_DESCRIPTIONS
    )


class SmartBatteryNumber(NumberEntity):
    """A Smart Battery Charging number entity backed by config options."""

    entity_description: SmartBatteryNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartBatteryCoordinator,
        description: SmartBatteryNumberDescription,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Smart Battery Charging",
            "model": "Virtual",
        }
        # Set unit for price from config
        if description.key == "max_charge_price":
            self._attr_native_unit_of_measurement = coordinator.currency

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.entity_description.getter(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        self.entity_description.setter(self.coordinator, value)
        await self.coordinator.async_request_refresh()
