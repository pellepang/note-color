import io
import re
import shutil
import sys

from terminal_tab_display import NOTEHEAD_GLYPH, TabDisplay


def _render(monkeypatch, rows, cols=80, pushes=(), notehead_style="symbol", legend_on=True):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (cols, rows))
    display = TabDisplay(fps=20)
    display._last_size = (cols, rows)  # skip the resize-clear noise for these probes
    for pitch_class, octave, label in pushes:
        display.push(pitch_class, octave, (200, 50, 50), label)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status", notehead_style=notehead_style, legend_on=legend_on)
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


def test_name_style_shows_bare_letter_without_octave(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(1, 4, "Db4")], notehead_style="name")
    assert "Db" in out
    assert "Db4" not in out
    assert NOTEHEAD_GLYPH not in out


def test_symbol_style_shows_glyph_with_accidental_marker(monkeypatch):
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(1, 4, "Db4")], notehead_style="symbol")
    assert NOTEHEAD_GLYPH + "♭" in out  # flat marker adjacent to the glyph
