"""Pure logic for daily consumption tracking with a sliding window.

No Home Assistant dependencies — fully unit-testable.
"""

from __future__ import annotations


class ConsumptionTracker:
    """Tracks daily consumption using a sliding window average."""

    def __init__(self, window_days: int = 7, fallback_kwh: float = 20.0) -> None:
        self._window_days = window_days
        self._fallback_kwh = fallback_kwh

    @property
    def fallback_kwh(self) -> float:
        """Return the fallback consumption value."""
        return self._fallback_kwh

    @fallback_kwh.setter
    def fallback_kwh(self, value: float) -> None:
        """Set the fallback consumption value."""
        self._fallback_kwh = value

    def average(self, history: list[float]) -> float:
        """Compute the sliding window average consumption.

        Args:
            history: List of daily consumption values (most recent first).

        Returns:
            Average of the values in the window, or fallback if empty.
        """
        values = [v for v in history[: self._window_days] if v > 0]
        if not values:
            return self._fallback_kwh
        return round(sum(values) / len(values), 2)

    def add_entry(self, history: list[float], value: float) -> list[float]:
        """Add a new daily value to the front of history, trimming to window size.

        Args:
            history: Existing history list.
            value: New daily consumption in kWh.

        Returns:
            New history list (does not mutate input).
        """
        if value <= 0:
            return list(history)
        return [round(value, 2)] + history[: self._window_days - 1]

    @property
    def window_days(self) -> int:
        """Return the window size."""
        return self._window_days

    def days_tracked(self, history: list[float]) -> int:
        """Return the number of valid entries in history."""
        return len([v for v in history[: self._window_days] if v > 0])

    def source(self, history: list[float]) -> str:
        """Return whether the average comes from 'sliding_window' or 'fallback'."""
        values = [v for v in history[: self._window_days] if v > 0]
        return "sliding_window" if values else "fallback"
