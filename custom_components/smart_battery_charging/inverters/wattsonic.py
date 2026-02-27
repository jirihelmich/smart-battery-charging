"""Wattsonic GEN2 inverter (EMS Modbus RTU)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .ems_mixin import EmsInverterMixin

TEMPLATE = InverterTemplate(
    id="wattsonic_ems",
    label="Wattsonic GEN2 (EMS Modbus)",
    description="Wattsonic hybrid inverters via Modbus RTU — EMS battery control mode",
    control_type="ems_power",
    battery_capacity=10.0,
    ems_charge_mode_value=771,   # 0x0303 = EMS_BattCtrlMode
    ems_normal_mode_value=257,   # 0x0101 = General Mode
    entity_hints={
        "inverter_soc_sensor": "Modbus sensor for register 43000 (SOC %)",
        "inverter_capacity_sensor": "Battery capacity (configure in Modbus integration)",
        "inverter_actual_solar_sensor": "Modbus sensor for register 41005 (PV today kWh)",
        "inverter_working_mode_number": "Modbus number for register 50000 (Working Mode)",
        "inverter_battery_power_number": "Modbus number for register 50207 (Battery Power W)",
        "inverter_ac_lower_limit_number": "Modbus number for register 50209 (Min AC Power W)",
        "inverter_battery_dod_number": "Modbus number for register 52503 (On-grid DOD %)",
    },
)


class WattsonicInverter(EmsInverterMixin, BaseInverterController):
    """Wattsonic GEN2 inverter controller."""
