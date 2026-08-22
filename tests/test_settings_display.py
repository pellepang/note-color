"""Tests for issue #43's pure settings-screen logic -- field layout,
value formatting/parsing, and the config-store-backed edit helpers.
Per this repo's test convention, the interactive blessed-driven
run_settings_screen loop itself is smoke-tested manually, not here.
"""

import pytest

import config
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

def test_fields_cover_every_keybind_then_every_note_then_every_numeric():
    n_keybinds = len(sd.KEYBIND_ACTIONS)
    n_colors = len(NOTE_NAMES)
    n_numeric = len(sd.NUMERIC_FIELDS)
    assert len(sd.FIELDS) == n_keybinds + n_colors + n_numeric
    assert [kind for kind, _ in sd.FIELDS[:n_keybinds]] == ["keybind"] * n_keybinds
    assert [kind for kind, _ in sd.FIELDS[n_keybinds:n_keybinds + n_colors]] == ["color"] * n_colors
    assert [kind for kind, _ in sd.FIELDS[n_keybinds + n_colors:]] == ["numeric"] * n_numeric


def test_move_wraps_both_directions():
    assert sd.move(0, -1) == len(sd.FIELDS) - 1
    assert sd.move(len(sd.FIELDS) - 1, 1) == 0


# --- value formatting ----------------------------------------------------

def test_keybind_value_reflects_default_and_spells_out_space():
    assert sd.keybind_value("source_toggle") == "m"
    assert sd.keybind_value("freeze_toggle") == "space"


def test_rhythm_reanalysis_keybind_row_defaults_to_r():
    assert "rhythm_reanalysis" in sd.KEYBIND_ACTIONS
    assert sd.keybind_label("rhythm_reanalysis") == "Rhythm re-analysis (tab view)"
    assert sd.keybind_value("rhythm_reanalysis") == "r"
    assert sd.keybind_value("rhythm_reanalysis") == config.DEFAULT_KEYBINDS["rhythm_reanalysis"]


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


def test_apply_field_edit_rhythm_reanalysis_keybind_row_persists_through_store():
    index = sd.FIELDS.index(("keybind", "rhythm_reanalysis"))
    sd.apply_field_edit(index, "z")
    assert sd.keybind_value("rhythm_reanalysis") == "z"


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


# --- numeric fields ------------------------------------------------------

def _numeric_spec(key):
    return next(spec for spec in sd.NUMERIC_FIELDS if spec.key == key)


def test_numeric_fields_cover_rhythm_reanalysis_window_and_tab_scrollback():
    rhythm_spec = _numeric_spec("rhythm_reanalysis_window_seconds")
    assert rhythm_spec.label == "Rhythm re-analysis window (seconds)"
    assert (rhythm_spec.min, rhythm_spec.max, rhythm_spec.step) == (5, 1800, 5)
    assert rhythm_spec.default == config.RHYTHM_REANALYSIS_WINDOW_SECONDS

    scrollback_spec = _numeric_spec("tab_scrollback_seconds")
    assert scrollback_spec.label == "Tab scrollback window (seconds)"
    assert (scrollback_spec.min, scrollback_spec.max, scrollback_spec.step) == (30, 3600, 30)
    assert scrollback_spec.default == config.TAB_SCROLLBACK_SECONDS


def test_numeric_value_reflects_default_until_overridden():
    spec = _numeric_spec("rhythm_reanalysis_window_seconds")
    assert sd.numeric_value(spec) == f"{spec.default:.0f}s"
    index = sd.FIELDS.index(("numeric", spec))
    sd.apply_field_edit(index, 120.0)
    assert sd.numeric_value(spec) == "120s"


def test_field_label_and_value_dispatch_numeric_kind():
    spec = _numeric_spec("tab_scrollback_seconds")
    index = sd.FIELDS.index(("numeric", spec))
    assert sd.field_label(index) == spec.label
    assert sd.field_value(index) == f"{spec.default:.0f}s"


def test_apply_field_edit_numeric_row_persists_through_store():
    spec = _numeric_spec("tab_scrollback_seconds")
    index = sd.FIELDS.index(("numeric", spec))
    sd.apply_field_edit(index, 600.0)
    assert sd.numeric_value(spec) == "600s"


def test_clear_field_resets_numeric_row_to_spec_default():
    spec = _numeric_spec("rhythm_reanalysis_window_seconds")
    index = sd.FIELDS.index(("numeric", spec))
    sd.apply_field_edit(index, 900.0)
    assert sd.numeric_value(spec) != f"{spec.default:.0f}s"
    sd.clear_field(index)
    assert sd.numeric_value(spec) == f"{spec.default:.0f}s"


def test_parse_numeric_input_empty_means_reset_to_default():
    assert sd.parse_numeric_input("", 5, 1800) is None
    assert sd.parse_numeric_input("   ", 5, 1800) is None


def test_parse_numeric_input_clamps_below_min():
    assert sd.parse_numeric_input("1", 5, 1800) == 5


def test_parse_numeric_input_clamps_above_max():
    assert sd.parse_numeric_input("5000", 5, 1800) == 1800


def test_parse_numeric_input_passes_through_in_range_value():
    assert sd.parse_numeric_input("90", 5, 1800) == 90


def test_parse_numeric_input_rejects_non_numeric():
    with pytest.raises(ValueError):
        sd.parse_numeric_input("not-a-number", 5, 1800)
