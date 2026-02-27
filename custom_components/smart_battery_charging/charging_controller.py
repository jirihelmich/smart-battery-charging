"""Charging state machine — manages the charging lifecycle.

State transitions handle starting, monitoring, and stopping the inverter.
Delegates all Modbus writes to InverterController.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import CHARGE_HISTORY_DAYS, STALL_ABORT_TICKS, STALL_RETRY_TICKS, START_FAILURE_MAX_RETRIES
from .models import ChargingSchedule, ChargingSession, ChargingState

if TYPE_CHECKING:
    from datetime import datetime

    from .coordinator import SmartBatteryCoordinator
    from .inverters.base import BaseInverterController as InverterController
    from .notifier import ChargingNotifier

_LOGGER = logging.getLogger(__name__)


class ChargingStateMachine:
    """State machine managing the charging lifecycle."""

    def __init__(
        self,
        coordinator: SmartBatteryCoordinator,
        inverter: InverterController,
        notifier: ChargingNotifier | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._inverter = inverter
        self._notifier = notifier
        self._session: ChargingSession | None = None
        # Stall detection (Fix 3)
        self._stall_start_soc: float | None = None
        self._stall_tick_count: int = 0
        # Start failure tracking (C3)
        self._start_fail_count: int = 0

    @property
    def state(self) -> ChargingState:
        return self._coordinator.charging_state

    async def _set_state(self, value: ChargingState) -> None:
        """Set state on coordinator and persist to store."""
        _LOGGER.info("State transition: %s → %s", self._coordinator.charging_state.value, value.value)
        self._coordinator.charging_state = value
        await self._coordinator.store.async_set_charging_state(value.value)

    @property
    def schedule(self) -> ChargingSchedule | None:
        return self._coordinator.current_schedule

    async def _set_schedule(self, value: ChargingSchedule | None) -> None:
        """Set schedule on coordinator and persist to store."""
        self._coordinator.current_schedule = value
        if value is None:
            await self._coordinator.store.async_set_current_schedule(None)
        else:
            await self._coordinator.store.async_set_current_schedule({
                "start_hour": value.start_hour,
                "end_hour": value.end_hour,
                "window_hours": value.window_hours,
                "avg_price": value.avg_price,
                "required_kwh": value.required_kwh,
                "target_soc": value.target_soc,
            })

    def _now(self) -> datetime:
        """Get current time (override in tests)."""
        return dt_util.now()

    def _is_in_window(self, schedule: ChargingSchedule) -> bool:
        """Check if current time is within the charging window."""
        now = self._now()
        current_hour = now.hour

        start = schedule.start_hour
        end = schedule.end_hour

        # Handle midnight wraparound (e.g., 22:00 - 06:00)
        if start > end:
            return current_hour >= start or current_hour < end
        else:
            return start <= current_hour < end

    async def async_on_plan(self, schedule: ChargingSchedule | None) -> None:
        """Handle a new plan from the planner.

        IDLE/COMPLETE + schedule → SCHEDULED
        IDLE + None → IDLE (record no charging needed)
        """
        if schedule is None:
            if self.state in (ChargingState.IDLE, ChargingState.COMPLETE):
                self._session = ChargingSession(result="No charging needed")
                await self._save_session()
                await self._set_state(ChargingState.IDLE)
            _LOGGER.info("No charging scheduled")
            return

        if self.state in (
            ChargingState.IDLE,
            ChargingState.COMPLETE,
            ChargingState.SCHEDULED,
        ):
            await self._set_schedule(schedule)
            self._session = ChargingSession(avg_price=schedule.avg_price)
            self._start_fail_count = 0
            await self._set_state(ChargingState.SCHEDULED)
            _LOGGER.info(
                "Charging scheduled: %02d:00-%02d:00, target %.0f%%",
                schedule.start_hour,
                schedule.end_hour,
                schedule.target_soc,
            )
        else:
            _LOGGER.debug(
                "Ignoring plan in state %s", self.state.value
            )

    async def async_on_tick(self) -> None:
        """Handle periodic tick (every 2 minutes).

        SCHEDULED + in_window + soc < target → start charging → CHARGING
        SCHEDULED + in_window + soc >= target → already reached → COMPLETE
        CHARGING + soc >= target → stop → COMPLETE
        CHARGING + window ended → stop → COMPLETE
        """
        if self.state == ChargingState.SCHEDULED:
            await self._handle_scheduled_tick()
        elif self.state == ChargingState.CHARGING:
            await self._handle_charging_tick()

    async def _handle_scheduled_tick(self) -> None:
        """Handle tick while in SCHEDULED state."""
        schedule = self.schedule
        if schedule is None:
            await self._set_state(ChargingState.IDLE)
            return

        if not self._is_in_window(schedule):
            return  # Not yet time

        if not self._coordinator.soc_sensor_available:
            _LOGGER.debug("SOC sensor unavailable, skipping scheduled tick")
            return

        soc = self._coordinator.current_soc

        if soc >= schedule.target_soc:
            # Already at or above target
            _LOGGER.info("SOC %.0f%% already at target %.0f%%, skipping charge", soc, schedule.target_soc)
            if self._session:
                self._session.start_soc = soc
                self._session.end_soc = soc
                self._session.result = "Already at target"
                now_str = self._now().isoformat()
                self._session.start_time = now_str
                self._session.end_time = now_str
            await self._save_session()
            await self._set_state(ChargingState.COMPLETE)
            return

        # Start charging
        _LOGGER.info("Starting charge: SOC %.0f%%, target %.0f%%", soc, schedule.target_soc)
        ok = await self._inverter.async_start_charging(schedule.target_soc)

        if not ok:
            # C3: Don't transition to CHARGING when start fails
            self._start_fail_count += 1
            _LOGGER.warning(
                "Inverter did not confirm charge start (attempt %d/%d)",
                self._start_fail_count, START_FAILURE_MAX_RETRIES,
            )
            if self._start_fail_count >= START_FAILURE_MAX_RETRIES:
                _LOGGER.error(
                    "Inverter start failed %d times, aborting schedule",
                    self._start_fail_count,
                )
                if self._session:
                    self._session.start_soc = soc
                    self._session.end_soc = soc
                    self._session.result = "Inverter command failed"
                    now_str = self._now().isoformat()
                    self._session.start_time = now_str
                    self._session.end_time = now_str
                await self._save_session()
                await self._set_state(ChargingState.IDLE)
                if self._notifier:
                    await self._notifier.async_notify_charging_stalled(
                        soc, schedule.target_soc, 0
                    )
            return  # Stay SCHEDULED, retry on next tick

        if self._session:
            self._session.start_soc = soc
            self._session.start_time = self._now().isoformat()

        # Reset stall counters and start failure count
        self._stall_start_soc = soc
        self._stall_tick_count = 0
        self._start_fail_count = 0

        await self._set_state(ChargingState.CHARGING)

        if self._notifier:
            await self._notifier.async_notify_charging_started(
                soc, schedule.target_soc, schedule.required_kwh
            )

    async def _handle_charging_tick(self) -> None:
        """Handle tick while in CHARGING state."""
        schedule = self.schedule
        if schedule is None:
            # Shouldn't happen, but recover gracefully
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            await self._set_state(ChargingState.IDLE)
            return

        if not self._coordinator.soc_sensor_available:
            _LOGGER.warning("SOC sensor unavailable during charging, skipping tick")
            return

        soc = self._coordinator.current_soc

        if soc >= schedule.target_soc:
            # Target reached
            _LOGGER.info("Target SOC %.0f%% reached (current: %.0f%%)", schedule.target_soc, soc)
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            if self._session:
                self._session.end_soc = soc
                self._session.end_time = self._now().isoformat()
                self._session.result = "Target reached"
            await self._save_session()
            await self._set_state(ChargingState.COMPLETE)
            if self._notifier and self._session:
                await self._notifier.async_notify_charging_complete(
                    self._session, schedule.target_soc
                )
            return

        if not self._is_in_window(schedule):
            # Window ended
            _LOGGER.info("Charging window ended, SOC at %.0f%%", soc)
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            if self._session:
                self._session.end_soc = soc
                self._session.end_time = self._now().isoformat()
                self._session.result = "Window ended"
            await self._save_session()
            await self._set_state(ChargingState.COMPLETE)
            if self._notifier and self._session:
                await self._notifier.async_notify_charging_complete(
                    self._session, schedule.target_soc
                )
            return

        # Stall detection: check if SOC has moved
        if self._stall_start_soc is not None and soc == self._stall_start_soc:
            self._stall_tick_count += 1

            if self._stall_tick_count == STALL_RETRY_TICKS:
                _LOGGER.warning(
                    "Charging stalled at %.0f%% for %d ticks, retrying charge command",
                    soc, self._stall_tick_count,
                )
                await self._inverter.async_start_charging(schedule.target_soc)

            elif self._stall_tick_count >= STALL_ABORT_TICKS:
                minutes_stalled = self._stall_tick_count * 2
                _LOGGER.error(
                    "Charging stalled at %.0f%% for %d min, aborting",
                    soc, minutes_stalled,
                )
                await self._inverter.async_stop_charging(self._coordinator.min_soc)
                if self._session:
                    self._session.end_soc = soc
                    self._session.end_time = self._now().isoformat()
                    self._session.result = "Charging stalled"
                await self._save_session()
                await self._set_state(ChargingState.COMPLETE)
                if self._notifier:
                    await self._notifier.async_notify_charging_stalled(
                        soc, schedule.target_soc, minutes_stalled
                    )
                return
        else:
            # SOC changed — reset stall tracking
            self._stall_start_soc = soc
            self._stall_tick_count = 0

        _LOGGER.debug("Charging in progress: SOC %.0f%%, target %.0f%%", soc, schedule.target_soc)

    async def async_on_morning_safety(self) -> None:
        """Handle morning safety trigger (sunrise - 15min).

        Ensures inverter is in Self Use mode regardless of current state.
        If we were CHARGING or SCHEDULED, record a safety stop.
        Also stops if mode sensor is unavailable (safer to assume manual mode).
        """
        # Always check if inverter is in manual mode and restore if needed
        current_mode = await self._inverter.async_get_current_mode()
        mode_unavailable = current_mode == ""

        if self.state == ChargingState.CHARGING:
            _LOGGER.warning("Morning safety: stopping active charge")
            soc = self._coordinator.current_soc
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            if self._session:
                self._session.end_soc = soc
                self._session.end_time = self._now().isoformat()
                self._session.result = "Morning safety stop"
            await self._save_session()
            await self._set_state(ChargingState.IDLE)
            if self._notifier:
                await self._notifier.async_notify_morning_safety(soc)
        elif self.state == ChargingState.SCHEDULED:
            # After HA restart, CHARGING resumes as SCHEDULED — inverter may still be in Manual Mode
            _LOGGER.warning("Morning safety: clearing stale SCHEDULED state")
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            await self._set_state(ChargingState.IDLE)
        elif self._inverter.is_manual_mode(current_mode) or mode_unavailable:
            if mode_unavailable:
                _LOGGER.warning("Morning safety: mode sensor unavailable, restoring Self Use as precaution")
            else:
                _LOGGER.warning("Morning safety: inverter in Manual Mode, restoring Self Use")
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            await self._set_state(ChargingState.IDLE)
        else:
            _LOGGER.debug("Morning safety: all clear, inverter in %s", current_mode)

        # Reset schedule for new day
        await self._set_schedule(None)

    async def async_on_disable(self) -> None:
        """Handle master switch being turned off.

        If charging is active, stop the inverter.
        """
        if self.state == ChargingState.CHARGING:
            _LOGGER.info("Disabled while charging, stopping inverter")
            soc = self._coordinator.current_soc
            await self._inverter.async_stop_charging(self._coordinator.min_soc)
            if self._session:
                self._session.end_soc = soc
                self._session.end_time = self._now().isoformat()
                self._session.result = "Disabled"
            await self._save_session()

        await self._set_schedule(None)
        await self._set_state(ChargingState.DISABLED)

    async def async_on_enable(self) -> None:
        """Handle master switch being turned on."""
        if self.state == ChargingState.DISABLED:
            await self._set_state(ChargingState.IDLE)
            _LOGGER.info("Charging re-enabled")

    async def _save_session(self) -> None:
        """Persist the current session to storage and record charge history (M1)."""
        if self._session:
            await self._coordinator.store.async_set_last_session(self._session)
            # M1: Append kWh to charge_history if there was actual charging
            kwh = self._session.kwh_charged(self._coordinator.battery_capacity)
            if kwh > 0:
                history = list(self._coordinator.store.charge_history)
                history.append(kwh)
                history = history[-CHARGE_HISTORY_DAYS:]
                await self._coordinator.store.async_set_charge_history(history)
                # Record session cost for analytics
                await self._coordinator.async_record_session_cost(self._session)
