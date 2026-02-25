"""Inverter integration templates for pre-filling config flow."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InverterTemplate:
    """Template for a known inverter integration."""

    id: str
    label: str
    description: str
    control_type: str = "select"  # "select" or "ems_power"
    mode_self_use: str = ""
    mode_manual: str = ""
    charge_force: str = ""
    charge_stop: str = ""
    battery_capacity: float = 15.0
    entity_hints: dict[str, str] = field(default_factory=dict)
    # EMS power control fields (for control_type="ems_power")
    ems_charge_mode_value: int = 0
    ems_normal_mode_value: int = 0


INVERTER_TEMPLATES: dict[str, InverterTemplate] = {
    "solax_modbus": InverterTemplate(
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
    ),
    "solaredge_modbus": InverterTemplate(
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
    ),
    "huawei_solar": InverterTemplate(
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
    ),
    "wattsonic_ems": InverterTemplate(
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
    ),
    "custom": InverterTemplate(
        id="custom",
        label="Custom / Other",
        description="Manual configuration for any inverter",
        control_type="select",
        battery_capacity=15.0,
        entity_hints={},
    ),
}


def get_template(template_id: str) -> InverterTemplate:
    """Get a template by ID, falling back to custom."""
    return INVERTER_TEMPLATES.get(template_id, INVERTER_TEMPLATES["custom"])
