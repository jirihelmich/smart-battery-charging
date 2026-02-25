"""Data models for the Smart Battery Charging integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ChargingState(Enum):
    """State machine states for the charging controller."""

    IDLE = "idle"
    SCHEDULED = "scheduled"
    CHARGING = "charging"
    COMPLETE = "complete"
    DISABLED = "disabled"


@dataclass
class PriceWindow:
    """A contiguous window of hours with their prices."""

    start_hour: int
    end_hour: int
    avg_price: float
    window_hours: int
    prices: list[float] = field(default_factory=list)


@dataclass
class ChargingSchedule:
    """A planned charging session."""

    start_hour: int
    end_hour: int
    window_hours: int
    avg_price: float
    required_kwh: float
    target_soc: float
    created_at: datetime | None = None


@dataclass
class ChargingSession:
    """Record of a completed (or in-progress) charging session."""

    start_soc: float = 0.0
    end_soc: float = 0.0
    start_time: str = ""
    end_time: str = ""
    avg_price: float = 0.0
    result: str = ""

    @property
    def charged_kwh(self) -> float:
        """Cannot compute without battery capacity — use kwh_charged(capacity)."""
        return 0.0

    def kwh_charged(self, battery_capacity_kwh: float) -> float:
        """Calculate kWh charged from SOC delta."""
        if self.end_soc > self.start_soc:
            return round((self.end_soc - self.start_soc) / 100 * battery_capacity_kwh, 2)
        return 0.0

    def total_cost(self, battery_capacity_kwh: float) -> float:
        """Calculate total cost of the charging session."""
        return round(self.kwh_charged(battery_capacity_kwh) * self.avg_price, 1)


@dataclass
class EnergyDeficit:
    """Result of the energy deficit calculation."""

    consumption: float
    solar_raw: float
    solar_adjusted: float
    forecast_error_pct: float
    deficit: float
    charge_needed: float
    usable_capacity: float


@dataclass
class OvernightNeed:
    """Result of the overnight survival calculation.

    Determines whether the battery can bridge the gap from window_start
    (e.g. 22:00) until solar production meaningfully covers consumption.
    """

    dark_hours: float  # Hours from window_start to solar coverage
    overnight_consumption: float  # kWh consumed during dark hours
    battery_at_window_start: float  # Estimated usable kWh at window start
    charge_needed: float  # max(0, overnight_consumption - battery), clamped
    solar_start_hour: float  # Hour when PV covers consumption
    source: str  # "forecast_solar" or "sun_entity" or "fallback"
