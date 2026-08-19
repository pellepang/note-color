import io
import re
import shutil
import sys

import pytest

import config
from terminal_tab_display import NOTEHEAD_GLYPH, TabDisplay, _aged_lightness, _column_note_rgb


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
