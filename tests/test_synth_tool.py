"""`synth_tool.py` -- the synth tool's non-rendering runtime (map #99,
ticket #119, decision #107).

The invariant most of this file is really about: **every plain letter and
number plays a note, always.** That is the property an instrument needs
(#107 point 5), and it decides the whole key map by elimination -- arrows
drive parameters, Tab cycles layouts, and every remaining command is
`Shift`+key. `test_resolve_action_*` below is where that invariant is
actually pinned down.
"""

import os

import pytest

import config
import kitty_keys as kk
import patch_format
import synth_layout as sl
import synth_tool as st


def press(key, mods=0, text=None, event=kk.PRESS):
    return kk.KeyEvent(key=key, event=event, mods=mods,
                       text=key if text is None else text,
                       codepoint=ord(key) if len(key) == 1 else 0)


def shift(key):
    """A Shift+letter as the *kitty* path reports it: layout-position key
    plus the text it produced."""
    return press(key.lower(), mods=kk.MOD_SHIFT, text=key.upper())


def legacy_shift(key):
    """The same keystroke as `main.RawKeys._synthetic_press()` builds it
    on a terminal without the protocol: no associated text at all."""
    return press(key.lower(), mods=kk.MOD_SHIFT, text="")


# --- the always-plays invariant -------------------------------------------

@pytest.mark.parametrize("key", list("abcdefghijklmnopqrstuvwxyz0123456789"))
def test_no_plain_letter_or_number_is_ever_a_command(key):
    # The whole tool rests on this: there is no state in which pressing a
    # key does something other than sound a note.
    assert st.resolve_action(press(key)) is None
    for overlay in (st.OVERLAY_PATCH, st.OVERLAY_SAVE, st.OVERLAY_SAMPLE):
        assert st.resolve_action(press(key), overlay) is None


def test_tab_cycles_layouts_because_nearly_every_other_key_plays():
    assert st.resolve_action(press("TAB")) == "layout_cycle"


def test_arrows_drive_the_parameter_panel():
    assert st.resolve_action(press("UP")) == "param_prev"
    assert st.resolve_action(press("DOWN")) == "param_next"
    assert st.resolve_action(press("LEFT")) == "param_dec"
    assert st.resolve_action(press("RIGHT")) == "param_inc"


def test_shift_left_right_is_the_coarse_sweep_escape_hatch():
    assert st.resolve_action(press("LEFT", kk.MOD_SHIFT)) == "param_dec_coarse"
    assert st.resolve_action(press("RIGHT", kk.MOD_SHIFT)) == "param_inc_coarse"


def test_shift_up_down_transposes_the_keyboard():
    # The octave shift has nowhere else to live: every plain letter is a
    # note, so the transpose cannot have a letter of its own.
    assert st.resolve_action(press("UP", kk.MOD_SHIFT)) == "octave_up"
    assert st.resolve_action(press("DOWN", kk.MOD_SHIFT)) == "octave_down"


@pytest.mark.parametrize("key,action", sorted(st.SHIFT_ACTIONS.items()))
def test_every_shift_action_resolves_on_both_keyboard_paths(key, action):
    # One action table has to work with and without the kitty protocol:
    # `text` carries the shifted character on one path, `key`+MOD_SHIFT on
    # the other.
    assert st.resolve_action(shift(key)) == action
    assert st.resolve_action(legacy_shift(key)) == action


def test_pipe_returns_to_the_menu_like_every_other_view():
    assert st.resolve_action(press("|")) == "menu"


def test_a_release_is_never_a_command():
    # A note-off must reach the key policy, not the action dispatcher.
    assert st.resolve_action(press("p", kk.MOD_SHIFT, "P", event=kk.RELEASE)) is None
    assert st.resolve_action(press("TAB", event=kk.RELEASE)) is None
    assert st.resolve_action(None) is None


def test_arrows_re_point_at_the_list_while_an_overlay_is_open():
    assert st.resolve_action(press("UP"), st.OVERLAY_PATCH) == "overlay_prev"
    assert st.resolve_action(press("DOWN"), st.OVERLAY_PATCH) == "overlay_next"
    assert st.resolve_action(press("LEFT"), st.OVERLAY_SAMPLE) == "overlay_back"
    assert st.resolve_action(press("RIGHT"), st.OVERLAY_SAMPLE) == "overlay_forward"
    assert st.resolve_action(press("ENTER"), st.OVERLAY_PATCH) == "overlay_confirm"
    assert st.resolve_action(press("BACKSPACE"), st.OVERLAY_SAVE) == "overlay_backspace"
    assert st.resolve_action(press("ESC"), st.OVERLAY_PATCH) == "overlay_cancel"


def test_enter_and_backspace_do_nothing_with_no_overlay_open():
    assert st.resolve_action(press("ENTER")) is None
    assert st.resolve_action(press("BACKSPACE")) is None


def test_typed_text_reconstructs_the_shifted_character_without_the_protocol():
    assert st.typed_text(shift("w")) == "W"
    assert st.typed_text(legacy_shift("w")) == "W"
    assert st.typed_text(press("w")) == "w"
    assert st.typed_text(press("TAB", text="")) == ""
    assert st.typed_text(None) == ""


# --- engine routing (#107's implementation note) --------------------------

class _RecordingEngine:
    def __init__(self, name):
        self.name = name
        self.seen = []

    def note_on(self, event, sample_rate):
        self.seen.append(event.pitch)
        return self.name


def test_channel_router_sends_pads_to_the_kit_and_keys_to_the_synth():
    from sound_engine import NoteOn

    notes, pads = _RecordingEngine("synth"), _RecordingEngine("kit")
    router = st.ChannelRouter(notes, pads)
    assert router.note_on(NoteOn(60, 1.0, sl.NOTE_CHANNEL), 48000) == "synth"
    assert router.note_on(NoteOn(36, 1.0, sl.PAD_CHANNEL), 48000) == "kit"
    assert notes.seen == [60] and pads.seen == [36]


def test_channel_router_yields_a_silent_voice_rather_than_none():
    # The Engine Protocol promises a Voice; a None return would push a
    # None check down into the voice manager.
    from sampler import SilentVoice
    from sound_engine import NoteOn

    router = st.ChannelRouter(_RecordingEngine("synth"), None)
    voice = router.note_on(NoteOn(36, 1.0, sl.PAD_CHANNEL), 48000)
    assert isinstance(voice, SilentVoice)


def test_a_dual_layout_gets_its_own_lower_voice_budget():
    # One cap now feeds two hands, so the risk is starvation: a drum hit
    # arriving to find every slot held by sustained synth notes.
    assert st.polyphony_for_layout(sl.octave_pads_layout()) == config.POLYPHONY_SYNTH_DUAL
    assert st.polyphony_for_layout(sl.two_octave_layout()) == config.POLYPHONY_STANDALONE
    assert st.polyphony_for_layout(sl.pad_square_layout()) == config.POLYPHONY_STANDALONE
    assert st.polyphony_for_layout(None) == config.POLYPHONY_STANDALONE
    assert config.POLYPHONY_SYNTH_DUAL < config.POLYPHONY_STANDALONE


# --- overlays -------------------------------------------------------------

def test_overlay_cursor_clamps_and_reports_its_current_entry():
    overlay = st.Overlay(st.OVERLAY_PATCH, [("a", "/a"), ("b", "/b")])
    assert overlay.current == ("a", "/a")
    assert overlay.move(-1) == 0
    assert overlay.move(1) == 1 and overlay.current == ("b", "/b")
    assert overlay.move(5) == 1
    assert st.Overlay(st.OVERLAY_PATCH, []).current is None


def test_overlay_buffer_appends_and_backspaces():
    overlay = st.Overlay(st.OVERLAY_SAVE, buffer="Fat")
    assert overlay.append(" bass") == "Fat bass"
    assert overlay.backspace() == "Fat bas"


def test_sample_entries_lists_parent_then_directories_then_wavs(tmp_path):
    (tmp_path / "kicks").mkdir()
    (tmp_path / "b.wav").write_bytes(b"")
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / ".hidden.wav").write_bytes(b"")
    entries = st.sample_entries(str(tmp_path))
    assert [(label, kind) for label, _path, kind in entries] == [
        ("..", "dir"), ("kicks/", "dir"), ("a.wav", "wav"), ("b.wav", "wav"),
    ]


def test_sample_entries_of_an_unreadable_directory_still_offers_a_way_out(tmp_path):
    entries = st.sample_entries(str(tmp_path / "does-not-exist"))
    assert [label for label, _p, _k in entries] == [".."]


# --- pads as a view onto a sampler kit ------------------------------------

def test_assigning_a_sample_creates_a_one_key_wide_zone_at_the_pads_key():
    kit = patch_format.new_patch(engine="sampler")
    zone = st.assign_sample_to_pad(kit, 0, "kick.wav")
    assert zone.low_key == zone.high_key == sl.pad_midi_key(0)
    # root_key on the pad's own key, so a drum hit plays untransposed.
    assert zone.root_key == zone.low_key
    assert kit.is_kit()


def test_assigning_to_an_occupied_pad_replaces_rather_than_stacks():
    kit = patch_format.new_patch(engine="sampler")
    st.assign_sample_to_pad(kit, 3, "old.wav")
    st.assign_sample_to_pad(kit, 3, "new.wav")
    assert len(kit.zones) == 1 and kit.zones[0].sample == "new.wav"


def test_zone_for_pad_finds_what_a_pad_will_play():
    kit = patch_format.new_patch(engine="sampler")
    st.assign_sample_to_pad(kit, 2, "snare.wav")
    assert st.zone_for_pad(kit, 2).sample == "snare.wav"
    assert st.zone_for_pad(kit, 5) is None
    assert st.zone_for_pad(None, 2) is None


# --- tool state -----------------------------------------------------------

@pytest.fixture
def state():
    return st.SynthToolState(layouts=[
        sl.two_octave_layout(), sl.octave_pads_layout(), sl.pad_square_layout(),
    ])


def test_tab_cycles_through_every_layout_and_wraps(state):
    assert state.layout.name == "two-octave"
    assert state.cycle_layout().name == "octave-pads"
    assert state.cycle_layout().name == "pads"
    assert state.cycle_layout().name == "two-octave"


def test_octave_shift_clamps_rather_than_wrapping(state):
    # A shift that wrapped from +3 to -3 would move the whole keyboard
    # six octaves under the player's hands.
    for _ in range(10):
        state.shift_octave(1)
    assert state.octave_shift == config.SYNTH_OCTAVE_SHIFT_MAX
    for _ in range(20):
        state.shift_octave(-1)
    assert state.octave_shift == -config.SYNTH_OCTAVE_SHIFT_MAX


def test_a_pads_only_layout_with_a_kit_puts_the_kit_in_the_panel(state):
    # Editing an oscillator you cannot currently play would be a panel
    # about nothing.
    state.kit = patch_format.new_patch(name="Kit", engine="sampler")
    state.layout_index = 2  # pads
    assert state.panel_patch() is state.kit
    state.layout_index = 0  # two-octave
    assert state.panel_patch() is state.patch
    state.layout_index = 1  # dual: the keys are the half with knobs
    assert state.panel_patch() is state.patch


def test_loading_routes_a_patch_by_its_own_engine(state):
    kit = patch_format.new_patch(name="Kit", engine="sampler")
    state.set_patch(kit)
    assert state.kit is kit and state.patch is not kit

    sound = patch_format.new_patch(name="Lead", engine="synth")
    state.set_patch(sound, "/tmp/lead.toml")
    assert state.patch is sound and state.patch_path == "/tmp/lead.toml"
    assert state.kit is kit  # loading a synth patch didn't clear the kit


def test_adjusting_a_parameter_marks_the_patch_dirty(state):
    assert not state.patch_dirty
    state.move_param(3)
    assert state.adjust_param(1) is not None
    assert state.patch_dirty


def test_param_selection_stays_inside_the_current_patchs_spec_list(state):
    state.move_param(500)
    assert state.selected_spec() is not None
    assert state.param_index == len(state.specs()) - 1


def test_saving_keeps_the_typed_name_but_slugifies_the_filename(state, tmp_path):
    path = state.save_patch_as("Fat Bass 2", str(tmp_path))
    assert os.path.basename(path) == "Fat-Bass-2.toml"
    assert patch_format.load_patch(path).name == "Fat Bass 2"
    assert not state.patch_dirty and state.patch_path == path


def test_saving_a_blank_name_falls_back_to_the_patchs_own(state, tmp_path):
    state.patch.name = "Init"
    assert os.path.basename(state.save_patch_as("   ", str(tmp_path))) == "Init.toml"


# --- custom layouts -------------------------------------------------------

def test_a_new_custom_layout_copies_the_active_one_and_is_selected(state):
    custom = state.new_custom_layout("mine")
    assert state.layout is custom and not custom.builtin
    assert custom.keys() == sl.two_octave_layout().keys()
    assert state.layout_dirty


def test_builtins_are_never_edited_in_place(state):
    state.last_key = "z"
    assert state.cycle_bind_kind() is None
    assert state.nudge_bind_value(1) is None
    assert state.save_layout() is None


def test_binding_acts_on_whichever_key_was_played_last(state):
    state.new_custom_layout("mine")
    assert state.bind_target() is None      # nothing played yet
    state.last_key = "z"
    assert state.bind_target().key == "z"
    assert state.cycle_bind_kind().kind == sl.PAD
    assert state.cycle_bind_kind().kind == sl.UNBOUND
    assert state.cycle_bind_kind().kind == sl.NOTE


def test_nudging_moves_a_note_binding_one_semitone(state):
    state.new_custom_layout("mine")
    state.last_key = "z"
    before = state.layout.slot_for("z").value
    assert state.nudge_bind_value(1).value == before + 1
    assert state.nudge_bind_value(-1).value == before


def test_nudging_clamps_to_the_midi_range(state):
    state.new_custom_layout("mine")
    state.last_key = "z"
    state.layout.rebind("z", sl.NOTE, 127)
    assert state.nudge_bind_value(1).value == 127
    state.layout.rebind("z", sl.NOTE, 0)
    assert state.nudge_bind_value(-1).value == 0


def test_saving_a_custom_layout_clears_its_dirty_flag(state, tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "layouts_dir", lambda: str(tmp_path))
    state.new_custom_layout("mine")
    path = state.save_layout()
    assert os.path.isfile(path) and not state.layout_dirty


def test_state_falls_back_to_a_layout_rather_than_none(monkeypatch):
    monkeypatch.setattr(sl, "available_layouts", lambda *a, **k: [])
    assert st.SynthToolState().layout is not None


def test_toggling_the_same_overlay_twice_closes_it(state):
    assert state.toggle_overlay(st.OVERLAY_PATCH) is not None
    assert state.toggle_overlay(st.OVERLAY_PATCH) is None
    assert state.overlay is None
