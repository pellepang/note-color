"""Score editor (issue #98): the Chord builder screen -- opened by Enter
on a column in the main editor view (score_editor_display.py), closed by
`b` (chord_builder_exit). Five independently spinnable/typeahead-able
Reels (root / quality / 3rd / 5th / 7th) build a chord tone-by-tone;
every spin/typeahead applies live to this screen's own working
`BuilderState` (no separate confirm step, matching #87's validated reel
mechanics), and `chord_builder_exit` commits the resulting notes back into
the real column -- mirroring the prototype's own "b always means I'm
done, not a discardable draft" convention (see
prototypes/score-editor-cursor-concept/README.md).

Reuses that prototype's reel *mechanics* (root ordered around the circle
of fifths via color_map.fifths_index(), typeahead matching, third/fifth/
seventh degree tables) per issue #98's spec -- #87's own non-decision was
about that prototype's *keybind vocabulary* (settled instead by #88), not
its reel math, which this module reimplements against
score_editor_state.EditorNote/EditorColumn rather than the prototype's
own plain (pitch_class, octave) tuples.

Not this app's real ~360-template chord_templates.py dictionary -- that
module only ever goes chroma -> name (recognizing a chord from live
audio); this is the reverse direction (name -> notes, for constructing
one by hand), same reasoning the prototype's own README gives for why it
needed its own small table.

Per this repo's test convention: every pure stepping/typeahead/state
function below is unit-tested (tests/test_chord_builder_display.py);
`render()`'s actual screen layout is smoke-tested manually only, same
convention as every other run_terminal_*/run_*_screen loop in this
codebase.
"""

import shutil
import sys

from color_map import NOTE_NAMES_FIFTHS, fifths_index
from score_editor_state import EditorNote

BUILDER_SLOTS = ["root", "quality", "third", "fifth", "seventh"]

# The ROOT reel is ordered around the circle of fifths (C, G, D, A, E, B,
# F#, Db, Ab, Eb, Bb, F), the same order the wheel view and every
# fifths-scheme coloring already use -- reaching a closely-related key
# never means spinning through the whole reel.
ROOT_REEL = sorted(range(12), key=fifths_index)

# (token, interval-in-semitones-from-root-or-None, display label) -- index 0
# is always "(none)" so an empty slot's default is "not part of the
# chord," not an arbitrary interval. Mirrors the prototype's own tables.
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

# quality preset key -> (display label, third_token, fifth_token,
# seventh_token) -- filled into the three degree reels in one move
# (QUALITY's "fast preset shortcut" mechanic). Doesn't touch root.
QUALITY_PRESETS = [
    ("maj", "Major", "3", "5", "none"),
    ("min", "Minor", "b3", "5", "none"),
    ("dim", "Diminished", "b3", "b5", "none"),
    ("aug", "Augmented", "3", "#5", "none"),
    ("dom7", "Dominant 7th", "3", "5", "b7"),
    ("maj7", "Major 7th", "3", "5", "7"),
    ("min7", "Minor 7th", "b3", "5", "b7"),
    ("dim7", "Diminished 7th", "b3", "b5", "dim7"),
    ("sus2", "Sus2", "sus2", "5", "none"),
    ("sus4", "Sus4", "sus4", "5", "none"),
]

QUALITY_ALIASES = {
    "": "maj", "maj": "maj", "M": "maj",
    "m": "min", "min": "min", "-": "min",
    "7": "dom7", "dom7": "dom7",
    "maj7": "maj7", "M7": "maj7",
    "m7": "min7", "min7": "min7", "-7": "min7",
    "dim": "dim", "o": "dim",
    "dim7": "dim7", "o7": "dim7",
    "aug": "aug", "+": "aug",
    "sus2": "sus2", "sus4": "sus4",
}

NATURAL_LETTER_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _option_index_for_token(options, token):
    return next((i for i, (tok, _iv, _lbl) in enumerate(options) if tok == token), 0)


def _label_for(options, token):
    return next((lbl for tok, _iv, lbl in options if tok == token), token)


class BuilderState:
    """The Chord builder's own working copy of one column's chord --
    edits land here, not the real `EditorColumn`, until
    `notes_from_state()`'s result is committed by the caller on
    `chord_builder_exit` (see the module docstring). `root_just_jumped`
    backs the ROOT reel's two-keystroke typeahead (a letter jump, then an
    optional immediately-following '#'/'b' nudge -- see
    `step_root_typeahead()`); `typed` is the alias-typeahead buffer shared
    by the quality/degree reels (see `step_alias_typeahead()`) -- cleared
    whenever focus moves to a different reel."""

    def __init__(self, root_pc, third_token, fifth_token, seventh_token, base_octave):
        self.slot = 0  # index into BUILDER_SLOTS
        self.root_pc = root_pc
        self.third_token = third_token
        self.fifth_token = fifth_token
        self.seventh_token = seventh_token
        self.base_octave = base_octave
        self.root_just_jumped = False
        self.typed = ""


def state_from_column(column, default_octave=4):
    """Seeds a BuilderState from a column's current notes -- best-effort
    reverse-lookup, not a stored/round-tripped state: root is the lowest
    note's pitch class, each degree reel starts at whichever of its known
    intervals (if any) is actually present above that root, "(none)"
    otherwise. A column with no notes seeds root=C, every degree
    "(none)", base_octave=default_octave -- an empty starting point, not
    a guess."""
    if not column.notes:
        return BuilderState(0, "none", "none", "none", default_octave)
    lowest = min(column.notes, key=lambda n: n.octave * 12 + n.pitch_class)
    root_pc = lowest.pitch_class
    intervals_present = {(n.pitch_class - root_pc) % 12 for n in column.notes}

    def _match(options):
        for tok, iv, _lbl in options[1:]:
            if iv is not None and iv in intervals_present:
                return tok
        return "none"

    return BuilderState(root_pc, _match(THIRD_OPTIONS), _match(FIFTH_OPTIONS), _match(SEVENTH_OPTIONS),
                         lowest.octave)


def notes_from_state(state):
    """The BuilderState's current reel settings -> a list[EditorNote]:
    root plus whichever degree reels aren't "(none)" -- the actual
    construction `chord_builder_exit` commits back to the column."""
    notes = [EditorNote(pitch_class=state.root_pc, octave=state.base_octave)]
    for token, options in ((state.third_token, THIRD_OPTIONS), (state.fifth_token, FIFTH_OPTIONS),
                           (state.seventh_token, SEVENTH_OPTIONS)):
        interval = next((iv for tok, iv, _lbl in options if tok == token), None)
        if interval is not None:
            pc = (state.root_pc + interval) % 12
            octave = state.base_octave + (state.root_pc + interval) // 12
            notes.append(EditorNote(pitch_class=pc, octave=octave))
    return notes


def move_slot(slot, delta):
    return (slot + delta) % len(BUILDER_SLOTS)


def spin_root(root_pc, delta):
    idx = ROOT_REEL.index(root_pc)
    return ROOT_REEL[(idx + delta) % len(ROOT_REEL)]


def spin_degree(token, options, delta):
    idx = _option_index_for_token(options, token)
    return options[(idx + delta) % len(options)][0]


def spin_quality(current_index, delta):
    """Steps QUALITY_PRESETS by delta, returning (new_index, preset_key) --
    the caller applies preset_key via `apply_quality_preset()`."""
    new_index = (current_index + delta) % len(QUALITY_PRESETS)
    return new_index, QUALITY_PRESETS[new_index][0]


def apply_quality_preset(state, preset_key):
    """Fills the third/fifth/seventh reels from a QUALITY_PRESETS entry
    keyed by `preset_key` (e.g. "maj7") -- a no-op (returns False) if
    `preset_key` isn't a real preset. Never touches `state.root_pc` --
    the quality reel/typeahead only ever drives the three degree reels."""
    for key, _label, third, fifth, seventh in QUALITY_PRESETS:
        if key == preset_key:
            state.third_token, state.fifth_token, state.seventh_token = third, fifth, seventh
            return True
    return False


def step_root_typeahead(root_pc, root_just_jumped, ch):
    """One keystroke's effect on the ROOT reel: an uppercase natural
    letter (A-G, i.e. Shift+letter on a real keyboard) jumps straight
    there; 'b' or '#' immediately following such a jump (root_just_jumped
    True) nudges the just-set root a semitone down/up instead of jumping
    again. Letter matching is deliberately exact-case, not
    case-insensitive like every other remappable action in this app --
    lowercase 'b' is the one letter that collides with the flat
    accidental symbol itself, so the only way to disambiguate "the letter
    B" from "flatten what I just picked" is case: 'B' (shifted) is the
    letter, 'b' (unshifted) is always the accidental. Any other keystroke
    is treated as a fresh (failed) jump attempt, clearing
    root_just_jumped. Returns (new_root_pc, new_root_just_jumped)."""
    if ch in NATURAL_LETTER_TO_PC:
        return NATURAL_LETTER_TO_PC[ch], True
    if root_just_jumped and ch == "#":
        return (root_pc + 1) % 12, False
    if root_just_jumped and ch == "b":
        return (root_pc - 1) % 12, False
    return root_pc, False


def step_alias_typeahead(buffer, ch, alias_map):
    """One keystroke's effect on a typed alias buffer -- used for both
    the QUALITY reel (`alias_map=QUALITY_ALIASES`) and, via
    `degree_alias_map()`, a degree reel's own token set treated as a
    trivial identity alias map. Returns (new_buffer, resolved_value):
    auto-commits (clearing the buffer) the instant `buffer + ch` is a
    real key AND no longer a strict prefix of any other real key (e.g.
    '7' commits immediately on QUALITY, since nothing else starts with
    '7'; 'm' keeps buffering, since 'm'/'maj'/'m7'/'min'/... are all
    still reachable). A keystroke that isn't a real key or a valid prefix
    of one resets the buffer to empty (a mistyped character can't
    permanently wedge the reel)."""
    candidate = buffer + ch
    is_exact = candidate in alias_map
    has_longer_match = any(k != candidate and k.startswith(candidate) for k in alias_map)
    if is_exact and not has_longer_match:
        return "", alias_map[candidate]
    if is_exact or has_longer_match:
        return candidate, None
    return "", None


def force_commit_alias(buffer, alias_map):
    """Enter's "force-commit whatever's currently an exact match" action
    -- returns the resolved value for an exact-but-still-ambiguous-as-a-
    prefix buffer (e.g. 'm' alone, resolving to 'min' even though 'maj'
    also starts with 'm'), or None if `buffer` isn't an exact key at
    all."""
    return alias_map.get(buffer)


def degree_alias_map(options):
    """A degree reel's own non-"(none)" tokens as a trivial identity
    alias map, so `step_alias_typeahead()`/`force_commit_alias()` work
    the same way for the 3rd/5th/7th reels as they do for QUALITY."""
    return {tok: tok for tok, _iv, _lbl in options if tok != "none"}


def render(state, quality_index, status):
    """Smoke-tested manually only, per this module's docstring."""
    lines = [
        "Chord builder",
        "Left/Right: switch reel  Up/Down: spin  type: jump  Enter: force-commit typed  b: done",
        "",
    ]
    root_label = NOTE_NAMES_FIFTHS[state.root_pc]
    quality_label = QUALITY_PRESETS[quality_index][1]
    rows_spec = [
        ("root", f"Root:            {root_label}"),
        ("quality", f"Quality preset:  {quality_label}  (shortcut -- fills 3rd/5th/7th below)"),
        ("third", f"3rd:             {_label_for(THIRD_OPTIONS, state.third_token)}"),
        ("fifth", f"5th:             {_label_for(FIFTH_OPTIONS, state.fifth_token)}"),
        ("seventh", f"7th:             {_label_for(SEVENTH_OPTIONS, state.seventh_token)}"),
    ]
    for slot_name, text in rows_spec:
        marker = "> " if BUILDER_SLOTS[state.slot] == slot_name else "  "
        line = f"{marker}{text}"
        if BUILDER_SLOTS[state.slot] == slot_name:
            line = f"\033[7m{line}\033[0m"
        lines.append(line)
    lines.append("")
    preview = ", ".join(f"{NOTE_NAMES_FIFTHS[n.pitch_class]}{n.octave}" for n in notes_from_state(state))
    lines.append(f"Notes: {preview}")
    if state.typed:
        lines.append(f"typing: {state.typed}")
    lines.append("")
    lines.append(status)

    size = shutil.get_terminal_size(fallback=(80, 24))
    out = ["\033[2J"]
    for i, line in enumerate(lines, start=1):
        out.append(f"\033[{i};1H\033[K{line[:size.columns]}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()
