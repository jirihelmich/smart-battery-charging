"""Tests for ChargingStateMachine — state transitions and inverter control."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# Mock HA modules before importing
for mod_name in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.selector",
    "homeassistant.helpers.event",
    "homeassistant.components",
    "homeassistant.components.switch",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.number",
    "homeassistant.data_entry_flow",
    "homeassistant.util",
    "homeassistant.util.dt",
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_battery_charging.charging_controller import ChargingStateMachine
from smart_battery_charging.const import STALL_ABORT_TICKS, STALL_RETRY_TICKS, START_FAILURE_MAX_RETRIES
from smart_battery_charging.models import ChargingSchedule, ChargingState


def _make_schedule(start_hour=1, end_hour=3, target_soc=80.0, avg_price=1.5):
    """Create a test schedule."""
    return ChargingSchedule(
        start_hour=start_hour,
        end_hour=end_hour,
        window_hours=end_hour - start_hour if end_hour > start_hour else (24 - start_hour + end_hour),
        avg_price=avg_price,
        required_kwh=5.0,
        target_soc=target_soc,
    )


def _make_coordinator(state=ChargingState.IDLE, current_soc=50.0, min_soc=20.0):
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.charging_state = state
    coord.current_schedule = None
    coord.current_soc = current_soc
    coord.min_soc = min_soc
    coord.battery_capacity = 15.0
    coord.store = MagicMock()
    coord.store.async_set_last_session = AsyncMock()
    coord.store.async_set_charging_state = AsyncMock()
    coord.store.async_set_current_schedule = AsyncMock()
    coord.store.async_set_charge_history = AsyncMock()
    coord.store.charge_history = []
    coord.async_record_session_cost = AsyncMock()
    return coord


def _make_session():
    """Create a mock session compatible with _save_session (M1 charge history)."""
    session = MagicMock()
    session.kwh_charged.return_value = 0.0
    return session


def _make_inverter():
    """Create a mock inverter controller."""
    inv = MagicMock()
    inv.async_start_charging = AsyncMock(return_value=True)
    inv.async_stop_charging = AsyncMock(return_value=True)
    inv.async_get_current_mode = AsyncMock(return_value="Self Use Mode")
    inv.is_manual_mode = MagicMock(return_value=False)
    return inv


class TestOnPlan:
    """Test plan handling."""

    @pytest.mark.asyncio
    async def test_idle_with_schedule_goes_to_scheduled(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule()
        await sm.async_on_plan(schedule)

        assert coord.charging_state == ChargingState.SCHEDULED
        assert coord.current_schedule == schedule

    @pytest.mark.asyncio
    async def test_idle_with_none_stays_idle(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_plan(None)

        assert coord.charging_state == ChargingState.IDLE
        # Should have saved "No charging needed" session
        coord.store.async_set_last_session.assert_called_once()
        session = coord.store.async_set_last_session.call_args[0][0]
        assert session.result == "No charging needed"

    @pytest.mark.asyncio
    async def test_complete_with_schedule_goes_to_scheduled(self):
        coord = _make_coordinator(state=ChargingState.COMPLETE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule()
        await sm.async_on_plan(schedule)

        assert coord.charging_state == ChargingState.SCHEDULED

    @pytest.mark.asyncio
    async def test_charging_ignores_new_plan(self):
        coord = _make_coordinator(state=ChargingState.CHARGING)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule()
        await sm.async_on_plan(schedule)

        # Should stay in CHARGING
        assert coord.charging_state == ChargingState.CHARGING

    @pytest.mark.asyncio
    async def test_disabled_ignores_plan(self):
        coord = _make_coordinator(state=ChargingState.DISABLED)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_plan(_make_schedule())

        assert coord.charging_state == ChargingState.DISABLED

    @pytest.mark.asyncio
    async def test_plan_stores_avg_price_in_session(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(avg_price=2.5)
        await sm.async_on_plan(schedule)

        assert sm._session is not None
        assert sm._session.avg_price == 2.5


class TestOnTick:
    """Test periodic tick handling."""

    @pytest.mark.asyncio
    async def test_scheduled_in_window_starts_charging(self):
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        # Simulate time being 01:30
        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.CHARGING
        inv.async_start_charging.assert_called_once_with(80.0)
        assert sm._session.start_soc == 30.0

    @pytest.mark.asyncio
    async def test_scheduled_not_in_window_stays(self):
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3)
        coord.current_schedule = schedule

        # Simulate time being 22:30 (not in 01-03 window)
        sm._now = lambda: datetime(2026, 2, 15, 22, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.SCHEDULED
        inv.async_start_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduled_already_at_target(self):
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=85.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.COMPLETE
        inv.async_start_charging.assert_not_called()
        assert sm._session.result == "Already at target"

    @pytest.mark.asyncio
    async def test_charging_target_reached(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=82.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.COMPLETE
        inv.async_stop_charging.assert_called_once_with(20.0)
        assert sm._session.result == "Target reached"
        assert sm._session.end_soc == 82.0

    @pytest.mark.asyncio
    async def test_charging_window_ended(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=60.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        # Time past window end
        sm._now = lambda: datetime(2026, 2, 15, 4, 0)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.COMPLETE
        inv.async_stop_charging.assert_called_once_with(20.0)
        assert sm._session.result == "Window ended"

    @pytest.mark.asyncio
    async def test_charging_continues_in_window(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=60.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        # Should stay CHARGING
        assert coord.charging_state == ChargingState.CHARGING
        inv.async_stop_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_tick_is_noop(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_tick()

        inv.async_start_charging.assert_not_called()
        inv.async_stop_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_midnight_crossing_window(self):
        """Test window that crosses midnight (e.g., 22:00 - 02:00)."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=22, end_hour=2, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        # Time is 23:30 — should be in window
        sm._now = lambda: datetime(2026, 2, 15, 23, 30)

        await sm.async_on_tick()
        assert coord.charging_state == ChargingState.CHARGING

    @pytest.mark.asyncio
    async def test_midnight_crossing_before_window(self):
        """Before midnight-crossing window starts."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=22, end_hour=2, target_soc=80.0)
        coord.current_schedule = schedule

        # Time is 20:00 — before window
        sm._now = lambda: datetime(2026, 2, 15, 20, 0)

        await sm.async_on_tick()
        assert coord.charging_state == ChargingState.SCHEDULED

    @pytest.mark.asyncio
    async def test_midnight_crossing_after_window(self):
        """After midnight-crossing window ends."""
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=50.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=22, end_hour=2, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        # Time is 03:00 — after window
        sm._now = lambda: datetime(2026, 2, 15, 3, 0)

        await sm.async_on_tick()
        assert coord.charging_state == ChargingState.COMPLETE
        inv.async_stop_charging.assert_called_once()


class TestMorningSafety:
    """Test morning safety handler."""

    @pytest.mark.asyncio
    async def test_stops_active_charging(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=70.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 7, 0)

        await sm.async_on_morning_safety()

        assert coord.charging_state == ChargingState.IDLE
        inv.async_stop_charging.assert_called_once_with(20.0)
        assert sm._session.result == "Morning safety stop"
        assert sm._session.end_soc == 70.0
        assert coord.current_schedule is None

    @pytest.mark.asyncio
    async def test_restores_self_use_when_manual(self):
        coord = _make_coordinator(state=ChargingState.IDLE, min_soc=20.0)
        inv = _make_inverter()
        inv.async_get_current_mode.return_value = "Manual Mode"
        inv.is_manual_mode.return_value = True
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_morning_safety()

        inv.async_stop_charging.assert_called_once_with(20.0)
        assert coord.charging_state == ChargingState.IDLE

    @pytest.mark.asyncio
    async def test_noop_when_self_use(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        inv.async_get_current_mode.return_value = "Self Use Mode"
        inv.is_manual_mode.return_value = False
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_morning_safety()

        inv.async_stop_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_schedule(self):
        coord = _make_coordinator(state=ChargingState.SCHEDULED)
        coord.current_schedule = _make_schedule()
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_morning_safety()

        assert coord.current_schedule is None


class TestDisableEnable:
    """Test disable/enable transitions."""

    @pytest.mark.asyncio
    async def test_disable_while_charging_stops_inverter(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=65.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_disable()

        assert coord.charging_state == ChargingState.DISABLED
        inv.async_stop_charging.assert_called_once_with(20.0)
        assert sm._session.result == "Disabled"
        assert coord.current_schedule is None

    @pytest.mark.asyncio
    async def test_disable_while_idle(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_disable()

        assert coord.charging_state == ChargingState.DISABLED
        inv.async_stop_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_disable_while_scheduled(self):
        coord = _make_coordinator(state=ChargingState.SCHEDULED)
        coord.current_schedule = _make_schedule()
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_disable()

        assert coord.charging_state == ChargingState.DISABLED
        assert coord.current_schedule is None

    @pytest.mark.asyncio
    async def test_enable_from_disabled(self):
        coord = _make_coordinator(state=ChargingState.DISABLED)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_enable()

        assert coord.charging_state == ChargingState.IDLE

    @pytest.mark.asyncio
    async def test_enable_when_not_disabled_is_noop(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_enable()

        # Should stay IDLE (not change from already non-disabled state)
        assert coord.charging_state == ChargingState.IDLE


class TestSessionPersistence:
    """Test that sessions are saved to storage."""

    @pytest.mark.asyncio
    async def test_session_saved_on_target_reached(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=82.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        coord.store.async_set_last_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_saved_on_disable(self):
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=60.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_disable()

        coord.store.async_set_last_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_saved_on_no_charging_needed(self):
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_plan(None)

        coord.store.async_set_last_session.assert_called_once()
        session = coord.store.async_set_last_session.call_args[0][0]
        assert session.result == "No charging needed"


class TestStallDetection:
    """Test charging stall detection and retry."""

    @pytest.mark.asyncio
    async def test_stall_retry_at_threshold(self):
        """After STALL_RETRY_TICKS without SOC change, retry charge command."""
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=50.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=5, target_soc=80.0)
        coord.current_schedule = schedule
        sm._stall_start_soc = 50.0
        sm._stall_tick_count = STALL_RETRY_TICKS - 1  # one tick away from retry

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        # Should have retried charging
        inv.async_start_charging.assert_called_once_with(80.0)
        assert coord.charging_state == ChargingState.CHARGING  # still charging

    @pytest.mark.asyncio
    async def test_stall_abort_at_threshold(self):
        """After STALL_ABORT_TICKS without SOC change, abort and notify."""
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=50.0, min_soc=20.0)
        inv = _make_inverter()
        notifier = MagicMock()
        notifier.async_notify_charging_stalled = AsyncMock()
        sm = ChargingStateMachine(coord, inv, notifier)

        schedule = _make_schedule(start_hour=1, end_hour=5, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()
        sm._stall_start_soc = 50.0
        sm._stall_tick_count = STALL_ABORT_TICKS - 1

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        # Should have stopped charging
        inv.async_stop_charging.assert_called_once_with(20.0)
        assert coord.charging_state == ChargingState.COMPLETE
        assert sm._session.result == "Charging stalled"
        notifier.async_notify_charging_stalled.assert_called_once()

    @pytest.mark.asyncio
    async def test_stall_resets_on_soc_change(self):
        """SOC change resets stall counters."""
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=55.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=5, target_soc=80.0)
        coord.current_schedule = schedule
        sm._stall_start_soc = 50.0  # was 50, now 55 → SOC changed
        sm._stall_tick_count = 10

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        # SOC changed, counters should reset
        assert sm._stall_start_soc == 55.0
        assert sm._stall_tick_count == 0
        assert coord.charging_state == ChargingState.CHARGING

    @pytest.mark.asyncio
    async def test_stall_counters_reset_on_charge_start(self):
        """Stall counters are reset when entering CHARGING state."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.CHARGING
        assert sm._stall_start_soc == 30.0
        assert sm._stall_tick_count == 0


class TestStartFailureRetry:
    """Test C3: don't transition to CHARGING when start fails."""

    @pytest.mark.asyncio
    async def test_start_failure_stays_scheduled(self):
        """When inverter returns False, stay in SCHEDULED."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        inv.async_start_charging = AsyncMock(return_value=False)
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()

        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.SCHEDULED
        assert sm._start_fail_count == 1

    @pytest.mark.asyncio
    async def test_start_failure_aborts_after_max_retries(self):
        """After START_FAILURE_MAX_RETRIES failures, abort to IDLE."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()
        inv.async_start_charging = AsyncMock(return_value=False)
        notifier = MagicMock()
        notifier.async_notify_charging_stalled = AsyncMock()
        sm = ChargingStateMachine(coord, inv, notifier)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()
        sm._start_fail_count = START_FAILURE_MAX_RETRIES - 1

        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.IDLE
        assert sm._session.result == "Inverter command failed"
        coord.store.async_set_last_session.assert_called_once()
        notifier.async_notify_charging_stalled.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_failure_counter_resets_on_success(self):
        """Successful start after failures resets the counter."""
        coord = _make_coordinator(state=ChargingState.SCHEDULED, current_soc=30.0)
        inv = _make_inverter()  # default: returns True
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()
        sm._start_fail_count = 2

        sm._now = lambda: datetime(2026, 2, 15, 1, 30)

        await sm.async_on_tick()

        assert coord.charging_state == ChargingState.CHARGING
        assert sm._start_fail_count == 0


class TestStatePersistence:
    """Test that state and schedule are persisted to store (C1)."""

    @pytest.mark.asyncio
    async def test_state_persisted_on_transition(self):
        """State transitions call store.async_set_charging_state."""
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule()
        await sm.async_on_plan(schedule)

        coord.store.async_set_charging_state.assert_called_with("scheduled")

    @pytest.mark.asyncio
    async def test_schedule_persisted_on_plan(self):
        """Schedule is persisted when a plan is set."""
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        await sm.async_on_plan(schedule)

        coord.store.async_set_current_schedule.assert_called_once()
        saved = coord.store.async_set_current_schedule.call_args[0][0]
        assert saved["start_hour"] == 1
        assert saved["end_hour"] == 3
        assert saved["target_soc"] == 80.0

    @pytest.mark.asyncio
    async def test_schedule_cleared_on_morning_safety(self):
        """Morning safety clears the persisted schedule."""
        coord = _make_coordinator(state=ChargingState.IDLE)
        inv = _make_inverter()
        inv.is_manual_mode.return_value = False
        sm = ChargingStateMachine(coord, inv)

        await sm.async_on_morning_safety()

        coord.store.async_set_current_schedule.assert_called_with(None)

    @pytest.mark.asyncio
    async def test_charge_history_appended_on_session_save(self):
        """M1: charge_history is appended when session has kWh > 0."""
        coord = _make_coordinator(state=ChargingState.CHARGING, current_soc=80.0, min_soc=20.0)
        inv = _make_inverter()
        sm = ChargingStateMachine(coord, inv)

        schedule = _make_schedule(start_hour=1, end_hour=3, target_soc=80.0)
        coord.current_schedule = schedule
        sm._session = _make_session()
        sm._session.kwh_charged.return_value = 5.0

        sm._now = lambda: datetime(2026, 2, 15, 2, 0)

        await sm.async_on_tick()

        coord.store.async_set_charge_history.assert_called_once()
