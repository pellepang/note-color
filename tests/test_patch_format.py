"""Tests for patch_format.py (map #99, issue #115).

Every test that touches the filesystem uses pytest's tmp_path -- the real
~/.config/note-color/ is never read or written, same convention as
tests/test_config_store.py and tests/test_settings_display.py.
"""

import os

import patch_format
from patch_format import (
    EffectSpec,
    Patch,
    Zone,
    load_patch,
    new_patch,
    parse_patch_text,
    patch_from_toml,
    patch_paths,
    patch_to_toml,
    save_patch,
    select_zone,
)


# --- Defaults and the no-version-field, everything-optional posture ---

def test_empty_toml_is_an_all_defaults_patch():
    patch = patch_from_toml({})
    blank = Patch()
    assert patch.engine == "synth"
    assert patch.osc1 == blank.osc1
    assert patch.osc2.level == 0.0  # osc2 defaults off
    assert patch.amp_env == blank.amp_env
    assert patch.voice == blank.voice
    assert patch.effects == [] and patch.zones == []


def test_missing_field_means_its_default_not_an_error():
    patch = patch_from_toml({"name": "Lead", "filter": {"cutoff": 800.0}})
    assert patch.name == "Lead"
    assert patch.filter.cutoff == 800.0
    assert patch.filter.resonance == 0.1
    assert patch.filter.type == "lp"


def test_unknown_keys_and_unknown_tables_are_ignored():
    patch = patch_from_toml({
        "engine": "synth",
        "nonsense_table": {"a": 1},
        "osc1": {"waveform": "square", "wobble": 12},
    })
    assert patch.osc1.waveform == "square"
    assert not hasattr(patch.osc1, "wobble")


def test_unknown_engine_falls_back_to_synth():
    assert patch_from_toml({"engine": "granular"}).engine == "synth"
    assert patch_from_toml({"engine": "SAMPLER"}).engine == "sampler"


def test_wrong_typed_value_falls_back_to_default():
    patch = patch_from_toml({
        "osc1": {"level": "loud", "octave": None, "waveform": 7},
        "voice": {"polyphony": True},  # bool is not a number here
    })
    assert patch.osc1.level == 1.0
    assert patch.osc1.octave == 0
    assert patch.osc1.waveform == "saw"
    assert patch.voice.polyphony == 16


def test_out_of_range_values_are_clamped_not_rejected():
    patch = patch_from_toml({
        "filter": {"cutoff": 999999.0, "resonance": -3.0, "env_amount": 9.0},
        "osc1": {"octave": 12, "semitones": -99},
        "voice": {"polyphony": 0},
    })
    assert patch.filter.cutoff == 20000.0
    assert patch.filter.resonance == 0.0
    assert patch.filter.env_amount == 1.0
    assert patch.osc1.octave == 2
    assert patch.osc1.semitones == -12
    assert patch.voice.polyphony == 1


def test_no_version_field_is_read_or_written():
    text = patch_to_toml(new_patch("Fat bass"))
    assert "version" not in text
    # ...and one present in a file is simply an ignored unknown key.
    assert patch_from_toml({"version": 3, "name": "x"}).name == "x"


# --- Malformed files degrade instead of refusing to open ---

def test_malformed_toml_loads_as_far_as_it_parses():
    text = (
        'name = "Half good"\n'
        "[filter]\n"
        "cutoff = 400.0\n"
        "[amp_env\n"          # <- broken table header
        "attack = 9.0\n"
    )
    patch = patch_from_toml(parse_patch_text(text))
    assert patch.name == "Half good"
    assert patch.filter.cutoff == 400.0
    assert patch.amp_env.attack == Patch().amp_env.attack


def test_completely_unparseable_text_yields_defaults():
    patch = patch_from_toml(parse_patch_text("this is not [valid toml"))
    assert patch == Patch()


def test_absent_file_loads_as_defaults_named_for_the_file(tmp_path):
    patch = load_patch(str(tmp_path / "ghost.toml"))
    assert patch.name == "ghost"
    assert patch.engine == "synth"


def test_a_bad_zone_entry_costs_only_that_entry(tmp_path):
    path = tmp_path / "kit.toml"
    path.write_text(
        'engine = "sampler"\n'
        "[[zones]]\n"
        'sample = "kick.wav"\n'
        "low_key = 36\nhigh_key = 36\nroot_key = 36\n"
        "[[zones]\n"          # <- broken
        'sample = "snare.wav"\n'
    )
    patch = load_patch(str(path))
    assert [z.sample for z in patch.zones] == ["kick.wav"]


def test_non_dict_effects_and_zones_entries_are_skipped():
    patch = patch_from_toml({"effects": ["delay", {"type": "chorus"}], "zones": "nope"})
    assert [e.type for e in patch.effects] == ["chorus"]
    assert patch.zones == []


# --- Zones: key range, root key, velocity band ---

def test_zone_defaults_cover_the_full_velocity_range():
    zone = Zone.from_toml({"sample": "snare.wav", "low_key": 38, "high_key": 38})
    assert (zone.low_vel, zone.high_vel) == (0, 127)
    assert zone.gain == 0.0 and zone.choke_group == 0


def test_zone_sample_is_reduced_to_a_bare_name():
    zone = Zone.from_toml({"sample": "/home/someone/private/kick.wav"})
    assert zone.sample == "kick.wav"


def test_zone_reversed_ranges_are_normalised():
    zone = Zone.from_toml({"low_key": 60, "high_key": 40, "low_vel": 100, "high_vel": 20})
    assert (zone.low_key, zone.high_key) == (40, 60)
    assert (zone.low_vel, zone.high_vel) == (20, 100)


def _zone(sample, low_key, high_key, low_vel=0, high_vel=127):
    return Zone(sample=sample, low_key=low_key, high_key=high_key,
                root_key=low_key, low_vel=low_vel, high_vel=high_vel)


def test_select_zone_matches_on_key_range():
    zones = [_zone("kick.wav", 36, 36), _zone("snare.wav", 38, 38)]
    assert select_zone(zones, 38, 100).sample == "snare.wav"
    assert select_zone(zones, 36, 100).sample == "kick.wav"


def test_select_zone_returns_none_outside_every_key_range():
    assert select_zone([_zone("kick.wav", 36, 36)], 60, 100) is None
    assert select_zone([], 60, 100) is None


def test_select_zone_picks_the_matching_velocity_layer():
    zones = [
        _zone("soft.wav", 38, 38, 0, 63),
        _zone("medium.wav", 38, 38, 64, 95),
        _zone("hard.wav", 38, 38, 96, 127),
    ]
    assert select_zone(zones, 38, 10).sample == "soft.wav"
    assert select_zone(zones, 38, 80).sample == "medium.wav"
    assert select_zone(zones, 38, 127).sample == "hard.wav"


def test_select_zone_narrowest_velocity_band_wins():
    zones = [
        _zone("catchall.wav", 38, 38, 0, 127),
        _zone("hard.wav", 38, 38, 96, 127),
    ]
    assert select_zone(zones, 38, 110).sample == "hard.wav"
    assert select_zone(zones, 38, 10).sample == "catchall.wav"


def test_select_zone_narrowest_key_span_breaks_a_velocity_tie():
    zones = [
        _zone("wide.wav", 30, 90, 0, 127),
        _zone("narrow.wav", 60, 60, 0, 127),
    ]
    assert select_zone(zones, 60, 64).sample == "narrow.wav"


def test_select_zone_uses_the_nearest_band_rather_than_falling_silent():
    """A velocity in an unmapped gap must still sound -- decision #106's
    velocity addendum is explicit that a kit never goes quiet for it."""
    zones = [
        _zone("soft.wav", 38, 38, 0, 40),
        _zone("hard.wav", 38, 38, 100, 127),
    ]
    assert select_zone(zones, 38, 45).sample == "soft.wav"   # 5 away vs 55
    assert select_zone(zones, 38, 90).sample == "hard.wav"   # 10 away vs 50


def test_select_zone_is_deterministic_on_a_full_tie():
    zones = [_zone("first.wav", 38, 38), _zone("second.wav", 38, 38)]
    assert select_zone(zones, 38, 64).sample == "first.wav"


def test_choked_zones_only_within_a_shared_nonzero_group():
    closed = Zone(sample="hh_closed.wav", low_key=42, high_key=42, choke_group=1)
    open_hh = Zone(sample="hh_open.wav", low_key=46, high_key=46, choke_group=1)
    kick = Zone(sample="kick.wav", low_key=36, high_key=36, choke_group=0)
    assert patch_format.choked_zones([closed, open_hh, kick], closed) == [open_hh]
    assert patch_format.choked_zones([closed, open_hh, kick], kick) == []
    assert patch_format.choked_zones([closed, open_hh], None) == []


def test_is_kit_is_the_one_key_wide_sampler_case():
    kit = Patch(engine="sampler", zones=[_zone("kick.wav", 36, 36), _zone("snare.wav", 38, 38)])
    instrument = Patch(engine="sampler", zones=[_zone("piano.wav", 48, 60)])
    assert kit.is_kit()
    assert not instrument.is_kit()
    assert not Patch(engine="synth").is_kit()


# --- Samples are referenced by bare name, and may be missing ---

def test_sample_path_resolves_against_the_samples_directory(tmp_path):
    path = patch_format.sample_path("kick.wav", directory=str(tmp_path))
    assert path == os.path.join(str(tmp_path), "kick.wav")


def test_sample_path_cannot_escape_the_samples_directory(tmp_path):
    path = patch_format.sample_path("../../etc/passwd", directory=str(tmp_path))
    assert os.path.dirname(path) == str(tmp_path)


def test_missing_sample_leaves_the_zone_unavailable_but_the_kit_loads(tmp_path):
    (tmp_path / "kick.wav").write_bytes(b"RIFF")
    patch = Patch(engine="sampler", zones=[
        _zone("kick.wav", 36, 36), _zone("gone.wav", 38, 38),
    ])
    assert patch_format.zone_available(patch.zones[0], directory=str(tmp_path))
    assert not patch_format.zone_available(patch.zones[1], directory=str(tmp_path))
    assert patch_format.missing_samples(patch, directory=str(tmp_path)) == ["gone.wav"]
    # Selection still returns the zone -- availability is the renderer's call.
    assert select_zone(patch.zones, 38, 100).sample == "gone.wav"


def test_missing_samples_includes_an_absent_soundfont(tmp_path):
    patch = patch_from_toml({"engine": "sf2", "sf2": {"soundfont": "piano.sf2"}})
    assert patch_format.missing_samples(patch, directory=str(tmp_path)) == ["piano.sf2"]


# --- Effects chain ---

def test_effects_keep_file_order_and_carry_arbitrary_params():
    patch = patch_from_toml({"effects": [
        {"type": "delay", "time": 0.25, "feedback": 0.4},
        {"type": "chorus", "rate": 1.0},
    ]})
    assert [e.type for e in patch.effects] == ["delay", "chorus"]
    assert patch.effects[0].params == {"time": 0.25, "feedback": 0.4}


def test_unknown_effect_type_survives_a_round_trip(tmp_path):
    """An older build must not silently strip an effect a newer one wrote."""
    patch = Patch(name="Wet", effects=[EffectSpec(type="reverb", params={"size": 0.8})])
    path = str(tmp_path / "wet.toml")
    save_patch(patch, path)
    reloaded = load_patch(path)
    assert reloaded.effects[0].type == "reverb"
    assert reloaded.effects[0].params == {"size": 0.8}


# --- Round trips ---

def test_synth_patch_round_trips(tmp_path):
    patch = new_patch("Fat bass", engine="synth")
    patch.osc2.level = 0.7
    patch.osc2.fine = -7.0
    patch.filter.cutoff = 900.0
    patch.filter.type = "bp"
    patch.amp_env.release = 1.25
    patch.lfo.destination = "filter"
    patch.voice.velocity_to_filter = 0.6
    path = str(tmp_path / "fat_bass.toml")
    save_patch(patch, path)
    assert load_patch(path) == patch


def test_sampler_patch_with_velocity_layers_round_trips(tmp_path):
    patch = new_patch("Kit", engine="sampler")
    patch.zones = [
        Zone(sample="snare_soft.wav", low_key=38, high_key=38, root_key=38,
             low_vel=0, high_vel=95, gain=-2.0, choke_group=0),
        Zone(sample="snare_hard.wav", low_key=38, high_key=38, root_key=38,
             low_vel=96, high_vel=127, gain=0.0, choke_group=0),
    ]
    path = str(tmp_path / "kit.toml")
    save_patch(patch, path)
    reloaded = load_patch(path)
    assert reloaded.zones == patch.zones
    assert reloaded.is_kit()


def test_sf2_patch_round_trips(tmp_path):
    patch = new_patch("Grand", engine="sf2")
    patch.sf2.soundfont = "piano.sf2"
    patch.sf2.bank = 0
    patch.sf2.preset = 4
    path = str(tmp_path / "grand.toml")
    save_patch(patch, path)
    assert load_patch(path) == patch


def test_saved_file_is_hand_editable_toml_with_explicit_sections(tmp_path):
    path = str(tmp_path / "p.toml")
    save_patch(new_patch("Lead"), path)
    text = (tmp_path / "p.toml").read_text()
    for section in ("[osc1]", "[osc2]", "[noise]", "[filter]",
                    "[amp_env]", "[filter_env]", "[lfo]", "[voice]"):
        assert section in text
    assert "[[zones]]" not in text  # synth patch


def test_save_creates_the_directory(tmp_path):
    path = str(tmp_path / "patches" / "new.toml")
    save_patch(new_patch("New"), path)
    assert os.path.isfile(path)


def test_a_pre_addendum_zone_keeps_its_meaning(tmp_path):
    """A zone written before velocity layering existed has no low_vel/
    high_vel; it must still match every velocity."""
    path = tmp_path / "old.toml"
    path.write_text(
        'engine = "sampler"\n'
        "[[zones]]\n"
        'sample = "kick.wav"\n'
        "low_key = 36\nhigh_key = 36\nroot_key = 36\n"
    )
    patch = load_patch(str(path))
    assert select_zone(patch.zones, 36, 1).sample == "kick.wav"
    assert select_zone(patch.zones, 36, 127).sample == "kick.wav"


# --- Directories and listing ---

def test_patch_paths_is_a_flat_sorted_glob(tmp_path):
    for name in ("b.toml", "a.toml", "notes.txt"):
        (tmp_path / name).write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.toml").write_text("")
    assert [os.path.basename(p) for p in patch_paths(str(tmp_path))] == ["a.toml", "b.toml"]


def test_patch_paths_on_a_missing_directory_is_empty(tmp_path):
    assert patch_paths(str(tmp_path / "nope")) == []


def test_directories_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert patch_format.patches_dir() == os.path.join(str(tmp_path), "note-color", "patches")
    assert patch_format.samples_dir() == os.path.join(str(tmp_path), "note-color", "samples")


def test_directories_fall_back_to_dot_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: "/home/tester" if p == "~" else p)
    assert patch_format.patches_dir() == "/home/tester/.config/note-color/patches"


# --- MIDI CCs are documented, not stored ---

def test_standard_midi_cc_numbers_are_documented_but_never_in_a_patch():
    assert patch_format.STANDARD_MIDI_CC["filter.cutoff"] == 74
    assert patch_format.STANDARD_MIDI_CC["filter.resonance"] == 71
    assert patch_format.STANDARD_MIDI_CC["amp_env.attack"] == 73
    assert patch_format.STANDARD_MIDI_CC["amp_env.decay"] == 75
    assert patch_format.STANDARD_MIDI_CC["amp_env.release"] == 72
    assert patch_format.STANDARD_MIDI_CC["lfo.rate"] == 76
    assert patch_format.STANDARD_MIDI_CC["lfo.depth"] == 77
    assert patch_format.STANDARD_MIDI_CC["lfo.delay"] == 78
    assert "_cc" not in patch_to_toml(new_patch("Any"))


def test_cc_keys_in_a_patch_file_are_ignored_like_any_unknown_key():
    patch = patch_from_toml({"filter": {"cutoff": 500.0, "cutoff_cc": 74}})
    assert patch.filter.cutoff == 500.0
    assert not hasattr(patch.filter, "cutoff_cc")


def test_copy_is_independent():
    patch = new_patch("A", engine="sampler")
    patch.zones = [_zone("kick.wav", 36, 36)]
    clone = patch.copy()
    clone.zones[0].sample = "other.wav"
    clone.filter.cutoff = 100.0
    assert patch.zones[0].sample == "kick.wav"
    assert patch.filter.cutoff == 12000.0
