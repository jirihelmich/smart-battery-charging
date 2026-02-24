"""Pure logic for solar forecast error tracking and correction.

No Home Assistant dependencies — fully unit-testable.

Tracks the ratio (forecast - actual) / forecast over a sliding window.
Positive values = forecast overestimates (common in winter).
Negative values = forecast underestimates (common in summer).
"""

from __future__ import annotations


class ForecastCorrector:
    """Tracks and corrects solar forecast errors using a sliding window."""

    def __init__(self, window_days: int = 7, min_forecast_kwh: float = 0.5) -> None:
        self._window_days = window_days
        self._min_forecast_kwh = min_forecast_kwh

    def compute_error(self, forecast_kwh: float, actual_kwh: float) -> float | None:
        """Compute forecast error ratio for a single day.

        Returns:
            Error ratio (0.0 to ~1.0 for overestimates, negative for underestimates),
            or None if forecast is too low to be meaningful.
        """
        if forecast_kwh < self._min_forecast_kwh:
            return None
        return round((forecast_kwh - actual_kwh) / forecast_kwh, 4)

    def average_error(self, history: list[float]) -> float:
        """Compute the average forecast error from history.

        Args:
            history: List of error ratios (most recent first).

        Returns:
            Average error as a ratio (e.g., 0.42 = 42% overestimate).
            Returns 0.0 if history is empty.
        """
        values = history[: self._window_days]
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    def average_error_pct(self, history: list[float]) -> float:
        """Compute the average forecast error as a percentage.

        Args:
            history: List of error ratios (most recent first).

        Returns:
            Average error as percentage (e.g., 42.0 = 42% overestimate).
        """
        return round(self.average_error(history) * 100, 1)

    def add_entry(self, history: list[float], error: float) -> list[float]:
        """Add a new error entry to the front of history, trimming to window size.

        Args:
            history: Existing history list.
            error: New error ratio to prepend.

        Returns:
            New history list (does not mutate input).
        """
        return [error] + history[: self._window_days - 1]

    def adjust_forecast(self, forecast_kwh: float, history: list[float]) -> float:
        """Adjust a solar forecast based on historical error.

        Only reduces forecast when historical error is positive (overestimate).
        When forecast underestimates (negative error), returns original value.

        Args:
            forecast_kwh: Raw forecast value.
            history: Error history list.

        Returns:
            Adjusted forecast value.
        """
        avg_error = self.average_error(history)
        # Only correct downward (for overestimates)
        correction = max(0.0, avg_error)
        return round(forecast_kwh * (1 - correction), 2)
