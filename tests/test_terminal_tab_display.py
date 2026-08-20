import io
import re
import shutil
import sys

import pytest

import config
from duration_tracker import DEFAULT_DURATION_CLASS
from terminal_tab_display import (
    BARLINE_GLYPH,
    DOT_GLYPH,
    FLAG_GLYPHS,
    NOTEHEAD_GLYPH,
    STEM_GLYPH,
    TabDisplay,
    _aged_lightness,
    _column_note_rgb,
)


def _render(monkeypatch, rows, cols=80, pushes=(), notehead_style="symbol", legend_on=True, frozen=False):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (cols, rows))
    display = TabDisplay(fps=20)
    display._last_size = (cols, rows)  # skip the resize-clear noise for these probes
    for pitch_class, octave, label in pushes:
        display.push(pitch_class, octave, (200, 50, 50), label)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status", notehead_style=notehead_style, legend_on=legend_on, frozen=frozen)
    return buf.getvalue()


def test_small_terminal_never_addresses_a_row_past_its_height(monkeypatch):
    for rows in (30, 22, 21, 15, 10, 8):
        out = _render(monkeypatch, rows, pushes=[(i % 12, 4, "X") for i in range(20)])
        max_row = max(int(m) for m in re.findall(r"\033\[(\d+);1H", out))
        assert max_row <= rows


def test_note_outside_shrunk_range_is_dropped_not_misplaced(monkeypatch):
    # A 24-row terminal shrinks the staff enough that B5 (the top of the
    # app's range) falls outside it -- it must not render at all, since
    # drawing it clamped to the boundary row would show it at the wrong
    # staff position.
    out = _render(monkeypatch, rows=24, pushes=[(11, 5, "B5")])
    assert NOTEHEAD_GLYPH not in out


def test_note_inside_range_still_renders(monkeypatch):
    out = _render(monkeypatch, rows=30, pushes=[(9, 4, "A4")])
    assert NOTEHEAD_GLYPH in out  # A is natural -- glyph alone, no accidental marker


def test_legend_shows_clefs_and_staff_line_names(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100)
    assert "\U0001D11E" in out  # treble clef, on the G4 line
    assert "\U0001D122" in out  # bass clef, on the F3 line
    for letter in ("G", "B", "D", "A", "E", "F"):
        assert letter in out  # bare letters -- F3/G4 covered by the clef glyphs instead
    for name in ("G2", "B2", "D3", "A3", "E4", "B4", "D5", "F5"):
        assert name not in out  # octave digit dropped from the merged legend column (#20)


def test_legend_width_is_reserved_from_note_columns(monkeypatch):
    # A terminal exactly one note-column wide beyond the legend should still
    # fit that column onscreen, not have it silently swallowed by ignoring
    # the legend's width in the visible_cols calculation.
    import config
    cols = config.TAB_LEGEND_WIDTH + config.TAB_COLUMN_WIDTH
    out = _render(monkeypatch, rows=30, cols=cols, pushes=[(9, 4, "A4")])
    assert NOTEHEAD_GLYPH in out


def test_legend_off_reclaims_column_width(monkeypatch):
    # With the legend hidden, its width should no longer be subtracted from
    # the note-column budget -- mirrors test_legend_width_is_reserved_from_
    # note_columns above, but for legend_on=False (#19's stated intent for
    # the L toggle).
    import config
    cols = config.TAB_COLUMN_WIDTH  # no room to spare for a legend at all
    out = _render(monkeypatch, rows=30, cols=cols, pushes=[(9, 4, "A4")], legend_on=False)
    assert NOTEHEAD_GLYPH in out


def test_legend_off_hides_clef_glyphs(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100, legend_on=False)
    assert "\U0001D11E" not in out
    assert "\U0001D122" not in out


def test_legend_labels_space_rows_not_just_staff_lines(monkeypatch):
    # Issue #36 fix 1: every staff row gets a letter, not just the 5 line
    # rows per staff -- e.g. "C" only ever lands on a space row (rows 1,3,
    # 10, 13...), never a STAFF_LINE_ROWS row, so its presence in the
    # legend proves space rows are labeled too.
    out = _render(monkeypatch, rows=30, cols=100)
    assert "C" in out


def test_legend_clef_and_letter_render_in_separate_columns(monkeypatch):
    # Issue #36 fix 2: the clef glyph and the row letter must occupy their
    # own sub-columns (clef column, then letter column), not share a cell
    # the way the earlier merged single-region legend did -- so the
    # treble clef's own anchor row (G4, row 14) still carries a separate
    # "G" letter cell to its right, not the clef glyph standing in for it.
    import config

    out = _render(monkeypatch, rows=30, cols=100)
    # Each rendered line starts right after a "\033[K" erase-to-end-of-line
    # code; find the one carrying the treble clef glyph and check its
    # legend-width prefix carries a separate "G" letter cell too, not just
    # the clef glyph standing in for it.
    for line in out.split("\033[K"):
        if "\U0001D11E" in line:
            prefix = line[: config.TAB_LEGEND_WIDTH + 20]
            assert "\U0001D11E" in prefix
            assert "G" in prefix.replace("\U0001D11E", "")
            break
    else:
        raise AssertionError("treble clef glyph not found in any rendered line")


def test_name_style_shows_bare_letter_without_octave(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(1, 4, "Db4")], notehead_style="name")
    assert "Db" in out
    assert "Db4" not in out
    assert NOTEHEAD_GLYPH not in out


def test_symbol_style_shows_glyph_with_accidental_marker(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(1, 4, "Db4")], notehead_style="symbol")
    assert NOTEHEAD_GLYPH + "♭" in out  # flat marker adjacent to the glyph


# --- issue #22: per-column age-based dimming ---

def test_aged_lightness_at_age_zero_is_full_tab_note_lightness():
    assert _aged_lightness(0) == config.TAB_NOTE_LIGHTNESS


def test_aged_lightness_fades_linearly_between_endpoints():
    lo = _aged_lightness(config.FADE_COLUMNS)  # already at/past the floor
    mid = _aged_lightness(config.FADE_COLUMNS // 2)
    hi = _aged_lightness(0)
    assert lo == pytest.approx(config.DIM_LIGHTNESS)
    assert lo < mid < hi


def test_aged_lightness_holds_floor_beyond_fade_columns():
    at_edge = _aged_lightness(config.FADE_COLUMNS - 1)
    well_past = _aged_lightness(config.FADE_COLUMNS * 10)
    assert at_edge == pytest.approx(config.DIM_LIGHTNESS)
    assert well_past == pytest.approx(config.DIM_LIGHTNESS)


def test_column_note_rgb_only_lightness_moves_with_age():
    # Same pitch class at two different ages must keep the same hue/
    # saturation (same relative channel ratios) -- only overall lightness
    # (and thus every channel's magnitude together) should move.
    bright = _column_note_rgb(0, 0)
    dim = _column_note_rgb(0, config.FADE_COLUMNS)
    lum_bright = 0.299 * bright[0] + 0.587 * bright[1] + 0.114 * bright[2]
    lum_dim = 0.299 * dim[0] + 0.587 * dim[1] + 0.114 * dim[2]
    assert lum_bright > lum_dim


def _bg_luminances(out):
    """Every `\\033[48;2;r;g;bm` background color in render() output, in
    the order they appear (left to right, top to bottom) -- note cells are
    the only thing in this view's output wrapped in a background color
    code, so this picks out exactly the rendered note swatches."""
    rgbs = [tuple(int(v) for v in m) for m in re.findall(r"\033\[48;2;(\d+);(\d+);(\d+)m", out)]
    return [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in rgbs]


def test_older_column_renders_dimmer_than_newest(monkeypatch):
    # Two columns, same pitch class/octave so both land on the same screen
    # row -- the newest (second-pushed, rightmost) column should render
    # brighter than the older (first-pushed) one now that it's no longer
    # newest.
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(0, 4, "C4"), (0, 4, "C4")])
    luminances = _bg_luminances(out)
    assert len(luminances) == 2
    older, newest = luminances
    assert newest > older


def test_freeze_forces_all_visible_columns_to_full_brightness(monkeypatch):
    # Same two-column setup as above, but frozen=True -- issue #23 pins
    # every visible column's age to 0, so the older column should now
    # match the newest one exactly rather than reading dimmer.
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(0, 4, "C4"), (0, 4, "C4")], frozen=True)
    luminances = _bg_luminances(out)
    assert len(luminances) == 2
    assert luminances[0] == luminances[1]


# --- issue #55: rhythm notation (durations + barlines) ---

def _render_display(monkeypatch, rows, cols=80, setup=None, notehead_style="symbol", legend_on=True,
                     frozen=False, chord_mode=False):
    """Like `_render()` above, but takes an arbitrary `setup(display)`
    callback instead of a flat list of monophonic pushes -- needed for
    tests that call push_notes()/push_barline()/finalize_duration()
    directly. Returns (rendered_output, display) so a test can also
    inspect display state after rendering."""
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (cols, rows))
    display = TabDisplay(fps=20)
    display._last_size = (cols, rows)
    if setup is not None:
        setup(display)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status", chord_mode=chord_mode, notehead_style=notehead_style, legend_on=legend_on,
                    frozen=frozen)
    return buf.getvalue(), display


def _fg_luminances(out):
    """Every `\\033[38;2;r;g;bm` foreground color in render() output, in
    the order they appear -- barline cells are foreground-only (see
    `_barline_cell`), so isolate them by rendering nothing but barlines."""
    rgbs = [tuple(int(v) for v in m) for m in re.findall(r"\033\[38;2;(\d+);(\d+);(\d+)m", out)]
    return [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in rgbs]


def test_finalize_duration_sets_matching_note_and_noops_for_unknown_key(monkeypatch):
    display = TabDisplay(fps=20)
    display.push(0, 4, (200, 50, 50), "C4")
    display.finalize_duration(0, 4, "eighth")
    assert display.entries[-1].notes[0]["duration_class"] == "eighth"

    # Unknown (pitch_class, octave) key -- silent no-op, must not raise.
    display.finalize_duration(5, 5, "quarter")


def test_rapid_reattack_supersedes_older_open_note(monkeypatch):
    display = TabDisplay(fps=20)
    display.push(0, 4, (200, 50, 50), "C4-old")
    old_note = display.entries[-1].notes[0]
    display.push(0, 4, (200, 50, 50), "C4-new")  # same key, re-attack
    new_note = display.entries[-1].notes[0]

    display.finalize_duration(0, 4, "half")

    assert new_note["duration_class"] == "half"
    assert old_note["duration_class"] is None  # abandoned, never finalized


def test_push_barline_renders_glyph_spanning_staff_at_barline_width(monkeypatch):
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4")
        display.push_barline()

    out, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup)
    cells = re.findall(r"\033\[38;2;\d+;\d+;\d+m(.*?)\033\[0m", out)
    barline_cells = [c for c in cells if BARLINE_GLYPH in c]
    assert len(barline_cells) > 1  # spans multiple staff rows, not just one
    for cell in barline_cells:
        assert len(cell) == config.TAB_BARLINE_WIDTH


def test_push_barline_does_not_crash_render_in_either_chord_mode(monkeypatch):
    for chord_mode in (False, True):
        def setup(display, chord_mode=chord_mode):
            if chord_mode:
                display.push_notes([(0, 4, (200, 50, 50), "C4")], "C")
            else:
                display.push(0, 4, (200, 50, 50), "C4")
            display.push_barline()

        out, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup, chord_mode=chord_mode)
        assert BARLINE_GLYPH in out


def test_older_barline_renders_dimmer_than_newest(monkeypatch):
    def setup(display):
        display.push_barline()
        display.push_barline()

    out, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup)
    luminances = _fg_luminances(out)
    assert len(luminances) >= 2 and len(luminances) % 2 == 0
    older_vals = luminances[0::2]
    newest_vals = luminances[1::2]
    assert max(older_vals) == min(older_vals)  # constant color per column, across rows
    assert max(newest_vals) == min(newest_vals)
    assert newest_vals[0] > older_vals[0]


def _setup_single_chord_note(pitch_class=0, octave=4, duration_class=None):
    def setup(display):
        display.push_notes([(pitch_class, octave, (200, 50, 50), "C4")], "Cmaj")
        if duration_class is not None:
            display.finalize_duration(pitch_class, octave, duration_class)
    return setup


def test_symbol_style_eighth_note_renders_stem_and_flag(monkeypatch):
    out, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="symbol",
        setup=_setup_single_chord_note(duration_class="eighth"),
    )
    assert STEM_GLYPH in out
    assert FLAG_GLYPHS["eighth"] in out


def test_symbol_style_whole_note_renders_no_stem(monkeypatch):
    out, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="symbol",
        setup=_setup_single_chord_note(duration_class="whole"),
    )
    assert STEM_GLYPH not in out


def test_symbol_style_dotted_quarter_renders_dot(monkeypatch):
    out, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="symbol",
        setup=_setup_single_chord_note(duration_class="dotted-quarter"),
    )
    assert DOT_GLYPH in out


def test_name_style_eighth_note_renders_duration_suffix(monkeypatch):
    out, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="name",
        setup=_setup_single_chord_note(duration_class="eighth"),
    )
    assert "·8th" in out  # "\xb7" == middle dot


def test_unfinalized_note_renders_like_default_duration_class(monkeypatch):
    out_unfinalized, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="symbol",
        setup=_setup_single_chord_note(duration_class=None),
    )
    out_default, _ = _render_display(
        monkeypatch, rows=30, cols=100, chord_mode=True, notehead_style="symbol",
        setup=_setup_single_chord_note(duration_class=DEFAULT_DURATION_CLASS),
    )
    assert out_unfinalized == out_default
