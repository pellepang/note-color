"""Session log -> score editor, quantized on the way in (map #99, ticket
#122, decision #110).

Decision #110's third point: **capture raw, quantize at import.** The
`session_log_*.jsonl` a live view or the synth tool writes holds real,
unrounded onset times and durations; nothing rounds them on the way to
disk, because a rounded log cannot be un-rounded and a quantized capture
destroys the performance's real timing irreversibly. This module is where
the rounding finally happens -- at the moment a log becomes an
`EditorScore`, against a **selectable grid**, re-runnable at another
resolution against the same untouched log as many times as the user
likes.

That is a different thing from `score_writer.py`'s existing 32nd-note
offset quantization, which is not a musical judgement at all: it exists
because a real (non-quantized) onset produces a rest music21 cannot
express in MusicXML. This module quantizes *rhythm*, on purpose, where
the user can see the result and try again.

Two layers, split along this repo's usual line:

* `quantize_columns()` and the grid helpers are pure -- plain event
  dicts in, plain `(notes, duration_class)` columns out, no music21
  anywhere, directly unit-tested.
* `score_from_events()`/`import_log()` wrap that in an actual
  `score_editor_state.EditorScore`, importing that module (and therefore
  music21) locally so nothing here pays for music21 unless a score is
  genuinely being built.

Two properties of the editor's own model shape the output and are worth
stating plainly:

1. **A column's duration is when the next column starts.** `EditorScore`
   is a fixed sequence of columns, not a set of notes with independent
   onsets, so a played note that decays before the next one arrives
   becomes a note column followed by a **Rest column** for the remaining
   silence -- that is how the gap survives the trip.
2. **Only standard note values exist** (`duration_tracker.
   DURATION_CLASS_ORDER`, plain and dotted, no tuplets -- the same set
   `score_writer.py` restricts itself to). A quantized span of, say, five
   sixteenths has no exact name, so it snaps to the nearest one and the
   sequence drifts slightly against the grid. The alternative -- tie-split
   columns -- is a notation feature this editor does not have.

Simultaneous notes (a chord played with several keys down at once) land
in one column: after quantization they share an onset step, which is
exactly `session_player.group_columns()`'s own "same `t` is one column"
rule applied to a grid instead of to exact equality.
"""

from collections import namedtuple

import config
from duration_tracker import duration_class_for_beats

#: The selectable grids (decision #110 point 3), coarsest first, each as
#: the length of one grid step in beats (quarter notes). Deliberately no
#: triplet grids: `duration_tracker.DURATION_CLASS_ORDER` has no tuplet
#: values to write the result as, and `score_writer.py` has never emitted
#: one either (issue #62 deferred tuplet detection) -- offering a grid
#: whose output cannot be notated would be a worse lie than not offering
#: it.
GRID_CHOICES = [
    ("quarter", 1.0),
    ("eighth", 0.5),
    ("sixteenth", 0.25),
    ("thirtysecond", 0.125),
]

GRID_NAMES = [name for name, _ in GRID_CHOICES]

DEFAULT_GRID = config.IMPORT_DEFAULT_GRID

#: One imported column: `notes` is a list of `(pitch_class, octave)`
#: pairs (empty == a Rest), `duration_class` a
#: `duration_tracker.DURATION_CLASS_ORDER` name. Deliberately not
#: `score_editor_state.EditorColumn` -- that lives behind a music21
#: import, and everything in this module's pure half must stay callable
#: without one.
ImportedColumn = namedtuple("ImportedColumn", "notes duration_class")


def grid_beats(name):
    """Length of one grid step, in beats. An unknown name falls back to
    the default grid rather than raising -- same defaults-and-degradation
    posture `config_store.py` takes toward an unrecognized TOML value."""
    for grid_name, beats in GRID_CHOICES:
        if grid_name == name:
            return beats
    return grid_beats(DEFAULT_GRID) if name != DEFAULT_GRID else 0.25


def cycle_grid(name, delta):
    """Step the grid selection by `delta`, clamped at both ends rather
    than wrapping -- the same clamp-not-wrap convention
    `score_properties_display.spin_tempo()` uses for a bounded quantity,
    and here it also means the coarsest and finest grids are reachable by
    holding one direction without sailing past them."""
    try:
        index = GRID_NAMES.index(name)
    except ValueError:
        index = GRID_NAMES.index(DEFAULT_GRID)
    return GRID_NAMES[max(0, min(len(GRID_NAMES) - 1, index + int(delta)))]


def tempo_from_events(events, default=None):
    """The tempo to quantize against: the first real `bpm_estimate` any
    event carries, else `default`.

    A *detected* log has a live tempo estimate per note and that estimate
    is the honest reference for its own timing. A *played* log has none at
    all -- decision #110 writes `bpm_estimate` as null for a synth note
    precisely so no fictional figure ends up in the file -- and falls back
    to `config.PLAYED_NOTE_REFERENCE_BPM`, the same figure the recorder
    derived that note's `duration_class` against, so a replay's glyphs and
    an import's columns agree by construction."""
    if default is None:
        default = config.PLAYED_NOTE_REFERENCE_BPM
    for event in events:
        bpm = event.get("bpm_estimate")
        if bpm:
            return float(bpm)
    return float(default)


def _note_events(events):
    return [e for e in events if e.get("kind") != "barline"]


def quantize_columns(events, tempo_bpm, grid=DEFAULT_GRID):
    """Pure: session-log events -> a list of `ImportedColumn`.

    Onsets are snapped to the nearest grid step and events landing on the
    same step become one column. Each column's span runs to the next
    column's onset; when the notes in it stopped sounding earlier than
    that, the remainder becomes a Rest column so the silence survives (see
    the module docstring). The final column has no successor to measure
    against, so its own quantized duration is used.

    A span always rounds up to at least one grid step: a note shorter than
    the grid is still a note, and rounding it to zero would silently drop
    it."""
    notes = _note_events(events)
    if not notes:
        return []
    step_beats = grid_beats(grid)
    beats_per_second = float(tempo_bpm) / 60.0

    groups = {}
    for event in sorted(notes, key=lambda e: e.get("t", 0.0)):
        step = int(round((event.get("t", 0.0) * beats_per_second) / step_beats))
        groups.setdefault(max(0, step), []).append(event)

    steps = sorted(groups)
    columns = []
    if steps[0] > 0:
        columns.append(_rest(steps[0] * step_beats))

    for i, step in enumerate(steps):
        group = groups[step]
        sounded = max((e.get("duration_seconds") or 0.0) for e in group)
        sounded_steps = max(1, int(round((sounded * beats_per_second) / step_beats)))
        if i + 1 < len(steps):
            span_steps = steps[i + 1] - step
            note_steps = max(1, min(sounded_steps, span_steps))
        else:
            span_steps = note_steps = sounded_steps
        columns.append(ImportedColumn(
            notes=_pitches(group),
            duration_class=duration_class_for_beats(note_steps * step_beats),
        ))
        rest_steps = span_steps - note_steps
        if rest_steps > 0:
            columns.append(_rest(rest_steps * step_beats))
    return columns


def _rest(beats):
    return ImportedColumn(notes=[], duration_class=duration_class_for_beats(beats))


def _pitches(group):
    """`(pitch_class, octave)` per event in a column, de-duplicated with
    order preserved -- two log lines for the same pitch at the same
    quantized instant (a fast repeated note collapsed by the grid, or a
    detected note logged by both the mono and chord paths) are one
    notehead, not two stacked on the same staff row."""
    out = []
    for event in group:
        pair = (event["pc"], event["octave"])
        if pair not in out:
            out.append(pair)
    return out


def score_from_events(events, tempo_bpm=None, grid=DEFAULT_GRID,
                       time_signature=None, key_fifths=0):
    """A quantized `score_editor_state.EditorScore` from session-log
    events. `score_editor_state` is imported locally so this module's pure
    half stays free of music21, same convention `main.py` follows for
    every music21/librosa-backed feature.

    An empty (or note-less) log yields a blank score rather than an error
    or a zero-column one -- an editor can never have zero columns, and
    "the log had nothing in it" is a thing a user can see for themselves
    once the editor opens."""
    import score_editor_state as ses

    if tempo_bpm is None:
        tempo_bpm = tempo_from_events(events)
    columns = quantize_columns(events, tempo_bpm, grid)
    score = ses.new_blank_score()
    score.tempo_bpm = float(tempo_bpm)
    score.key_fifths = int(key_fifths)
    if time_signature is not None:
        score.time_signature = tuple(time_signature)
    if columns:
        score.columns = [
            ses.EditorColumn(
                notes=[ses.EditorNote(pitch_class=pc, octave=octave) for pc, octave in column.notes],
                duration_class=column.duration_class,
            )
            for column in columns
        ]
    return score


def import_log(path, tempo_bpm=None, grid=DEFAULT_GRID, time_signature=None, key_fifths=0):
    """`session_log_*.jsonl` path -> an `EditorScore`. Reads through
    `session_player.load_events()` rather than reimplementing JSONL
    parsing, the same reuse `stats_display.load_sessions()` already
    makes."""
    from session_player import load_events

    return score_from_events(load_events(path), tempo_bpm=tempo_bpm, grid=grid,
                              time_signature=time_signature, key_fifths=key_fifths)


def default_score_path(log_path):
    """Where an imported log's score is written by default: a `.musicxml`
    sibling of the log, same basename. Nothing is written here -- the
    editor's own `save` (`w`) does that, so an import that turns out badly
    at this grid costs nothing on disk and can simply be redone at
    another."""
    import os

    base = os.path.basename(log_path)
    stem = base[:-len(".jsonl")] if base.endswith(".jsonl") else base
    return os.path.join(os.path.dirname(os.path.abspath(log_path)), stem + ".musicxml")
