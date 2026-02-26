"""Smart Battery Charging integration for Home Assistant.

Phase 2: Full charging automation with inverter control.
Computes charging schedules and controls the inverter via Modbus services.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_sunrise,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .charging_controller import ChargingStateMachine
from .const import (
    CONF_CONTROL_TYPE,
    CONF_INVERTER_TEMPLATE,
    CONF_PRICE_SENSOR,
    CONTROL_TYPE_SELECT,
    DOMAIN,
    MORNING_SAFETY_OFFSET_MINUTES,
    PLATFORMS,
)
from .coordinator import SmartBatteryCoordinator
from .inverter_controller import InverterController
from .inverter_templates import get_template
from .models import ChargingSchedule, ChargingState
from .notifier import ChargingNotifier
from .planner import ChargingPlanner
from .storage import SmartBatteryStore

_LOGGER = logging.getLogger(__name__)


def _restore_schedule_from_dict(data: dict) -> ChargingSchedule | None:
    """Deserialize a schedule dict from storage into a ChargingSchedule."""
    if not data:
        return None
    try:
        return ChargingSchedule(
            start_hour=int(data["start_hour"]),
            end_hour=int(data["end_hour"]),
            window_hours=int(data["window_hours"]),
            avg_price=float(data["avg_price"]),
            required_kwh=float(data["required_kwh"]),
            target_soc=float(data["target_soc"]),
        )
    except (KeyError, ValueError, TypeError):
        _LOGGER.warning("Could not restore schedule from storage: %s", data)
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Charging from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Initialize storage
    store = SmartBatteryStore(hass, entry.entry_id)
    await store.async_load()

    # Create coordinator, restore enabled state from store
    coordinator = SmartBatteryCoordinator(hass, entry, store)
    coordinator.enabled = store.enabled
    await coordinator.async_config_entry_first_refresh()

    # Determine control type from template or config
    template_id = entry.data.get(CONF_INVERTER_TEMPLATE, "custom")
    template = get_template(template_id)
    control_type = entry.data.get(CONF_CONTROL_TYPE, template.control_type)

    # Create Phase 2 components
    inverter = InverterController(hass, dict(entry.data), control_type=control_type)
    planner = ChargingPlanner(coordinator)
    notifier = ChargingNotifier(hass, coordinator)
    state_machine = ChargingStateMachine(coordinator, inverter, notifier)

    # Wire into coordinator
    coordinator.inverter = inverter
    coordinator.planner = planner
    coordinator.state_machine = state_machine
    coordinator.notifier = notifier

    # Restore charging state from store (C1)
    _restore_charging_state(coordinator, store)

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register event listeners
    _register_event_listeners(hass, entry, coordinator, planner, state_machine, notifier)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Smart Battery Charging setup complete for %s", entry.title)
    return True


def _restore_charging_state(
    coordinator: SmartBatteryCoordinator,
    store: SmartBatteryStore,
) -> None:
    """Restore charging state and schedule from persistent storage."""
    stored_state = store.charging_state
    stored_schedule = store.current_schedule

    try:
        state = ChargingState(stored_state)
    except ValueError:
        _LOGGER.warning("Unknown stored charging state '%s', defaulting to IDLE", stored_state)
        state = ChargingState.IDLE

    # If we were CHARGING on restart, resume as SCHEDULED (safer — next tick re-evaluates)
    if state == ChargingState.CHARGING:
        _LOGGER.info("Was CHARGING on shutdown, resuming as SCHEDULED for safe re-evaluation")
        state = ChargingState.SCHEDULED

    coordinator.charging_state = state

    if stored_schedule:
        schedule = _restore_schedule_from_dict(stored_schedule)
        if schedule:
            coordinator.current_schedule = schedule
            _LOGGER.info(
                "Restored schedule: %02d:00-%02d:00, target %.0f%%",
                schedule.start_hour, schedule.end_hour, schedule.target_soc,
            )

    if state != ChargingState.IDLE:
        _LOGGER.info("Restored charging state: %s", state.value)


def _register_event_listeners(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: SmartBatteryCoordinator,
    planner: ChargingPlanner,
    state_machine: ChargingStateMachine,
    notifier: ChargingNotifier,
) -> None:
    """Register all event listeners for charging automation."""

    async def _run_planner(_event_or_time=None) -> None:
        """Run the planner and pass result to state machine."""
        if not coordinator.enabled:
            _LOGGER.debug("Skipping planner — charging disabled")
            return
        if not coordinator.sensors_ready:
            _LOGGER.debug("Skipping planner — sensors not ready yet (startup)")
            return
        try:
            now = dt_util.now()
            deficit = planner.compute_energy_deficit(now=now)
            schedule = planner.plan_charging(now=now)
            overnight = planner.last_overnight_need
            await state_machine.async_on_plan(schedule)
            await notifier.async_notify_plan(schedule, deficit, overnight)
            await coordinator.async_request_refresh()
        except Exception:
            _LOGGER.exception("Error running charging planner")

    async def _on_tick(_now=None) -> None:
        """Handle periodic tick."""
        if not coordinator.enabled:
            return
        try:
            await state_machine.async_on_tick()
            await coordinator.async_request_refresh()
        except Exception:
            _LOGGER.exception("Error in charging tick")

    async def _on_morning_safety(_now=None) -> None:
        """Handle morning safety trigger."""
        try:
            await state_machine.async_on_morning_safety()
            await coordinator.async_request_refresh()
        except Exception:
            _LOGGER.exception("Error in morning safety handler")

    async def _on_morning_soc(_now=None) -> None:
        """Record battery SOC at sunrise."""
        try:
            await coordinator.async_record_morning_soc()
        except Exception:
            _LOGGER.exception("Error recording morning SOC")

    async def _on_daily_record(_now=None) -> None:
        """Record daily consumption, forecast error, and BMS capacity at 23:55."""
        try:
            await coordinator.async_record_daily_consumption()
            await coordinator.async_record_forecast_error()
            await coordinator.async_record_bms_capacity()
        except Exception:
            _LOGGER.exception("Error recording daily data")

    # 1. Price sensor state change → run planner
    price_sensor = entry.data.get(CONF_PRICE_SENSOR, "")
    if price_sensor:
        unsub = async_track_state_change_event(
            hass, [price_sensor], _run_planner
        )
        entry.async_on_unload(unsub)

    # 2. 20:00 fallback → run planner (in case prices arrived earlier without event)
    unsub = async_track_time_change(hass, _run_planner, hour=20, minute=0, second=0)
    entry.async_on_unload(unsub)

    # 3. Every 2 minutes → tick
    unsub = async_track_time_interval(hass, _on_tick, timedelta(minutes=2))
    entry.async_on_unload(unsub)

    # 4. Sunrise - N minutes → morning safety
    unsub = async_track_sunrise(hass, _on_morning_safety, offset=timedelta(minutes=-MORNING_SAFETY_OFFSET_MINUTES))
    entry.async_on_unload(unsub)

    # 5. 23:55 → daily consumption + forecast error + BMS capacity recorder
    unsub = async_track_time_change(hass, _on_daily_record, hour=23, minute=55, second=0)
    entry.async_on_unload(unsub)

    # 6. Sunrise → morning SOC recorder
    unsub = async_track_sunrise(hass, _on_morning_soc, offset=timedelta(minutes=0))
    entry.async_on_unload(unsub)

    _LOGGER.debug("Registered 6 event listeners for charging automation")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry — clean up storage."""
    store = SmartBatteryStore(hass, entry.entry_id)
    await store.async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
