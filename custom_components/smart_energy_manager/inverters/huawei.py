"""Huawei Solar inverter (wlcrs integration)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .select_mixin import SelectInverterMixin

TEMPLATE = InverterTemplate(
    id="huawei_solar",
    label="Huawei Solar (wlcrs)",
    description="Huawei inverters — TOU-based workaround (service-based force charge planned)",
    control_type="select",
    mode_self_use="Maximise Self Consumption",
    mode_manual="Time Of Use",
    charge_force="Time Of Use",
    charge_stop="Maximise Self Consumption",
    battery_capacity=10.0,
    entity_hints={
        "inverter_soc_sensor": "e.g. sensor.battery_state_of_capacity",
        "inverter_mode_select": "e.g. select.battery_working_mode",
    },
)


class HuaweiInverter(SelectInverterMixin, BaseInverterController):
    """Huawei Solar inverter controller."""
