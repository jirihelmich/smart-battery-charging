"""Charging planner — pure orchestration, no HA service calls.

Reads all data through the coordinator and its sub-components.
Decides whether charging is needed, how much, and when.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import ChargingSchedule, EnergyDeficit, OvernightNeed

if TYPE_CHECKING:
    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


class ChargingPlanner:
    """Plans charging sessions based on energy deficit and price analysis."""

    def __init__(self, coordinator: SmartBatteryCoordinator) -> None:
        self._coordinator = coordinator
        self.last_overnight_need: OvernightNeed | None = None

    def compute_energy_deficit(self) -> EnergyDeficit:
        """Compute energy deficit for tomorrow.

        Returns an EnergyDeficit with consumption, solar (raw and adjusted),
        forecast error percentage, deficit, charge needed, and usable capacity.
        """
        c = self._coordinator

        # Get consumption average
        consumption = c.consumption_tracker.average(c.store.consumption_history)

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

    def has_tomorrow_prices(self) -> bool:
        """Check if tomorrow's prices are available in the price sensor attributes."""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        attrs = self._coordinator.price_attributes
        for key in attrs:
            key_str = str(key)
            if len(key_str) >= 10 and key_str[:10] == tomorrow:
                return True
        return False

    def compute_overnight_need(self) -> OvernightNeed:
        """Compute how much energy the battery needs to survive until solar kicks in.

        Simulates hour-by-hour drain from window_start until solar production
        covers consumption. Compares cumulative drain against estimated battery
        at window_start.
        """
        c = self._coordinator
        hourly_consumption = c.consumption_tracker.average(c.store.consumption_history) / 24
        usable_capacity = c.battery_capacity * (c.max_charge_level - c.min_soc) / 100

        window_start = c.price_analyzer._window_start  # e.g. 22
        window_end = c.price_analyzer._window_end  # e.g. 6

        # Determine solar start hour and hourly data
        hourly_solar = c.solar_forecast_tomorrow_hourly
        source = "fallback"

        if hourly_solar:
            source = "forecast_solar"
            # Find the hour when solar >= hourly consumption
            solar_start_hour = float(window_end + 3)  # default: 3h after window end
            for hour in range(24):
                if hourly_solar.get(hour, 0) >= hourly_consumption:
                    solar_start_hour = float(hour)
                    break
        else:
            sunrise = c.sunrise_hour_tomorrow
            if sunrise is not None:
                source = "sun_entity"
                solar_start_hour = sunrise + 2.0  # 2h buffer for PV ramp-up
            else:
                solar_start_hour = float(window_end + 3)  # fallback: ~09:00

        # Simulate hour-by-hour from window_start to solar_start_hour
        # Normalize solar_start relative to window_start for easy comparison
        solar_start_normalized = solar_start_hour
        if solar_start_normalized < window_start:
            solar_start_normalized += 24
        target_hours = solar_start_normalized - window_start

        cumulative_drain = 0.0
        dark_hours = 0.0
        while dark_hours < target_hours and dark_hours < 14:
            actual_hour = int(window_start + dark_hours) % 24
            solar_this_hour = hourly_solar.get(actual_hour, 0.0) if hourly_solar else 0.0
            net_drain = max(0.0, hourly_consumption - solar_this_hour)
            cumulative_drain += net_drain
            dark_hours += 1.0

        # Estimate battery at window_start
        now = datetime.now()
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
            0.0, hours_to_window * hourly_consumption - remaining_solar_today
        )
        battery_at_window_start = max(0.0, current_usable - pre_window_drain)

        # Shortfall
        shortfall = cumulative_drain - battery_at_window_start
        charge_needed = round(max(0.0, min(shortfall, usable_capacity)), 2)

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

    def plan_charging(self) -> ChargingSchedule | None:
        """Full planning pipeline.

        Returns a ChargingSchedule if charging is needed and prices are acceptable,
        or None if no charging needed / prices not available / prices too high.

        Considers both daily energy deficit AND overnight survival. The larger
        of the two determines the actual charge needed.
        """
        c = self._coordinator

        if not c.enabled:
            _LOGGER.debug("Charging disabled, skipping planning")
            return None

        # Check if tomorrow's prices are available
        if not self.has_tomorrow_prices():
            _LOGGER.debug("Tomorrow's prices not available yet")
            return None

        # Compute energy deficit
        deficit = self.compute_energy_deficit()
        _LOGGER.info(
            "Energy deficit: consumption=%.1f, solar_adjusted=%.1f, deficit=%.1f, charge_needed=%.1f",
            deficit.consumption,
            deficit.solar_adjusted,
            deficit.deficit,
            deficit.charge_needed,
        )

        # Compute overnight survival need
        overnight = self.compute_overnight_need()
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

        if effective_charge <= 0:
            _LOGGER.info("No charging needed — solar covers consumption and battery covers overnight")
            return None

        if overnight.charge_needed > deficit.charge_needed:
            _LOGGER.info(
                "Overnight survival triggers charging: overnight=%.1f kWh > daily_deficit=%.1f kWh",
                overnight.charge_needed,
                deficit.charge_needed,
            )

        # Calculate hours needed
        hours_needed = c.price_analyzer.calculate_hours_needed(
            effective_charge, c.max_charge_power
        )
        if hours_needed == 0:
            return None

        # Extract night prices and find cheapest window
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        night_slots = c.price_analyzer.extract_night_prices(
            c.price_attributes, today, tomorrow
        )

        if not night_slots:
            _LOGGER.warning("No night price slots available")
            return None

        window = c.price_analyzer.find_cheapest_window(night_slots, hours_needed)
        if window is None:
            _LOGGER.warning(
                "Could not find contiguous %d-hour window in night prices",
                hours_needed,
            )
            return None

        # Check price threshold
        if window.avg_price > c.max_charge_price:
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
