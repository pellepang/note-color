"""`synth_params.py` -- the arrow-driven parameter panel's model (map #99,
ticket #119, decision #107 point 5).

Decision #107 settled the interaction; this module exists so that
interaction is pure and testable. Two behaviours carry most of the weight
below: **log-scaled parameters step by ratio, not by amount** (a cutoff
sweep that adds a fixed number of Hz crawls at the bottom of its range
and leaps at the top), and **numbers clamp while choices wrap** -- the
same clamp-not-wrap/wrap-a-ring split `settings_display.py` and the score
editor's Chord builder already follow.
"""

import pytest

import config
import patch_format
import synth_params as sp


def _spec(patch, path):
    return next(s for s in sp.specs_for(patch) if s.path == path)


@pytest.fixture
def patch():
    return patch_format.new_patch()


# --- which parameters a patch shows ---------------------------------------

def test_synth_patch_shows_oscillators_through_lfo_plus_voice(patch):
    titles = [title for title, _ in sp.sections_for(patch)]
    assert titles == ["OSC 1", "OSC 2", "NOISE", "FILTER", "AMP ENV",
                      "FILTER ENV", "LFO", "VOICE"]


def test_a_sampler_kit_shows_only_voice():
    # Its sound lives in its zones -- which are files, changed by
    # importing a sample onto a pad -- not in a bank of knobs. A wall of
    # dead oscillator rows would misrepresent what is adjustable.
    kit = patch_format.new_patch(engine="sampler")
    assert [title for title, _ in sp.sections_for(kit)] == ["VOICE"]


def test_an_sf2_patch_shows_its_program_selection_and_voice():
    assert [t for t, _ in sp.sections_for(patch_format.new_patch(engine="sf2"))] == ["SF2", "VOICE"]


def test_every_spec_addresses_a_field_that_really_exists(patch):
    # ParamSpec reads and writes by getattr/setattr, so a typo'd attr
    # would only surface when someone pressed Left on that row.
    for spec in sp.specs_for(patch):
        assert hasattr(getattr(patch, spec.section), spec.attr), spec.path


def test_section_of_names_the_owning_section_for_every_index(patch):
    specs = sp.specs_for(patch)
    assert sp.section_of(patch, 0) == "OSC 1"
    assert sp.section_of(patch, len(specs) - 1) == "VOICE"
    assert sp.section_of(patch, len(specs)) == ""  # out of range renders blank, never raises


# --- stepping -------------------------------------------------------------

def test_choices_wrap_in_both_directions(patch):
    spec = _spec(patch, "osc1.waveform")
    assert sp.step_value(spec, "saw", 1) == "square"
    assert sp.step_value(spec, "saw", -1) == "sine"      # wrapped off the front
    assert sp.step_value(spec, "sine", 1) == "saw"       # ...and off the back
    assert sp.step_value(spec, "not an option", 1) == "square"


def test_numbers_clamp_rather_than_wrap(patch):
    spec = _spec(patch, "voice.volume")
    assert sp.step_value(spec, 1.0, 1) == pytest.approx(1.0)
    assert sp.step_value(spec, 0.0, -1) == pytest.approx(0.0)


def test_integers_step_whole_numbers_and_clamp(patch):
    spec = _spec(patch, "osc1.octave")
    assert sp.step_value(spec, 0, 1) == 1
    assert sp.step_value(spec, 2, 1) == 2       # clamped at spec.high
    assert sp.step_value(spec, -2, -1) == -2
    assert isinstance(sp.step_value(spec, 0, 1), int)


def test_a_log_parameter_steps_by_ratio_so_a_sweep_is_musically_even(patch):
    # The whole reason SCALE_LOG exists: one press is the same musical
    # distance at 100Hz as at 10kHz. Here that step is a semitone.
    spec = _spec(patch, "filter.cutoff")
    for start in (100.0, 1000.0, 10000.0):
        assert sp.step_value(spec, start, 1) / start == \
            pytest.approx(config.SYNTH_PARAM_CUTOFF_RATIO)


def test_a_log_parameter_can_leave_zero_and_return_to_it(patch):
    # A ratio step can never lift a value off zero on its own, so the
    # first press has to jump -- and stepping back down must land on the
    # spec minimum rather than approaching it forever, because "no attack
    # at all" is a real, reachable synth setting.
    spec = _spec(patch, "amp_env.attack")
    assert spec.scale == sp.SCALE_LOG and spec.low == 0.0
    up = sp.step_value(spec, 0.0, 1)
    assert up == pytest.approx(config.SYNTH_PARAM_LOG_FLOOR)
    assert sp.step_value(spec, up, -1) == 0.0


def test_a_log_parameter_stays_inside_its_range(patch):
    spec = _spec(patch, "filter.cutoff")
    value = spec.high
    for _ in range(50):
        value = sp.step_value(spec, value, 1)
    assert value == pytest.approx(spec.high)


def test_shift_makes_a_coarse_jump_worth_ten_ordinary_presses(patch):
    # #107 point 5's escape hatch, applied to a sweep that would
    # otherwise take a hundred presses to cross the filter's range.
    linear = _spec(patch, "voice.volume")
    fine = sp.step_value(linear, 0.0, 1)
    coarse = sp.step_value(linear, 0.0, 1, coarse=True)
    assert coarse == pytest.approx(fine * config.SYNTH_PARAM_COARSE_STEPS)

    log = _spec(patch, "filter.cutoff")
    assert sp.step_value(log, 1000.0, 1, coarse=True) / 1000.0 == \
        pytest.approx(config.SYNTH_PARAM_CUTOFF_RATIO ** config.SYNTH_PARAM_COARSE_STEPS)

    integer = _spec(patch, "osc1.semitones")
    assert sp.step_value(integer, 0, 1, coarse=True) == 10


def test_coarse_still_clamps_at_the_ends(patch):
    spec = _spec(patch, "voice.volume")
    assert sp.step_value(spec, 0.9, 1, coarse=True) == pytest.approx(1.0)


def test_adjust_writes_the_stepped_value_back_onto_the_patch(patch):
    spec = _spec(patch, "filter.resonance")
    before = sp.read(patch, spec)
    returned = sp.adjust(patch, spec, 1)
    assert patch.filter.resonance == returned > before


# --- selection and viewport ----------------------------------------------

def test_selection_clamps_rather_than_wrapping():
    # A long list of knobs is a ruler, not a ring: wrapping from the last
    # row to the first would be a surprise on every overshoot.
    assert sp.move_selection(0, 10, -1) == 0
    assert sp.move_selection(9, 10, 1) == 9
    assert sp.move_selection(4, 10, 1) == 5
    assert sp.move_selection(0, 0, 1) == 0


def test_visible_range_keeps_the_selection_on_screen():
    assert sp.visible_range(0, 5, 10) == (0, 5)     # everything fits
    assert sp.visible_range(0, 40, 10) == (0, 10)   # clamped at the top
    assert sp.visible_range(39, 40, 10) == (30, 40)  # ...and at the bottom
    start, end = sp.visible_range(20, 40, 10)
    assert start <= 20 < end and end - start == 10


# --- formatting -----------------------------------------------------------

def test_format_value_is_readable_at_both_ends_of_each_unit(patch):
    assert sp.format_value(_spec(patch, "filter.cutoff"), 12000.0) == "12.00kHz"
    assert sp.format_value(_spec(patch, "filter.cutoff"), 440.0) == "440Hz"
    assert sp.format_value(_spec(patch, "amp_env.attack"), 0.005) == "5ms"
    assert sp.format_value(_spec(patch, "amp_env.attack"), 2.5) == "2.500s"
    assert sp.format_value(_spec(patch, "osc1.waveform"), "saw") == "saw"


def test_format_value_signs_bipolar_fields_so_the_direction_is_visible(patch):
    env_amount = _spec(patch, "filter.env_amount")
    assert sp.format_value(env_amount, 0.5) == "+0.50"
    assert sp.format_value(env_amount, -0.5) == "-0.50"
    assert sp.format_value(env_amount, 0.0) == "0.00"
    assert sp.format_value(_spec(patch, "osc1.octave"), 1) == "+1"
    assert sp.format_value(_spec(patch, "osc1.octave"), -1) == "-1"


def test_note_name_uses_this_repos_flat_biased_fifths_spelling():
    assert sp.note_name(60) == "C4"
    assert sp.note_name(61) == "Db4"     # flat-biased, not C#
    assert sp.note_name(48) == "C3"
    assert sp.note_name(36) == "C2"
