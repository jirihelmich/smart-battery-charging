"""Charging planner — pure orchestration, no HA service calls.

Reads all data through the coordinator and its sub-components.
Decides whether charging is needed, how much, and when.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .const import (
    EMERGENCY_SOC_THRESHOLD,
    MAX_OVERNIGHT_HOURS,
    PV_FALLBACK_BUFFER_HOURS,
    PV_RAMP_BUFFER_HOURS,
)
from .models import ChargingSchedule, EnergyDeficit, OvernightNeed

if TYPE_CHECKING:
    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


def _default_now() -> datetime:
    """Fallback for when no `now` is passed (e.g. in tests)."""
    return datetime.now()


class ChargingPlanner:
    """Plans charging sessions based on energy deficit and price analysis."""

    def __init__(self, coordinator: SmartBatteryCoordinator) -> None:
        self._coordinator = coordinator
        self.last_overnight_need: OvernightNeed | None = None

    def compute_energy_deficit(self, *, now: datetime | None = None) -> EnergyDeficit:
        """Compute energy deficit for tomorrow.

        Returns an EnergyDeficit with consumption, solar (raw and adjusted),
        forecast error percentage, deficit, charge needed, and usable capacity.
        Applies weekend multiplier if tomorrow is Saturday or Sunday.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        # Get consumption average
        consumption = c.consumption_tracker.average(c.store.consumption_history)

        # Apply weekend multiplier if tomorrow is weekend
        tomorrow = now + timedelta(days=1)
        if tomorrow.weekday() >= 5:  # Saturday=5, Sunday=6
            consumption *= c.weekend_consumption_multiplier

        # Get solar forecast and adjust for historical error
        solar_raw = c.solar_forecast_tomorrow
        error_history = c.store.forecast_error_history
        solar_adjusted = c.forecast_corrector.adjust_forecast(solar_raw, error_history)
        forecast_error_pct = c.forecast_corrector.average_error_pct(error_history)

        # Compute deficit
        deficit = consumption - solar_adjusted

        # Usable battery capacity
        usable_capacity = c.battery_capacity * (c.max_charge_level - c.min_soc) / 100

        # Charge needed, clamped to usable capacity
        charge_needed = max(0.0, min(deficit, usable_capacity)) if deficit > 0 else 0.0

        return EnergyDeficit(
            consumption=round(consumption, 2),
            solar_raw=round(solar_raw, 2),
            solar_adjusted=round(solar_adjusted, 2),
            forecast_error_pct=forecast_error_pct,
            deficit=round(max(deficit, 0), 2),
            charge_needed=round(charge_needed, 2),
            usable_capacity=round(usable_capacity, 2),
        )

    def has_tomorrow_prices(self, *, now: datetime | None = None) -> bool:
        """Check if tomorrow's prices are available in the price sensor attributes."""
        if now is None:
            now = _default_now()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        attrs = self._coordinator.price_attributes
        for key in attrs:
            key_str = str(key)
            if len(key_str) >= 10 and key_str[:10] == tomorrow:
                return True
        return False

    def _hourly_consumption(self, hour: int, daily: float) -> float:
        """Return kWh consumption for a given hour using the 3-period model.

        Periods: Day (06-18) multiplier=1.0, Evening (18-23) multiplier=E, Night (23-06) multiplier=N.
        base_rate = daily / (12*1.0 + 5*E + 7*N)
        """
        c = self._coordinator
        e = c.evening_consumption_multiplier
        n = c.night_consumption_multiplier
        base_rate = daily / (12 * 1.0 + 5 * e + 7 * n)

        if 6 <= hour < 18:
            return base_rate * 1.0
        elif 18 <= hour < 23:
            return base_rate * e
        else:  # 23-06
            return base_rate * n

    def compute_overnight_need(self, *, now: datetime | None = None) -> OvernightNeed:
        """Compute how much energy the battery needs to survive until solar kicks in.

        Simulates hour-by-hour drain from window_start until solar production
        covers consumption. Compares cumulative drain against estimated battery
        at window_start. Uses per-hour consumption profiles.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator
        daily_consumption = c.consumption_tracker.average(c.store.consumption_history)

        # Apply weekend multiplier if tomorrow is weekend
        tomorrow = now + timedelta(days=1)
        if tomorrow.weekday() >= 5:
            daily_consumption *= c.weekend_consumption_multiplier

        flat_hourly = daily_consumption / 24
        usable_capacity = c.battery_capacity * (c.max_charge_level - c.min_soc) / 100

        window_start = c.price_analyzer._window_start  # e.g. 22
        window_end = c.price_analyzer._window_end  # e.g. 6

        # Determine solar start hour and hourly data
        hourly_solar = c.solar_forecast_tomorrow_hourly
        source = "fallback"

        if hourly_solar:
            source = "forecast_solar"
            # Find the hour when solar >= hourly consumption
            solar_start_hour = float(window_end + PV_FALLBACK_BUFFER_HOURS)
            for hour in range(24):
                if hourly_solar.get(hour, 0) >= flat_hourly:
                    solar_start_hour = float(hour)
                    break
        else:
            sunrise = c.sunrise_hour_tomorrow
            if sunrise is not None:
                source = "sun_entity"
                solar_start_hour = sunrise + PV_RAMP_BUFFER_HOURS
            else:
                solar_start_hour = float(window_end + PV_FALLBACK_BUFFER_HOURS)

        # Simulate hour-by-hour from window_start to solar_start_hour
        solar_start_normalized = solar_start_hour
        if solar_start_normalized < window_start:
            solar_start_normalized += 24
        target_hours = solar_start_normalized - window_start

        cumulative_drain = 0.0
        dark_hours = 0.0
        while dark_hours < target_hours and dark_hours < MAX_OVERNIGHT_HOURS:
            actual_hour = int(window_start + dark_hours) % 24
            hourly_cons = self._hourly_consumption(actual_hour, daily_consumption)
            solar_this_hour = hourly_solar.get(actual_hour, 0.0) if hourly_solar else 0.0
            net_drain = max(0.0, hourly_cons - solar_this_hour)
            cumulative_drain += net_drain
            dark_hours += 1.0

        # Estimate battery at window_start
        current_hour = now.hour + now.minute / 60
        hours_to_window = window_start - current_hour
        if hours_to_window < 0:
            hours_to_window += 24

        current_usable = c.battery_capacity * (c.current_soc - c.min_soc) / 100
        current_usable = max(0.0, current_usable)

        # Pre-window discharge: consumption minus remaining solar today
        remaining_solar_today = max(
            0.0,
            c.forecast_corrector.adjust_forecast(
                c.solar_forecast_today, c.store.forecast_error_history
            ) - c.actual_solar_today,
        )
        pre_window_drain = max(
            0.0, hours_to_window * flat_hourly - remaining_solar_today
        )
        battery_at_window_start = max(0.0, current_usable - pre_window_drain)

        # Shortfall — apply charging efficiency
        shortfall = cumulative_drain - battery_at_window_start
        charge_needed = max(0.0, min(shortfall, usable_capacity))
        if charge_needed > 0:
            charge_needed /= c.charging_efficiency
            charge_needed = min(charge_needed, usable_capacity)
        charge_needed = round(charge_needed, 2)

        return OvernightNeed(
            dark_hours=round(dark_hours, 1),
            overnight_consumption=round(cumulative_drain, 2),
            battery_at_window_start=round(battery_at_window_start, 2),
            charge_needed=charge_needed,
            solar_start_hour=round(solar_start_hour, 2),
            source=source,
        )

    def compute_target_soc(
        self, deficit: EnergyDeficit, *, charge_kwh: float | None = None
    ) -> float:
        """Compute target SOC from energy deficit or explicit charge amount.

        target = min_soc + (charge_needed / capacity * 100), clamped to max_charge_level.
        """
        c = self._coordinator
        effective_charge = charge_kwh if charge_kwh is not None else deficit.charge_needed
        if effective_charge <= 0:
            return c.min_soc

        charge_pct = effective_charge / c.battery_capacity * 100
        target = c.min_soc + charge_pct
        return min(round(target, 1), c.max_charge_level)

    def plan_charging(self, *, now: datetime | None = None) -> ChargingSchedule | None:
        """Full planning pipeline.

        Returns a ChargingSchedule if charging is needed and prices are acceptable,
        or None if no charging needed / prices not available / prices too high.

        Considers both daily energy deficit AND overnight survival. The larger
        of the two determines the actual charge needed.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        if not c.enabled:
            _LOGGER.debug("Charging disabled, skipping planning")
            return None

        # Check if tomorrow's prices are available
        if not self.has_tomorrow_prices(now=now):
            _LOGGER.debug("Tomorrow's prices not available yet")
            return None

        # Compute energy deficit
        deficit = self.compute_energy_deficit(now=now)
        _LOGGER.info(
            "Energy deficit: consumption=%.1f, solar_adjusted=%.1f, deficit=%.1f, charge_needed=%.1f",
            deficit.consumption,
            deficit.solar_adjusted,
            deficit.deficit,
            deficit.charge_needed,
        )

        # Compute overnight survival need
        overnight = self.compute_overnight_need(now=now)
        self.last_overnight_need = overnight
        c._last_overnight = overnight
        _LOGGER.info(
            "Overnight survival: dark_hours=%.1f, consumption=%.1f, battery_at_start=%.1f, charge_needed=%.1f (%s)",
            overnight.dark_hours,
            overnight.overnight_consumption,
            overnight.battery_at_window_start,
            overnight.charge_needed,
            overnight.source,
        )

        # Use the larger of daily deficit and overnight need
        effective_charge = max(deficit.charge_needed, overnight.charge_needed)

        # Apply charging efficiency to daily deficit portion
        if deficit.charge_needed > 0 and deficit.charge_needed >= overnight.charge_needed:
            effective_charge = deficit.charge_needed / c.charging_efficiency
            usable_capacity = c.battery_capacity * (c.max_charge_level - c.min_soc) / 100
            effective_charge = min(effective_charge, usable_capacity)

        if effective_charge <= 0:
            _LOGGER.info("No charging needed — solar covers consumption and battery covers overnight")
            return None

        if overnight.charge_needed > deficit.charge_needed:
            _LOGGER.info(
                "Overnight survival triggers charging: overnight=%.1f kWh > daily_deficit=%.1f kWh",
                overnight.charge_needed,
                deficit.charge_needed,
            )

        # Extract night prices and find cheapest window
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        night_slots = c.price_analyzer.extract_night_prices(
            c.price_attributes, today, tomorrow
        )

        if not night_slots:
            _LOGGER.warning("No night price slots available")
            return None

        # Fix 11: Negative price exploitation — charge to max when it's free/profitable
        usable_capacity = c.battery_capacity * (c.max_charge_level - c.min_soc) / 100
        if night_slots:
            cheapest_price = min(slot.price for slot in night_slots)
            if cheapest_price <= 0 and effective_charge < usable_capacity:
                _LOGGER.info(
                    "Negative prices detected (%.2f), charging to maximum capacity %.1f kWh",
                    cheapest_price, usable_capacity,
                )
                effective_charge = usable_capacity

        # Calculate hours needed
        hours_needed = c.price_analyzer.calculate_hours_needed(
            effective_charge, c.max_charge_power
        )
        if hours_needed == 0:
            return None

        window = c.price_analyzer.find_cheapest_window(night_slots, hours_needed)
        if window is None:
            _LOGGER.warning(
                "Could not find contiguous %d-hour window in night prices",
                hours_needed,
            )
            return None

        # Check price threshold — skip check if avg price is <= 0 (getting paid)
        if window.avg_price > 0 and window.avg_price > c.max_charge_price:
            # M2: Emergency low-battery override
            current_soc = c.current_soc
            if current_soc < EMERGENCY_SOC_THRESHOLD and effective_charge > 0:
                _LOGGER.warning(
                    "Battery at %.0f%% (below emergency threshold %.0f%%) — "
                    "overriding price threshold (%.2f > %.2f)",
                    current_soc, EMERGENCY_SOC_THRESHOLD,
                    window.avg_price, c.max_charge_price,
                )
            else:
                _LOGGER.info(
                    "Cheapest window avg price %.2f exceeds threshold %.2f, skipping",
                    window.avg_price,
                    c.max_charge_price,
                )
                return None

        target_soc = self.compute_target_soc(deficit, charge_kwh=effective_charge)

        schedule = ChargingSchedule(
            start_hour=window.start_hour,
            end_hour=window.end_hour,
            window_hours=window.window_hours,
            avg_price=window.avg_price,
            required_kwh=round(effective_charge, 2),
            target_soc=target_soc,
            created_at=now,
        )

        _LOGGER.info(
            "Charging scheduled: %02d:00-%02d:00, %.1f kWh, target %.0f%%, avg price %.2f",
            schedule.start_hour,
            schedule.end_hour,
            schedule.required_kwh,
            schedule.target_soc,
            schedule.avg_price,
        )

        return schedule
