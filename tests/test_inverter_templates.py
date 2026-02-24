"""Tests for inverter templates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "smart_battery_charging"
sys.path.insert(0, str(_COMPONENT_DIR))

from inverter_templates import INVERTER_TEMPLATES, InverterTemplate, get_template


REQUIRED_STRING_FIELDS = ("id", "label", "description", "mode_self_use", "mode_manual", "charge_force", "charge_stop")


class TestInverterTemplates:
    """Tests for the INVERTER_TEMPLATES registry."""

    def test_all_templates_have_required_fields(self) -> None:
        """Every template must have all required string fields set (or empty for custom)."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            assert isinstance(tmpl, InverterTemplate), f"{tid}: not an InverterTemplate"
            for field_name in REQUIRED_STRING_FIELDS:
                value = getattr(tmpl, field_name)
                assert isinstance(value, str), f"{tid}.{field_name} should be str, got {type(value)}"
            assert isinstance(tmpl.battery_capacity, float), f"{tid}.battery_capacity should be float"
            assert tmpl.battery_capacity > 0, f"{tid}.battery_capacity must be positive"
            assert isinstance(tmpl.entity_hints, dict), f"{tid}.entity_hints should be dict"

    def test_all_templates_id_matches_key(self) -> None:
        """Template .id must match the dict key."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            assert tmpl.id == tid, f"Key {tid!r} != template.id {tmpl.id!r}"

    def test_custom_template_has_empty_strings(self) -> None:
        """Custom template should have empty mode/command strings."""
        custom = INVERTER_TEMPLATES["custom"]
        assert custom.mode_self_use == ""
        assert custom.mode_manual == ""
        assert custom.charge_force == ""
        assert custom.charge_stop == ""
        assert custom.entity_hints == {}

    def test_solax_template_correct_values(self) -> None:
        """Solax template should have the known Modbus mode strings."""
        solax = INVERTER_TEMPLATES["solax_modbus"]
        assert solax.mode_self_use == "Self Use Mode"
        assert solax.mode_manual == "Manual Mode"
        assert solax.charge_force == "Force Charge"
        assert solax.charge_stop == "Stop Charge and Discharge"
        assert solax.battery_capacity == 15.0
        assert len(solax.entity_hints) == 7

    def test_non_custom_templates_have_nonempty_modes(self) -> None:
        """All templates except 'custom' must have non-empty mode strings."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            if tid == "custom":
                continue
            assert tmpl.mode_self_use, f"{tid}.mode_self_use is empty"
            assert tmpl.mode_manual, f"{tid}.mode_manual is empty"
            assert tmpl.charge_force, f"{tid}.charge_force is empty"
            assert tmpl.charge_stop, f"{tid}.charge_stop is empty"

    def test_get_template_known_id(self) -> None:
        """get_template returns the correct template for a known ID."""
        assert get_template("solax_modbus") is INVERTER_TEMPLATES["solax_modbus"]
        assert get_template("custom") is INVERTER_TEMPLATES["custom"]

    def test_get_template_unknown_id_falls_back_to_custom(self) -> None:
        """get_template returns custom for unknown IDs."""
        result = get_template("nonexistent_inverter")
        assert result is INVERTER_TEMPLATES["custom"]

    def test_expected_template_count(self) -> None:
        """We expect exactly 5 templates (4 integrations + custom)."""
        assert len(INVERTER_TEMPLATES) == 5

    def test_templates_are_frozen(self) -> None:
        """Templates should be immutable (frozen dataclass)."""
        tmpl = INVERTER_TEMPLATES["solax_modbus"]
        with pytest.raises(AttributeError):
            tmpl.mode_self_use = "changed"  # type: ignore[misc]
