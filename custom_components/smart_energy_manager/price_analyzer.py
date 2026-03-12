"""Pure logic for electricity price analysis and cheapest window selection.

No Home Assistant dependencies — fully unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PriceSlot:
    """A single hour price slot."""

    hour: int
    price: float


@dataclass
class PriceWindow:
    """A contiguous window of hours with average price."""

    start_hour: int
    end_hour: int
    window_hours: int
    avg_price: float
    prices: list[PriceSlot]


class PriceAnalyzer:
    """Analyzes electricity prices and finds cheapest charging windows."""

    def __init__(
        self,
        window_start_hour: int = 22,
        window_end_hour: int = 6,
    ) -> None:
        self._window_start = window_start_hour
        self._window_end = window_end_hour

    def extract_night_prices(
        self,
        all_prices: dict[str, float],
        today_date: str,
        tomorrow_date: str,
    ) -> list[PriceSlot]:
        """Extract prices for the night charging window.

        Args:
            all_prices: Dict mapping datetime strings to prices.
                Keys are ISO format like "2026-02-08T00:00:00+01:00"
                or "2026-02-08T00:00" etc.
            today_date: Today's date as "YYYY-MM-DD".
            tomorrow_date: Tomorrow's date as "YYYY-MM-DD".

        Returns:
            List of PriceSlot for hours in the charging window.
        """
        slots: list[PriceSlot] = []
        seen_hours: set[int] = set()

        for key, value in all_prices.items():
            key_str = str(key)
            if len(key_str) < 13:
                continue
            # Expect format like "2026-02-08T22:00:00+01:00" or similar
            if key_str[4:5] != "-" or key_str[7:8] != "-":
                continue

            date_part = key_str[:10]
            try:
                hour = int(key_str[11:13])
            except (ValueError, IndexError):
                continue

            try:
                price = float(value)
            except (ValueError, TypeError):
                continue

            # Tonight's hours (22-23) from today, morning hours (0-end) from tomorrow
            if date_part == today_date and hour >= self._window_start:
                if hour not in seen_hours:
                    slots.append(PriceSlot(hour=hour, price=price))
                    seen_hours.add(hour)
            elif date_part == tomorrow_date and hour < self._window_end:
                if hour not in seen_hours:
                    slots.append(PriceSlot(hour=hour, price=price))
                    seen_hours.add(hour)

        # Sort by hour, wrapping around midnight
        slots.sort(key=lambda s: s.hour if s.hour >= self._window_start else s.hour + 24)
        return slots

    def calculate_hours_needed(
        self,
        required_kwh: float,
        charge_power_kw: float,
    ) -> int:
        """Calculate how many hours of charging are needed.

        Returns integer hours, rounded up, clamped to [1, max_window_size].
        """
        if required_kwh <= 0 or charge_power_kw <= 0:
            return 0

        hours_float = required_kwh / charge_power_kw
        # H3: Round up to nearest integer (ceil)
        hours = max(1, math.ceil(hours_float))
        max_window = self._get_window_size()
        return min(hours, max_window)

    def find_cheapest_window(
        self,
        slots: list[PriceSlot],
        window_hours: int,
    ) -> PriceWindow | None:
        """Find the cheapest contiguous window of the given length.

        Args:
            slots: Available price slots (must be sorted).
            window_hours: Number of contiguous hours needed.

        Returns:
            The cheapest PriceWindow, or None if not enough slots.
        """
        if not slots or window_hours <= 0 or len(slots) < window_hours:
            return None

        # Build ordered hour sequence for the window
        ordered_hours = []
        for slot in slots:
            normalized = slot.hour if slot.hour >= self._window_start else slot.hour + 24
            ordered_hours.append(normalized)

        best: PriceWindow | None = None

        for i in range(len(slots) - window_hours + 1):
            window_slots = slots[i : i + window_hours]

            # Check contiguity
            is_contiguous = True
            for j in range(1, len(window_slots)):
                expected = ordered_hours[i + j - 1] + 1
                actual = ordered_hours[i + j]
                if actual != expected:
                    is_contiguous = False
                    break

            if not is_contiguous:
                continue

            avg_price = sum(s.price for s in window_slots) / window_hours
            start_hour = window_slots[0].hour
            end_hour = (window_slots[-1].hour + 1) % 24

            window = PriceWindow(
                start_hour=start_hour,
                end_hour=end_hour,
                window_hours=window_hours,
                avg_price=round(avg_price, 4),
                prices=window_slots,
            )

            if best is None or avg_price < best.avg_price:
                best = window

        return best

    def find_cheapest_hours(
        self,
        all_prices: dict[str, float],
        target_date: str,
        n: int = 3,
    ) -> list[PriceSlot]:
        """Find the N cheapest hours for a given date.

        Args:
            all_prices: Dict mapping datetime strings to prices.
            target_date: Date as "YYYY-MM-DD".
            n: Number of cheapest hours to return.

        Returns:
            List of PriceSlot sorted by price (cheapest first).
        """
        day_slots: list[PriceSlot] = []

        for key, value in all_prices.items():
            key_str = str(key)
            if len(key_str) < 13:
                continue
            if key_str[4:5] != "-" or key_str[7:8] != "-":
                continue
            if key_str[:10] != target_date:
                continue

            try:
                hour = int(key_str[11:13])
                price = float(value)
            except (ValueError, TypeError, IndexError):
                continue

            day_slots.append(PriceSlot(hour=hour, price=price))

        day_slots.sort(key=lambda s: s.price)
        return day_slots[:n]

    def classify_price(
        self,
        current_price: float,
        charge_threshold: float,
    ) -> str:
        """Classify the current price relative to the charge threshold.

        Returns one of: "Very Cheap", "Cheap", "Normal", "Expensive".
        """
        if charge_threshold <= 0:
            return "Normal"
        if current_price < charge_threshold * 0.7:
            return "Very Cheap"
        if current_price < charge_threshold:
            return "Cheap"
        if current_price < charge_threshold * 1.5:
            return "Normal"
        return "Expensive"

    def _get_window_size(self) -> int:
        """Get total hours in the charging window."""
        if self._window_end <= self._window_start:
            return (24 - self._window_start) + self._window_end
        return self._window_end - self._window_start
