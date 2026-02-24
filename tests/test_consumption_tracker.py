"""Tests for ConsumptionTracker — pure logic, no HA deps."""

from __future__ import annotations

import pytest

from consumption_tracker import ConsumptionTracker


class TestAverage:
    """Test sliding window average computation."""

    def test_basic_average(self, consumption_tracker: ConsumptionTracker):
        history = [16.97, 17.58, 16.22]
        avg = consumption_tracker.average(history)
        expected = round((16.97 + 17.58 + 16.22) / 3, 2)
        assert avg == expected

    def test_empty_returns_fallback(self, consumption_tracker: ConsumptionTracker):
        assert consumption_tracker.average([]) == 20.0

    def test_zero_values_excluded(self, consumption_tracker: ConsumptionTracker):
        history = [16.0, 0.0, 17.0]
        avg = consumption_tracker.average(history)
        assert avg == round((16.0 + 17.0) / 2, 2)

    def test_all_zeros_returns_fallback(self, consumption_tracker: ConsumptionTracker):
        assert consumption_tracker.average([0.0, 0.0, 0.0]) == 20.0

    def test_window_truncation(self):
        tracker = ConsumptionTracker(window_days=3, fallback_kwh=20.0)
        history = [10.0, 11.0, 12.0, 99.0, 99.0]
        avg = tracker.average(history)
        assert avg == round((10.0 + 11.0 + 12.0) / 3, 2)

    def test_custom_fallback(self):
        tracker = ConsumptionTracker(window_days=7, fallback_kwh=15.0)
        assert tracker.average([]) == 15.0


class TestAddEntry:
    """Test history management."""

    def test_prepend(self, consumption_tracker: ConsumptionTracker):
        history = [16.0, 17.0]
        new = consumption_tracker.add_entry(history, 15.5)
        assert new == [15.5, 16.0, 17.0]

    def test_trim(self):
        tracker = ConsumptionTracker(window_days=3)
        history = [10.0, 11.0, 12.0]
        new = tracker.add_entry(history, 9.0)
        assert len(new) == 3
        assert new == [9.0, 10.0, 11.0]

    def test_skip_zero(self, consumption_tracker: ConsumptionTracker):
        history = [16.0]
        new = consumption_tracker.add_entry(history, 0.0)
        assert new == [16.0]

    def test_skip_negative(self, consumption_tracker: ConsumptionTracker):
        history = [16.0]
        new = consumption_tracker.add_entry(history, -5.0)
        assert new == [16.0]

    def test_does_not_mutate(self, consumption_tracker: ConsumptionTracker):
        history = [16.0, 17.0]
        consumption_tracker.add_entry(history, 15.0)
        assert history == [16.0, 17.0]

    def test_rounds_value(self, consumption_tracker: ConsumptionTracker):
        new = consumption_tracker.add_entry([], 16.9712345)
        assert new == [16.97]


class TestMetadata:
    """Test metadata helpers."""

    def test_days_tracked(self, consumption_tracker: ConsumptionTracker):
        assert consumption_tracker.days_tracked([16.0, 0.0, 17.0]) == 2

    def test_source_sliding_window(self, consumption_tracker: ConsumptionTracker):
        assert consumption_tracker.source([16.0]) == "sliding_window"

    def test_source_fallback(self, consumption_tracker: ConsumptionTracker):
        assert consumption_tracker.source([]) == "fallback"

    def test_fallback_setter(self):
        tracker = ConsumptionTracker(fallback_kwh=20.0)
        tracker.fallback_kwh = 15.0
        assert tracker.average([]) == 15.0
