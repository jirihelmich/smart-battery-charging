"""Tests for ForecastCorrector — pure logic, no HA deps."""

from __future__ import annotations

import pytest

from forecast_corrector import ForecastCorrector


class TestComputeError:
    """Test single-day error computation."""

    def test_overestimate(self, forecast_corrector: ForecastCorrector):
        # Forecast 8.0, actual 5.0 → 37.5% overestimate
        error = forecast_corrector.compute_error(8.0, 5.0)
        assert error == pytest.approx(0.375)

    def test_underestimate(self, forecast_corrector: ForecastCorrector):
        # Forecast 5.0, actual 8.0 → -60% (underestimate)
        error = forecast_corrector.compute_error(5.0, 8.0)
        assert error == pytest.approx(-0.6)

    def test_exact(self, forecast_corrector: ForecastCorrector):
        error = forecast_corrector.compute_error(5.0, 5.0)
        assert error == 0.0

    def test_low_forecast_returns_none(self, forecast_corrector: ForecastCorrector):
        # Below minimum threshold
        assert forecast_corrector.compute_error(0.3, 0.1) is None

    def test_zero_forecast_returns_none(self, forecast_corrector: ForecastCorrector):
        assert forecast_corrector.compute_error(0.0, 5.0) is None

    def test_real_winter_data(self, forecast_corrector: ForecastCorrector):
        """From actual SQL analysis: Feb 9, forecast 7.58, actual 5.52 → 27%."""
        error = forecast_corrector.compute_error(7.58, 5.52)
        assert error == pytest.approx(0.2717, abs=0.001)


class TestAverageError:
    """Test sliding window average."""

    def test_basic_average(self, forecast_corrector: ForecastCorrector):
        history = [0.27, 0.56, 0.64, 0.32, 0.56]
        avg = forecast_corrector.average_error(history)
        expected = sum(history) / len(history)
        assert avg == pytest.approx(expected, abs=0.001)

    def test_empty_history(self, forecast_corrector: ForecastCorrector):
        assert forecast_corrector.average_error([]) == 0.0

    def test_window_truncation(self):
        """Only use last N days."""
        corrector = ForecastCorrector(window_days=3)
        history = [0.1, 0.2, 0.3, 0.9, 0.9]
        avg = corrector.average_error(history)
        # Should only average first 3: (0.1 + 0.2 + 0.3) / 3 = 0.2
        assert avg == pytest.approx(0.2)

    def test_percentage(self, forecast_corrector: ForecastCorrector):
        history = [0.42]
        pct = forecast_corrector.average_error_pct(history)
        assert pct == 42.0


class TestAddEntry:
    """Test history management."""

    def test_prepend(self, forecast_corrector: ForecastCorrector):
        history = [0.3, 0.4]
        new = forecast_corrector.add_entry(history, 0.2)
        assert new == [0.2, 0.3, 0.4]

    def test_trim_to_window(self):
        corrector = ForecastCorrector(window_days=3)
        history = [0.1, 0.2, 0.3]
        new = corrector.add_entry(history, 0.05)
        assert len(new) == 3
        assert new == [0.05, 0.1, 0.2]

    def test_does_not_mutate(self, forecast_corrector: ForecastCorrector):
        history = [0.1, 0.2]
        forecast_corrector.add_entry(history, 0.05)
        assert history == [0.1, 0.2]


class TestAdjustForecast:
    """Test forecast adjustment."""

    def test_overestimate_reduces(self, forecast_corrector: ForecastCorrector):
        # 42% average overestimate → reduce forecast by 42%
        history = [0.42]
        adjusted = forecast_corrector.adjust_forecast(10.0, history)
        assert adjusted == pytest.approx(5.8)

    def test_underestimate_no_change(self, forecast_corrector: ForecastCorrector):
        # Negative error (underestimate) → don't increase forecast
        history = [-0.3]
        adjusted = forecast_corrector.adjust_forecast(10.0, history)
        assert adjusted == 10.0

    def test_no_history(self, forecast_corrector: ForecastCorrector):
        adjusted = forecast_corrector.adjust_forecast(10.0, [])
        assert adjusted == 10.0

    def test_mixed_history(self, forecast_corrector: ForecastCorrector):
        # Mix of over and underestimates — net positive
        history = [0.4, -0.1, 0.3, 0.5, -0.2, 0.3, 0.1]
        avg = sum(history) / len(history)  # ~0.186
        adjusted = forecast_corrector.adjust_forecast(10.0, history)
        expected = 10.0 * (1 - max(0, avg))
        assert adjusted == pytest.approx(expected, abs=0.1)
