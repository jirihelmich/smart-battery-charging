"""Constants for the Smart Battery Charging integration."""

DOMAIN = "smart_battery_charging"

# Config flow steps
CONF_INVERTER_SOC_SENSOR = "inverter_soc_sensor"
CONF_INVERTER_CAPACITY_SENSOR = "inverter_capacity_sensor"
CONF_INVERTER_ACTUAL_SOLAR_SENSOR = "inverter_actual_solar_sensor"
CONF_INVERTER_MODE_SELECT = "inverter_mode_select"
CONF_INVERTER_CHARGE_COMMAND_SELECT = "inverter_charge_command_select"
CONF_INVERTER_CHARGE_SOC_LIMIT = "inverter_charge_soc_limit"
CONF_INVERTER_DISCHARGE_MIN_SOC = "inverter_discharge_min_soc"

# Inverter option strings
CONF_MODE_SELF_USE = "mode_self_use"
CONF_MODE_MANUAL = "mode_manual"
CONF_CHARGE_FORCE = "charge_force"
CONF_CHARGE_STOP = "charge_stop"

# Price sensor
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_ATTRIBUTE_FORMAT = "price_attribute_format"

# Solar forecast
CONF_SOLAR_FORECAST_TODAY = "solar_forecast_today"
CONF_SOLAR_FORECAST_TOMORROW = "solar_forecast_tomorrow"

# Consumption
CONF_CONSUMPTION_SENSOR = "consumption_sensor"

# Settings (also exposed as number entities)
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_MAX_CHARGE_LEVEL = "max_charge_level"
CONF_MIN_SOC = "min_soc"
CONF_MAX_CHARGE_POWER = "max_charge_power"
CONF_MAX_CHARGE_PRICE = "max_charge_price"
CONF_FALLBACK_CONSUMPTION = "fallback_consumption"
CONF_WINDOW_START_HOUR = "window_start_hour"
CONF_WINDOW_END_HOUR = "window_end_hour"
CONF_CURRENCY = "currency"

# Defaults
DEFAULT_BATTERY_CAPACITY = 15.0
DEFAULT_MAX_CHARGE_LEVEL = 90.0
DEFAULT_MIN_SOC = 20.0
DEFAULT_MAX_CHARGE_POWER = 10.0
DEFAULT_MAX_CHARGE_PRICE = 4.0
DEFAULT_FALLBACK_CONSUMPTION = 20.0
DEFAULT_WINDOW_START_HOUR = 22
DEFAULT_WINDOW_END_HOUR = 6
DEFAULT_CURRENCY = "Kč/kWh"
DEFAULT_PRICE_ATTRIBUTE_FORMAT = "iso_datetime"

# Price attribute formats
PRICE_FORMAT_ISO_DATETIME = "iso_datetime"
PRICE_FORMAT_HOUR_INT = "hour_int"

# Coordinator
UPDATE_INTERVAL_SECONDS = 30

# Consumption tracker
CONSUMPTION_WINDOW_DAYS = 7

# Forecast corrector
FORECAST_ERROR_WINDOW_DAYS = 7

# Charge history
CHARGE_HISTORY_DAYS = 7

# Platforms
PLATFORMS = ["sensor", "binary_sensor", "number", "switch"]
