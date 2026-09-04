"""`synth_layout.py` -- the synth tool's four layouts (map #99, ticket
#119, decision #107 point 2).

The tests that matter most here are the *seam* ones: this module gets its
note pitches from `score_audition.PIANO_KEY_SEMITONES` (ticket #120)
rather than restating the tracker keyboard, and the first block below is
what holds that sharing honest -- a second hand-written copy of
`zsxdcvgbhnjm`/`q2w3er5t6y7u` would pass every other test in this file
right up until the day the two drifted apart.
"""

import os

import pytest

import config
import score_audition
import sound_engine
import synth_layout as sl


# --- the shared tracker keyboard (the seam with ticket #120) --------------

def test_two_octave_layout_plays_exactly_the_tracker_keyboards_keys():
    layout = sl.two_octave_layout(3)
    expected = set(score_audition.PIANO_LOWER_ROW + score_audition.PIANO_UPPER_ROW)
    assert set(layout.keys()) == expected
    assert len(layout.slots) == 24


@pytest.mark.parametrize("base_octave", [2, 3, 4])
def test_every_key_sounds_the_pitch_score_audition_would_play(base_octave):
    # The drift guard. `score_audition.pitch_for_key()` is what the score
    # editor's piano mode plays; a key that sounded differently in the
    # two tools would be the same keyboard "nearly", which is worse than
    # two openly different ones.
    layout = sl.two_octave_layout(base_octave)
    for slot in layout.slots:
        pitch_class, octave = score_audition.pitch_for_key(slot.key, base_octave)
        assert slot.value == sound_engine.midi_pitch(pitch_class, octave), slot.key


def test_lower_row_starts_on_c_of_the_base_octave():
    layout = sl.two_octave_layout(3)
    assert layout.slot_for("z").value == sound_engine.midi_pitch(0, 3)
    # ...and the upper row is exactly one octave above it.
    assert layout.slot_for("q").value == layout.slot_for("z").value + 12


def test_base_octave_defaults_to_the_configured_one():
    assert sl.two_octave_layout().slot_for("z").value == \
        sl.two_octave_layout(config.SYNTH_BASE_OCTAVE).slot_for("z").value


# --- geometry (the part this module adds on top of the shared table) ------

def test_black_keys_sit_physically_between_their_white_neighbours():
    layout = sl.two_octave_layout(3)
    grid = layout.grid()
    # Lower octave: white keys on display row 3, black keys on row 2. C#
    # ('s') must be drawn on the black row, physically between C ('z')
    # and D ('x') on the row below.
    c_sharp = layout.slot_for("s")
    assert grid[2][c_sharp.col] is c_sharp
    assert layout.slot_for("z").col < c_sharp.col < layout.slot_for("x").col


def test_grid_is_keyed_by_row_then_column():
    layout = sl.pad_square_layout()
    grid = layout.grid()
    assert sorted(grid) == [0, 1, 2, 3]
    assert grid[3][0].key == "z"
    assert layout.width() == 7 and layout.height() == 4


# --- pads -----------------------------------------------------------------

def test_pad_square_numbers_pads_bottom_row_first():
    # Hardware convention: on an MPC the bottom-left pad is pad 1, which
    # is where a kick belongs.
    layout = sl.pad_square_layout()
    assert layout.slot_for("z").value == 0
    assert layout.slot_for("v").value == 3
    assert layout.slot_for("1").value == 12
    assert layout.slot_for("4").value == 15


def test_pad_midi_key_round_trips_through_pad_index():
    for index in range(16):
        assert sl.pad_index_for_key(sl.pad_midi_key(index)) == index
    assert sl.pad_midi_key(0) == sl.PAD_BASE_KEY == 36


def test_pads_never_transpose_with_the_octave_shift():
    # A kick is a kick: shifting a kit's keys would silently point every
    # pad at a different zone.
    pad = sl.pad_square_layout().slot_for("z")
    assert pad.midi_key(0) == pad.midi_key(2) == pad.midi_key(-3)


def test_note_slots_do_transpose_with_the_octave_shift():
    note = sl.two_octave_layout(3).slot_for("z")
    assert note.midi_key(1) == note.midi_key(0) + 12
    assert note.midi_key(-1) == note.midi_key(0) - 12


def test_slots_route_to_their_own_midi_channel():
    assert sl.two_octave_layout().slot_for("z").channel() == sl.NOTE_CHANNEL == 0
    assert sl.pad_square_layout().slot_for("z").channel() == sl.PAD_CHANNEL == 9


# --- the layout set -------------------------------------------------------

def test_only_layout_two_is_dual():
    assert not sl.two_octave_layout().is_dual
    assert sl.octave_pads_layout().is_dual
    assert not sl.pad_square_layout().is_dual


def test_octave_pads_layout_has_one_octave_of_keys_and_eight_pads():
    layout = sl.octave_pads_layout(3)
    notes = [s for s in layout.slots if s.kind == sl.NOTE]
    pads = [s for s in layout.slots if s.kind == sl.PAD]
    assert len(notes) == 12 and len(pads) == 8
    # No key does both jobs -- the collision layouts exist to avoid.
    assert not ({s.key for s in notes} & {s.key for s in pads})


def test_pad_square_and_two_octave_would_collide_which_is_why_they_never_coexist():
    keyboard = set(sl.two_octave_layout().keys())
    pads = set(sl.pad_square_layout().keys())
    assert keyboard & pads  # they genuinely fight over 1234/qwer/asdf/zxcv


def test_cycle_layout_wraps_forward_and_backward():
    assert sl.cycle_layout(0, 3) == 1
    assert sl.cycle_layout(2, 3) == 0
    assert sl.cycle_layout(0, 3, -1) == 2
    assert sl.cycle_layout(0, 0) == 0  # no layouts: no crash, no move


def test_slot_for_is_case_insensitive_and_ignores_non_keys():
    layout = sl.two_octave_layout()
    assert layout.slot_for("Z") is layout.slot_for("z")
    assert layout.slot_for("UP") is None
    assert layout.slot_for("") is None
    assert layout.slot_for(None) is None


def test_builtin_returns_a_fresh_instance_each_call():
    first, second = sl.builtin("two-octave"), sl.builtin("two-octave")
    assert first is not second
    first.rebind("z", sl.UNBOUND, 0)
    assert second.slot_for("z").kind == sl.NOTE
    assert sl.builtin("nonexistent") is None


# --- custom layouts (their own file, decision #107 point 4) ---------------

def test_rebind_keeps_the_keys_position_so_the_layer_never_shifts():
    layout = sl.two_octave_layout()
    before = layout.slot_for("z")
    after = layout.rebind("z", sl.PAD, 3)
    assert (after.row, after.col) == (before.row, before.col)
    assert after.kind == sl.PAD and after.value == 3
    assert layout.rebind("UNBOUND-KEY", sl.NOTE, 60) is None


def test_rebind_falls_back_to_unbound_for_an_unknown_kind():
    layout = sl.two_octave_layout()
    assert layout.rebind("z", "nonsense", 5).kind == sl.UNBOUND


def test_new_custom_from_copies_a_builtin_without_aliasing_it():
    base = sl.two_octave_layout()
    custom = sl.new_custom_from(base, "mine")
    assert custom.name == "mine" and not custom.builtin and custom.path is None
    custom.rebind("z", sl.PAD, 0)
    assert base.slot_for("z").kind == sl.NOTE


def test_layout_survives_a_save_load_round_trip(tmp_path):
    custom = sl.new_custom_from(sl.octave_pads_layout(), "my hands")
    custom.rebind("q", sl.PAD, 7)
    path = sl.save_layout(custom, str(tmp_path / "mine.toml"))
    assert custom.path == path and not custom.builtin

    loaded = sl.load_layout(path)
    assert loaded.name == "my hands" and not loaded.builtin
    assert loaded.path == path
    assert {(s.key, s.kind, s.value, s.row, s.col) for s in loaded.slots} == \
        {(s.key, s.kind, s.value, s.row, s.col) for s in custom.slots}


def test_save_layout_derives_a_filename_from_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "layouts_dir", lambda: str(tmp_path / "layouts"))
    path = sl.save_layout(sl.new_custom_from(sl.pad_square_layout(), "My Kit!"))
    assert os.path.basename(path) == "My-Kit.toml"
    assert os.path.isfile(path)


def test_layout_from_toml_ignores_junk_rather_than_raising():
    layout = sl.layout_from_toml({
        "name": "ok",
        "keys": [
            "not a table",
            {"key": "toolong", "kind": "note", "value": 60},
            {"key": "z", "kind": "bogus", "value": "not a number"},
            {"key": "X", "kind": "pad", "value": 2, "row": -5, "col": 1},
        ],
    })
    assert [(s.key, s.kind, s.value) for s in layout.slots] == \
        [("z", sl.UNBOUND, 0), ("x", sl.PAD, 2)]
    assert layout.slots[1].row == 0  # negative row clamped, not crashed


def test_layout_from_toml_names_itself_after_its_file_when_unnamed():
    assert sl.layout_from_toml({}, "/tmp/hands.toml").name == "hands"
    assert sl.layout_from_toml({}).name == "custom"


def test_layout_paths_is_a_flat_sorted_glob(tmp_path):
    for name in ("b.toml", "a.toml", "ignored.txt"):
        (tmp_path / name).write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.toml").write_text("")
    assert [os.path.basename(p) for p in sl.layout_paths(str(tmp_path))] == ["a.toml", "b.toml"]


def test_available_layouts_skips_an_unparseable_custom_file(tmp_path):
    good = tmp_path / "good.toml"
    good.write_text('name = "good"\n')
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not [ valid toml\n")
    layouts = sl.available_layouts([str(bad), str(good)])
    assert [l.name for l in layouts] == list(sl.BUILTIN_NAMES) + ["good"]


def test_slugify_can_never_escape_its_directory():
    assert sl.slugify("../../etc/passwd") == "etc-passwd"
    assert sl.slugify("") == "untitled"
    assert sl.slugify("   ") == "untitled"
    assert sl.slugify("Fat Bass 2") == "Fat-Bass-2"


# --- pad colouring --------------------------------------------------------

def test_sample_hue_step_is_stable_across_processes_and_in_range():
    # Deliberately not hash(), which is randomised per process -- a pad
    # that changed colour every launch would be worse than no colour.
    assert sl.sample_hue_step("kick.wav") == sl.sample_hue_step("kick.wav")
    assert sl.sample_hue_step("kick.wav") == sum(b"kick.wav") % 12
    assert 0 <= sl.sample_hue_step("snare.wav") < 12
    assert sl.sample_hue_step("") is None
    assert sl.sample_hue_step(None) is None
