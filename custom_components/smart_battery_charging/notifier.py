"""Charging notification gateway — the ONLY class that calls notify services.

Sends rich notifications for planning, charging lifecycle, and morning safety events.
Each notification type has an independent toggle in the options flow.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_NOTIFICATION_SERVICE,
    CONF_NOTIFY_CHARGING_COMPLETE,
    CONF_NOTIFY_CHARGING_START,
    CONF_NOTIFY_MORNING_SAFETY,
    CONF_NOTIFY_PLANNING,
    DEFAULT_NOTIFICATION_SERVICE,
    DEFAULT_NOTIFY_CHARGING_COMPLETE,
    DEFAULT_NOTIFY_CHARGING_START,
    DEFAULT_NOTIFY_MORNING_SAFETY,
    DEFAULT_NOTIFY_PLANNING,
)
from .models import ChargingSchedule, ChargingSession, EnergyDeficit

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


class ChargingNotifier:
    """Single gateway for all charging notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SmartBatteryCoordinator,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._last_plan_hash: str | None = None
        self._last_plan_date: date | None = None

    @property
    def _service_name(self) -> str:
        return str(
            self._coordinator._opt(
                CONF_NOTIFICATION_SERVICE, DEFAULT_NOTIFICATION_SERVICE
            )
        )

    def _is_enabled(self, toggle_key: str, default: bool) -> bool:
        return bool(self._coordinator._opt(toggle_key, default))

    async def _async_send(self, title: str, message: str) -> None:
        """Send a notification via the configured service. Safe to call even if unconfigured."""
        service = self._service_name
        if not service:
            return
        try:
            await self._hass.services.async_call(
                "notify",
                service,
                {"title": title, "message": message},
            )
        except Exception:
            _LOGGER.exception("Failed to send notification via notify.%s", service)

    def _compute_plan_hash(
        self, schedule: ChargingSchedule | None, deficit: EnergyDeficit
    ) -> str:
        """Compute a hash of the plan for deduplication."""
        if schedule is None:
            key = f"no_schedule:{deficit.charge_needed:.1f}:{deficit.deficit:.1f}"
        else:
            key = (
                f"{schedule.start_hour}:{schedule.end_hour}:"
                f"{schedule.required_kwh:.1f}:{schedule.target_soc:.0f}:"
                f"{schedule.avg_price:.2f}"
            )
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _is_duplicate_plan(
        self, schedule: ChargingSchedule | None, deficit: EnergyDeficit
    ) -> bool:
        """Check if this plan is the same as the last one sent today."""
        today = date.today()
        plan_hash = self._compute_plan_hash(schedule, deficit)

        if self._last_plan_date == today and self._last_plan_hash == plan_hash:
            return True

        self._last_plan_hash = plan_hash
        self._last_plan_date = today
        return False

    async def async_notify_plan(
        self,
        schedule: ChargingSchedule | None,
        deficit: EnergyDeficit,
    ) -> None:
        """Send planning notification (3 variants: scheduled, not scheduled, not needed)."""
        if not self._is_enabled(CONF_NOTIFY_PLANNING, DEFAULT_NOTIFY_PLANNING):
            return
        if self._is_duplicate_plan(schedule, deficit):
            _LOGGER.debug("Skipping duplicate plan notification")
            return

        currency = self._coordinator.currency
        soc = self._coordinator.current_soc

        if deficit.charge_needed <= 0:
            # Solar covers consumption
            title = "☀️ No Charging Needed"
            message = (
                f"Solar forecast covers tomorrow's consumption.\n\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
            )
        elif schedule is not None:
            # Charging scheduled
            title = "🔋 Charging Scheduled"
            message = (
                f"Window: {schedule.start_hour:02d}:00–{schedule.end_hour:02d}:00 "
                f"({schedule.window_hours}h)\n"
                f"Charge: {schedule.required_kwh:.1f} kWh\n"
                f"SOC: {soc:.0f}% → {schedule.target_soc:.0f}%\n"
                f"Avg price: {schedule.avg_price:.2f} {currency}\n\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
            )
        else:
            # Deficit exists but no schedule (price too high or no prices)
            title = "⏸️ Charging Not Scheduled"
            max_price = self._coordinator.max_charge_price
            message = (
                f"Charging needed ({deficit.charge_needed:.1f} kWh) but not scheduled.\n"
                f"SOC: {soc:.0f}%\n"
                f"Price threshold: {max_price:.2f} {currency}\n\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
            )

        await self._async_send(title, message)

    async def async_notify_charging_started(
        self,
        current_soc: float,
        target_soc: float,
        required_kwh: float,
    ) -> None:
        """Send notification when charging starts."""
        if not self._is_enabled(
            CONF_NOTIFY_CHARGING_START, DEFAULT_NOTIFY_CHARGING_START
        ):
            return

        from datetime import datetime

        now = datetime.now().strftime("%H:%M")
        title = "🔋 Charging Started"
        message = (
            f"Time: {now}\n"
            f"SOC: {current_soc:.0f}% → {target_soc:.0f}%\n"
            f"Charge needed: {required_kwh:.1f} kWh"
        )
        await self._async_send(title, message)

    async def async_notify_charging_complete(
        self,
        session: ChargingSession,
        target_soc: float,
    ) -> None:
        """Send notification when charging completes."""
        if not self._is_enabled(
            CONF_NOTIFY_CHARGING_COMPLETE, DEFAULT_NOTIFY_CHARGING_COMPLETE
        ):
            return

        title = "✅ Charging Complete"

        # Calculate duration
        duration_str = ""
        if session.start_time and session.end_time:
            start_display = (
                session.start_time[11:16]
                if len(session.start_time) > 15
                else session.start_time
            )
            end_display = (
                session.end_time[11:16]
                if len(session.end_time) > 15
                else session.end_time
            )
            duration_str = f"Duration: {start_display}–{end_display}\n"

        message = (
            f"Reason: {session.result}\n"
            f"SOC: {session.start_soc:.0f}% → {session.end_soc:.0f}%\n"
            f"{duration_str}"
            f"Target was: {target_soc:.0f}%"
        )
        await self._async_send(title, message)

    async def async_notify_morning_safety(self, soc: float) -> None:
        """Send notification when morning safety stops charging."""
        if not self._is_enabled(
            CONF_NOTIFY_MORNING_SAFETY, DEFAULT_NOTIFY_MORNING_SAFETY
        ):
            return

        title = "🌅 Morning: Charging Stopped"
        message = (
            f"Morning safety triggered.\n"
            f"SOC: {soc:.0f}%\n"
            f"Mode restored to Self Use."
        )
        await self._async_send(title, message)
