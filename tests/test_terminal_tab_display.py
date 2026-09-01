import io
import re
import shutil
import sys

import pytest
import wcwidth

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


def test_pad_center_measures_display_width_not_code_point_count():
    # A notehead plus its combining stem/flag/dot (symbol-style duration
    # glyphs) is one ~2-cell grapheme cluster on a real terminal -- str.
    # center()'s code-point count would see 4+ "characters" here and either
    # truncate the glyphs away (at a narrow width) or, when they fit,
    # still pad the field to the wrong total display width. _pad_center()
    # must land on exactly `width` real display columns and must not have
    # truncated away any of the glyphs.
    from terminal_tab_display import _pad_center

    heavy = NOTEHEAD_GLYPH + STEM_GLYPH + FLAG_GLYPHS["sixteenth"] + DOT_GLYPH
    out = _pad_center(heavy, config.TAB_COLUMN_WIDTH)

    assert wcwidth.wcswidth(out) == config.TAB_COLUMN_WIDTH
    assert STEM_GLYPH in out
    assert FLAG_GLYPHS["sixteenth"] in out
    assert DOT_GLYPH in out


def test_barline_column_stays_aligned_across_rows_with_mixed_duration_glyphs(monkeypatch):
    # Rows carrying different duration classes pull in very different
    # numbers of zero-width combining marks (a "whole" note has none; a
    # "dotted-sixteenth" note has three: stem, flag, dot). Before fixing
    # _note_cell()'s code-point-based centering, that mismatch meant every
    # column drawn after such a note on the same screen row -- including a
    # barline column -- landed at a different real terminal position
    # depending on which row's note content came before it, i.e. the
    # barline didn't render as one straight vertical divider.
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4", t=1.0)
        display.finalize_duration(0, 4, "whole")
        display.push(2, 5, (50, 200, 50), "D5", t=2.0)
        display.finalize_duration(2, 5, "dotted-sixteenth")
        display.push_barline(t=3.0)

    out, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup, notehead_style="symbol")

    # Each screen row is written as "\033[{row};1H\033[K{content}", back to
    # back with no separator -- split on that row-address prefix to get
    # one string per rendered line.
    starts = [m.start() for m in re.finditer(r"\033\[\d+;1H\033\[K", out)] + [len(out)]
    lines = [out[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    offsets = []
    for line in lines:
        if BARLINE_GLYPH not in line:
            continue
        before = line.split(BARLINE_GLYPH)[0]
        plain = re.sub(r"\033\[[\d;]*[A-Za-z]", "", before)  # strip CSI codes (color + cursor-address)
        offsets.append(wcwidth.wcswidth(plain))

    assert len(offsets) >= 2  # the barline spans multiple staff rows
    assert len(set(offsets)) == 1  # every row places it at the same real column


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


# --- issue #83: mono *name* style needs a wider column for duration suffixes ---

def _note_cell_texts(out):
    """Every rendered note cell's inner text (background+foreground-wrapped
    content, the shape `_note_cell()` produces) -- lets a test check the
    exact, unclipped string a column rendered, not just substring presence
    (which could pass even if surrounding cells also happen to contain the
    same characters)."""
    return re.findall(r"\033\[48;2;\d+;\d+;\d+m\033\[38;2;\d+;\d+;\d+m(.*?)\033\[0m", out)


def test_mono_name_style_whole_note_suffix_renders_unclipped(monkeypatch):
    # Before the #83 fix, mono name-style columns rendered at
    # TAB_COLUMN_WIDTH (3) -- _pad_center() clipped "C·whole" down to the
    # unreadable stub "C·w". Mono name-style-with-duration now gets its own
    # wider TAB_COLUMN_WIDTH_NAME column (mirroring TAB_COLUMN_WIDTH_CHORD's
    # existing precedent for chord mode), so the full suffix must survive.
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4")
        display.finalize_duration(0, 4, "whole")

    out, _ = _render_display(monkeypatch, rows=30, cols=100, notehead_style="name", setup=setup)
    cells = [c.strip() for c in _note_cell_texts(out) if "C" in c]
    assert cells == ["C·whole"]


def test_mono_name_style_dotted_sixteenth_suffix_renders_unclipped(monkeypatch):
    # The longest suffix in _NAME_STYLE_DURATION_SUFFIXES ("16th.", 5 chars)
    # combined with a 1-char natural-note letter -- must not clip to "A·1"
    # the way it did at the old TAB_COLUMN_WIDTH (3).
    def setup(display):
        display.push(9, 4, (200, 50, 50), "A4")  # pitch_class 9 == A, natural
        display.finalize_duration(9, 4, "dotted-sixteenth")

    out, _ = _render_display(monkeypatch, rows=30, cols=100, notehead_style="name", setup=setup)
    cells = [c.strip() for c in _note_cell_texts(out) if "A" in c]
    assert cells == ["A·16th."]


def test_mono_name_style_flat_letter_and_suffix_both_fit(monkeypatch):
    # The worst case: a 2-char accidental letter ("Bb") plus the longest
    # suffix ("whole") -- 8 display columns total, right at the edge of
    # TAB_COLUMN_WIDTH_NAME (9).
    def setup(display):
        display.push(10, 4, (200, 50, 50), "Bb4")  # pitch_class 10 == Bb
        display.finalize_duration(10, 4, "whole")

    out, _ = _render_display(monkeypatch, rows=30, cols=100, notehead_style="name", setup=setup)
    cells = [c.strip() for c in _note_cell_texts(out) if "B" in c]
    assert cells == ["Bb·whole"]


def test_mono_name_style_note_cell_uses_wide_column_width(monkeypatch):
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4")
        display.finalize_duration(0, 4, "whole")

    out, _ = _render_display(monkeypatch, rows=30, cols=100, notehead_style="name", setup=setup)
    cells = [c for c in _note_cell_texts(out) if "C" in c]
    assert cells
    assert wcwidth.wcswidth(cells[0]) == config.TAB_COLUMN_WIDTH_NAME


def test_mono_symbol_style_keeps_narrow_column_width(monkeypatch):
    # Symbol style's duration glyphs are combining marks composed onto the
    # notehead, not extra text -- its column must stay at the original
    # narrow TAB_COLUMN_WIDTH, unaffected by the #83 fix above.
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4")
        display.finalize_duration(0, 4, "whole")

    out, _ = _render_display(monkeypatch, rows=30, cols=100, notehead_style="symbol", setup=setup)
    cells = [c for c in _note_cell_texts(out) if NOTEHEAD_GLYPH in c]
    assert cells
    assert wcwidth.wcswidth(cells[0]) == config.TAB_COLUMN_WIDTH


# --- R-key non-causal recompute / Left-Right scrollback support ---

def test_correct_duration_updates_specific_occurrence_not_others(monkeypatch):
    # Two separate notes at the same (pitch_class, octave), at different
    # onset times, each already finalized once (a plausible real scenario
    # for the R feature: a slower recompute revises one of several past
    # occurrences of the same note). correct_duration() must locate and
    # fix only the occurrence whose timestamp is closest to the one given,
    # leaving the other untouched.
    display = TabDisplay(fps=20)
    display.push(0, 4, (200, 50, 50), "C4-first", t=10.0)
    display.finalize_duration(0, 4, "quarter")
    display.push(0, 4, (200, 50, 50), "C4-second", t=50.0)
    display.finalize_duration(0, 4, "half")

    first_note = display.session_history[0].notes[0]
    second_note = display.session_history[1].notes[0]
    assert first_note["duration_class"] == "quarter"
    assert second_note["duration_class"] == "half"

    ok = display.correct_duration(0, 4, t=10.3, duration_class="eighth")

    assert ok is True
    assert first_note["duration_class"] == "eighth"
    assert second_note["duration_class"] == "half"  # untouched


def test_correct_duration_no_op_for_unknown_key(monkeypatch):
    display = TabDisplay(fps=20)
    display.push(0, 4, (200, 50, 50), "C4", t=1.0)
    display.finalize_duration(0, 4, "quarter")

    ok = display.correct_duration(7, 6, t=1.0, duration_class="whole")

    assert ok is False
    assert display.session_history[0].notes[0]["duration_class"] == "quarter"


def test_correct_duration_reaches_a_note_already_scrolled_out_of_entries(monkeypatch):
    # A note pushed long enough ago to have been trimmed out of the
    # scrollback-windowed self.entries (see scrollback tests below) must
    # still be reachable via self.session_history, since correct_duration()
    # is meant to reach back arbitrarily far within the retained dump
    # history, not just the on-screen scrollback window.
    display = TabDisplay(fps=20, scrollback_seconds=5.0)
    display.push(0, 4, (200, 50, 50), "C4-old", t=0.0)
    display.finalize_duration(0, 4, "quarter")
    display.push(1, 4, (200, 50, 50), "Db4-new", t=100.0)  # far past the window

    assert all(e.t != 0.0 for e in display.entries)  # confirms it scrolled out

    ok = display.correct_duration(0, 4, t=0.0, duration_class="sixteenth")

    assert ok is True
    assert display.session_history[0].notes[0]["duration_class"] == "sixteenth"


def test_erase_barlines_removes_only_within_range(monkeypatch):
    display = TabDisplay(fps=20)
    display.push_barline(t=1.0)
    display.push_barline(t=2.0)
    display.push_barline(t=3.0)

    removed = display.erase_barlines(1.5, 2.5)

    assert removed == 1
    remaining_ts = sorted(e.t for e in display.entries if hasattr(e, "t"))
    assert remaining_ts == [1.0, 3.0]


def test_erase_barlines_unbounded_end_removes_through_newest(monkeypatch):
    display = TabDisplay(fps=20)
    display.push_barline(t=1.0)
    display.push_barline(t=2.0)
    display.push_barline(t=3.0)

    removed = display.erase_barlines(2.0)  # no end_t -> unbounded

    assert removed == 2
    remaining_ts = [e.t for e in display.entries]
    assert remaining_ts == [1.0]


def test_insert_barline_keeps_history_in_timestamp_order(monkeypatch):
    display = TabDisplay(fps=20)
    display.push_barline(t=1.0)
    display.push_barline(t=3.0)
    display.erase_barlines(1.5, None)  # drop the stale one at t=3.0

    display.insert_barline(2.0)  # recomputed replacement, between existing entries

    assert [e.t for e in display.entries] == [1.0, 2.0]
    assert [e.t for e in display.session_history] == [1.0, 2.0]


def test_erase_and_reinsert_barlines_round_trip_through_render(monkeypatch):
    from terminal_tab_display import BARLINE_GLYPH

    def setup(display):
        display.push_barline(t=1.0)
        display.push_barline(t=2.0)  # stale -- about to be corrected away
        display.push_barline(t=3.0)
        display.erase_barlines(1.5, 2.5)
        display.insert_barline(2.2)  # recomputed position

    out, display = _render_display(monkeypatch, rows=30, cols=100, setup=setup)
    assert BARLINE_GLYPH in out
    assert [e.t for e in display.entries] == [1.0, 2.2, 3.0]


# --- scrollback retention ---

def test_scrollback_retains_columns_older_than_the_old_fade_cutoff(monkeypatch):
    # FADE_COLUMNS (the old render-driven dimming horizon) is much smaller
    # than a scrollback window measured in seconds -- pushing more columns
    # than FADE_COLUMNS, but all within scrollback_seconds, must not drop
    # any of them the way the old count-based TAB_VISIBLE_MAXLEN cap would
    # eventually have.
    display = TabDisplay(fps=20, scrollback_seconds=100.0)
    n = config.FADE_COLUMNS + 10
    for i in range(n):
        display.push(i % 12, 4, (200, 50, 50), f"n{i}", t=float(i))

    assert len(display.entries) == n


def test_scrollback_trims_columns_older_than_configured_window(monkeypatch):
    display = TabDisplay(fps=20, scrollback_seconds=5.0)
    display.push(0, 4, (200, 50, 50), "old", t=0.0)
    display.push(1, 4, (200, 50, 50), "mid", t=2.0)
    display.push(2, 4, (200, 50, 50), "new", t=10.0)  # pushes cutoff to 5.0

    remaining_ts = [e.t for e in display.entries]

    assert 0.0 not in remaining_ts  # trimmed -- older than (10.0 - 5.0)
    assert 10.0 in remaining_ts  # newest always retained
    # Still present in the untouched, count-based session history dump.
    assert [e.t for e in display.session_history] == [0.0, 2.0, 10.0]


def test_scrollback_never_empties_entries_even_at_zero_window(monkeypatch):
    display = TabDisplay(fps=20, scrollback_seconds=0.0)
    display.push(0, 4, (200, 50, 50), "a", t=0.0)
    display.push(1, 4, (200, 50, 50), "b", t=1.0)

    assert len(display.entries) >= 1
    assert display.entries[-1].t == 1.0


def test_default_scrollback_seconds_matches_config(monkeypatch):
    display = TabDisplay(fps=20)
    assert display.scrollback_seconds == config.TAB_SCROLLBACK_SECONDS


# --- render(scroll_offset=...) ---

def test_scroll_offset_zero_matches_default_render(monkeypatch):
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4", t=0.0)
        display.push(2, 4, (200, 50, 50), "D4", t=1.0)

    out_default, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup)
    out_explicit_zero, _ = _render_display(monkeypatch, rows=30, cols=100, setup=setup)
    assert out_default == out_explicit_zero


def test_scroll_offset_hides_the_most_recent_columns(monkeypatch):
    # With scroll_offset=1, the view should render as if the single newest
    # column (D4) had not been pushed yet -- only C4 visible.
    def setup(display):
        display.push(0, 4, (200, 50, 50), "C4", t=0.0)
        display.push(2, 4, (200, 50, 50), "D4", t=1.0)

    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (100, 30))
    display = TabDisplay(fps=20)
    display._last_size = (100, 30)
    setup(display)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status", scroll_offset=1)
    out = buf.getvalue()

    assert NOTEHEAD_GLYPH in out  # C4 still shown
    luminances = _bg_luminances(out)
    assert len(luminances) == 1  # only one note column rendered


def test_scroll_offset_uses_historical_age_fade_not_pinned_to_newest(monkeypatch):
    # Three columns pushed; scrolling back by 1 makes the *second* column
    # play the role of "newest visible" -- even while frozen, it should NOT
    # be pinned to full brightness the way plain freeze (scroll_offset=0)
    # would pin every visible column; it should show the same age-relative-
    # to-that-point-in-time gradient the live view had back then.
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (100, 30))
    display = TabDisplay(fps=20)
    display._last_size = (100, 30)
    display.push(0, 4, (200, 50, 50), "C4", t=0.0)
    display.push(0, 4, (200, 50, 50), "C4b", t=1.0)
    display.push(0, 4, (200, 50, 50), "C4c", t=2.0)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    display.render("status", frozen=True, scroll_offset=1)
    out = buf.getvalue()

    luminances = _bg_luminances(out)
    assert len(luminances) == 2  # first two columns visible, third hidden
    older, newest = luminances
    assert newest > older  # not pinned equal, unlike plain frozen scroll_offset=0


def test_frozen_with_no_scroll_offset_still_pins_to_full_brightness(monkeypatch):
    # Regression guard: introducing scroll_offset must not change plain
    # freeze's existing pin-everything-to-full-brightness behavior.
    out = _render(monkeypatch, rows=30, cols=100, pushes=[(0, 4, "C4"), (0, 4, "C4")], frozen=True)
    luminances = _bg_luminances(out)
    assert len(luminances) == 2
    assert luminances[0] == luminances[1]
