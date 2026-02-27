"""Tests for the inverter registry and factory."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock HA modules before importing the package
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

from smart_battery_charging.inverters import (
    INVERTER_TEMPLATES,
    BaseInverterController,
    CustomInverter,
    HuaweiInverter,
    SolaxInverter,
    SolarEdgeInverter,
    WattsonicInverter,
    create_inverter_controller,
    get_template,
)


@pytest.fixture
def hass() -> MagicMock:
    return MagicMock()


class TestFactory:
    """Test create_inverter_controller factory."""

    def test_solax_returns_solax(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="solax_modbus")
        assert isinstance(ctrl, SolaxInverter)

    def test_solaredge_returns_solaredge(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="solaredge_modbus")
        assert isinstance(ctrl, SolarEdgeInverter)

    def test_huawei_returns_huawei(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="huawei_solar")
        assert isinstance(ctrl, HuaweiInverter)

    def test_wattsonic_returns_wattsonic(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="wattsonic_ems")
        assert isinstance(ctrl, WattsonicInverter)

    def test_custom_returns_custom(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="custom")
        assert isinstance(ctrl, CustomInverter)

    def test_unknown_falls_back_to_custom(self, hass):
        ctrl = create_inverter_controller(hass, {}, template_id="nonexistent")
        assert isinstance(ctrl, CustomInverter)

    def test_unknown_with_ems_type_falls_back_to_wattsonic(self, hass):
        ctrl = create_inverter_controller(
            hass, {}, template_id="nonexistent", control_type="ems_power"
        )
        assert isinstance(ctrl, WattsonicInverter)

    def test_all_controllers_are_base_subclass(self, hass):
        for template_id in INVERTER_TEMPLATES:
            ctrl = create_inverter_controller(hass, {}, template_id=template_id)
            assert isinstance(ctrl, BaseInverterController), (
                f"Controller for {template_id} is not a BaseInverterController"
            )


class TestGetTemplate:
    """Test get_template function."""

    def test_known_template(self):
        tmpl = get_template("solax_modbus")
        assert tmpl.id == "solax_modbus"

    def test_unknown_falls_back_to_custom(self):
        tmpl = get_template("nonexistent")
        assert tmpl.id == "custom"
