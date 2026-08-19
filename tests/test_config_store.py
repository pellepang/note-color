import os

import pytest

import config
from config_store import ConfigStore


def _write(path, text):
    path.write_text(text)


def test_absent_file_reproduces_defaults(tmp_path):
    store = ConfigStore(path=str(tmp_path / "missing.toml"))
    for action, default in config.DEFAULT_KEYBINDS.items():
        assert store.keybind(action) == default
    assert store.note_hue_override(0) is None
    assert store.preference("anything", "fallback") == "fallback"


def test_empty_file_reproduces_defaults(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, "")
    store = ConfigStore(path=str(path))
    assert store.keybind("source_toggle") == config.DEFAULT_KEYBINDS["source_toggle"]


def test_malformed_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, "this is not [valid toml")
    store = ConfigStore(path=str(path))
    assert store.keybind("chord_mode_toggle") == config.DEFAULT_KEYBINDS["chord_mode_toggle"]
    assert store.note_hue_override(3) is None


def test_keybind_remap_overrides_default(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, '[keybinds]\nsource_toggle = "x"\n')
    store = ConfigStore(path=str(path))
    assert store.keybind("source_toggle") == "x"
    # Un-remapped actions keep their default.
    assert store.keybind("legend_toggle") == config.DEFAULT_KEYBINDS["legend_toggle"]


def test_note_hue_override_accepts_sharp_and_flat_spelling(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, '[colors]\nC = 200\n"Eb" = 90\n')
    store = ConfigStore(path=str(path))
    assert store.note_hue_override(0) == 200      # C
    assert store.note_hue_override(3) == 90        # D#/Eb
    assert store.note_hue_override(1) is None       # C# untouched


def test_note_hue_override_wraps_into_0_360(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, "[colors]\nC = 400\n")
    store = ConfigStore(path=str(path))
    assert store.note_hue_override(0) == 40


def test_set_preference_persists_and_reloads(tmp_path):
    path = tmp_path / "config.toml"
    store = ConfigStore(path=str(path))
    store.set_preference("keybind_legend_on", False)

    reloaded = ConfigStore(path=str(path))
    assert reloaded.preference("keybind_legend_on", True) is False


def test_set_preference_preserves_other_tables(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, '[keybinds]\nsource_toggle = "x"\n')
    store = ConfigStore(path=str(path))
    store.set_preference("keybind_legend_on", True)

    reloaded = ConfigStore(path=str(path))
    assert reloaded.keybind("source_toggle") == "x"
    assert reloaded.preference("keybind_legend_on", False) is True


def test_hot_reload_picks_up_external_edit(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, '[keybinds]\nsource_toggle = "x"\n')
    store = ConfigStore(path=str(path))
    assert store.keybind("source_toggle") == "x"

    _write(path, '[keybinds]\nsource_toggle = "y"\n')
    # Force a distinct mtime -- some filesystems have coarse (1s) mtime
    # resolution, and this test must not depend on wall-clock timing.
    later = os.stat(path).st_mtime + 2
    os.utime(path, (later, later))

    assert store.keybind("source_toggle") == "y"


def test_deleting_file_after_load_reverts_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    _write(path, '[keybinds]\nsource_toggle = "x"\n')
    store = ConfigStore(path=str(path))
    assert store.keybind("source_toggle") == "x"

    os.remove(path)
    assert store.keybind("source_toggle") == config.DEFAULT_KEYBINDS["source_toggle"]
