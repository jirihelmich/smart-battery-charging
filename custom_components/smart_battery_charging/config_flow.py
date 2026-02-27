"""Config flow for Smart Battery Charging integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_FORCE,
    CONF_CHARGE_STOP,
    CONF_CHARGING_EFFICIENCY,
    CONF_CONSUMPTION_SENSOR,
    CONF_CONTROL_TYPE,
    CONF_DAILY_SOLAR_SENSOR,
    CONF_CURRENCY,
    CONF_EMS_CHARGE_MODE_VALUE,
    CONF_EMS_NORMAL_MODE_VALUE,
    CONF_EVENING_CONSUMPTION_MULTIPLIER,
    CONF_FALLBACK_CONSUMPTION,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_INVERTER_AC_LOWER_LIMIT_NUMBER,
    CONF_INVERTER_ACTUAL_SOLAR_SENSOR,
    CONF_INVERTER_BATTERY_DOD_NUMBER,
    CONF_INVERTER_BATTERY_POWER_NUMBER,
    CONF_INVERTER_CAPACITY_SENSOR,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_INVERTER_SOC_SENSOR,
    CONF_INVERTER_TEMPLATE,
    CONF_INVERTER_WORKING_MODE_NUMBER,
    CONF_MAX_CHARGE_LEVEL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_CHARGE_PRICE,
    CONF_MIN_SOC,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
    CONF_NIGHT_CONSUMPTION_MULTIPLIER,
    CONF_NOTIFICATION_SERVICE,
    CONF_NOTIFY_CHARGING_COMPLETE,
    CONF_NOTIFY_CHARGING_STALLED,
    CONF_NOTIFY_CHARGING_START,
    CONF_NOTIFY_MORNING_SAFETY,
    CONF_NOTIFY_PLANNING,
    CONF_NOTIFY_SENSOR_UNAVAILABLE,
    CONF_PRICE_ATTRIBUTE_FORMAT,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_WEEKEND_CONSUMPTION_MULTIPLIER,
    CONF_WINDOW_END_HOUR,
    CONF_WINDOW_START_HOUR,
    CONTROL_TYPE_EMS_POWER,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGING_EFFICIENCY,
    DEFAULT_CURRENCY,
    DEFAULT_EVENING_CONSUMPTION_MULTIPLIER,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_INVERTER_TEMPLATE,
    DEFAULT_MAX_CHARGE_LEVEL,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_CHARGE_PRICE,
    DEFAULT_MIN_SOC,
    DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER,
    DEFAULT_NOTIFICATION_SERVICE,
    DEFAULT_NOTIFY_CHARGING_COMPLETE,
    DEFAULT_NOTIFY_CHARGING_STALLED,
    DEFAULT_NOTIFY_CHARGING_START,
    DEFAULT_NOTIFY_MORNING_SAFETY,
    DEFAULT_NOTIFY_PLANNING,
    DEFAULT_NOTIFY_SENSOR_UNAVAILABLE,
    DEFAULT_PRICE_ATTRIBUTE_FORMAT,
    DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER,
    DEFAULT_WINDOW_END_HOUR,
    DEFAULT_WINDOW_START_HOUR,
    DOMAIN,
    PRICE_FORMAT_HOUR_INT,
    PRICE_FORMAT_ISO_DATETIME,
)
from .inverters import INVERTER_TEMPLATES, get_template

_LOGGER = logging.getLogger(__name__)


def _entity_selector(domain: str, multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, multiple=multiple)
    )


def _select_selector(options: list[str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class SmartBatteryChargingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Battery Charging."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow handler."""
        return SmartBatteryChargingOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Name."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter_template()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="Smart Battery Charging"): str,
                }
            ),
        )

    async def async_step_inverter_template(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Select inverter integration template."""
        if user_input is not None:
            self._data.update(user_input)
            template = get_template(
                self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
            )
            # Store control type from template
            self._data[CONF_CONTROL_TYPE] = template.control_type
            return await self.async_step_inverter()

        template_options = [
            selector.SelectOptionDict(value=tid, label=tmpl.label)
            for tid, tmpl in INVERTER_TEMPLATES.items()
        ]

        return self.async_show_form(
            step_id="inverter_template",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTER_TEMPLATE,
                        default=DEFAULT_INVERTER_TEMPLATE,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=template_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Inverter entity selectors (varies by control type)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter_values()

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )
        hints = template.entity_hints
        hint_lines = "\n".join(
            f"- **{key}**: {hint}" for key, hint in hints.items()
        ) if hints else ""

        # Build schema based on control type
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_INVERTER_SOC_SENSOR): _entity_selector("sensor"),
            vol.Required(CONF_INVERTER_CAPACITY_SENSOR): _entity_selector("sensor"),
            vol.Required(CONF_INVERTER_ACTUAL_SOLAR_SENSOR): _entity_selector("sensor"),
        }

        if template.control_type == CONTROL_TYPE_EMS_POWER:
            # Wattsonic / EMS power control: number entities
            schema_dict[vol.Required(CONF_INVERTER_WORKING_MODE_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Required(CONF_INVERTER_BATTERY_POWER_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Required(CONF_INVERTER_AC_LOWER_LIMIT_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Optional(CONF_INVERTER_BATTERY_DOD_NUMBER)] = _entity_selector("number")
        else:
            # Select-based control (Solax, SolarEdge, etc.)
            schema_dict[vol.Required(CONF_INVERTER_MODE_SELECT)] = _entity_selector("select")
            schema_dict[vol.Required(CONF_INVERTER_CHARGE_COMMAND_SELECT)] = _entity_selector("select")
            schema_dict[vol.Required(CONF_INVERTER_CHARGE_SOC_LIMIT)] = _entity_selector("number")
            schema_dict[vol.Optional(CONF_INVERTER_DISCHARGE_MIN_SOC)] = _entity_selector("number")

        return self.async_show_form(
            step_id="inverter",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "template_name": template.label,
                "entity_hints": hint_lines,
            },
        )

    async def async_step_inverter_values(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Inverter option strings / EMS values (pre-filled from template)."""
        errors: dict[str, str] = {}

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_price()

        if template.control_type == CONTROL_TYPE_EMS_POWER:
            # EMS mode: show integer values for working mode registers
            return self.async_show_form(
                step_id="inverter_values",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_EMS_CHARGE_MODE_VALUE,
                            default=template.ems_charge_mode_value,
                        ): vol.Coerce(int),
                        vol.Required(
                            CONF_EMS_NORMAL_MODE_VALUE,
                            default=template.ems_normal_mode_value,
                        ): vol.Coerce(int),
                    }
                ),
                errors=errors,
            )

        # Select-based: show mode string dropdowns
        mode_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_MODE_SELECT, "")
        )
        charge_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_CHARGE_COMMAND_SELECT, "")
        )

        # Use template defaults, falling back to generic defaults for custom
        default_self_use = template.mode_self_use or "Self Use Mode"
        default_manual = template.mode_manual or "Manual Mode"
        default_force = template.charge_force or "Force Charge"
        default_stop = template.charge_stop or "Stop Charge and Discharge"

        schema_dict: dict[Any, Any] = {}
        if mode_options:
            schema_dict[vol.Required(CONF_MODE_SELF_USE)] = _select_selector(mode_options)
            schema_dict[vol.Required(CONF_MODE_MANUAL)] = _select_selector(mode_options)
        else:
            schema_dict[vol.Required(CONF_MODE_SELF_USE, default=default_self_use)] = str
            schema_dict[vol.Required(CONF_MODE_MANUAL, default=default_manual)] = str

        if charge_options:
            schema_dict[vol.Required(CONF_CHARGE_FORCE)] = _select_selector(charge_options)
            schema_dict[vol.Required(CONF_CHARGE_STOP)] = _select_selector(charge_options)
        else:
            schema_dict[vol.Required(CONF_CHARGE_FORCE, default=default_force)] = str
            schema_dict[vol.Required(CONF_CHARGE_STOP, default=default_stop)] = str

        return self.async_show_form(
            step_id="inverter_values",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5: Price sensor configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_solar()

        return self.async_show_form(
            step_id="price",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRICE_SENSOR): _entity_selector("sensor"),
                    vol.Required(
                        CONF_PRICE_ATTRIBUTE_FORMAT,
                        default=DEFAULT_PRICE_ATTRIBUTE_FORMAT,
                    ): _select_selector(
                        [PRICE_FORMAT_ISO_DATETIME, PRICE_FORMAT_HOUR_INT]
                    ),
                }
            ),
        )

    async def async_step_solar(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 6: Solar forecast entities (supports multiple orientations)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_consumption()

        return self.async_show_form(
            step_id="solar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOLAR_FORECAST_TODAY): _entity_selector(
                        "sensor", multiple=True
                    ),
                    vol.Required(CONF_SOLAR_FORECAST_TOMORROW): _entity_selector(
                        "sensor", multiple=True
                    ),
                }
            ),
        )

    async def async_step_consumption(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 7: Daily consumption sensor."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_analytics()

        return self.async_show_form(
            step_id="consumption",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONSUMPTION_SENSOR): _entity_selector("sensor"),
                }
            ),
        )

    async def async_step_analytics(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 7b: Optional analytics sensors (grid import/export/solar)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_settings()

        return self.async_show_form(
            step_id="analytics",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_GRID_IMPORT_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_GRID_EXPORT_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_DAILY_SOLAR_SENSOR): _entity_selector("sensor"),
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 8: Battery and charging settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # M3: Validate min_soc < max_charge_level
            min_soc = user_input.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
            max_charge = user_input.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL)
            if min_soc >= max_charge:
                errors["base"] = "min_soc_exceeds_max"
            else:
                self._data.update(user_input)
                await self.async_set_unique_id(
                    f"{DOMAIN}_{self._data.get('name', 'default')}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._data.get("name", "Smart Battery Charging"),
                    data=self._data,
                )

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )
        battery_default = template.battery_capacity

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY, default=battery_default
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_LEVEL, default=DEFAULT_MAX_CHARGE_LEVEL
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MIN_SOC, default=DEFAULT_MIN_SOC
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_POWER, default=DEFAULT_MAX_CHARGE_POWER
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_PRICE, default=DEFAULT_MAX_CHARGE_PRICE
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_FALLBACK_CONSUMPTION, default=DEFAULT_FALLBACK_CONSUMPTION
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_START_HOUR, default=DEFAULT_WINDOW_START_HOUR
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_WINDOW_END_HOUR, default=DEFAULT_WINDOW_END_HOUR
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_CURRENCY, default=DEFAULT_CURRENCY
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _get_select_options(self, entity_id: str) -> list[str]:
        """Get available options from a select entity."""
        if not entity_id:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        options = state.attributes.get("options", [])
        return list(options) if options else []


class SmartBatteryChargingOptionsFlow(OptionsFlow):
    """Handle options for Smart Battery Charging."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the settings options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # M3: Validate min_soc < max_charge_level
            min_soc = user_input.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
            max_charge = user_input.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL)
            if min_soc >= max_charge:
                errors["base"] = "min_soc_exceeds_max"
            else:
                return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY,
                        default=current.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_LEVEL,
                        default=current.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MIN_SOC,
                        default=current.get(CONF_MIN_SOC, DEFAULT_MIN_SOC),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_POWER,
                        default=current.get(CONF_MAX_CHARGE_POWER, DEFAULT_MAX_CHARGE_POWER),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_PRICE,
                        default=current.get(CONF_MAX_CHARGE_PRICE, DEFAULT_MAX_CHARGE_PRICE),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_FALLBACK_CONSUMPTION,
                        default=current.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_START_HOUR,
                        default=current.get(CONF_WINDOW_START_HOUR, DEFAULT_WINDOW_START_HOUR),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_WINDOW_END_HOUR,
                        default=current.get(CONF_WINDOW_END_HOUR, DEFAULT_WINDOW_END_HOUR),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_CURRENCY,
                        default=current.get(CONF_CURRENCY, DEFAULT_CURRENCY),
                    ): str,
                    # Analytics sensors (optional)
                    vol.Optional(
                        CONF_GRID_IMPORT_SENSOR,
                        default=current.get(CONF_GRID_IMPORT_SENSOR, ""),
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_GRID_EXPORT_SENSOR,
                        default=current.get(CONF_GRID_EXPORT_SENSOR, ""),
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_DAILY_SOLAR_SENSOR,
                        default=current.get(CONF_DAILY_SOLAR_SENSOR, ""),
                    ): _entity_selector("sensor"),
                    # Advanced: Efficiency & Consumption Profiles
                    vol.Optional(
                        CONF_CHARGING_EFFICIENCY,
                        default=current.get(CONF_CHARGING_EFFICIENCY, DEFAULT_CHARGING_EFFICIENCY),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.7, max=1.0)),
                    vol.Optional(
                        CONF_EVENING_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_EVENING_CONSUMPTION_MULTIPLIER, DEFAULT_EVENING_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3.0)),
                    vol.Optional(
                        CONF_NIGHT_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_NIGHT_CONSUMPTION_MULTIPLIER, DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                    vol.Optional(
                        CONF_WEEKEND_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_WEEKEND_CONSUMPTION_MULTIPLIER, DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2.0)),
                    # Notifications
                    vol.Optional(
                        CONF_NOTIFICATION_SERVICE,
                        default=current.get(CONF_NOTIFICATION_SERVICE, DEFAULT_NOTIFICATION_SERVICE),
                    ): str,
                    vol.Optional(
                        CONF_NOTIFY_PLANNING,
                        default=current.get(CONF_NOTIFY_PLANNING, DEFAULT_NOTIFY_PLANNING),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_START,
                        default=current.get(CONF_NOTIFY_CHARGING_START, DEFAULT_NOTIFY_CHARGING_START),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_COMPLETE,
                        default=current.get(CONF_NOTIFY_CHARGING_COMPLETE, DEFAULT_NOTIFY_CHARGING_COMPLETE),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_MORNING_SAFETY,
                        default=current.get(CONF_NOTIFY_MORNING_SAFETY, DEFAULT_NOTIFY_MORNING_SAFETY),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_STALLED,
                        default=current.get(CONF_NOTIFY_CHARGING_STALLED, DEFAULT_NOTIFY_CHARGING_STALLED),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_SENSOR_UNAVAILABLE,
                        default=current.get(CONF_NOTIFY_SENSOR_UNAVAILABLE, DEFAULT_NOTIFY_SENSOR_UNAVAILABLE),
                    ): bool,
                }
            ),
            errors=errors,
        )
