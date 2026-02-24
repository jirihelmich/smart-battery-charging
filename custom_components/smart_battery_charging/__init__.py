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

from .charging_controller import ChargingStateMachine
from .const import CONF_PRICE_SENSOR, DOMAIN, PLATFORMS
from .coordinator import SmartBatteryCoordinator
from .inverter_controller import InverterController
from .notifier import ChargingNotifier
from .planner import ChargingPlanner
from .storage import SmartBatteryStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Charging from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Initialize storage
    store = SmartBatteryStore(hass, entry.entry_id)
    await store.async_load()

    # Create coordinator
    coordinator = SmartBatteryCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()

    # Create Phase 2 components
    inverter = InverterController(hass, dict(entry.data))
    planner = ChargingPlanner(coordinator)
    notifier = ChargingNotifier(hass, coordinator)
    state_machine = ChargingStateMachine(coordinator, inverter, notifier)

    # Wire into coordinator
    coordinator.inverter = inverter
    coordinator.planner = planner
    coordinator.state_machine = state_machine
    coordinator.notifier = notifier

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register event listeners
    _register_event_listeners(hass, entry, coordinator, planner, state_machine)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Smart Battery Charging setup complete for %s", entry.title)
    return True


def _register_event_listeners(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: SmartBatteryCoordinator,
    planner: ChargingPlanner,
    state_machine: ChargingStateMachine,
) -> None:
    """Register all event listeners for charging automation."""

    async def _run_planner(_event_or_time=None) -> None:
        """Run the planner and pass result to state machine."""
        if not coordinator.enabled:
            _LOGGER.debug("Skipping planner — charging disabled")
            return
        try:
            deficit = planner.compute_energy_deficit()
            schedule = planner.plan_charging()
            await state_machine.async_on_plan(schedule)
            await notifier.async_notify_plan(schedule, deficit)
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

    async def _on_daily_record(_now=None) -> None:
        """Record daily consumption and forecast error at 23:55."""
        try:
            await coordinator.async_record_daily_consumption()
            await coordinator.async_record_forecast_error()
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

    # 4. Sunrise - 15 minutes → morning safety
    unsub = async_track_sunrise(hass, _on_morning_safety, offset=timedelta(minutes=-15))
    entry.async_on_unload(unsub)

    # 5. 23:55 → daily consumption + forecast error recorder
    unsub = async_track_time_change(hass, _on_daily_record, hour=23, minute=55, second=0)
    entry.async_on_unload(unsub)

    _LOGGER.debug("Registered 5 event listeners for charging automation")


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
