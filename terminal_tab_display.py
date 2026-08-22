"""Scrolling note-history view for the terminal: notes enter on the right
and scroll left over time, each placed at its correct vertical position on
a grand staff (bass + treble, see staff_map.py) and colored per the active
color scheme. Two callers decide *when* a new column is pushed (see
main.py's run_terminal_tab: 'fix' on a timer, 'onset' on a new note-attack)
-- this module only owns layout/rendering and the session history used for
the on-quit ANSI dump.

Two live-togglable notehead render styles (issue #21's finalized decision,
`N` keybind in main.py): *symbol* (an open notehead glyph, U+1D157, plus a
real Unicode flat/sharp marker if needed -- no letter or octave text at
all) and *name* (bare letter + ASCII accidental, e.g. "A", "Bb", "F#" --
no octave digit, since the staff row already conveys octave). Both use
`color_map.NOTE_NAMES_FIFTHS` for spelling, matching every other flat-
biased label in this app. `TabEntry.notes` still stores each note's
pitch_class/octave (for `_cell_text()` to render fresh against whichever
style is currently selected -- so toggling `N` live restyles history
that's still on screen, not just future pushes) alongside its precomputed
letter+octave `label`, which exists purely for `dump_ansi()`'s on-quit
text dump (unaffected by the `N`/`L` toggles, per the map's standing
decision) and is never used by `render()`.

Two further live-togglable behaviors (issues #22/#23, `main.py`'s render-
thread-local state, no `TabDisplay`-side toggle methods -- same pattern as
`notehead_style`/`legend_on`): per-column age-based dimming, and a `Space`
freeze-frame. Dimming (#22): the newest visible column renders at
`config.TAB_NOTE_LIGHTNESS` (tab's normal, always-used note lightness);
every older column's lightness fades linearly down to `config.
DIM_LIGHTNESS` (the same floor `terminal_wheel_display.py` uses for its
own inactive wedges -- promoted to `config.py` so the two can't drift)
over `config.FADE_COLUMNS` columns of age, held at that floor beyond it.
Age is each column's distance from the newest *visible* column (0 =
newest), recomputed fresh every `render()` call from `pitch_class` (see
`_column_note_rgb()`) rather than baked in at push time -- a column's age
changes every frame as newer columns scroll in, so its color can't be
fixed when pushed. `TabEntry.notes`' precomputed `rgb` field is therefore
only ever read by `dump_ansi()` now, same as `label`. Freeze-frame (#23,
`render(..., frozen=True)`): forces every visible column's age to 0,
overriding the fade entirely, while `main.py` stops pushing new columns --
`render()` itself doesn't know or care that no new columns are arriving,
it just keeps redrawing the same history with age pinned at 0.

Rhythm/onset/duration/tempo (issue #55) added two things to this module:

- Each note in `TabEntry.notes` is now a mutable dict (not a 4-tuple) with
  a `duration_class` field that starts `None` and is filled in later, once
  `DurationTracker` (elsewhere) measures how long the note actually
  sounded -- which is necessarily *after* the column carrying it was
  already pushed (and quite possibly already on screen). `TabDisplay.
  finalize_duration()` mutates that dict in place via a side index,
  `self._open_notes`, so both `self.entries` and `self.session_history`
  (which hold the very same dict objects, not copies) see the update with
  no need to touch each container separately. A note whose duration never
  finalizes (still sounding at quit, or superseded by a same-key
  re-attack before it finalized) simply keeps `duration_class=None`
  forever; rendering treats that the same as `duration_tracker.
  DEFAULT_DURATION_CLASS` ("quarter") -- resolved at render time, not by
  ever writing that fallback into the dict, so the distinction between
  "genuinely still open" and "measured as a quarter note" survives for
  any future consumer that cares (e.g. `dump_ansi()`).
- Barlines are a second, distinct column type (`BarlineEntry`, pushed via
  `push_barline()`) mixed into the same `entries`/`session_history`
  history as `TabEntry` columns -- not a variant of `TabEntry`, since a
  barline has no notes and no staff-row placement, just a glyph spanning
  the full staff height at a narrower fixed column width
  (`config.TAB_BARLINE_WIDTH`). Barline columns age/dim exactly like note
  columns (same age computation, same `_aged_lightness()` curve) but with
  no hue, via `_barline_rgb()`.

Three more additions support the (separately built) `R`-while-frozen
non-causal rhythm re-analysis and Left/Right-arrow scrollback features --
this module owns only the `TabDisplay`-side data/render capability those
features call into, not the recompute or keybind wiring itself:

- `self.entries` retains history by *timestamp* window
  (`self.scrollback_seconds`, defaulting to `config.TAB_SCROLLBACK_SECONDS`,
  overridable via the constructor's `scrollback_seconds=` -- see
  `_trim_entries()`) rather than the old fixed column count
  (`TAB_VISIBLE_MAXLEN`, since removed) -- columns arrive at irregular,
  onset-driven intervals, so only a time window gives scrollback a
  consistent, predictable reach regardless of how busy the playing was.
  `self.session_history` is unaffected -- it keeps its own separate,
  much longer, count-based cap for the on-quit dump, same as before.
- `render(..., scroll_offset=N)` renders exactly what the live view
  looked like `N` history entries (note and barline columns alike, not
  raw terminal character columns) ago, including that historical
  window's own age-fade gradient from that point in time -- not the
  current live age-fade, and not freeze's usual "pin everything to full
  brightness" override either (that override only applies at
  `scroll_offset=0`; a scrolled-back-to column is expected to show
  whatever gradient it actually had back then, per the module's
  Left/Right-arrow design).
- `correct_duration()`/`erase_barlines()`/`insert_barline()` let an
  external recompute overwrite results already committed to history:
  `correct_duration()` retroactively fixes one specific already-finalized
  note's `duration_class` (disambiguated from repeated notes at the same
  key by closest column timestamp, since `finalize_duration()` can only
  ever reach the currently *open* note at a key); `erase_barlines()` +
  `insert_barline()` replace a stale barline set within a time range with
  recomputed ones, the latter inserting in sorted position rather than
  just appending, since a correction's timestamp isn't guaranteed to be
  the newest.
"""

import bisect
import shutil
import sys
import time
from collections import deque, namedtuple

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from duration_tracker import DEFAULT_DURATION_CLASS
from staff_map import (
    staff_row, ledger_rows, row_note_name, STAFF_LINE_ROWS, TOP_ROW, BOTTOM_ROW,
    BASS_CLEF_ROW, TREBLE_CLEF_ROW,
)

TabEntry = namedtuple("TabEntry", "notes chord_name t")
# `notes` is a list of mutable per-note dicts -- one entry in monophonic
# mode, up to CHORD_MAX_NOTES in chord mode -- each shaped:
#   {"pitch_class":, "octave":, "rgb":, "label":, "duration_class": None}
# `duration_class` starts None and is filled in later by
# `TabDisplay.finalize_duration()`, once the note's actual sounding
# duration has been measured (see the module docstring).

BarlineEntry = namedtuple("BarlineEntry", "t")
# A barline column: no notes, no staff-row placement, just the barline
# glyph spanning the visible staff height at a fixed, narrow column width.

LEDGER_CHAR = "─"
BASS_CLEF_GLYPH = "𝄢"
TREBLE_CLEF_GLYPH = "𝄞"

NOTEHEAD_GLYPH = "\U0001D157"  # MUSICAL SYMBOL VOID NOTEHEAD -- #15's winner
# Real Unicode accidental signs for *symbol* style, keyed by the ASCII
# suffix NOTE_NAMES_FIFTHS already uses -- #21's finalized decision (an
# earlier ASCII "b"/"#" stand-in was replaced with these after live
# reaction; the East_Asian_Width=Ambiguous risk #14 flagged for both is
# expected/accepted, not a reason to avoid them).
SYMBOL_ACCIDENTALS = {"b": "♭", "#": "♯"}

BARLINE_GLYPH = "\U0001D100"       # MUSICAL SYMBOL SINGLE BARLINE

# Duration glyphs for *symbol* style (issue #55/#49's surveyed codepoints,
# standard Western-notation "N flags = 2^-(N+2) of a whole note"
# convention). Composed onto the notehead+accidental text in `_cell_text()`
# as: notehead+accidental, then a stem (every duration except "whole"),
# then a flag glyph if the duration has one, then a dot if dotted.
STEM_GLYPH = "\U0001D165"          # MUSICAL SYMBOL COMBINING STEM
# One codepoint per flag *count* (not one codepoint concatenated per flag) --
# U+1D16E is "combining flag-1" (one flag, i.e. an eighth note's single
# flag), U+1D16F is "combining flag-2" (two flags, sixteenth), U+1D170 is
# "combining flag-3" (three flags, thirtysecond-note) -- issue #49's
# surveyed codepoints, standard Western-notation "N flags = 2^-(N+2) of a
# whole note" convention.
FLAG_GLYPHS = {
    "eighth": "\U0001D16E",
    "dotted-eighth": "\U0001D16E",
    "sixteenth": "\U0001D16F",
    "dotted-sixteenth": "\U0001D16F",
    "thirtysecond": "\U0001D170",
}
DOT_GLYPH = "\U0001D16D"           # MUSICAL SYMBOL COMBINING AUGMENTATION DOT

# Duration suffix for *name* style -- composed as f"{letter}·{suffix}"
# (middle dot, U+00B7), e.g. "Bb·8th". Kept as short text rather than a
# notation glyph so the *name* style stays internally consistent (text
# throughout, never a symbol).
_NAME_STYLE_DURATION_SUFFIXES = {
    "whole": "whole",
    "dotted-half": "half.",
    "half": "half",
    "dotted-quarter": "4th.",
    "quarter": "4th",
    "dotted-eighth": "8th.",
    "eighth": "8th",
    "dotted-sixteenth": "16th.",
    "sixteenth": "16th",
    "thirtysecond": "32nd",
}

# One rendered column's shape, whichever kind it is. `row_map`/`ledgers`/
# `chord_name` are only meaningful for kind == "note"; `rgb` is only
# meaningful for kind == "barline" (a barline has one flat color across
# its whole height, not a per-row map).
Column = namedtuple("Column", "kind width row_map ledgers chord_name rgb")


class TabDisplay:
    def __init__(self, fps=20, scrollback_seconds=None):
        self.fps = fps
        # How far back (in each entry's own `.t` timestamp, not a fixed
        # column count) self.entries retains history -- the window the
        # `R`/Left-Right-arrow scrollback feature scrolls within. `None`
        # (the default) falls back to config.TAB_SCROLLBACK_SECONDS;
        # callers that want a user's config.toml override pass the
        # already-resolved value in (e.g.
        # config_store.store.preference("tab_scrollback_seconds", ...)).
        self.scrollback_seconds = (
            config.TAB_SCROLLBACK_SECONDS if scrollback_seconds is None else scrollback_seconds
        )
        self.entries = deque()
        self.session_history = []
        # (pitch_class, octave) -> the most recently pushed, not-yet-
        # finalized note dict at that key -- see finalize_duration().
        self._open_notes = {}
        self._t0 = time.monotonic()
        self._last_size = None
        sys.stdout.write("\033[?25l\033[2J")
        sys.stdout.flush()

    def push(self, pitch_class, octave, rgb, label, t=None):
        """Monophonic mode: push one note as a new scrolling column. `t`
        overrides the column's recorded timestamp (seconds) -- live
        callers omit it and get wall-clock time-since-construction, same
        as always; batch transcription (main.run_batch_transcribe(), issue
        #55) passes the note's real onset_time from the recording, since
        wall-clock time during a fast offline sweep is meaningless (every
        column would otherwise land at ~0.00s)."""
        self._push_entry([(pitch_class, octave, rgb, label)], chord_name=None, t=t)

    def push_notes(self, notes, chord_name, t=None):
        """Chord mode: push up to CHORD_MAX_NOTES (pitch_class, octave,
        rgb, label) tuples as one stacked scrolling column, plus the
        recognized chord name shown in the header row above it. `t`: see
        push()."""
        self._push_entry(list(notes), chord_name, t=t)

    def push_barline(self, t=None):
        """Push a barline column -- no notes, just a divider glyph spanning
        the staff height, mixed into the same scrolling history as note
        columns. `t`: see push()."""
        self._append_history(BarlineEntry(self._resolve_t(t)))

    def _resolve_t(self, t):
        return t if t is not None else time.monotonic() - self._t0

    def _push_entry(self, notes, chord_name, t=None):
        note_dicts = []
        for pitch_class, octave, rgb, label in notes:
            note = {
                "pitch_class": pitch_class,
                "octave": octave,
                "rgb": rgb,
                "label": label,
                "duration_class": None,
            }
            note_dicts.append(note)
            if pitch_class is not None:
                # Overwrites any previous still-open note at this key --
                # a rapid re-attack simply abandons the older one (it never
                # gets a duration_class, which is fine; see the module
                # docstring).
                self._open_notes[(pitch_class, octave)] = note
        self._append_history(TabEntry(note_dicts, chord_name, self._resolve_t(t)))

    def _append_history(self, entry):
        self.entries.append(entry)
        if len(self.session_history) < config.TAB_SESSION_HISTORY_MAX:
            self.session_history.append(entry)
        self._trim_entries()

    def _trim_entries(self):
        """Scrollback retention: trims `self.entries` from the left using
        each entry's own `.t` timestamp, not a fixed column count --
        columns arrive at irregular, onset-driven intervals (mono) or a
        rate that varies by scroll mode ('fix'), so a count-based cap
        (the old TAB_VISIBLE_MAXLEN) doesn't correspond to a real time
        window the way `self.scrollback_seconds` does. Never empties
        `self.entries` entirely -- the newest entry's own timestamp is
        always <= itself, so it's never trimmed regardless of window
        size. Does not touch `self.session_history`, which keeps its own
        separate, much longer, count-based cap (TAB_SESSION_HISTORY_MAX)
        for the on-quit dump."""
        if not self.entries:
            return
        cutoff = self.entries[-1].t - self.scrollback_seconds
        while len(self.entries) > 1 and self.entries[0].t < cutoff:
            self.entries.popleft()

    def finalize_duration(self, pitch_class, octave, duration_class):
        """Set `duration_class` on the most recent still-open note at
        (pitch_class, octave), mutating the dict already sitting in both
        `self.entries` and `self.session_history` in place. A silent
        no-op if there's no open entry at that key (nothing to finalize,
        e.g. a stale/duplicate call)."""
        note = self._open_notes.pop((pitch_class, octave), None)
        if note is not None:
            note["duration_class"] = duration_class

    def correct_duration(self, pitch_class, octave, t, duration_class):
        """Overwrite the `duration_class` of a *specific* note at
        (pitch_class, octave), for the future `R`-key non-causal
        recompute: unlike `finalize_duration()` (which only ever reaches
        the note still currently open at that key), this can reach back
        and correct a note that already finalized -- and may already have
        been superseded by a later note at the same key -- many columns
        ago, once a slower/more-accurate recompute revises its measured
        duration.

        Disambiguates *which* occurrence at that repeated key by picking
        whichever one's column timestamp (`TabEntry.t` -- the same
        timestamp `push()`/`push_notes()`'s `t=` override already
        records; every note in a column shares its column's onset time,
        so no separate per-note id is needed) is closest to `t`, rather
        than requiring an exact match -- a recompute's own reconstructed
        onset time won't necessarily equal the original hop-granular
        value bit-for-bit.

        Searches `self.session_history` (a strict superset of
        `self.entries`, sharing the exact same note dict objects) so a
        correction lands even on a note that's since scrolled out of the
        retained `self.entries` window but is still kept for the on-quit
        dump. Returns True if a note was found and corrected, False if no
        note at that key exists anywhere in retained history (a no-op,
        same silent-failure convention as `finalize_duration()`)."""
        best_note, best_dt = None, None
        for entry in self.session_history:
            if not isinstance(entry, TabEntry):
                continue
            for note in entry.notes:
                if note["pitch_class"] != pitch_class or note["octave"] != octave:
                    continue
                dt = abs(entry.t - t)
                if best_dt is None or dt < best_dt:
                    best_note, best_dt = note, dt
        if best_note is None:
            return False
        best_note["duration_class"] = duration_class
        return True

    def erase_barlines(self, start_t, end_t=None):
        """Remove every barline column at or after `start_t` and, if
        `end_t` is given, strictly before it (a half-open [start_t, end_t)
        interval; `end_t` omitted/None means unbounded -- erase every
        barline from `start_t` through the newest entry). Removes from
        both `self.entries` and `self.session_history`.

        Pairs with `insert_barline()` for the `R`-key non-causal recompute
        path: a corrected tempo/beat estimate implies different bar
        boundaries than the live estimate guessed, so the stale barline
        set within the recomputed range is erased here, then
        `insert_barline()` places fresh ones at the recomputed times.
        Returns the number of barline columns removed from
        `self.entries`."""
        def _matches(e):
            return isinstance(e, BarlineEntry) and e.t >= start_t and (end_t is None or e.t < end_t)

        removed = sum(1 for e in self.entries if _matches(e))
        self.entries = deque(e for e in self.entries if not _matches(e))
        self.session_history = [e for e in self.session_history if not _matches(e)]
        return removed

    def insert_barline(self, t):
        """Insert a corrected barline column at timestamp `t`, keeping
        `self.entries`/`self.session_history` in timestamp order --
        unlike `push_barline()` (which always appends at the tail,
        correct for its live, always-chronological caller, `main.py`'s
        beat-accumulator), a corrected barline from the `R`-key recompute
        path isn't guaranteed to land after every existing entry. Pairs
        with `erase_barlines()`. Respects the same `session_history` cap
        `push_barline()` does."""
        entry = BarlineEntry(self._resolve_t(t))
        _sorted_insert(self.entries, entry)
        if len(self.session_history) < config.TAB_SESSION_HISTORY_MAX:
            _sorted_insert(self.session_history, entry)
        self._trim_entries()

    def render(self, status, chord_mode=False, notehead_style="symbol", legend_on=True, frozen=False,
               help_legend="", scroll_offset=0):
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = size

        # Only the current frame's row/column count is redrawn each time;
        # on a resize (frequent in a tiling WM) that region shrinks or the
        # note columns re-align, so the previous frame's content outside
        # it would otherwise never get overwritten and linger as ghosts.
        clear = "\033[2J" if size != self._last_size else ""
        self._last_size = size

        header_rows = 1 if chord_mode else 0
        # `help_legend` (issue #40's H toggle) is a *different* toggle from
        # this method's own `legend_on` (the staff clef/letter column, an
        # older, unrelated feature -- see the module docstring) and reserves
        # its own extra trailing row only when populated, same convention
        # as the other terminal views' render().
        text_rows = 2 if help_legend else 1
        usable_rows = max(rows - text_rows - header_rows, 1)  # reserve the trailing row(s) for status/legend text

        top, bottom = TOP_ROW, BOTTOM_ROW
        shrink = (top - bottom + 1) - usable_rows
        while shrink > 0 and (top > 20 or bottom < 0):
            if bottom < 0:
                bottom += 1
                shrink -= 1
            if shrink > 0 and top > 20:
                top -= 1
                shrink -= 1

        width = config.TAB_COLUMN_WIDTH_CHORD if chord_mode else config.TAB_COLUMN_WIDTH
        # `L` (legend_on) reclaims the legend column's width for note
        # columns entirely when off, rather than just blanking its
        # content -- issue #19's stated intent for the toggle.
        legend_width = config.TAB_LEGEND_WIDTH if legend_on else 0
        available_width = max(cols - legend_width, 0)

        # Walk the history from newest to oldest, accumulating each
        # column's *actual* width (barline columns are narrower than note
        # columns -- config.TAB_BARLINE_WIDTH vs. `width`) until the
        # available screen width is used up. The newest column is always
        # included even if it alone doesn't fit, same guarantee the old
        # fixed-width `max(..., 1)` gave.
        all_entries = list(self.entries)
        # Scrollback (Left/Right-arrow feature, frozen-only): `scroll_offset`
        # is a count of history entries (note *and* barline columns alike,
        # not raw terminal character columns) to hide off the tail --
        # slicing them off here, before the width-budget walk below, makes
        # the remaining tail entry play the role of "the newest visible
        # column" for every purpose below (width budget, age-from-that-
        # point-in-time), i.e. this renders exactly what the live view
        # looked like `scroll_offset` columns ago, not the current live
        # tail with some cosmetic offset applied on top.
        scroll_offset = max(scroll_offset, 0)
        if scroll_offset:
            all_entries = all_entries[: max(len(all_entries) - scroll_offset, 0)]
        # Freeze (#23) pins every visible column to age 0 -- but only when
        # not also scrolled back: a scrolled-back-to column should show the
        # age-fade gradient it actually had at that point in history (see
        # the module docstring), not render as if it just arrived the way
        # plain freeze-with-no-scrolling does.
        pin_to_newest = frozen and not scroll_offset
        visible_entries = []
        used_width = 0
        for e in reversed(all_entries):
            e_width = config.TAB_BARLINE_WIDTH if isinstance(e, BarlineEntry) else width
            if visible_entries and used_width + e_width > available_width:
                break
            used_width += e_width
            visible_entries.append(e)
        visible_entries.reverse()
        if visible_entries:
            pad = max((available_width - used_width) // width, 0)
        else:
            pad = max(available_width // width, 1)

        columns = [Column("note", width, {}, frozenset(), None, None)] * pad
        last_index = len(visible_entries) - 1
        for index, e in enumerate(visible_entries):
            # Age is distance from the newest *visible* column (0 = newest);
            # #23's freeze-frame pins every column's age to 0, overriding
            # #22's fade entirely, without this loop needing to know why.
            age = 0 if pin_to_newest else last_index - index

            if isinstance(e, BarlineEntry):
                columns.append(
                    Column("barline", config.TAB_BARLINE_WIDTH, {}, frozenset(), None, _barline_rgb(age))
                )
                continue

            row_map = {}
            ledgers = set()
            for note in e.notes:
                pitch_class = note["pitch_class"]
                octave = note["octave"]
                # Notes still outside [bottom, top] after the shrink above
                # (only possible once the staff has hit its 21-row floor,
                # on a terminal too short even for that) are dropped rather
                # than clamped onto the boundary row -- clamping would
                # silently draw the note at the wrong staff position
                # instead of just not showing it.
                if pitch_class is None:
                    continue
                row = staff_row(pitch_class, octave)
                if not (bottom <= row <= top):
                    continue
                # Store the raw pitch_class, not the precomputed `label`
                # (that's dump_ansi()'s letter+octave text, untouched by
                # notehead_style) -- _cell_text() below renders fresh
                # against whichever style is current, so a live `N` toggle
                # restyles already-on-screen columns too. Color is also
                # recomputed fresh here (_column_note_rgb), not the
                # precomputed `rgb` this note dict carries -- that
                # precomputed value is always TAB_NOTE_LIGHTNESS and is
                # only ever read by dump_ansi(); a column's age (and thus
                # its dimming) changes every render as newer columns scroll
                # in, so it can't be baked in at push time. duration_class
                # is resolved to the DEFAULT_DURATION_CLASS fallback here,
                # at render time -- the dict itself keeps None so the
                # "genuinely still open" / "measured as a quarter note"
                # distinction survives for dump_ansi() and any future
                # consumer.
                duration_class = note["duration_class"] or DEFAULT_DURATION_CLASS
                row_map[row] = (_column_note_rgb(pitch_class, age), pitch_class, duration_class)
                ledgers.update(r for r in ledger_rows(row) if bottom <= r <= top)
            columns.append(Column("note", width, row_map, frozenset(ledgers), e.chord_name, None))

        # The staff itself is never shrunk below 21 rows (top=20..bottom=0),
        # even on a terminal shorter than that -- cap what we actually emit
        # to usable_rows so cursor addressing never targets a row beyond the
        # real terminal height, which would scroll/corrupt the fixed-position
        # rendering instead of just cropping the staff.
        lines = []
        if chord_mode:
            header_cells = [" " * legend_width]
            for col in columns:
                # A barline column has no chord name -- it contributes an
                # empty cell to the header row, same width as its own
                # column, rather than skipping the header entirely.
                header_cells.append((col.chord_name or "")[:col.width].ljust(col.width))
            lines.append("".join(header_cells))

        for screen_row in range(top, bottom - 1, -1):
            if len(lines) >= usable_rows + header_rows:
                break
            if not legend_on:
                legend = ""
            else:
                # Two side-by-side sub-columns (issue #36's "variant B" split,
                # previously merged into one shared region): a clef-only
                # column, blank except on its own anchor row, then a letter
                # column labeling EVERY row -- line and space alike, not just
                # STAFF_LINE_ROWS -- via row_note_name()'s general diatonic-
                # step math (every staff row, line or space, is a natural
                # note; accidentals share their natural's row, same as
                # noteheads do).
                if screen_row == BASS_CLEF_ROW:
                    clef_cell = BASS_CLEF_GLYPH.center(config.TAB_CLEF_WIDTH)
                elif screen_row == TREBLE_CLEF_ROW:
                    clef_cell = TREBLE_CLEF_GLYPH.center(config.TAB_CLEF_WIDTH)
                else:
                    clef_cell = " " * config.TAB_CLEF_WIDTH
                letter_cell = row_note_name(screen_row).center(config.TAB_LETTER_WIDTH)
                legend = clef_cell + letter_cell
            cells = [legend]
            for col in columns:
                if col.kind == "barline":
                    # Every visible staff row gets the barline glyph --
                    # not just staff-line/ledger rows -- so it reads as one
                    # continuous vertical divider across the whole height,
                    # independent of legend/chord-mode bookkeeping.
                    cells.append(_barline_cell(col.rgb, col.width))
                elif screen_row in col.row_map:
                    rgb, pitch_class, duration_class = col.row_map[screen_row]
                    cells.append(_note_cell(rgb, _cell_text(pitch_class, notehead_style, duration_class), col.width))
                elif screen_row in col.ledgers or screen_row in STAFF_LINE_ROWS:
                    cells.append(LEDGER_CHAR * col.width)
                else:
                    cells.append(" " * col.width)
            lines.append("".join(cells))

        out = []
        for i, line in enumerate(lines, start=1):
            out.append(f"\033[{i};1H\033[K{line}")
        out.append(f"\033[{len(lines) + 1};1H\033[K{status}")
        if help_legend:
            out.append(f"\033[{len(lines) + 2};1H\033[K{help_legend}")
        sys.stdout.write(clear + "".join(out))
        sys.stdout.flush()

    def dump_ansi(self, path):
        lines = []
        for i, e in enumerate(self.session_history):
            if isinstance(e, BarlineEntry):
                lines.append(f"{e.t:8.2f}s  {i:5d}  |")
                continue
            sounding = [n for n in e.notes if n["pitch_class"] is not None]
            if not sounding:
                lines.append(f"{e.t:8.2f}s  {i:5d}  --")
                continue
            note_strs = []
            for n in sounding:
                r, g, b = n["rgb"]
                swatch = f"\033[48;2;{r};{g};{b}m  \033[0m"
                note_strs.append(f"{swatch}  {n['label']}")
            chord_part = f"  [{e.chord_name}]" if e.chord_name else ""
            lines.append(f"{e.t:8.2f}s  {i:5d}  " + "  ".join(note_strs) + chord_part)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def quit(self):
        sys.stdout.write("\033[0m\033[2J\033[H\033[?25h")
        sys.stdout.flush()


def _sorted_insert(container, entry):
    """Insert `entry` (a TabEntry/BarlineEntry, both carrying a `.t`
    timestamp) into `container` (a list or deque) at the position that
    keeps it sorted by timestamp -- used by `TabDisplay.insert_barline()`
    for the `R`-key correction path, where a recomputed barline's
    timestamp isn't guaranteed to land after every existing entry the way
    a live push always does. `container.insert()` works the same way on
    both `list` and `collections.deque`."""
    ts = [e.t for e in container]
    idx = bisect.bisect_right(ts, entry.t)
    container.insert(idx, entry)


def _note_cell(rgb, label, width):
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    fg = (20, 20, 20) if lum > 140 else (230, 230, 230)
    text = (label or "")[:width].center(width)
    return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{text}\033[0m"


def _barline_cell(rgb, width):
    """A barline column's cell: the glyph in foreground color only (no
    background swatch, unlike a note cell) -- a barline is a divider line,
    not a data block."""
    r, g, b = rgb
    text = BARLINE_GLYPH.center(width)
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


def _aged_lightness(age):
    """Issue #22's dimming curve: linear fade from `config.TAB_NOTE_LIGHTNESS`
    at `age` 0 (the newest visible column) down to `config.DIM_LIGHTNESS`
    by `config.FADE_COLUMNS` columns of age, held at that floor beyond it.
    Freeze-frame (#23) passes `age=0` for every visible column, which this
    formula already resolves to full `TAB_NOTE_LIGHTNESS` -- no separate
    freeze branch needed here, the caller just lies about age."""
    if config.FADE_COLUMNS <= 1:
        return config.DIM_LIGHTNESS
    fraction = min(age / (config.FADE_COLUMNS - 1), 1.0)
    return config.TAB_NOTE_LIGHTNESS - (config.TAB_NOTE_LIGHTNESS - config.DIM_LIGHTNESS) * fraction


def _column_note_rgb(pitch_class, age):
    """A note cell's on-screen color, recomputed fresh every render: the
    same fixed fifths hue/saturation `main.py`'s `_tab_note_rgb()` computes
    when a note is first pushed, but with lightness taken from
    `_aged_lightness()` instead of the fixed `config.TAB_NOTE_LIGHTNESS`
    that precomputed value always uses. `note_to_hsl(..., scheme="fifths")`
    is used (not `--color-scheme`) for the same reason `_tab_note_rgb()`
    hardcodes it: `tab` reads as the same color as `wheel`, independent of
    the active color scheme."""
    hue, sat, _light = note_to_hsl(pitch_class, config.MAX_OCTAVE, scheme="fifths")
    return hsl_to_rgb255(hue, sat, _aged_lightness(age))


def _barline_rgb(age):
    """A barline column's on-screen color: the same age-based fade curve
    every note column uses (_aged_lightness()) but with zero saturation --
    a barline has no pitch-class identity, so no hue applies, just a
    neutral/grayscale fade."""
    return hsl_to_rgb255(0, 0.0, _aged_lightness(age))


def _accidental_suffix(pitch_class):
    """'b'/'#' suffix from NOTE_NAMES_FIFTHS, or '' for a natural note."""
    name = NOTE_NAMES_FIFTHS[pitch_class]
    return name[1:] if len(name) > 1 else ""


def _cell_text(pitch_class, style, duration_class):
    """The notehead's on-screen text for the given render style -- see the
    module docstring for what each style shows. `style` is whatever the
    caller's live `N` toggle currently selects; unrecognized values fall
    back to *name* rather than raising, so a stale/typo'd style string
    degrades gracefully instead of crashing the render loop.

    `duration_class` should already be resolved (None -> `duration_tracker.
    DEFAULT_DURATION_CLASS`) by the caller; resolved again here as a
    defensive fallback so a direct call with duration_class=None still
    degrades gracefully instead of raising.
    """
    duration_class = duration_class or DEFAULT_DURATION_CLASS
    if style == "symbol":
        marker = SYMBOL_ACCIDENTALS.get(_accidental_suffix(pitch_class), "")
        text = NOTEHEAD_GLYPH + marker
        if duration_class != "whole":
            text += STEM_GLYPH
            flag = FLAG_GLYPHS.get(duration_class)
            if flag:
                text += flag
        if duration_class.startswith("dotted-"):
            text += DOT_GLYPH
        return text
    letter = NOTE_NAMES_FIFTHS[pitch_class]
    suffix = _NAME_STYLE_DURATION_SUFFIXES.get(duration_class)
    return f"{letter}·{suffix}" if suffix else letter
