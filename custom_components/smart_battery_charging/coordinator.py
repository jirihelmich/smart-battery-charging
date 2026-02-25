"""DataUpdateCoordinator for Smart Battery Charging.

Central hub that holds all sub-components and recomputes derived values.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .charging_controller import ChargingStateMachine
    from .inverter_controller import InverterController
    from .notifier import ChargingNotifier
    from .planner import ChargingPlanner

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_CHARGING_EFFICIENCY,
    CONF_CONSUMPTION_SENSOR,
    CONF_CURRENCY,
    CONF_EVENING_CONSUMPTION_MULTIPLIER,
    CONF_FALLBACK_CONSUMPTION,
    CONF_INVERTER_ACTUAL_SOLAR_SENSOR,
    CONF_INVERTER_CAPACITY_SENSOR,
    CONF_INVERTER_SOC_SENSOR,
    CONF_MAX_CHARGE_LEVEL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_CHARGE_PRICE,
    CONF_MIN_SOC,
    CONF_NIGHT_CONSUMPTION_MULTIPLIER,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_WEEKEND_CONSUMPTION_MULTIPLIER,
    CONF_WINDOW_END_HOUR,
    CONF_WINDOW_START_HOUR,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGING_EFFICIENCY,
    DEFAULT_CURRENCY,
    DEFAULT_EVENING_CONSUMPTION_MULTIPLIER,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_MAX_CHARGE_LEVEL,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_CHARGE_PRICE,
    DEFAULT_MIN_SOC,
    DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER,
    DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER,
    DEFAULT_WINDOW_END_HOUR,
    DEFAULT_WINDOW_START_HOUR,
    DOMAIN,
    SENSOR_UNAVAILABLE_TICKS,
    UPDATE_INTERVAL_SECONDS,
)
from .consumption_tracker import ConsumptionTracker
from .forecast_corrector import ForecastCorrector
from .models import ChargingSchedule, ChargingSession, ChargingState, OvernightNeed
from .price_analyzer import PriceAnalyzer
from .storage import SmartBatteryStore

_LOGGER = logging.getLogger(__name__)


class SmartBatteryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that recomputes all derived sensor values."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: SmartBatteryStore,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.store = store

        # Sub-components (pure logic, no HA deps)
        self.consumption_tracker = ConsumptionTracker(
            window_days=7,
            fallback_kwh=self._opt(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION),
        )
        self.forecast_corrector = ForecastCorrector(window_days=7)
        self.price_analyzer = PriceAnalyzer(
            window_start_hour=self._opt(CONF_WINDOW_START_HOUR, DEFAULT_WINDOW_START_HOUR),
            window_end_hour=self._opt(CONF_WINDOW_END_HOUR, DEFAULT_WINDOW_END_HOUR),
        )

        # Mutable state — initialized from store in __init__.py after async_load()
        self.enabled: bool = True  # overwritten from store.enabled
        self.charging_state: ChargingState = ChargingState.IDLE
        self.current_schedule: ChargingSchedule | None = None
        self._last_overnight: OvernightNeed | None = None

        # Phase 2 components (set from __init__.py after construction)
        self.inverter: InverterController | None = None
        self.state_machine: ChargingStateMachine | None = None
        self.planner: ChargingPlanner | None = None
        self.notifier: ChargingNotifier | None = None

        # Sensor health tracking (H1)
        self._soc_unavailable_ticks: int = 0
        self._price_unavailable_ticks: int = 0
        self._soc_unavailable_notified: bool = False
        self._price_unavailable_notified: bool = False

    def _opt(self, key: str, default: Any) -> Any:
        """Get a config value, preferring options over data."""
        return self.entry.options.get(key, self.entry.data.get(key, default))

    # --- Config accessors (live, re-read each cycle) ---

    @property
    def battery_capacity(self) -> float:
        """Battery capacity in kWh. Reads from BMS sensor, falls back to config setting."""
        return self.inverter_capacity_kwh

    @property
    def _configured_battery_capacity(self) -> float:
        """Battery capacity from config (fallback when BMS unavailable)."""
        return float(self._opt(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY))

    @property
    def max_charge_level(self) -> float:
        return float(self._opt(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL))

    @max_charge_level.setter
    def max_charge_level(self, value: float) -> None:
        self._update_option(CONF_MAX_CHARGE_LEVEL, value)

    @property
    def min_soc(self) -> float:
        return float(self._opt(CONF_MIN_SOC, DEFAULT_MIN_SOC))

    @min_soc.setter
    def min_soc(self, value: float) -> None:
        self._update_option(CONF_MIN_SOC, value)

    @property
    def max_charge_power(self) -> float:
        return float(self._opt(CONF_MAX_CHARGE_POWER, DEFAULT_MAX_CHARGE_POWER))

    @max_charge_power.setter
    def max_charge_power(self, value: float) -> None:
        self._update_option(CONF_MAX_CHARGE_POWER, value)

    @property
    def max_charge_price(self) -> float:
        return float(self._opt(CONF_MAX_CHARGE_PRICE, DEFAULT_MAX_CHARGE_PRICE))

    @max_charge_price.setter
    def max_charge_price(self, value: float) -> None:
        self._update_option(CONF_MAX_CHARGE_PRICE, value)

    @property
    def fallback_consumption(self) -> float:
        return float(self._opt(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION))

    @fallback_consumption.setter
    def fallback_consumption(self, value: float) -> None:
        self._update_option(CONF_FALLBACK_CONSUMPTION, value)
        self.consumption_tracker.fallback_kwh = value

    @property
    def charging_efficiency(self) -> float:
        return float(self._opt(CONF_CHARGING_EFFICIENCY, DEFAULT_CHARGING_EFFICIENCY))

    @charging_efficiency.setter
    def charging_efficiency(self, value: float) -> None:
        self._update_option(CONF_CHARGING_EFFICIENCY, value)

    @property
    def evening_consumption_multiplier(self) -> float:
        return float(self._opt(CONF_EVENING_CONSUMPTION_MULTIPLIER, DEFAULT_EVENING_CONSUMPTION_MULTIPLIER))

    @evening_consumption_multiplier.setter
    def evening_consumption_multiplier(self, value: float) -> None:
        self._update_option(CONF_EVENING_CONSUMPTION_MULTIPLIER, value)

    @property
    def night_consumption_multiplier(self) -> float:
        return float(self._opt(CONF_NIGHT_CONSUMPTION_MULTIPLIER, DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER))

    @night_consumption_multiplier.setter
    def night_consumption_multiplier(self, value: float) -> None:
        self._update_option(CONF_NIGHT_CONSUMPTION_MULTIPLIER, value)

    @property
    def weekend_consumption_multiplier(self) -> float:
        return float(self._opt(CONF_WEEKEND_CONSUMPTION_MULTIPLIER, DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER))

    @weekend_consumption_multiplier.setter
    def weekend_consumption_multiplier(self, value: float) -> None:
        self._update_option(CONF_WEEKEND_CONSUMPTION_MULTIPLIER, value)

    @property
    def currency(self) -> str:
        return str(self._opt(CONF_CURRENCY, DEFAULT_CURRENCY))

    def _update_option(self, key: str, value: Any) -> None:
        """Update a single option in the config entry."""
        new_options = {**self.entry.options, key: value}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)

    # --- HA state reading helpers ---

    def _get_state_float(self, entity_id: str, default: float = 0.0) -> float:
        """Get a float state value from HA."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _is_sensor_available(self, entity_id: str) -> bool:
        """Check if a sensor entity is available."""
        if not entity_id:
            return True  # No entity configured, skip
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return False
        return True

    def _get_state_attrs(self, entity_id: str) -> dict[str, Any]:
        """Get attributes dict from a HA entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return {}
        return dict(state.attributes)

    def _sum_sensor_states(self, entity_ids: list[str]) -> float:
        """Sum the float states of multiple sensors."""
        return sum(self._get_state_float(eid) for eid in entity_ids)

    # --- Computed values ---

    @property
    def current_soc(self) -> float:
        """Current battery SOC percentage."""
        return self._get_state_float(self.entry.data.get(CONF_INVERTER_SOC_SENSOR, ""))

    @property
    def inverter_capacity_kwh(self) -> float:
        """Battery capacity from inverter BMS (Wh → kWh), falls back to config.

        M4: Uses unit_of_measurement attribute when available, falls back to heuristic.
        """
        entity_id = self.entry.data.get(CONF_INVERTER_CAPACITY_SENSOR, "")
        raw = self._get_state_float(entity_id, 0)
        if raw <= 0:
            return self._configured_battery_capacity

        # M4: Check unit of measurement first
        state = self.hass.states.get(entity_id)
        if state is not None:
            uom = state.attributes.get("unit_of_measurement", "")
            if uom in ("Wh", "wh"):
                return raw / 1000
            if uom in ("kWh", "kwh"):
                return raw

        # Fallback heuristic: BMS often reports in Wh
        if raw > 1000:
            return raw / 1000
        return raw

    @property
    def actual_solar_today(self) -> float:
        """Actual solar production today in kWh."""
        return self._get_state_float(
            self.entry.data.get(CONF_INVERTER_ACTUAL_SOLAR_SENSOR, "")
        )

    @property
    def solar_forecast_today(self) -> float:
        """Combined solar forecast for today."""
        sensors = self.entry.data.get(CONF_SOLAR_FORECAST_TODAY, [])
        if isinstance(sensors, str):
            sensors = [sensors]
        return round(self._sum_sensor_states(sensors), 2)

    @property
    def solar_forecast_tomorrow(self) -> float:
        """Combined solar forecast for tomorrow."""
        sensors = self.entry.data.get(CONF_SOLAR_FORECAST_TOMORROW, [])
        if isinstance(sensors, str):
            sensors = [sensors]
        return round(self._sum_sensor_states(sensors), 2)

    @property
    def current_price(self) -> float:
        """Current electricity price."""
        return self._get_state_float(self.entry.data.get(CONF_PRICE_SENSOR, ""))

    @property
    def price_attributes(self) -> dict[str, Any]:
        """All attributes from the price sensor."""
        return self._get_state_attrs(self.entry.data.get(CONF_PRICE_SENSOR, ""))

    @property
    def daily_consumption_current(self) -> float:
        """Today's consumption so far."""
        return self._get_state_float(self.entry.data.get(CONF_CONSUMPTION_SENSOR, ""))

    @property
    def solar_forecast_tomorrow_hourly(self) -> dict[int, float]:
        """Hourly solar forecast for tomorrow from the forecast_solar integration.

        Returns dict mapping hour (0-23) to kWh production for that hour.
        Combines all forecast_solar config entries.
        """
        result: dict[int, float] = {}
        now = dt_util.now()
        tomorrow = (now + timedelta(days=1)).date()

        try:
            entries = self.hass.config_entries.async_entries("forecast_solar")
        except Exception:
            return result

        for entry in entries:
            runtime_data = getattr(entry, "runtime_data", None)
            if runtime_data is None:
                continue
            # forecast_solar stores an Estimate object on runtime_data
            estimate = getattr(runtime_data, "data", runtime_data)
            wh_period = getattr(estimate, "wh_period", None)
            if not isinstance(wh_period, dict):
                continue
            for dt_key, wh_value in wh_period.items():
                try:
                    if hasattr(dt_key, "date") and dt_key.date() == tomorrow:
                        hour = dt_key.hour
                        kwh = float(wh_value) / 1000.0
                        result[hour] = result.get(hour, 0.0) + kwh
                except (ValueError, TypeError, AttributeError):
                    continue

        return result

    @property
    def sunrise_hour_tomorrow(self) -> float | None:
        """Hour of sunrise tomorrow as a float (e.g., 6.5 = 06:30).

        Reads from the sun.sun entity's next_rising attribute.
        Returns None if sun.sun is unavailable.
        """
        state = self.hass.states.get("sun.sun")
        if state is None:
            return None
        next_rising = state.attributes.get("next_rising")
        if next_rising is None:
            return None
        try:
            from homeassistant.util.dt import as_local, parse_datetime

            dt = parse_datetime(str(next_rising))
            if dt is None:
                return None
            local_dt = as_local(dt)
            return round(local_dt.hour + local_dt.minute / 60, 2)
        except (ValueError, TypeError):
            return None

    # --- Daily recorders ---

    async def async_record_daily_consumption(self) -> None:
        """Record today's consumption value to history (called at 23:55)."""
        value = self.daily_consumption_current
        if value <= 0:
            _LOGGER.warning("Daily consumption is %.1f, skipping recording", value)
            return

        history = self.store.consumption_history
        new_history = self.consumption_tracker.add_entry(history, value)
        await self.store.async_set_consumption_history(new_history)
        _LOGGER.info("Recorded daily consumption: %.2f kWh (%d days tracked)", value, len(new_history))

    async def async_record_forecast_error(self) -> None:
        """Record today's forecast error to history (called at 23:55)."""
        forecast = self.solar_forecast_today
        actual = self.actual_solar_today

        error = self.forecast_corrector.compute_error(forecast, actual)
        if error is None:
            _LOGGER.debug("Skipping forecast error recording (forecast too low: %.1f)", forecast)
            return

        history = self.store.forecast_error_history
        new_history = self.forecast_corrector.add_entry(history, error)
        await self.store.async_set_forecast_error_history(new_history)
        _LOGGER.info(
            "Recorded forecast error: forecast=%.1f, actual=%.1f, error=%.1f%%",
            forecast, actual, error * 100,
        )

    # --- Sensor health monitoring (H1) ---

    async def _check_sensor_health(self, data: dict[str, Any]) -> None:
        """Check critical sensors for unavailability and notify if prolonged."""
        soc_entity = self.entry.data.get(CONF_INVERTER_SOC_SENSOR, "")
        price_entity = self.entry.data.get(CONF_PRICE_SENSOR, "")

        # SOC sensor
        if soc_entity and not self._is_sensor_available(soc_entity):
            self._soc_unavailable_ticks += 1
            data["soc_sensor_available"] = False
            if self._soc_unavailable_ticks >= SENSOR_UNAVAILABLE_TICKS and not self._soc_unavailable_notified:
                self._soc_unavailable_notified = True
                if self.notifier:
                    await self.notifier.async_notify_sensor_unavailable("Battery SOC", soc_entity)
        else:
            self._soc_unavailable_ticks = 0
            self._soc_unavailable_notified = False
            data["soc_sensor_available"] = True

        # Price sensor
        if price_entity and not self._is_sensor_available(price_entity):
            self._price_unavailable_ticks += 1
            data["price_sensor_available"] = False
            if self._price_unavailable_ticks >= SENSOR_UNAVAILABLE_TICKS and not self._price_unavailable_notified:
                self._price_unavailable_notified = True
                if self.notifier:
                    await self.notifier.async_notify_sensor_unavailable("Electricity Price", price_entity)
        else:
            self._price_unavailable_ticks = 0
            self._price_unavailable_notified = False
            data["price_sensor_available"] = True

    # --- Main update ---

    async def _async_update_data(self) -> dict[str, Any]:
        """Recompute all derived values."""
        now = dt_util.now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        # Sync fallback consumption from options
        self.consumption_tracker.fallback_kwh = self.fallback_consumption

        # Consumption average
        consumption_history = self.store.consumption_history
        avg_consumption = self.consumption_tracker.average(consumption_history)

        # Forecast error
        error_history = self.store.forecast_error_history
        forecast_error_avg = self.forecast_corrector.average_error_pct(error_history)
        forecast_error_ratio = self.forecast_corrector.average_error(error_history)

        # Solar forecasts
        solar_today = self.solar_forecast_today
        solar_tomorrow = self.solar_forecast_tomorrow
        actual_solar = self.actual_solar_today

        # Today's live forecast error
        today_forecast_error = 0.0
        if solar_today > 0.5:
            err = self.forecast_corrector.compute_error(solar_today, actual_solar)
            today_forecast_error = round(err * 100, 1) if err is not None else 0.0

        # Adjusted tomorrow solar
        adjusted_solar_tomorrow = self.forecast_corrector.adjust_forecast(
            solar_tomorrow, error_history
        )

        # H4: Energy deficit — use planner if available for consistent values
        if self.planner is not None:
            try:
                deficit_result = self.planner.compute_energy_deficit(now=now)
                energy_deficit = deficit_result.deficit
                charge_needed = deficit_result.charge_needed
                usable_capacity = deficit_result.usable_capacity
            except Exception:
                _LOGGER.debug("Planner energy deficit failed, using fallback")
                energy_deficit = avg_consumption - adjusted_solar_tomorrow
                usable_capacity = self.battery_capacity * (self.max_charge_level - self.min_soc) / 100
                charge_needed = max(0.0, min(energy_deficit, usable_capacity)) if energy_deficit > 0 else 0.0
        else:
            energy_deficit = avg_consumption - adjusted_solar_tomorrow
            usable_capacity = self.battery_capacity * (self.max_charge_level - self.min_soc) / 100
            charge_needed = max(0.0, min(energy_deficit, usable_capacity)) if energy_deficit > 0 else 0.0

        # Battery calculations
        capacity_kwh = self.battery_capacity
        soc = self.current_soc
        battery_charge_kwh = round(capacity_kwh * soc / 100, 2)
        min_kwh = capacity_kwh * self.min_soc / 100
        battery_usable = round(max(battery_charge_kwh - min_kwh, 0), 2)
        max_kwh = capacity_kwh * self.max_charge_level / 100
        battery_to_max = round(max(max_kwh - battery_charge_kwh, 0), 2)

        # Price analysis
        price_attrs = self.price_attributes
        price_status = self.price_analyzer.classify_price(
            self.current_price, self.max_charge_price
        )

        today_cheapest = self.price_analyzer.find_cheapest_hours(price_attrs, today, 3)
        tomorrow_cheapest = self.price_analyzer.find_cheapest_hours(price_attrs, tomorrow, 3)

        # Night price window (for display)
        night_slots = self.price_analyzer.extract_night_prices(price_attrs, today, tomorrow)

        # Charging status
        charging_status = self._compute_charging_status(soc)

        # Charge history
        charge_history = self.store.charge_history

        # Last session
        last_session = self.store.last_session
        last_kwh = last_session.kwh_charged(capacity_kwh) if last_session else 0.0
        last_cost = last_session.total_cost(capacity_kwh) if last_session else 0.0

        # Charging recommended
        charging_recommended = (
            self.current_price < self.max_charge_price
            and soc < self.max_charge_level
        )

        data: dict[str, Any] = {
            # Consumption
            "average_daily_consumption": avg_consumption,
            "consumption_days_tracked": self.consumption_tracker.days_tracked(consumption_history),
            "consumption_source": self.consumption_tracker.source(consumption_history),
            "consumption_history_raw": consumption_history,
            # Solar
            "today_solar_forecast": solar_today,
            "tomorrow_solar_forecast": solar_tomorrow,
            "solar_forecast_error_average": forecast_error_avg,
            "solar_forecast_error_ratio": forecast_error_ratio,
            "today_solar_forecast_error": today_forecast_error,
            "actual_solar_today": actual_solar,
            "forecast_error_days_tracked": len(error_history),
            "forecast_error_history_raw": error_history,
            # Energy forecast
            "tomorrow_energy_forecast": round(adjusted_solar_tomorrow - avg_consumption, 2),
            "solar_raw_tomorrow": solar_tomorrow,
            "solar_adjusted_tomorrow": adjusted_solar_tomorrow,
            "forecast_error_pct_used": forecast_error_avg,
            "consumption_estimate_used": avg_consumption,
            "energy_deficit": round(max(energy_deficit, 0), 2),
            "charge_needed": round(charge_needed, 2),
            # Battery
            "battery_soc": soc,
            "battery_charge_kwh": battery_charge_kwh,
            "battery_usable_charge": battery_usable,
            "battery_capacity_to_max": battery_to_max,
            "battery_capacity_kwh": capacity_kwh,
            "usable_capacity_total": round(usable_capacity, 2),
            # Price
            "current_price": self.current_price,
            "electricity_price_status": price_status,
            "today_cheapest_hours": today_cheapest,
            "tomorrow_cheapest_hours": tomorrow_cheapest,
            "night_price_slots": night_slots,
            # Charging state
            "night_charging_status": charging_status,
            "charging_active": self.charging_state == ChargingState.CHARGING,
            "charging_recommended": charging_recommended,
            "schedule": self.current_schedule,
            # Overnight survival
            "overnight_dark_hours": self._last_overnight.dark_hours if self._last_overnight else None,
            "overnight_consumption_estimate": self._last_overnight.overnight_consumption if self._last_overnight else None,
            "overnight_battery_at_window_start": self._last_overnight.battery_at_window_start if self._last_overnight else None,
            "overnight_charge_needed": self._last_overnight.charge_needed if self._last_overnight else None,
            # Last session
            "last_session": last_session,
            "last_night_charge_kwh": last_kwh,
            "last_charge_battery_range": self._format_battery_range(last_session),
            "last_charge_time_range": self._format_time_range(last_session),
            "last_charge_total_cost": last_cost,
            "last_charge_result": last_session.result if last_session else "",
            "charge_history_raw": charge_history,
            # Settings (for sensor attributes)
            "currency": self.currency,
            "enabled": self.enabled,
            # Sensor health (H1)
            "soc_sensor_available": True,
            "price_sensor_available": True,
        }

        # H1: Check sensor health
        await self._check_sensor_health(data)

        return data

    def _compute_charging_status(self, soc: float) -> str:
        """Compute the night charging status string."""
        if not self.enabled:
            return "Disabled"
        if self.charging_state == ChargingState.CHARGING:
            return "Charging"
        if self.charging_state == ChargingState.COMPLETE:
            return "Complete"
        if self.current_schedule is not None:
            return "Scheduled"
        return "Idle"

    def _format_battery_range(self, session: ChargingSession | None) -> str:
        if not session:
            return "N/A"
        s = round(session.start_soc)
        e = round(session.end_soc)
        return f"{s}% → {e}%"

    def _format_time_range(self, session: ChargingSession | None) -> str:
        if not session or not session.start_time or not session.end_time:
            return "N/A"
        start = session.start_time[11:16] if len(session.start_time) > 15 else session.start_time
        end = session.end_time[11:16] if len(session.end_time) > 15 else session.end_time
        return f"{start}\u2013{end}"
