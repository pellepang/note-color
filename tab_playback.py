"""Frozen-buffer playback for the `tab` view (map #99, build ticket #121,
implementing decision #109): turning what is currently *on screen* into a
schedule of note events the sound engine can play.

This module is the pure half. It decides which of `TabDisplay`'s history
entries are in scope, expands each column's detected note stack, and
computes every note's onset time and length -- all of it a plain
data-in/data-out computation with no thread, no audio device and no
terminal anywhere in it. `main.py`'s `_handle_playback_key()` /
`_playback_worker()` own the timing thread and the `sound_engine.
SoundEngine` calls, the same "pure logic unit-tested, real I/O and
threading smoke-tested" split `rhythm_reanalysis.recompute()` already has
against main.py's own `R`-key wiring.

**Scope: the marked range if one is set, else every visible column
(#109 decision 2).** Identical to what #108 settled for the score
editor's own audition, so the same gesture means the same thing in both
places. "Every visible column" is not re-derived here: `select_columns()`
takes the very list `TabDisplay.visible_entries()` returns, which runs the
renderer's own width-budget walk, so playback can never disagree with
what the user is looking at.

**Chord mode plays the full detected stack (#109 decision 3).** Every
note in a column sounds, each with its own tracked `duration_class` --
the honest playback of what was actually detected. Re-voicing from the
matched chord *name* was rejected: it would sound better precisely by
concealing detection errors the user has every reason to hear. At up to
`CHORD_MAX_NOTES` per column this sits far inside the polyphony budget
(#100).

**Timing comes from two different sources, deliberately.** A note's
*onset* is its column's own recorded timestamp (`TabEntry.t`), so
playback reproduces the real gaps between what was played -- rushed
passages and pauses survive, exactly as `virtualnote replay` reproduces a
session's pacing rather than flattening it to a metronome. A note's
*length* is its measured `duration_class` converted against a tempo,
because that is the only length this pipeline ever measured; a
column-to-column gap is not a duration (the next column may be silence,
or a note an octave away in the same voice).

A note whose duration never finalized (still sounding when the view was
frozen, or superseded by a re-attack) has `duration_class is None` and
falls back to `duration_tracker.DEFAULT_DURATION_CLASS`, the same
resolution `TabDisplay.render()` already applies to it -- so what is
heard matches the glyph that is drawn.
"""

from collections import namedtuple

import config
from duration_tracker import BEATS_BY_DURATION_CLASS, DEFAULT_DURATION_CLASS
from sound_engine import midi_pitch

PlaybackNote = namedtuple(
    "PlaybackNote", "start_seconds pitch pitch_class octave duration_seconds velocity"
)
# One scheduled note-on plus the note-off arrangement that goes with it
# (#105 decision 1: the engine has no duration-carrying primitive, so a
# caller that knows a length arranges its own note-off).
# `start_seconds` is relative to the start of playback, not to the
# TabDisplay's own clock. `pitch_class`/`octave` ride along beside the
# MIDI `pitch` purely so a caller (or a test) can talk about a note in
# this repo's own terms without converting back.


def select_columns(visible_entries, all_entries=None, mark_range=None):
    """The columns in scope for one playback press, oldest first.

    With `mark_range` set (both `[`/`]` markers placed -- see
    `main._mark_range()`), the scope is every note column in
    `all_entries` whose timestamp falls inside that inclusive `[lo, hi]`
    window, *regardless* of whether it is currently on screen: a marked
    range is an explicit statement about a region of history, and the
    user may well have scrolled elsewhere since placing it. With no
    marks, the scope is `visible_entries` -- literally what is being
    looked at.

    Barline columns and note columns holding nothing playable (a `fix`
    scroll-mode column pushed during silence carries a single note dict
    with `pitch_class is None`) are dropped either way: they are columns
    on screen, but there is no note in them to sound."""
    if mark_range is not None:
        lo, hi = mark_range
        source = list(all_entries if all_entries is not None else visible_entries)
        source = [e for e in source if lo <= getattr(e, "t", 0.0) <= hi]
    else:
        source = list(visible_entries)
    return [e for e in source if playable_notes(e)]


def playable_notes(entry):
    """The note dicts in `entry` that can actually sound -- empty for a
    `BarlineEntry` (no `notes` attribute at all) and for a silence column
    (`pitch_class is None`), which is what makes `select_columns()`'s
    filter a one-liner."""
    notes = getattr(entry, "notes", None)
    if not notes:
        return []
    return [n for n in notes if n.get("pitch_class") is not None]


def note_duration_seconds(duration_class, bpm):
    """How long one note sounds, in seconds. `duration_class` of `None`
    (never finalized) resolves to `duration_tracker.
    DEFAULT_DURATION_CLASS`, matching what `TabDisplay.render()` already
    draws for such a note. A missing/absurd `bpm` falls back to
    `config.TAB_PLAYBACK_DEFAULT_BPM`, and the result is clamped into
    `[TAB_PLAYBACK_MIN_NOTE_SECONDS, TAB_PLAYBACK_MAX_NOTE_SECONDS]` so
    neither a very fast nor a very slow tempo estimate can turn a note
    into a click or a drone."""
    beats = BEATS_BY_DURATION_CLASS.get(
        duration_class if duration_class is not None else DEFAULT_DURATION_CLASS,
        BEATS_BY_DURATION_CLASS[DEFAULT_DURATION_CLASS],
    )
    if not bpm or bpm <= 0:
        bpm = config.TAB_PLAYBACK_DEFAULT_BPM
    seconds = beats * 60.0 / bpm
    return min(max(seconds, config.TAB_PLAYBACK_MIN_NOTE_SECONDS),
               config.TAB_PLAYBACK_MAX_NOTE_SECONDS)


def build_schedule(columns, bpm=None, velocity=None):
    """`columns` (oldest first, as `select_columns()` returns them) -> a
    flat, time-ordered list of `PlaybackNote`s.

    Onsets are re-based so the first column starts at 0.0 -- playback
    begins immediately rather than after however many seconds of
    `TabDisplay`'s own clock happened to precede the scope. Within one
    column every note shares that onset (they were detected as
    simultaneous) and each carries its own measured duration, which is
    the full-detected-stack behaviour #109 decision 3 settled on.

    `velocity` defaults to `config.TAB_PLAYBACK_VELOCITY`: this pipeline
    never measured a per-note attack strength, so every note is played at
    one honest fixed level rather than a dynamic invented from `rms`."""
    columns = list(columns)
    if not columns:
        return []
    velocity = config.TAB_PLAYBACK_VELOCITY if velocity is None else velocity
    origin = columns[0].t
    schedule = []
    for entry in columns:
        start = max(entry.t - origin, 0.0)
        for note in playable_notes(entry):
            pitch_class, octave = note["pitch_class"], note["octave"]
            schedule.append(PlaybackNote(
                start_seconds=start,
                pitch=midi_pitch(pitch_class, octave),
                pitch_class=pitch_class,
                octave=octave,
                duration_seconds=note_duration_seconds(note.get("duration_class"), bpm),
                velocity=velocity,
            ))
    return schedule


def schedule_duration(schedule):
    """Wall-clock length of a whole schedule: the last note's own end, not
    just its onset, so a caller waiting for playback to finish doesn't cut
    the final note off. 0.0 for an empty schedule."""
    if not schedule:
        return 0.0
    return max(n.start_seconds + n.duration_seconds for n in schedule)
