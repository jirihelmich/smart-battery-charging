"""Sensor platform for Smart Battery Charging."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartBatteryCoordinator


@dataclass(frozen=True, kw_only=True)
class SmartBatterySensorDescription(SensorEntityDescription):
    """Describe a Smart Battery Charging sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _cheapest_hours_str(slots: list) -> str:
    """Format cheapest hours as comma-separated string."""
    if not slots:
        return "Not available"
    return ", ".join(str(s.hour) for s in slots[:3])


def _cheapest_price_attr(slots: list, currency: str) -> str:
    if not slots:
        return "Unknown"
    return f"{slots[0].price:.2f} {currency}"


SENSOR_DESCRIPTIONS: tuple[SmartBatterySensorDescription, ...] = (
    SmartBatterySensorDescription(
        key="average_daily_consumption",
        translation_key="average_daily_consumption",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["average_daily_consumption"],
        attrs_fn=lambda d: {
            "days_tracked": d["consumption_days_tracked"],
            "source": d["consumption_source"],
            "history": d["consumption_history_raw"],
        },
    ),
    SmartBatterySensorDescription(
        key="today_solar_forecast",
        translation_key="today_solar_forecast",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["today_solar_forecast"],
    ),
    SmartBatterySensorDescription(
        key="tomorrow_solar_forecast",
        translation_key="tomorrow_solar_forecast",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["tomorrow_solar_forecast"],
    ),
    SmartBatterySensorDescription(
        key="solar_forecast_error_average",
        translation_key="solar_forecast_error_average",
        native_unit_of_measurement="%",
        value_fn=lambda d: d["solar_forecast_error_average"],
        attrs_fn=lambda d: {
            "days_tracked": d["forecast_error_days_tracked"],
            "raw_factor": d["solar_forecast_error_ratio"],
            "history": d["forecast_error_history_raw"],
        },
    ),
    SmartBatterySensorDescription(
        key="today_solar_forecast_error",
        translation_key="today_solar_forecast_error",
        native_unit_of_measurement="%",
        value_fn=lambda d: d["today_solar_forecast_error"],
        attrs_fn=lambda d: {
            "forecast": d["today_solar_forecast"],
            "actual": d["actual_solar_today"],
        },
    ),
    SmartBatterySensorDescription(
        key="tomorrow_energy_forecast",
        translation_key="tomorrow_energy_forecast",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["tomorrow_energy_forecast"],
        attrs_fn=lambda d: {
            "solar_raw": d["solar_raw_tomorrow"],
            "solar_adjusted": d["solar_adjusted_tomorrow"],
            "forecast_error_pct": d["forecast_error_pct_used"],
            "consumption_estimate": d["consumption_estimate_used"],
            "deficit_or_surplus": "Surplus" if d["tomorrow_energy_forecast"] >= 0 else "Deficit",
        },
    ),
    SmartBatterySensorDescription(
        key="battery_charge_kwh",
        translation_key="battery_charge_kwh",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["battery_charge_kwh"],
    ),
    SmartBatterySensorDescription(
        key="battery_usable_charge",
        translation_key="battery_usable_charge",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["battery_usable_charge"],
        attrs_fn=lambda d: {
            "total_usable_capacity": f"{d['usable_capacity_total']} kWh",
        },
    ),
    SmartBatterySensorDescription(
        key="battery_capacity_to_max",
        translation_key="battery_capacity_to_max",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["battery_capacity_to_max"],
        attrs_fn=lambda d: {
            "usable_capacity_total": f"{d['usable_capacity_total']} kWh",
        },
    ),
    SmartBatterySensorDescription(
        key="night_charging_status",
        translation_key="night_charging_status",
        icon="mdi:battery-clock",
        value_fn=lambda d: d["night_charging_status"],
        attrs_fn=lambda d: {
            "schedule_start": d["schedule"].start_hour if d.get("schedule") else None,
            "schedule_end": d["schedule"].end_hour if d.get("schedule") else None,
            "charge_needed": d["charge_needed"],
            "battery_soc": d["battery_soc"],
        },
    ),
    SmartBatterySensorDescription(
        key="last_night_charge_kwh",
        translation_key="last_night_charge_kwh",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d["last_night_charge_kwh"],
        attrs_fn=lambda d: {
            "start_soc": f"{d['last_session'].start_soc:.1f}%" if d.get("last_session") else "N/A",
            "end_soc": f"{d['last_session'].end_soc:.1f}%" if d.get("last_session") else "N/A",
            "result": d["last_charge_result"],
            "history": d["charge_history_raw"],
        },
    ),
    SmartBatterySensorDescription(
        key="last_charge_battery_range",
        translation_key="last_charge_battery_range",
        icon="mdi:battery-arrow-up-outline",
        value_fn=lambda d: d["last_charge_battery_range"],
    ),
    SmartBatterySensorDescription(
        key="last_charge_time_range",
        translation_key="last_charge_time_range",
        icon="mdi:clock-outline",
        value_fn=lambda d: d["last_charge_time_range"],
    ),
    SmartBatterySensorDescription(
        key="last_charge_total_cost",
        translation_key="last_charge_total_cost",
        icon="mdi:cash",
        value_fn=lambda d: d["last_charge_total_cost"],
        attrs_fn=lambda d: {
            "currency": d["currency"],
        },
    ),
    SmartBatterySensorDescription(
        key="electricity_price_status",
        translation_key="electricity_price_status",
        icon="mdi:currency-usd",
        value_fn=lambda d: d["electricity_price_status"],
        attrs_fn=lambda d: {
            "current_price": d["current_price"],
        },
    ),
    SmartBatterySensorDescription(
        key="today_cheapest_hours",
        translation_key="today_cheapest_hours",
        icon="mdi:clock-check-outline",
        value_fn=lambda d: _cheapest_hours_str(d["today_cheapest_hours"]),
        attrs_fn=lambda d: {
            "cheapest_price": _cheapest_price_attr(d["today_cheapest_hours"], d["currency"]),
        },
    ),
    SmartBatterySensorDescription(
        key="tomorrow_cheapest_hours",
        translation_key="tomorrow_cheapest_hours",
        icon="mdi:clock-check-outline",
        value_fn=lambda d: _cheapest_hours_str(d["tomorrow_cheapest_hours"]),
        attrs_fn=lambda d: {
            "cheapest_price": _cheapest_price_attr(d["tomorrow_cheapest_hours"], d["currency"]),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Battery Charging sensors."""
    coordinator: SmartBatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SmartBatterySensor(coordinator, description, entry)
        for description in SENSOR_DESCRIPTIONS
    )


class SmartBatterySensor(
    CoordinatorEntity[SmartBatteryCoordinator], SensorEntity
):
    """A Smart Battery Charging sensor."""

    entity_description: SmartBatterySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartBatteryCoordinator,
        description: SmartBatterySensorDescription,
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
    def native_value(self) -> Any:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
