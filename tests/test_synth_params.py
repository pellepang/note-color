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

from notecolor.settings import config
from notecolor.settings import patch_format
from notecolor.tui import synth_params as sp


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


# --- the log floor is a lift-off rung, not a shared lower bound (#238) -----
#
# Decision 74. `SYNTH_PARAM_LOG_FLOOR` exists because a ratio step cannot
# lift a value off zero, so a spec whose minimum *is* zero needs somewhere
# to land on the first press. Reading it as a shared lower bound instead
# amputated any spec that legitimately goes below a millisecond -- the
# Short Delay's Time, whose sub-millisecond end is the comb a flanger is
# made of, was reachable only as a cliff: one press down from 1ms landed on
# the spec minimum, one press back up returned to 1ms, and the sweep
# between them could not be found by hand.

#: How many ordinary presses any log knob may take to cross its whole
#: range. The floor keeps a knob from being so coarse that the value a
#: user wants falls between two presses; the ceiling is really a statement
#: about `Shift` -- at ten ordinary presses per coarse one
#: (`SYNTH_PARAM_COARSE_STEPS`), 130 means no log knob is ever more than
#: thirteen coarse presses from end to end. The filter cutoff sits near
#: that ceiling on purpose: its ratio is a semitone, chosen for musical
#: evenness rather than for press count (decision #107 point 5).
MIN_PRESSES_ACROSS_RANGE = 20
MAX_PRESSES_ACROSS_RANGE = 130


def _gui_panel_specs():
    """Every `ParamSpec` the Synth View's knob panels build from. They live
    in `gui/synth_view.py` rather than here because they describe engine
    modules with no `Patch` section, but they are stepped by this module's
    `step_value()`, so this file's invariants have to cover them too."""
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from notecolor.gui import synth_view

    tables = list(synth_view.EFFECT_PARAM_SPECS.values())
    tables += list(synth_view.UTILITY_PARAM_SPECS.values())
    return [spec for table in tables for spec in table]


def _all_log_specs(patch):
    specs = list(sp.specs_for(patch)) + _gui_panel_specs()
    return [s for s in specs if s.scale == sp.SCALE_LOG]


def _sweep(spec, direction):
    """Steps `spec` from one end of its range to the other, returning the
    values passed through. Stops if a press stops moving the value."""
    value = float(spec.low) if direction > 0 else float(spec.high)
    seen = [value]
    for _ in range(10_000):
        nxt = sp.step_value(spec, value, direction)
        if (nxt <= value) if direction > 0 else (nxt >= value):
            break
        value = nxt
        seen.append(value)
    return seen


def test_the_log_floor_is_the_specs_own_minimum_when_that_is_positive(patch):
    # The constant only speaks for specs that start at zero. A spec whose
    # minimum is already positive needs no rung to jump to -- multiplying
    # its own minimum works perfectly well -- so its floor is that minimum,
    # however far below the constant it sits.
    zero_low = _spec(patch, "amp_env.attack")
    assert zero_low.low == 0.0
    assert sp.log_floor(zero_low) == pytest.approx(config.SYNTH_PARAM_LOG_FLOOR)

    positive_low = _spec(patch, "filter.cutoff")
    assert sp.log_floor(positive_low) == pytest.approx(positive_low.low)

    below_the_constant = sp.ParamSpec(
        "params", "time", "Time", sp.KIND_FLOAT, 0.0001, 0.05, 1.3, sp.SCALE_LOG)
    assert below_the_constant.low < config.SYNTH_PARAM_LOG_FLOOR
    assert sp.log_floor(below_the_constant) == pytest.approx(0.0001)


def test_the_short_delays_comb_range_steps_smoothly_in_both_directions():
    # #238's actual complaint: 0.1ms to 1ms is where the Short Delay is a
    # comb rather than a delay, and it has to be turnable by hand.
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from notecolor.gui import synth_view

    spec = next(s for s in synth_view.UTILITY_PARAM_SPECS["short_delay"]
                if s.attr == "time")
    assert spec.low == pytest.approx(0.0001)

    # Down from a millisecond: every press a real ratio step, none of them
    # the old cliff onto the spec minimum.
    value = 0.001
    comb = []
    for _ in range(6):
        nxt = sp.step_value(spec, value, -1)
        assert nxt == pytest.approx(value / spec.step), \
            "a press below 1ms must be a ratio step, not a jump to the minimum"
        value = nxt
        comb.append(value)
    assert all(spec.low < v < 0.001 for v in comb)
    assert len(set(comb)) == len(comb)

    # And back up again lands where it started, rather than snapping to 1ms.
    for _ in range(6):
        value = sp.step_value(spec, value, 1)
    assert value == pytest.approx(0.001)

    # The whole sub-millisecond end is reachable, not just its endpoints.
    down = _sweep(spec, -1)
    assert sum(1 for v in down if v < 0.001) >= 8


def test_every_log_spec_crosses_its_range_in_a_hand_turnable_number_of_presses(patch):
    # The generic invariant, so a spec added later cannot quietly become
    # either a knob with three usable positions or one that takes four
    # hundred presses to cross.
    for spec in _all_log_specs(patch):
        for direction in (1, -1):
            seen = _sweep(spec, direction)
            presses = len(seen) - 1
            assert MIN_PRESSES_ACROSS_RANGE <= presses <= MAX_PRESSES_ACROSS_RANGE, \
                f"{spec.path} takes {presses} presses going {direction:+d}"
            assert seen[-1] == pytest.approx(
                spec.high if direction > 0 else spec.low), \
                f"{spec.path} does not reach its end going {direction:+d}"


def test_no_log_spec_jumps_more_than_one_ratio_step_inside_its_range(patch):
    # The floor is allowed exactly one discontinuity -- the lift-off off
    # zero, and the landing back onto it -- and only for a spec whose
    # minimum is zero. Anywhere else a press must be a clean multiply, so
    # the sweep a user turns by hand has no cliff in it.
    for spec in _all_log_specs(patch):
        floor = sp.log_floor(spec)
        for direction in (1, -1):
            values = _sweep(spec, direction)
            for before, after in zip(values, values[1:]):
                if before <= floor or after <= floor:
                    continue          # the lift-off rung, if this spec has one
                if after == pytest.approx(spec.high) or after == pytest.approx(spec.low):
                    continue          # the clamp at the far end
                assert after == pytest.approx(
                    before * (spec.step ** direction)), \
                    f"{spec.path} jumps from {before} to {after}"


def test_the_gui_knob_hand_uses_the_same_floor_the_presses_do():
    # Rotation and stepping read one rule (`log_floor()`), so a knob that
    # can be stepped into the comb range also paints it, instead of pinning
    # every sub-millisecond value to the hard left.
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from notecolor.gui import synth_view

    spec = next(s for s in synth_view.UTILITY_PARAM_SPECS["short_delay"]
                if s.attr == "time")
    angles = [synth_view._rotation_for(spec, v)
              for v in (0.0001, 0.0002, 0.0004, 0.0008, 0.001)]
    assert angles == sorted(angles)
    assert len(set(angles)) == len(angles), \
        "the sub-millisecond sweep must move the hand, not pin it at -135"
    assert angles[0] == pytest.approx(-135.0)
