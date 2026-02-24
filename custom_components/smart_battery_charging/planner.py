"""Charging planner — pure orchestration, no HA service calls.

Reads all data through the coordinator and its sub-components.
Decides whether charging is needed, how much, and when.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import ChargingSchedule, EnergyDeficit

if TYPE_CHECKING:
    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


class ChargingPlanner:
    """Plans charging sessions based on energy deficit and price analysis."""

    def __init__(self, coordinator: SmartBatteryCoordinator) -> None:
        self._coordinator = coordinator

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

    def compute_target_soc(self, deficit: EnergyDeficit) -> float:
        """Compute target SOC from energy deficit.

        target = min_soc + (charge_needed / capacity * 100), clamped to max_charge_level.
        """
        c = self._coordinator
        if deficit.charge_needed <= 0:
            return c.min_soc

        charge_pct = deficit.charge_needed / c.battery_capacity * 100
        target = c.min_soc + charge_pct
        return min(round(target, 1), c.max_charge_level)

    def plan_charging(self) -> ChargingSchedule | None:
        """Full planning pipeline.

        Returns a ChargingSchedule if charging is needed and prices are acceptable,
        or None if no charging needed / prices not available / prices too high.
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

        if deficit.charge_needed <= 0:
            _LOGGER.info("No charging needed — solar covers consumption")
            return None

        # Calculate hours needed
        hours_needed = c.price_analyzer.calculate_hours_needed(
            deficit.charge_needed, c.max_charge_power
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

        target_soc = self.compute_target_soc(deficit)

        schedule = ChargingSchedule(
            start_hour=window.start_hour,
            end_hour=window.end_hour,
            window_hours=window.window_hours,
            avg_price=window.avg_price,
            required_kwh=deficit.charge_needed,
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
