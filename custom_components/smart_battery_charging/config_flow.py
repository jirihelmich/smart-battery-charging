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
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENCY,
    CONF_FALLBACK_CONSUMPTION,
    CONF_INVERTER_ACTUAL_SOLAR_SENSOR,
    CONF_INVERTER_CAPACITY_SENSOR,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_INVERTER_SOC_SENSOR,
    CONF_MAX_CHARGE_LEVEL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_CHARGE_PRICE,
    CONF_MIN_SOC,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
    CONF_PRICE_ATTRIBUTE_FORMAT,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_WINDOW_END_HOUR,
    CONF_WINDOW_START_HOUR,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CURRENCY,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_MAX_CHARGE_LEVEL,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_CHARGE_PRICE,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_ATTRIBUTE_FORMAT,
    DEFAULT_WINDOW_END_HOUR,
    DEFAULT_WINDOW_START_HOUR,
    DOMAIN,
    PRICE_FORMAT_HOUR_INT,
    PRICE_FORMAT_ISO_DATETIME,
)

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
            return await self.async_step_inverter()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="Smart Battery Charging"): str,
                }
            ),
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Inverter entity selectors."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter_values()

        return self.async_show_form(
            step_id="inverter",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INVERTER_SOC_SENSOR): _entity_selector("sensor"),
                    vol.Required(CONF_INVERTER_CAPACITY_SENSOR): _entity_selector("sensor"),
                    vol.Required(CONF_INVERTER_ACTUAL_SOLAR_SENSOR): _entity_selector("sensor"),
                    vol.Required(CONF_INVERTER_MODE_SELECT): _entity_selector("select"),
                    vol.Required(CONF_INVERTER_CHARGE_COMMAND_SELECT): _entity_selector("select"),
                    vol.Required(CONF_INVERTER_CHARGE_SOC_LIMIT): _entity_selector("number"),
                    vol.Optional(CONF_INVERTER_DISCHARGE_MIN_SOC): _entity_selector("number"),
                }
            ),
        )

    async def async_step_inverter_values(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Inverter option strings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_price()

        # Try to get options from the mode select entity
        mode_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_MODE_SELECT, "")
        )
        charge_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_CHARGE_COMMAND_SELECT, "")
        )

        schema_dict: dict[Any, Any] = {}
        if mode_options:
            schema_dict[vol.Required(CONF_MODE_SELF_USE)] = _select_selector(mode_options)
            schema_dict[vol.Required(CONF_MODE_MANUAL)] = _select_selector(mode_options)
        else:
            schema_dict[vol.Required(CONF_MODE_SELF_USE, default="Self Use Mode")] = str
            schema_dict[vol.Required(CONF_MODE_MANUAL, default="Manual Mode")] = str

        if charge_options:
            schema_dict[vol.Required(CONF_CHARGE_FORCE)] = _select_selector(charge_options)
            schema_dict[vol.Required(CONF_CHARGE_STOP)] = _select_selector(charge_options)
        else:
            schema_dict[vol.Required(CONF_CHARGE_FORCE, default="Force Charge")] = str
            schema_dict[vol.Required(CONF_CHARGE_STOP, default="Stop Charge and Discharge")] = str

        return self.async_show_form(
            step_id="inverter_values",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Price sensor configuration."""
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
        """Step 5: Solar forecast entities (supports multiple orientations)."""
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
        """Step 6: Daily consumption sensor."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_settings()

        return self.async_show_form(
            step_id="consumption",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONSUMPTION_SENSOR): _entity_selector("sensor"),
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 7: Battery and charging settings."""
        if user_input is not None:
            self._data.update(user_input)
            await self.async_set_unique_id(
                f"{DOMAIN}_{self._data.get('name', 'default')}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._data.get("name", "Smart Battery Charging"),
                data=self._data,
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY, default=DEFAULT_BATTERY_CAPACITY
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
        if user_input is not None:
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
                }
            ),
        )
