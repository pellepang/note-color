"""PROTOTYPE -- throwaway, wayfinder ticket #87 (map #85, score editor).

Three radically different concepts for "how does an editable score look
and behave in the terminal" -- cycle live with `v`. Answers the question
`terminal_tab_display.py`'s TabDisplay was never built to answer: it's a
scrolling, append-only, read-only live view (new columns arrive on the
right, nothing is ever addressed by position). An editor needs a loaded-
once, random-access buffer with a cursor. Reuses TabDisplay's real
rendering primitives (notehead glyph, staff-row math, fifths coloring)
throughout -- diverges from TabDisplay itself wherever its scrolling
model doesn't fit a fixed, cursor-addressable buffer.

Sample data is a short synthesized 8-column melody+chords sequence, held
in memory as plain dicts (not music21 objects) per #86's just-resolved
finding that the editor's live model should be a simple intermediate
structure, not music21's own Stream graph.

Run:
    cd ~/note-color
    .venv/bin/python prototypes/score-editor-cursor-concept/demo.py

Keys (global): v cycle variant | q / Ctrl+C quit
Keys (per-variant, shown live in the status line): vary -- see each
variant's own render function.

Variant A (the one real feedback converged on) does mutate its in-memory
SAMPLE_SCORE live once you drill into a column with Enter -- transposing a
tone, typing/selecting a chord, cycling inversions -- since demonstrating
the *editing* interaction itself is the whole point at this stage, not
just cursor movement. Nothing persists anywhere; quitting resets it.
Variants B/C are still cursor-movement-only, kept for reference.
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from color_map import NOTE_NAMES_FIFTHS, fifths_index, hsl_to_rgb255, note_to_hsl  # noqa: E402
from staff_map import (  # noqa: E402
    BASS_CLEF_ROW, BOTTOM_ROW, GRAND_STAFF_REF_STEP, LETTER_INDEX, STAFF_LINE_ROWS,
    TOP_ROW, TREBLE_CLEF_ROW, ledger_rows, staff_row,
)
from terminal_tab_display import LEDGER_CHAR, NOTEHEAD_GLYPH, SYMBOL_ACCIDENTALS  # noqa: E402
from main import RawKeys  # noqa: E402

RESET = "\033[0m"
INVERT = "\033[7m"
BOLD = "\033[1m"

LETTER_TO_PC = {name: pc for pc, name in enumerate(NOTE_NAMES_FIFTHS)}


def n(letter, octave):
    return (LETTER_TO_PC[letter], octave)


# A short melody sliding into two chords -- enough variety to exercise
# single notes, a triad, and an octave-spanning chord.
SAMPLE_SCORE = [
    {"notes": [n("C", 4)], "duration": "quarter", "chord_name": None},
    {"notes": [n("D", 4)], "duration": "quarter", "chord_name": None},
    {"notes": [n("E", 4)], "duration": "quarter", "chord_name": None},
    {"notes": [n("C", 4), n("E", 4), n("G", 4)], "duration": "half", "chord_name": "C"},
    {"notes": [n("F", 4)], "duration": "eighth", "chord_name": None},
    {"notes": [n("G", 4)], "duration": "eighth", "chord_name": None},
    {"notes": [n("A", 3), n("C", 4), n("E", 4)], "duration": "half", "chord_name": "Am"},
    {"notes": [n("G", 3)], "duration": "whole", "chord_name": None},
]


def note_rgb(pc, octave):
    hue, sat, light = note_to_hsl(pc, octave, scheme="fifths")
    return hsl_to_rgb255(hue, sat, light)


def ansi_fg(rgb):
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m"


def notehead_text(pc, with_letter=False, octave=None):
    letter = NOTE_NAMES_FIFTHS[pc]
    acc = SYMBOL_ACCIDENTALS.get(letter[1:], "") if len(letter) > 1 else ""
    glyph = NOTEHEAD_GLYPH + acc
    if with_letter:
        return f"{glyph}{letter[0]}{octave if octave is not None else ''}"
    return glyph


def pad_center(text, width):
    # Rough -- doesn't account for the combining-mark display-width subtlety
    # terminal_tab_display._display_width() handles; fine for a prototype
    # since this demo never composes duration glyphs onto noteheads.
    if len(text) >= width:
        return text[:width]
    left = (width - len(text)) // 2
    right = width - len(text) - left
    return " " * left + text + " " * right


def cell(text, width, rgb=None, inverted=False):
    color = ansi_fg(rgb) if rgb else ""
    body = pad_center(text, width)
    if inverted:
        return f"{INVERT}{color}{body}{RESET}"
    return f"{color}{body}{RESET}"


# ---------------------------------------------------------------------------
# Variant A: extend TabDisplay's column-per-note staff into a fixed,
# cursor-addressable buffer -- the "reuse the existing renderer" answer.

# Zoom is a stepped level, not a boolean -- each step shows strictly more
# detail than the last: (column width, show letter, show octave, show
# duration abbreviation next to the notehead).
ZOOM_LEVELS = [
    (3, False, False, False),   # bare notehead glyph
    (5, True, False, False),    # + letter
    (7, True, True, False),     # + octave digit
    (11, True, True, True),     # + duration abbreviation
]

# Real feedback: editing a note's pitch used to require drilling into a
# whole separate column view; wanted a way to do it "while in the total
# melody view" instead -- plus a free-roaming cursor, decoupled from
# existing notes entirely, for placing/removing notes at a staff position
# that may not have one yet. Up/Down means something different in each:
VIEW_MODES = ["select", "transpose", "freeview"]  # extensible -- more modes land here later
VIEW_MODE_HELP = {
    "select": "Up/Down: pick among this column's existing tones (browsing only, no mutation)",
    "transpose": "Up/Down: move the selected tone's own pitch a semitone, live, right here",
    "freeview": "Up/Down: free cursor, any staff row -- Space: place/remove a note here",
}

_DURATION_ABBR = {
    "whole": "1", "half": "2", "dotted-half": "2.", "quarter": "4",
    "dotted-quarter": "4.", "eighth": "8", "dotted-eighth": "8.", "sixteenth": "16",
}

# Small, illustrative chord-quality table for the column drill-in editor --
# NOT this app's real ~360-template dictionary (chord_templates.py owns
# that, for *recognizing* a chord from live audio). This is the reverse
# direction (name -> notes, for *constructing* one by hand), which nothing
# in the real codebase does yet -- a deliberately tiny stand-in set, just
# enough to demonstrate "type it" / "scroll a selector" as interactions.
# Ordered by music-theory family (real feedback: "organised like music
# theory"), not alphabetically or by however it first got typed in:
# triads, then sevenths, then sus chords.
CHORD_QUALITIES = [
    ("maj", [0, 4, 7], "Major"),
    ("min", [0, 3, 7], "Minor"),
    ("dim", [0, 3, 6], "Diminished"),
    ("aug", [0, 4, 8], "Augmented"),
    ("7", [0, 4, 7, 10], "Dominant 7th"),
    ("maj7", [0, 4, 7, 11], "Major 7th"),
    ("min7", [0, 3, 7, 10], "Minor 7th"),
    ("dim7", [0, 3, 6, 9], "Diminished 7th"),
    ("sus2", [0, 2, 7], "Sus2"),
    ("sus4", [0, 5, 7], "Sus4"),
]
# Compact display suffix per quality key -- distinct from the internal key
# itself (QUALITY_ALIASES needs unambiguous parse keys like "min7", but a
# label reads better as the conventional short form, "m7").
_QUALITY_SUFFIX = {
    "maj": "", "min": "m", "7": "7", "maj7": "maj7", "min7": "m7",
    "dim": "dim", "dim7": "dim7", "aug": "aug", "sus2": "sus2", "sus4": "sus4",
}
QUALITY_ALIASES = {
    "": "maj", "maj": "maj", "M": "maj",
    "m": "min", "min": "min", "-": "min",
    "7": "7", "dom7": "7",
    "maj7": "maj7", "M7": "maj7",
    "m7": "min7", "min7": "min7", "-7": "min7",
    "dim": "dim", "o": "dim",
    "dim7": "dim7", "o7": "dim7",
    "aug": "aug", "+": "aug",
    "sus2": "sus2", "sus4": "sus4",
}
# Generous enharmonic root spelling -- both sharp and flat names accepted
# on input, independent of NOTE_NAMES_FIFTHS's own single fixed spelling
# per pitch class (which is only ever a *display* convention).
ALL_NOTE_SPELLINGS = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}

# The chord builder's root "reel" is ordered around the circle of fifths
# (C, G, D, A, E, B, F#, Db, Ab, Eb, Bb, F) via this app's own
# color_map.fifths_index() -- the same order the wheel view and every
# fifths-scheme coloring already use, rather than a chromatic C-C#-D-...
# run. This *is* "organised like music theory" for a root picker: reaching
# a closely-related key never means spinning through the whole reel.
ROOT_REEL = sorted(range(12), key=fifths_index)

# Real feedback: "more columns assigned to the different parts of the
# chord" -- rather than only picking a whole preset quality, each chord
# tone above the root gets its own independently spinnable reel: the
# 3rd-ish voice (or a sus 2nd/4th in its place), the 5th, and the 7th.
# (token, interval-in-semitones-from-root-or-None, display label) --
# "none" is always index 0 so a fresh/empty slot's default is "not part
# of the chord", not an arbitrary interval.
THIRD_OPTIONS = [
    ("none", None, "(none)"),
    ("sus2", 2, "Sus2 (2nd)"),
    ("b3", 3, "Minor 3rd"),
    ("3", 4, "Major 3rd"),
    ("sus4", 5, "Sus4 (4th)"),
]
FIFTH_OPTIONS = [
    ("none", None, "(none)"),
    ("b5", 6, "Dim 5th"),
    ("5", 7, "Perfect 5th"),
    ("#5", 8, "Aug 5th"),
]
SEVENTH_OPTIONS = [
    ("none", None, "(none)"),
    ("dim7", 9, "Dim 7th"),
    ("b7", 10, "Minor 7th"),
    ("7", 11, "Major 7th"),
]


def _option_index_for_interval(options, interval):
    return next((i for i, (_tok, iv, _lbl) in enumerate(options) if iv == interval), 0)


BUILDER_SLOTS = ["root", "quality", "third", "fifth", "seventh"]


def build_chord(root_pc, intervals, base_octave):
    return [((root_pc + iv) % 12, base_octave + (root_pc + iv) // 12) for iv in intervals]


def _sort_by_pitch(notes):
    return sorted(notes, key=lambda pn: pn[1] * 12 + pn[0])


def invert_up(notes):
    """Move the whole chord up one inversion: its lowest note jumps an
    octave to become the new highest, same pitch classes, different
    voicing -- the "move a whole selected chord up through its
    inversions" interaction."""
    notes = _sort_by_pitch(notes)
    pc, octv = notes.pop(0)
    notes.append((pc, octv + 1))
    return _sort_by_pitch(notes)


def invert_down(notes):
    notes = _sort_by_pitch(notes)
    pc, octv = notes.pop(-1)
    notes.insert(0, (pc, octv - 1))
    return _sort_by_pitch(notes)


def transpose_semitone(pc, octave, direction):
    idx = pc + direction
    return idx % 12, octave + idx // 12


_NATURAL_PC_BY_LETTER_IDX = {letter_idx: pc for pc, letter_idx in LETTER_INDEX.items()}


def pitch_at_row(row):
    """Inverse of staff_map.staff_row(): the *natural* note (no
    accidental) that sits at a given staff row -- freeview placement
    always places a natural there, matching staff_map's own "an
    accidental shares its natural neighbor's row" convention (there's no
    separate row to place a sharp/flat on)."""
    step = row + GRAND_STAFF_REF_STEP
    letter_idx = step % 7
    octave = step // 7
    return _NATURAL_PC_BY_LETTER_IDX[letter_idx], octave


def guess_chord_label(notes):
    """Reverse-lookup a CHORD_QUALITIES match against notes' pitch-class
    *set* (octave/voicing-independent, so this still finds the right name
    after invert_up()/invert_down() has changed which note is on the
    bottom) -- purely a label for display, not a real recognizer like
    chord_templates.match() (no confidence scoring, no ~360-template
    coverage, no bass-driven tie-breaking)."""
    if len(notes) <= 1:
        return None
    pcs = sorted({pc for pc, _ in notes})
    for root_pc in pcs:
        candidate = sorted((pc - root_pc) % 12 for pc in pcs)
        for quality, intervals, _label in CHORD_QUALITIES:
            if sorted(intervals) == candidate:
                return f"{NOTE_NAMES_FIFTHS[root_pc]}{_QUALITY_SUFFIX[quality]}"
    return "?"


class VariantA:
    name = "A -- staff buffer + cursor overlay (extends TabDisplay)"

    def __init__(self):
        self.cursor_col = 0
        self.cursor_note = 0
        self.chords_only = False
        self.zoom_level = 0
        self.view_mode = "select"
        self.cursor_row = staff_row(*SAMPLE_SCORE[0]["notes"][0])  # kept in sync for freeview
        self.subview_col = None  # None = main staff view; else the column index being drilled into

    @property
    def hint(self):
        if self.subview_col is not None:
            return self._subview_hint()
        return (f"Left/Right: move column | m: mode ({self.view_mode}) | Enter: chord builder | "
                f"c: chords-only | +/-: zoom ({self.zoom_level + 1}/{len(ZOOM_LEVELS)}) | "
                f"{VIEW_MODE_HELP[self.view_mode]}")

    def _subview_hint(self):
        if self.subview_input_mode == "builder":
            slot = BUILDER_SLOTS[self.subview_builder_slot]
            typed = f" | typing: {self.subview_typed}_" if self.subview_typed else ""
            return (f"chord builder -- Left/Right: switch reel ({'/'.join(BUILDER_SLOTS)}) | "
                    f"Up/Down: spin {slot} | type to jump | b: done{typed}")
        focus = ("whole chord -- Up/Down: cycle inversion" if self.subview_focus == "chord"
                 else "single tone -- Left/Right: pick tone, Up/Down: transpose semitone")
        return f"editing column {self.subview_col + 1} | m: note/chord focus ({focus}) | t: chord builder | b: back"

    def handle_key(self, key):
        if self.subview_col is not None:
            self._handle_subview_key(key)
            return
        if key == "m":
            self._cycle_view_mode()
            return
        if key == "c":
            self.chords_only = not self.chords_only
            return
        if key == "+":
            self.zoom_level = min(len(ZOOM_LEVELS) - 1, self.zoom_level + 1)
            return
        if key == "-":
            self.zoom_level = max(0, self.zoom_level - 1)
            return
        if key in ("\r", "\n"):
            self._enter_subview()
            return
        if self.view_mode == "freeview":
            self._handle_freeview_key(key)
            return
        col = SAMPLE_SCORE[self.cursor_col]
        if key == "LEFT":
            self.cursor_col = max(0, self.cursor_col - 1)
            self.cursor_note = 0
        elif key == "RIGHT":
            self.cursor_col = min(len(SAMPLE_SCORE) - 1, self.cursor_col + 1)
            self.cursor_note = 0
        elif key == "UP" or key == "DOWN":
            if self.view_mode == "transpose":
                # Mutates the selected note in place, live, right in the
                # main melody view -- real feedback: editing a note used
                # to require drilling into a whole separate column view
                # just to nudge its pitch.
                pc, octv = col["notes"][self.cursor_note]
                col["notes"][self.cursor_note] = transpose_semitone(pc, octv, +1 if key == "UP" else -1)
                col["chord_name"] = guess_chord_label(col["notes"])
            else:
                # select mode: browse which existing tone is highlighted,
                # no mutation. Notes are stored low-to-high pitch, and a
                # higher pitch renders in a *higher* (visually upper)
                # staff row -- so UP must walk the index upward too,
                # matching the arrow's own direction on screen.
                if key == "UP":
                    self.cursor_note = min(len(col["notes"]) - 1, self.cursor_note + 1)
                else:
                    self.cursor_note = max(0, self.cursor_note - 1)

    def _cycle_view_mode(self):
        old_mode = self.view_mode
        self.view_mode = VIEW_MODES[(VIEW_MODES.index(old_mode) + 1) % len(VIEW_MODES)]
        col = SAMPLE_SCORE[self.cursor_col]
        if self.view_mode == "freeview" and old_mode != "freeview":
            # Entering freeview: start the free cursor exactly where the
            # selected note already was, so the switch feels continuous.
            pc, octv = col["notes"][self.cursor_note]
            self.cursor_row = staff_row(pc, octv)
        elif old_mode == "freeview" and self.view_mode != "freeview":
            # Leaving freeview: if the free cursor happens to be sitting
            # on a real note, select it; otherwise fall back to the first
            # tone rather than an index that might not exist.
            match = next((i for i, (npc, noctv) in enumerate(col["notes"]) if staff_row(npc, noctv) == self.cursor_row),
                         None)
            self.cursor_note = match if match is not None else 0

    def _handle_freeview_key(self, key):
        # Left/Right still move between columns (time is still the only
        # discrete axis this score has); Up/Down move the cursor to *any*
        # staff row, not just ones an existing note occupies -- "without
        # clamping to the existing note", per real feedback. The row
        # deliberately doesn't reset when the column changes: scanning
        # left/right at a fixed pitch height is the natural way to lay
        # down a melody line one column at a time.
        if key == "LEFT":
            self.cursor_col = max(0, self.cursor_col - 1)
        elif key == "RIGHT":
            self.cursor_col = min(len(SAMPLE_SCORE) - 1, self.cursor_col + 1)
        elif key == "UP":
            self.cursor_row = min(TOP_ROW, self.cursor_row + 1)
        elif key == "DOWN":
            self.cursor_row = max(BOTTOM_ROW, self.cursor_row - 1)
        elif key == " ":
            self._toggle_note_at_freeview_cursor()

    def _toggle_note_at_freeview_cursor(self):
        """Space in freeview: place a (natural) note at the highlighted
        staff row if nothing's there, or remove the one that is --
        "place a note or remove a note on the highlighted place", per
        real feedback. Refuses to remove a column's very last note (a
        column with zero notes isn't a meaningful state here)."""
        col = SAMPLE_SCORE[self.cursor_col]
        existing = next((i for i, (npc, noctv) in enumerate(col["notes"]) if staff_row(npc, noctv) == self.cursor_row),
                        None)
        if existing is not None:
            if len(col["notes"]) > 1:
                col["notes"].pop(existing)
        else:
            col["notes"].append(pitch_at_row(self.cursor_row))
            col["notes"] = _sort_by_pitch(col["notes"])
        col["chord_name"] = guess_chord_label(col["notes"])

    def _enter_subview(self):
        self.subview_col = self.cursor_col
        self.subview_notes = list(SAMPLE_SCORE[self.cursor_col]["notes"])
        self.subview_focus = "chord" if len(self.subview_notes) > 1 else "note"
        self.subview_tone_idx = min(self.cursor_note, len(self.subview_notes) - 1)
        self.subview_input_mode = None
        self.subview_typed = ""
        self.subview_error = None

    def _handle_subview_key(self, key):
        if self.subview_input_mode == "builder":
            self._handle_builder_key(key)
            return
        if key == "b":
            # Commit the working copy back into the real score buffer --
            # edits inside the subview are live, not a discardable draft;
            # "b" only ever means "I'm done looking at this column",
            # matching how leaving fill/wheel/tab's own live toggles never
            # asks "keep this change?" either.
            SAMPLE_SCORE[self.subview_col]["notes"] = self.subview_notes
            SAMPLE_SCORE[self.subview_col]["chord_name"] = guess_chord_label(self.subview_notes)
            self.subview_col = None
            return
        if key == "m":
            self.subview_focus = "note" if self.subview_focus == "chord" else "chord"
            return
        if key in ("t", "s"):
            self._enter_builder()
            return
        if self.subview_focus == "note":
            if key == "LEFT":
                self.subview_tone_idx = max(0, self.subview_tone_idx - 1)
            elif key == "RIGHT":
                self.subview_tone_idx = min(len(self.subview_notes) - 1, self.subview_tone_idx + 1)
            elif key in ("UP", "DOWN"):
                pc, octv = self.subview_notes[self.subview_tone_idx]
                self.subview_notes[self.subview_tone_idx] = transpose_semitone(pc, octv, +1 if key == "UP" else -1)
        else:  # whole-chord focus
            if key == "UP":
                self.subview_notes = invert_up(self.subview_notes)
            elif key == "DOWN":
                self.subview_notes = invert_down(self.subview_notes)

    def _enter_builder(self):
        """Chord builder: five independently spinnable 'reels' (root,
        a whole-quality preset shortcut, then third/fifth/seventh --
        each *part* of the chord on its own reel), slot-machine style --
        Left/Right picks which reel has focus, Up/Down spins it, and
        typing jumps the focused reel straight to a matching value.
        Every change applies live (no separate confirm step), so the
        column's notes/preview update as you spin. Real feedback:
        "more columns... assigned to the different parts of the chord" --
        third/fifth/seventh let you build a chord tone-by-tone (including
        ones no preset covers, e.g. a plain 5th with no 3rd at all, or a
        Sus2#5) instead of only ever picking a whole named quality; the
        quality reel stays as a fast preset that fills all three at once
        when you spin/type it, matching the earlier ask to keep the
        typed-symbol/scroll-a-list ergonomics too."""
        self.subview_input_mode = "builder"
        self.subview_typed = ""
        self.subview_builder_slot = 0
        self.subview_builder_quality_idx = 0  # cosmetic "last preset used" pointer only
        notes = _sort_by_pitch(self.subview_notes)
        root_pc = notes[0][0] if notes else 0
        self.subview_builder_root_idx = ROOT_REEL.index(root_pc)
        relative = sorted((pc - root_pc) % 12 for pc, _ in notes)
        self._sync_parts_from_intervals(relative)

    def _sync_parts_from_intervals(self, intervals):
        """Classify a chord's own relative-to-root intervals into which
        reel each belongs on (2-5 semitones -> third-ish, 6-8 -> fifth,
        9-11 -> seventh) -- used both to seed the reels from an existing
        column's notes on entry, and to fill them from a quality preset."""
        third_iv = next((iv for iv in intervals if iv in (2, 3, 4, 5)), None)
        fifth_iv = next((iv for iv in intervals if iv in (6, 7, 8)), None)
        seventh_iv = next((iv for iv in intervals if iv in (9, 10, 11)), None)
        self.subview_builder_third_idx = _option_index_for_interval(THIRD_OPTIONS, third_iv)
        self.subview_builder_fifth_idx = _option_index_for_interval(FIFTH_OPTIONS, fifth_iv)
        self.subview_builder_seventh_idx = _option_index_for_interval(SEVENTH_OPTIONS, seventh_iv)

    def _builder_rebuild_notes(self):
        root_pc = ROOT_REEL[self.subview_builder_root_idx]
        third_iv = THIRD_OPTIONS[self.subview_builder_third_idx][1]
        fifth_iv = FIFTH_OPTIONS[self.subview_builder_fifth_idx][1]
        seventh_iv = SEVENTH_OPTIONS[self.subview_builder_seventh_idx][1]
        intervals = [0] + [iv for iv in (third_iv, fifth_iv, seventh_iv) if iv is not None]
        base_octave = min(o for _, o in self.subview_notes) if self.subview_notes else 4
        self.subview_notes = build_chord(root_pc, intervals, base_octave)

    # Per-slot (reel index -> option list, current-index attr name) for
    # every reel except root (pitch-class based, handled separately) and
    # quality (a whole-preset shortcut, also handled separately since
    # picking one fans out to three other reels at once).
    _DEGREE_SLOTS = {
        2: ("subview_builder_third_idx", THIRD_OPTIONS),
        3: ("subview_builder_fifth_idx", FIFTH_OPTIONS),
        4: ("subview_builder_seventh_idx", SEVENTH_OPTIONS),
    }

    def _handle_builder_key(self, key):
        if key == "b":
            self.subview_input_mode = None
            self.subview_typed = ""
            return
        if key == "LEFT":
            self.subview_builder_slot = (self.subview_builder_slot - 1) % len(BUILDER_SLOTS)
            self.subview_typed = ""
            return
        if key == "RIGHT":
            self.subview_builder_slot = (self.subview_builder_slot + 1) % len(BUILDER_SLOTS)
            self.subview_typed = ""
            return
        if key in ("UP", "DOWN"):
            self._spin(key == "UP")
            return
        if key in ("\x7f", "\x08"):
            self.subview_typed = self.subview_typed[:-1]
            return
        if key in ("\r", "\n"):
            self._builder_force_commit()
            return
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            self._builder_type(key)

    def _spin(self, forward):
        slot = self.subview_builder_slot
        step = 1 if forward else -1
        if slot == 0:
            self.subview_builder_root_idx = (self.subview_builder_root_idx + step) % len(ROOT_REEL)
        elif slot == 1:
            self._apply_quality_index((self.subview_builder_quality_idx + step) % len(CHORD_QUALITIES))
            return  # _apply_quality_index already rebuilds
        else:
            attr, options = self._DEGREE_SLOTS[slot]
            setattr(self, attr, (getattr(self, attr) + step) % len(options))
        self._builder_rebuild_notes()

    def _builder_type(self, ch):
        slot = self.subview_builder_slot
        if slot == 0:
            self._root_typeahead(ch)
        elif slot == 1:
            self._alias_typeahead(ch, QUALITY_ALIASES, self._apply_quality)
        else:
            _attr, options = self._DEGREE_SLOTS[slot]
            alias_map = {tok: tok for tok, _iv, _lbl in options}
            self._alias_typeahead(ch, alias_map, lambda tok: self._apply_degree(slot, tok))

    def _builder_force_commit(self):
        slot = self.subview_builder_slot
        if slot == 1 and self.subview_typed in QUALITY_ALIASES:
            self._apply_quality(QUALITY_ALIASES[self.subview_typed])
        elif slot in self._DEGREE_SLOTS:
            _attr, options = self._DEGREE_SLOTS[slot]
            valid = {tok for tok, _iv, _lbl in options}
            if self.subview_typed in valid:
                self._apply_degree(slot, self.subview_typed)

    def _root_typeahead(self, ch):
        # Single keystroke each: a natural letter jumps the reel straight
        # to it; '#'/'b' right after nudges the *current* selection one
        # semitone chromatically (independent of the reel's own
        # fifths-order spin direction) -- so "F" then "#" reaches F#
        # without any multi-character buffering/lookahead ambiguity (an
        # earlier version tried to buffer "F#" as one token and broke:
        # "F" alone is *also* a valid root, so it committed and reset
        # before "#" ever arrived).
        if ch in "#b":
            pc = ROOT_REEL[self.subview_builder_root_idx]
            new_pc = (pc + (1 if ch == "#" else -1)) % 12
            self.subview_builder_root_idx = ROOT_REEL.index(new_pc)
            self._builder_rebuild_notes()
            return
        letter = ch.upper()
        if letter in ALL_NOTE_SPELLINGS:
            self.subview_builder_root_idx = ROOT_REEL.index(ALL_NOTE_SPELLINGS[letter])
            self._builder_rebuild_notes()

    def _alias_typeahead(self, ch, alias_map, apply_fn):
        # Shared by the quality reel and every third/fifth/seventh reel:
        # grows the buffer one key at a time, auto-committing the instant
        # it's an exact match that nothing longer could still extend
        # (e.g. "7", "dim", "b5") -- but holding off on a match that's a
        # strict prefix of a longer one still reachable (e.g. "m" could
        # become "maj"/"min"/"m7"/"min7"). An invalid continuation is
        # simply ignored rather than accepted and dead-ended. Enter
        # (_builder_force_commit) force-commits a still-waiting exact
        # match if you don't want to keep typing toward the longer one.
        candidate = self.subview_typed + ch
        extendable = any(k.startswith(candidate) for k in alias_map)
        if not extendable:
            return  # dead end -- ignore this keystroke, keep the prior buffer
        self.subview_typed = candidate
        if candidate in alias_map and not any(k != candidate and k.startswith(candidate) for k in alias_map):
            apply_fn(alias_map[candidate])
            self.subview_typed = ""

    def _apply_quality(self, quality):
        self._apply_quality_index(next(i for i, (q, _iv, _lbl) in enumerate(CHORD_QUALITIES) if q == quality))

    def _apply_quality_index(self, idx):
        self.subview_builder_quality_idx = idx
        _quality, intervals, _label = CHORD_QUALITIES[idx]
        self._sync_parts_from_intervals(intervals)
        self.subview_typed = ""
        self._builder_rebuild_notes()

    def _apply_degree(self, slot, token):
        attr, options = self._DEGREE_SLOTS[slot]
        setattr(self, attr, next(i for i, (tok, _iv, _lbl) in enumerate(options) if tok == token))
        self.subview_typed = ""
        self._builder_rebuild_notes()

    def render(self, width_budget):
        if self.subview_col is not None:
            return self._render_subview()
        col = SAMPLE_SCORE[self.cursor_col]
        lines = []
        if self.chords_only:
            # Lead-sheet mode: one label per column, no staff at all --
            # this is the "hide full notation detail" density lever.
            row = []
            for i, c in enumerate(SAMPLE_SCORE):
                label = c["chord_name"] or (NOTE_NAMES_FIFTHS[c["notes"][0][0]] if len(c["notes"]) == 1 else "?")
                row.append(cell(label, 6, rgb=(200, 200, 200), inverted=(i == self.cursor_col)))
            lines.append("".join(row))
        else:
            col_width, show_letter, show_octave, show_duration = ZOOM_LEVELS[self.zoom_level]
            for row in range(TOP_ROW, BOTTOM_ROW - 1, -1):
                clef = " "
                if row == TREBLE_CLEF_ROW:
                    clef = "\U0001D11E"
                elif row == BASS_CLEF_ROW:
                    clef = "\U0001D122"
                bg = LEDGER_CHAR if row in STAFF_LINE_ROWS else " "
                cells = []
                for ci, c in enumerate(SAMPLE_SCORE):
                    is_current_col = ci == self.cursor_col
                    text = bg * col_width
                    found = False
                    for ni, (npc, noctv) in enumerate(c["notes"]):
                        if staff_row(npc, noctv) == row:
                            text = notehead_text(npc, with_letter=show_letter, octave=noctv if show_octave else None)
                            if show_duration:
                                text += _DURATION_ABBR.get(c["duration"], c["duration"][:2])
                            found = True
                            if self.view_mode == "freeview":
                                inverted = is_current_col and row == self.cursor_row
                            else:
                                inverted = is_current_col and ni == self.cursor_note
                            cells.append(cell(text, col_width, rgb=note_rgb(npc, noctv), inverted=inverted))
                            break
                    if not found:
                        if self.view_mode == "freeview" and is_current_col and row == self.cursor_row:
                            # Free cursor sitting on a row with no note --
                            # still needs to be visible so placement has a
                            # target you can actually see.
                            cells.append(cell("+", col_width, rgb=(180, 180, 180), inverted=True))
                        else:
                            cells.append(bg * col_width)
                lines.append(f"{clef} " + "".join(cells))
        lines.append("")
        lines.append(f"mode: {self.view_mode} -- {VIEW_MODE_HELP[self.view_mode]}")
        if self.view_mode == "freeview":
            pc, octv = pitch_at_row(self.cursor_row)
            occupied = any(staff_row(npc, noctv) == self.cursor_row for npc, noctv in col["notes"])
            status = "note here -- Space removes it" if occupied else "empty -- Space places a note"
            lines.append(f"cursor: {NOTE_NAMES_FIFTHS[pc]}{octv} (row {self.cursor_row}) -- {status}")
        else:
            pc, octv = col["notes"][min(self.cursor_note, len(col["notes"]) - 1)]
            lines.append(f"cursor: {NOTE_NAMES_FIFTHS[pc]}{octv}  ({col['duration']}"
                          f"{', chord ' + col['chord_name'] if col['chord_name'] else ''})")
        return lines

    def _render_subview(self):
        """Column drill-in editor -- a dedicated small staff for just this
        one column's notes, entered with Enter from the main view (real
        user feedback: 'take a column and go into another view where I
        can edit the place of a note up and down but also add a whole
        chord by typing it or scrolling through a chord selector, and
        move a whole chord through its inversions'). Edits mutate
        self.subview_notes live; 'b' commits them back into SAMPLE_SCORE."""
        lines = [f"{BOLD}-- editing column {self.subview_col + 1} --{RESET}"]
        notes = _sort_by_pitch(self.subview_notes)
        for row in range(TOP_ROW, BOTTOM_ROW - 1, -1):
            clef = " "
            if row == TREBLE_CLEF_ROW:
                clef = "\U0001D11E"
            elif row == BASS_CLEF_ROW:
                clef = "\U0001D122"
            bg = LEDGER_CHAR if row in STAFF_LINE_ROWS else " "
            text = bg * 6
            for ni, (npc, noctv) in enumerate(notes):
                if staff_row(npc, noctv) == row:
                    label = notehead_text(npc, with_letter=True, octave=noctv)
                    highlighted = self.subview_focus == "chord" or ni == min(self.subview_tone_idx, len(notes) - 1)
                    text = cell(label, 6, rgb=note_rgb(npc, noctv), inverted=highlighted)
                    break
            lines.append(f"{clef} {text}")
        lines.append("")
        label = guess_chord_label(self.subview_notes) or "(single note)"
        lines.append(f"notes: {', '.join(f'{NOTE_NAMES_FIFTHS[p]}{o}' for p, o in notes)}   -- recognized as: {label}")
        if self.subview_input_mode == "builder":
            lines.append("")
            lines.extend(self._render_builder_reels())
        return lines

    def _render_builder_reels(self):
        """Five slot-machine-style reels side by side -- root (circle of
        fifths), a whole-quality preset shortcut (grouped by family), then
        third/fifth/seventh (each chord tone built independently). Each
        shows its immediate neighbors above/below the current selection so
        spinning feels continuous; the focused reel's current value is
        bracketed. Typing jumps any reel straight to a match without
        needing Enter (Enter force-commits an still-ambiguous typed
        prefix)."""
        window = 1
        headers = ["ROOT", "QUALITY", "3RD", "5TH", "7TH"]
        widths = [10, 16, 12, 12, 12]

        def items_for(slot):
            if slot == 0:
                return [NOTE_NAMES_FIFTHS[ROOT_REEL[(self.subview_builder_root_idx + off) % len(ROOT_REEL)]]
                        for off in range(-window, window + 1)]
            if slot == 1:
                return [CHORD_QUALITIES[(self.subview_builder_quality_idx + off) % len(CHORD_QUALITIES)][2]
                        for off in range(-window, window + 1)]
            attr, options = self._DEGREE_SLOTS[slot]
            idx = getattr(self, attr)
            return [options[(idx + off) % len(options)][2] for off in range(-window, window + 1)]

        columns = [items_for(slot) for slot in range(len(BUILDER_SLOTS))]
        lines = [" ".join(pad_center(h, w) for h, w in zip(headers, widths))]
        for row in range(2 * window + 1):
            centered = row == window
            cells = []
            for slot, (items, width) in enumerate(zip(columns, widths)):
                text = items[row]
                focused = centered and self.subview_builder_slot == slot
                text = f"»{text}«" if focused else text
                style = BOLD if centered else ""
                cells.append(f"{style}{pad_center(text, width)}{RESET}")
            lines.append(" ".join(cells))
        return lines


# ---------------------------------------------------------------------------
# Variant B: tracker-style pitch/time grid for editing, with a compact
# read-only staff preview strip underneath -- editing representation and
# display representation deliberately split into two views.

class VariantB:
    name = "B -- pitch/time grid + staff preview strip"
    hint = "Left/Right: move time | Up/Down: move pitch row | c: chords-only | +/-: zoom rows"

    def __init__(self):
        pitches = sorted({note for c in SAMPLE_SCORE for note in c["notes"]},
                          key=lambda pn: (pn[1], pn[0]), reverse=True)
        self.pitches = pitches
        self.cursor_col = 0
        self.cursor_row = 0
        self.chords_only = False
        self.row_span = 6

    def handle_key(self, key):
        if key == "LEFT":
            self.cursor_col = max(0, self.cursor_col - 1)
        elif key == "RIGHT":
            self.cursor_col = min(len(SAMPLE_SCORE) - 1, self.cursor_col + 1)
        elif key == "UP":
            self.cursor_row = max(0, self.cursor_row - 1)
        elif key == "DOWN":
            self.cursor_row = min(len(self.pitches) - 1, self.cursor_row + 1)
        elif key == "c":
            self.chords_only = not self.chords_only
        elif key == "+":
            self.row_span = min(len(self.pitches), self.row_span + 2)
        elif key == "-":
            self.row_span = max(3, self.row_span - 2)

    def render(self, width_budget):
        lines = []
        if self.chords_only:
            row = []
            for i, c in enumerate(SAMPLE_SCORE):
                label = c["chord_name"] or (NOTE_NAMES_FIFTHS[c["notes"][0][0]] if len(c["notes"]) == 1 else "?")
                row.append(cell(label, 6, rgb=(200, 200, 200), inverted=(i == self.cursor_col)))
            lines.append("".join(row))
        else:
            start = max(0, min(self.cursor_row - self.row_span // 2, len(self.pitches) - self.row_span))
            visible = list(enumerate(self.pitches))[start:start + self.row_span]
            for row_i, (pc, octv) in visible:
                label = pad_center(f"{NOTE_NAMES_FIFTHS[pc]}{octv}", 4)
                cells = []
                for ci, c in enumerate(SAMPLE_SCORE):
                    sounds = (pc, octv) in c["notes"]
                    text = "██" if sounds else "··"
                    cells.append(cell(text, 4, rgb=note_rgb(pc, octv) if sounds else (70, 70, 70),
                                       inverted=(ci == self.cursor_col and row_i == self.cursor_row)))
                lines.append(f"{label}| " + "".join(cells))
        lines.append("")
        lines.append("preview:  " + "".join(
            cell(notehead_text(c["notes"][0][0]), 3, rgb=note_rgb(*c["notes"][0]),
                 inverted=(i == self.cursor_col))
            for i, c in enumerate(SAMPLE_SCORE)))
        return lines


# ---------------------------------------------------------------------------
# Variant C: command/list-driven editing -- no spatial glyph cursor at
# all in the default view; the score is a numbered text buffer, and a
# separate toggle renders a read-only staff preview on demand.

class VariantC:
    name = "C -- numbered list buffer, staff preview on demand"
    hint = "Up/Down: move line | c: chords-only | p: toggle staff preview"

    def __init__(self):
        self.cursor_col = 0
        self.chords_only = False
        self.show_preview = False

    def handle_key(self, key):
        if key in ("UP", "LEFT"):
            self.cursor_col = max(0, self.cursor_col - 1)
        elif key in ("DOWN", "RIGHT"):
            self.cursor_col = min(len(SAMPLE_SCORE) - 1, self.cursor_col + 1)
        elif key == "c":
            self.chords_only = not self.chords_only
        elif key == "p":
            self.show_preview = not self.show_preview

    def render(self, width_budget):
        lines = []
        for i, c in enumerate(SAMPLE_SCORE):
            marker = "> " if i == self.cursor_col else "  "
            if self.chords_only and c["chord_name"]:
                desc = f"[chord] {c['chord_name']}"
            else:
                parts = []
                for pc, octv in c["notes"]:
                    parts.append(f"{ansi_fg(note_rgb(pc, octv))}{NOTE_NAMES_FIFTHS[pc]}{octv}{RESET}")
                desc = " ".join(parts)
                if c["chord_name"]:
                    desc += f"  ({c['chord_name']})"
            style = BOLD if i == self.cursor_col else ""
            lines.append(f"{style}{marker}{i + 1:>2}: {desc}  -- {c['duration']}{RESET}")
        if self.show_preview:
            lines.append("")
            lines.append("staff preview:")
            c = SAMPLE_SCORE[self.cursor_col]
            for pc, octv in c["notes"]:
                row = staff_row(pc, octv)
                ledger_note = ", ledger" if ledger_rows(row) else ""
                lines.append(f"  {NOTE_NAMES_FIFTHS[pc]}{octv}: staff row {row}{ledger_note} "
                             f"({ansi_fg(note_rgb(pc, octv))}{notehead_text(pc)}{RESET})")
        return lines


VARIANTS = [VariantA, VariantB, VariantC]


def main():
    raw = RawKeys()
    if not raw.active:
        print("This prototype needs a real interactive TTY -- run it directly in a terminal.")
        return
    variants = [cls() for cls in VARIANTS]
    current = 0
    sys.stdout.write("\033[?25l")
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(100, 40))
            variant = variants[current]
            body = variant.render(size.columns)
            sys.stdout.write("\033[H\033[2J")
            sys.stdout.write(f"{BOLD}Score editor cursor/view-mode prototype{RESET}  (wayfinder #87)\n")
            sys.stdout.write(f"{BOLD}{variant.name}{RESET}\n\n")
            sys.stdout.write("\n".join(body) + "\n\n")
            sys.stdout.write(f"{variant.hint}\n")
            # Typing a chord symbol takes every keystroke literally -- 'q'/'v'
            # would otherwise be stolen by the global quit/cycle keys before
            # ever reaching the chord-name buffer.
            typing = getattr(variant, "subview_input_mode", None) == "typing"
            if not typing:
                sys.stdout.write("v: cycle variant | q: quit\n")
            sys.stdout.flush()

            key = raw.poll()
            if key is None:
                time.sleep(0.03)
                continue
            if key == "\x03":
                break
            if not typing and key == "q":
                break
            if not typing and key == "v":
                current = (current + 1) % len(variants)
                continue
            variant.handle_key(key)
    finally:
        raw.restore()
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
