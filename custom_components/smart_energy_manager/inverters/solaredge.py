"""SolarEdge Modbus inverter (binsentsu integration)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .select_mixin import SelectInverterMixin

TEMPLATE = InverterTemplate(
    id="solaredge_modbus",
    label="SolarEdge Modbus",
    description="SolarEdge inverters via binsentsu Modbus integration",
    control_type="select",
    mode_self_use="Maximize Self Consumption",
    mode_manual="Remote Control",
    charge_force="Charge from PV and AC",
    charge_stop="Maximize self consumption",
    battery_capacity=10.0,
    entity_hints={
        "inverter_mode_select": "Storage control mode entity",
        "inverter_charge_command_select": "Storage default mode entity",
    },
)


class SolarEdgeInverter(SelectInverterMixin, BaseInverterController):
    """SolarEdge Modbus inverter controller."""
