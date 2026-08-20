"""Tests for issue #43's pure settings-screen logic -- field layout,
value formatting/parsing, and the config-store-backed edit helpers.
Per this repo's test convention, the interactive blessed-driven
run_settings_screen loop itself is smoke-tested manually, not here.
"""

import pytest

import settings_display as sd
from color_map import NOTE_NAMES
from config_store import ConfigStore


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own on-disk config file instead of touching the
    real ~/.config/note-color/config.toml the module-level singleton
    normally points at -- settings_display.store is monkeypatched directly
    since the module imported that name via `from config_store import
    store` (module-global lookup, so this reaches every helper)."""
    fresh = ConfigStore(path=str(tmp_path / "config.toml"))
    monkeypatch.setattr(sd, "store", fresh)
    return fresh


# --- field layout ------------------------------------------------------

def test_fields_cover_every_keybind_then_every_note():
    assert len(sd.FIELDS) == len(sd.KEYBIND_ACTIONS) + len(NOTE_NAMES)
    assert [kind for kind, _ in sd.FIELDS[:len(sd.KEYBIND_ACTIONS)]] == ["keybind"] * len(sd.KEYBIND_ACTIONS)
    assert [kind for kind, _ in sd.FIELDS[len(sd.KEYBIND_ACTIONS):]] == ["color"] * len(NOTE_NAMES)


def test_move_wraps_both_directions():
    assert sd.move(0, -1) == len(sd.FIELDS) - 1
    assert sd.move(len(sd.FIELDS) - 1, 1) == 0


# --- value formatting ----------------------------------------------------

def test_keybind_value_reflects_default_and_spells_out_space():
    assert sd.keybind_value("source_toggle") == "m"
    assert sd.keybind_value("freeze_toggle") == "space"


def test_color_value_defaults_until_overridden():
    assert sd.color_value(0) == "default"
    sd.apply_field_edit(sd.FIELDS.index(("color", 0)), 200.0)
    assert sd.color_value(0) == "200°"


def test_field_label_and_value_dispatch_by_kind():
    keybind_index = 0
    color_index = len(sd.KEYBIND_ACTIONS)
    assert sd.field_label(keybind_index) == sd.keybind_label(sd.KEYBIND_ACTIONS[0])
    assert sd.field_label(color_index) == NOTE_NAMES[0]
    assert sd.field_value(color_index) == "default"


# --- editing ---------------------------------------------------------------

def test_apply_field_edit_keybind_row_persists_through_store():
    index = sd.FIELDS.index(("keybind", "chord_mode_toggle"))
    sd.apply_field_edit(index, "x")
    assert sd.keybind_value("chord_mode_toggle") == "x"


def test_apply_field_edit_color_row_persists_through_store():
    index = sd.FIELDS.index(("color", 5))
    sd.apply_field_edit(index, 300.0)
    assert sd.color_value(5) == "300°"


def test_clear_field_resets_color_row_to_default():
    index = sd.FIELDS.index(("color", 2))
    sd.apply_field_edit(index, 100.0)
    assert sd.color_value(2) != "default"
    sd.clear_field(index)
    assert sd.color_value(2) == "default"


def test_clear_field_is_a_noop_on_keybind_rows():
    index = sd.FIELDS.index(("keybind", "legend_toggle"))
    sd.clear_field(index)
    assert sd.keybind_value("legend_toggle") == "l"  # unchanged, still the default


# --- input validation --------------------------------------------------

def test_is_valid_remap_key_accepts_single_printable_or_space():
    assert sd.is_valid_remap_key("x") is True
    assert sd.is_valid_remap_key(" ") is True
    assert sd.is_valid_remap_key("") is False
    assert sd.is_valid_remap_key("xy") is False
    assert sd.is_valid_remap_key("\x1b") is False  # a control character


def test_is_valid_remap_key_rejects_reserved_global_keys():
    # '|' (back-to-menu) and 'h'/'H' (help-legend toggle) are global keys
    # every terminal loop checks unconditionally -- remapping an action
    # onto either would make it double-fire, not a usable remap.
    assert sd.is_valid_remap_key("|") is False
    assert sd.is_valid_remap_key("h") is False
    assert sd.is_valid_remap_key("H") is False


def test_parse_hue_input_empty_means_clear():
    assert sd.parse_hue_input("") is None
    assert sd.parse_hue_input("   ") is None


def test_parse_hue_input_wraps_into_0_360():
    assert sd.parse_hue_input("400") == 40.0
    assert sd.parse_hue_input("200") == 200.0


def test_parse_hue_input_rejects_non_numeric():
    with pytest.raises(ValueError):
        sd.parse_hue_input("not-a-number")
