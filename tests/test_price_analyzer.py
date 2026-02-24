"""Tests for PriceAnalyzer — pure logic, no HA deps."""

from __future__ import annotations

import pytest

from price_analyzer import PriceAnalyzer, PriceSlot


class TestExtractNightPrices:
    """Test night price extraction from sensor attributes."""

    def test_basic_extraction(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        slots = price_analyzer.extract_night_prices(
            sample_prices, "2026-02-08", "2026-02-09"
        )
        hours = [s.hour for s in slots]
        # Should include 22, 23 from today and 0-5 from tomorrow
        assert 22 in hours
        assert 23 in hours
        assert 0 in hours
        assert 1 in hours
        assert 5 in hours
        # Should NOT include hours outside window
        assert 6 not in hours
        assert 20 not in hours

    def test_sorted_by_time(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        slots = price_analyzer.extract_night_prices(
            sample_prices, "2026-02-08", "2026-02-09"
        )
        hours = [s.hour for s in slots]
        # Should be in chronological order: 22, 23, 0, 1, 2, 3, 4, 5
        expected = [22, 23, 0, 1, 2, 3, 4, 5]
        assert hours == expected

    def test_empty_prices(self, price_analyzer: PriceAnalyzer):
        slots = price_analyzer.extract_night_prices({}, "2026-02-08", "2026-02-09")
        assert slots == []

    def test_no_tomorrow_prices(self, price_analyzer: PriceAnalyzer):
        prices = {
            "2026-02-08T22:00:00+01:00": 2.0,
            "2026-02-08T23:00:00+01:00": 1.8,
        }
        slots = price_analyzer.extract_night_prices(prices, "2026-02-08", "2026-02-09")
        assert len(slots) == 2
        assert slots[0].hour == 22
        assert slots[1].hour == 23

    def test_ignores_non_price_attributes(self, price_analyzer: PriceAnalyzer):
        prices = {
            "friendly_name": "Electricity Price",
            "unit_of_measurement": "Kč/kWh",
            "2026-02-09T01:00:00+01:00": 1.5,
        }
        slots = price_analyzer.extract_night_prices(prices, "2026-02-08", "2026-02-09")
        assert len(slots) == 1
        assert slots[0].price == 1.5


class TestCalculateHoursNeeded:
    """Test charging hours calculation."""

    def test_basic_calculation(self, price_analyzer: PriceAnalyzer):
        # 5 kWh at 10 kW = 0.5 hours → rounds to 1
        assert price_analyzer.calculate_hours_needed(5.0, 10.0) == 1

    def test_larger_charge(self, price_analyzer: PriceAnalyzer):
        # 25 kWh at 10 kW = 2.5 hours → rounds to 3
        assert price_analyzer.calculate_hours_needed(25.0, 10.0) == 3

    def test_max_capped(self, price_analyzer: PriceAnalyzer):
        # 100 kWh at 5 kW = 20 hours → capped at 8 (window size 22-06)
        assert price_analyzer.calculate_hours_needed(100.0, 5.0) == 8

    def test_zero_kwh(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.calculate_hours_needed(0.0, 10.0) == 0

    def test_negative_kwh(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.calculate_hours_needed(-5.0, 10.0) == 0

    def test_zero_power(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.calculate_hours_needed(5.0, 0.0) == 0


class TestFindCheapestWindow:
    """Test cheapest window selection."""

    def test_basic_window(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        slots = price_analyzer.extract_night_prices(
            sample_prices, "2026-02-08", "2026-02-09"
        )
        window = price_analyzer.find_cheapest_window(slots, 3)
        assert window is not None
        # Cheapest 3-hour window should start at hour 0 or 1
        # Prices: 22=2.1, 23=1.8, 0=1.5, 1=1.2, 2=1.4, 3=1.9, 4=2.3, 5=2.8
        # Window 0-2: avg = (1.5+1.2+1.4)/3 = 1.367
        # Window 23-1: avg = (1.8+1.5+1.2)/3 = 1.5
        # Window 1-3: avg = (1.2+1.4+1.9)/3 = 1.5
        assert window.start_hour == 0
        assert window.end_hour == 3
        assert window.window_hours == 3

    def test_single_hour_window(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        slots = price_analyzer.extract_night_prices(
            sample_prices, "2026-02-08", "2026-02-09"
        )
        window = price_analyzer.find_cheapest_window(slots, 1)
        assert window is not None
        assert window.start_hour == 1  # Cheapest single hour: 1.2
        assert window.avg_price == 1.2

    def test_no_slots(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.find_cheapest_window([], 2) is None

    def test_not_enough_slots(self, price_analyzer: PriceAnalyzer):
        slots = [PriceSlot(hour=1, price=1.5)]
        assert price_analyzer.find_cheapest_window(slots, 3) is None

    def test_non_contiguous_slots(self, price_analyzer: PriceAnalyzer):
        """Gaps in the slots should prevent window formation across the gap."""
        slots = [
            PriceSlot(hour=22, price=1.0),
            PriceSlot(hour=23, price=1.0),
            # Gap: hour 0 missing
            PriceSlot(hour=1, price=1.0),
            PriceSlot(hour=2, price=1.0),
        ]
        window = price_analyzer.find_cheapest_window(slots, 3)
        # Can't form 3 contiguous hours across the gap
        assert window is None


class TestFindCheapestHours:
    """Test finding N cheapest hours in a day."""

    def test_basic(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        cheapest = price_analyzer.find_cheapest_hours(
            sample_prices, "2026-02-09", n=3
        )
        assert len(cheapest) == 3
        # Should be sorted by price
        assert cheapest[0].price <= cheapest[1].price <= cheapest[2].price

    def test_no_prices_for_date(self, price_analyzer: PriceAnalyzer, sample_prices: dict):
        cheapest = price_analyzer.find_cheapest_hours(
            sample_prices, "2026-03-01", n=3
        )
        assert cheapest == []


class TestClassifyPrice:
    """Test price classification."""

    def test_very_cheap(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.classify_price(1.0, 4.0) == "Very Cheap"

    def test_cheap(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.classify_price(3.0, 4.0) == "Cheap"

    def test_normal(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.classify_price(5.0, 4.0) == "Normal"

    def test_expensive(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.classify_price(7.0, 4.0) == "Expensive"

    def test_zero_threshold(self, price_analyzer: PriceAnalyzer):
        assert price_analyzer.classify_price(1.0, 0.0) == "Normal"
