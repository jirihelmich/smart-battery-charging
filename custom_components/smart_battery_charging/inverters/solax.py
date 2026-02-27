"""Solax Modbus inverter (wills106/homeassistant-solax-modbus)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .select_mixin import SelectInverterMixin

TEMPLATE = InverterTemplate(
    id="solax_modbus",
    label="Solax Modbus (wills106)",
    description="SolaX Power inverters via Modbus RS485/TCP",
    control_type="select",
    mode_self_use="Self Use Mode",
    mode_manual="Manual Mode",
    charge_force="Force Charge",
    charge_stop="Stop Charge and Discharge",
    battery_capacity=15.0,
    entity_hints={
        "inverter_soc_sensor": "e.g. sensor.solax_inverter_battery_capacity",
        "inverter_capacity_sensor": "e.g. sensor.solax_inverter_battery_capacity_charge (Wh)",
        "inverter_actual_solar_sensor": "e.g. sensor.solax_inverter_today_s_solar_energy",
        "inverter_mode_select": "e.g. select.solax_inverter_charger_use_mode",
        "inverter_charge_command_select": "e.g. select.solax_inverter_charge_discharge_setting",
        "inverter_charge_soc_limit": "e.g. number.solax_inverter_charge_soc_limit",
        "inverter_discharge_min_soc": "e.g. number.solax_inverter_selfuse_discharge_min_soc",
    },
)


class SolaxInverter(SelectInverterMixin, BaseInverterController):
    """Solax Modbus inverter controller."""
