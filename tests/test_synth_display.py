"""`synth_display.py` -- the synth tool's screen (map #99, ticket #119,
decision #107 point 1): parameter panel above, always-visible input layer
below.

Everything up to `render()` is pure and tested here; `render()` itself
(cursor addressing, terminal-size fitting, the ANSI writes) is
smoke-tested manually only, the same convention every `run_terminal_*`
view's own render method in this codebase follows.
"""

import pytest

import config
import patch_format
import synth_display as sd
import synth_layout as sl
import synth_tool as st
from color_map import fifths_index, hsl_to_rgb255, hue_for_step


def _kit(**pads):
    kit = patch_format.new_patch(name="Kit", engine="sampler")
    for index, sample in pads.items():
        st.assign_sample_to_pad(kit, int(index), sample)
    return kit


# --- colour ---------------------------------------------------------------

def test_a_note_key_is_the_same_colour_as_that_note_everywhere_else():
    # A C on this keyboard must be the colour a detected C already is in
    # every other view -- that is the point of reusing the fifths palette
    # rather than inventing a keyboard palette.
    slot = sl.two_octave_layout(3).slot_for("z")
    assert sd.slot_hue_step(slot) == fifths_index(0)
    assert sd.slot_rgb(slot) == hsl_to_rgb255(
        hue_for_step(fifths_index(0)), config.BASE_SATURATION,
        config.SYNTH_KEY_DIM_LIGHTNESS)


def test_only_lightness_moves_between_a_key_at_rest_and_a_key_sounding():
    # Hue and saturation are the key's identity, exactly as `tab`'s
    # age-fade already treats a note's colour.
    slot = sl.two_octave_layout(3).slot_for("z")
    assert sd.slot_rgb(slot, lit=True) == hsl_to_rgb255(
        hue_for_step(fifths_index(0)), config.BASE_SATURATION,
        config.SYNTH_KEY_LIT_LIGHTNESS)
    assert config.SYNTH_KEY_LIT_LIGHTNESS > config.SYNTH_KEY_DIM_LIGHTNESS


def test_a_key_follows_its_pitch_class_through_an_octave_shift():
    slot = sl.two_octave_layout(3).slot_for("s")   # C#
    assert sd.slot_hue_step(slot, octave_shift=1) == sd.slot_hue_step(slot)


def test_a_pad_tints_by_its_sample_not_by_a_pitch_class():
    # Tinting a snare by "pitch class D#" would be a colour that means
    # nothing.
    pad = sl.pad_square_layout().slot_for("z")   # pad 0
    kit = _kit(**{"0": "snare.wav"})
    assert sd.slot_hue_step(pad, kit) == sl.sample_hue_step("snare.wav")


def test_an_empty_pad_and_an_unbound_key_claim_no_colour():
    assert sd.slot_hue_step(sl.pad_square_layout().slot_for("z"), _kit()) is None
    layout = sl.two_octave_layout()
    layout.rebind("z", sl.UNBOUND, 0)
    assert sd.slot_hue_step(layout.slot_for("z")) is None
    assert sd.slot_hue_step(None) is None


def test_a_colourless_slot_still_visibly_lights_when_played():
    unlit = sd.slot_rgb(None, lit=False)
    lit = sd.slot_rgb(None, lit=True)
    assert lit != unlit and sum(lit) > sum(unlit)
    assert all(0 <= c <= 255 for c in lit)


def test_two_different_samples_usually_get_two_different_tints():
    steps = {sl.sample_hue_step(n) for n in ("kick.wav", "snare.wav", "hat.wav")}
    assert len(steps) > 1


# --- captions -------------------------------------------------------------

def test_a_note_key_is_captioned_with_the_note_it_actually_sounds():
    layout = sl.two_octave_layout(3)
    assert sd.slot_caption(layout.slot_for("z")) == "C3"
    assert sd.slot_caption(layout.slot_for("z"), octave_shift=1) == "C4"
    assert sd.slot_caption(layout.slot_for("q")) == "C4"


def test_a_pad_is_captioned_by_its_sample_or_its_number():
    pad = sl.pad_square_layout().slot_for("z")
    assert sd.slot_caption(pad, kit=_kit(**{"0": "kick.wav"})) == "kic"
    assert sd.slot_caption(pad, kit=_kit()) == "P1"   # 1-based, like hardware
    assert sd.slot_caption(None) == "--"


# --- key lights -----------------------------------------------------------

def test_each_key_animates_independently_so_a_chord_does_not_average_out():
    # Several keys sound at once -- that is what a keyboard is -- and a
    # shared animator would smear them into one colour.
    layout = sl.two_octave_layout(3)
    lights = sd.KeyLights()
    for _ in range(60):
        colors = lights.update(0.05, layout, {"z", "b"})
    # Each held key settles on *its own* lit colour (C and G are different
    # hues), rather than every sounding key converging on one average.
    for key in ("z", "b"):
        assert colors[key] == pytest.approx(
            sd.slot_rgb(layout.slot_for(key), lit=True), abs=3), key
    # ...and a key that is not held stays at its own resting colour.
    assert colors["x"] == pytest.approx(sd.slot_rgb(layout.slot_for("x")), abs=3)
    assert set(colors) == set(layout.keys())


def test_a_released_key_fades_back_towards_its_resting_colour():
    layout = sl.two_octave_layout(3)
    lights = sd.KeyLights()
    for _ in range(60):
        held = lights.update(0.05, layout, {"z"})
    for _ in range(60):
        released = lights.update(0.05, layout, set())
    assert sum(released["z"]) < sum(held["z"])
    assert released["z"] == pytest.approx(sd.slot_rgb(layout.slot_for("z")), abs=3)


def test_key_lights_take_an_injected_dt_and_read_no_clock():
    # Pure math: a test steps the animation deterministically.
    layout = sl.two_octave_layout(3)
    one, two = sd.KeyLights(), sd.KeyLights()
    for _ in range(10):
        a = one.update(0.05, layout, {"z"})
        b = two.update(0.05, layout, {"z"})
    assert a == b


# --- the input layer ------------------------------------------------------

def test_the_input_layer_draws_one_line_per_populated_layout_row():
    layout = sl.two_octave_layout(3)
    lines = sd.input_layer_lines(layout, {})
    assert len(lines) == layout.height() == 4


def test_a_layout_with_a_gap_row_does_not_draw_a_blank_line_for_it():
    layout = sl.octave_pads_layout(3)
    assert layout.height() == 4          # rows 0, 1 and 3 are populated
    assert len(sd.input_layer_lines(layout, {})) == 3


def test_every_playable_key_is_visible_in_the_input_layer():
    # The input layer's whole job: show *exactly* the keys this layout
    # plays, so a player can see which key is which without counting.
    for layout in (sl.two_octave_layout(), sl.octave_pads_layout(), sl.pad_square_layout()):
        text = "".join(sd.input_layer_lines(layout, {}))
        for key in layout.keys():
            assert key in text, (layout.name, key)


def test_the_input_layer_captions_notes_with_their_names():
    text = "".join(sd.input_layer_lines(sl.two_octave_layout(3), {}))
    assert "C3" in text and "C4" in text


# --- the parameter panel --------------------------------------------------

def test_the_panel_shows_section_headings_with_their_own_parameters():
    lines = sd.panel_lines(patch_format.new_patch(), 0, 12)
    assert any("OSC 1" in line for line in lines)
    assert any("Wave" in line for line in lines)
    assert len(lines) <= 12


def test_the_panel_scrolls_to_keep_the_selected_parameter_visible():
    patch = patch_format.new_patch()
    import synth_params as sp

    last = len(sp.specs_for(patch)) - 1
    lines = sd.panel_lines(patch, last, 8)
    spec = sp.specs_for(patch)[last]
    assert any(spec.label in line for line in lines)


def test_the_selected_row_is_marked_and_only_one_row_is():
    lines = sd.panel_lines(patch_format.new_patch(), 0, 12)
    assert sum(1 for line in lines if "\033[7m" in line) == 1


def test_a_kit_panel_says_so_rather_than_showing_a_wall_of_dead_knobs():
    lines = sd.panel_lines(patch_format.new_patch(engine="sampler"), 0, 8)
    assert any("VOICE" in line for line in lines)
    assert not any("Cutoff" in line for line in lines)


# --- overlays -------------------------------------------------------------

def test_the_save_overlay_shows_the_typed_name_and_says_keys_still_play():
    overlay = st.Overlay(st.OVERLAY_SAVE, title="Save patch as", buffer="Fat bass")
    text = "\n".join(sd.overlay_lines(overlay, 8))
    assert "Fat bass" in text
    # The always-plays invariant holds even inside a text field, and the
    # overlay says so rather than leaving it a surprise.
    assert "always play" in text


def test_the_browser_overlay_lists_entries_and_marks_the_highlighted_one():
    overlay = st.Overlay(st.OVERLAY_PATCH, [("Lead  [synth]", "/a"), ("Kit  [sampler]", "/b")],
                         title="Load patch")
    overlay.move(1)
    lines = sd.overlay_lines(overlay, 8)
    text = "\n".join(lines)
    assert "Lead" in text and "Kit" in text
    assert "\033[7m" in [line for line in lines if "Kit" in line][0]


def test_an_empty_browser_says_so_rather_than_rendering_nothing():
    overlay = st.Overlay(st.OVERLAY_PATCH, [], title="Load patch")
    assert any("nothing here" in line for line in sd.overlay_lines(overlay, 8))


def test_an_overlay_never_draws_more_lines_than_it_was_given_room_for():
    entries = [(f"patch {i}", f"/p{i}") for i in range(50)]
    overlay = st.Overlay(st.OVERLAY_PATCH, entries, title="Load patch")
    overlay.move(40)
    lines = sd.overlay_lines(overlay, 6)
    assert len(lines) <= 6
    # ...and the highlighted entry is still among them.
    assert any("patch 40" in line for line in lines)
