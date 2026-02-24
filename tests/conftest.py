"""Shared test fixtures for Smart Battery Charging tests.

Import pure logic modules directly to avoid triggering HA imports from __init__.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the component directory to the path so we can import pure-logic modules directly
_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "smart_battery_charging"
sys.path.insert(0, str(_COMPONENT_DIR))

from consumption_tracker import ConsumptionTracker
from forecast_corrector import ForecastCorrector
from price_analyzer import PriceAnalyzer


@pytest.fixture
def price_analyzer() -> PriceAnalyzer:
    """Return a PriceAnalyzer with default window (22:00 - 06:00)."""
    return PriceAnalyzer(window_start_hour=22, window_end_hour=6)


@pytest.fixture
def forecast_corrector() -> ForecastCorrector:
    """Return a ForecastCorrector with 7-day window."""
    return ForecastCorrector(window_days=7)


@pytest.fixture
def consumption_tracker() -> ConsumptionTracker:
    """Return a ConsumptionTracker with 7-day window, 20 kWh fallback."""
    return ConsumptionTracker(window_days=7, fallback_kwh=20.0)


@pytest.fixture
def sample_prices() -> dict[str, float]:
    """Return a realistic set of hourly electricity prices."""
    return {
        # Today's evening prices
        "2026-02-08T20:00:00+01:00": 3.5,
        "2026-02-08T21:00:00+01:00": 3.2,
        "2026-02-08T22:00:00+01:00": 2.1,
        "2026-02-08T23:00:00+01:00": 1.8,
        # Tomorrow's morning prices
        "2026-02-09T00:00:00+01:00": 1.5,
        "2026-02-09T01:00:00+01:00": 1.2,
        "2026-02-09T02:00:00+01:00": 1.4,
        "2026-02-09T03:00:00+01:00": 1.9,
        "2026-02-09T04:00:00+01:00": 2.3,
        "2026-02-09T05:00:00+01:00": 2.8,
        # Tomorrow's daytime prices
        "2026-02-09T06:00:00+01:00": 3.1,
        "2026-02-09T07:00:00+01:00": 3.5,
        "2026-02-09T08:00:00+01:00": 4.0,
        "2026-02-09T12:00:00+01:00": 3.8,
        "2026-02-09T18:00:00+01:00": 4.5,
    }
