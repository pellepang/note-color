"""SessionRecorder -- appends a JSON-Lines session log from a stream of
RenderItem-shaped hop data (real `main.RenderItem` namedtuples, or plain
dicts carrying the same field names -- see `_get()` below).

This is Concept E from docs/research/notation-and-feature-ideas.md, made
concrete and runnable. See this directory's README.md for the full schema
write-up and the "System architecture reasoning" section on where a real
integration would hook into main.py's analysis_loop().

--- What counts as a "notable" hop ---

Logging literally every hop (~43/sec at this app's BLOCK_SIZE/SAMPLE_RATE)
would produce a firehose of mostly-redundant "still holding the same note"
lines -- useless for a human or a downstream tool to read, and needless
disk I/O on Pi-class hardware. Instead, SessionRecorder writes one line per
*finalized* note or chord-note event -- exactly the hop
`duration_tracker.DurationTracker` reports a duration for, which is also
exactly the hop a note's full information (pitch, duration, and the tempo
estimate at that moment) first becomes knowable. This mirrors
`batch_transcribe.NoteEvent`'s shape/timing on purpose (see the module
docstring there): both describe "what note sounded when for how long",
just computed causally-online here instead of non-causally-offline.

A `record_barline(t)` method is also provided for symmetry with
`TabDisplay.push_barline()`'s call site, and because Concept E's own
schema mockup includes a barline event kind. It is not RenderItem-derived
(barline placement is a `main.py`-level beat-accumulator decision, not a
field on the per-hop RenderItem) so a real integration calls it directly
from `run_terminal_tab()`'s beat-accumulator code, not from
`record_hop()`.

--- The mono duration_hops pairing subtlety ---

Per `CLAUDE.md`'s "Key design decisions" (`RenderItem.duration_hops`/
`bpm_estimate`): the monophonic path's `duration_hops` field, when set on
a given hop, describes the *previous* hop's `pitch_class`/`octave` -- not
that hop's own `pitch_class`/`octave`, which may already have moved on to
the next note (or gone silent). `record_hop()` tracks the previous hop's
mono pitch_class/octave itself (`_prev_mono`) so it can pair a
finalization correctly. Chord mode has no such indirection -- each
`note_stack` entry's own `duration_hops` already pairs with that same
entry's own `pitch_class`/`octave` (see main.py's
`chord_finalized_by_key` wiring) -- so chord-note events are logged
directly off `note_stack`, no lookback needed.
"""

import json

import _repo_paths  # noqa: F401  (sys.path bootstrap side effect)
import config
import color_map
import duration_tracker


def _get(item, field):
    """Field access that works on both a real RenderItem namedtuple and a
    plain dict carrying the same field names -- lets SessionRecorder
    accept either without the caller needing to convert."""
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field)


def _label(pitch_class, octave):
    return f"{color_map.NOTE_NAMES[pitch_class]}{octave}"


class SessionRecorder:
    """Appends one JSON object per line to `path` (opened in append mode,
    so resuming a session across process restarts is naturally supported
    -- an existing file is never truncated). Call `record_hop()` once per
    analysis hop with that hop's RenderItem (or dict) and a timestamp;
    call `record_barline()` wherever `TabDisplay.push_barline()` is
    called live; call `close()` when done (or use as a context manager).
    """

    def __init__(self, path):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8")
        self._prev_mono = None  # (pitch_class, octave) of the last hop that had one
        self.events_written = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        if not self._fh.closed:
            self._fh.close()

    # -- recording --------------------------------------------------

    def record_hop(self, item, t):
        """`item`: a RenderItem-shaped object or dict for this hop.
        `t`: this hop's timestamp in seconds -- session-relative elapsed
        time for a live integration, or a synthesized value for offline/
        test/demo use. Writes zero or more JSONL lines, per the "notable
        hop" policy above.

        Note: a finalization only becomes knowable once a note has
        already ended (or changed), so `t` here is the *finalization*
        hop's timestamp, not the note's onset -- `_write_note()` below
        derives the logged event's own `t` by subtracting the note's
        measured duration back off of it, so the logged timestamp is the
        note's onset time (matching `batch_transcribe.NoteEvent.
        onset_time`'s convention, and what a target-melody file's own `t`
        values mean -- see practice_scorer.py)."""
        pitch_class = _get(item, "pitch_class")
        octave = _get(item, "octave")
        duration_hops = _get(item, "duration_hops")
        bpm_estimate = _get(item, "bpm_estimate")
        note_stack = _get(item, "note_stack") or []

        # Mono finalization: duration_hops (if set this hop) describes the
        # PREVIOUS hop's pitch_class/octave -- see module docstring.
        if duration_hops is not None and self._prev_mono is not None:
            prev_pc, prev_octave = self._prev_mono
            self._write_note(t, prev_pc, prev_octave, duration_hops, bpm_estimate, chord_name=None)

        # Chord-mode finalizations: each note_stack entry pairs with its
        # own duration_hops directly, no lookback needed.
        chord_name = _get(item, "chord_name")
        for entry in note_stack:
            entry_duration = entry["duration_hops"] if isinstance(entry, dict) else entry.duration_hops
            if entry_duration is None:
                continue
            entry_pc = entry["pitch_class"] if isinstance(entry, dict) else entry.pitch_class
            entry_octave = entry["octave"] if isinstance(entry, dict) else entry.octave
            self._write_note(t, entry_pc, entry_octave, entry_duration, bpm_estimate, chord_name=chord_name)

        if pitch_class is not None:
            self._prev_mono = (pitch_class, octave)
        # Deliberately NOT reset to None on a silent hop: duration_hops is
        # only ever non-None on a genuine finalization hop, and by that
        # point _prev_mono already holds exactly the note that finalized
        # (the hop before pitch_class went to None/changed) -- see
        # DurationTracker's own absence-based finalization. A stale
        # _prev_mono sitting unused between finalizations is harmless.

    def record_barline(self, t):
        self._write({"t": round(t, 3), "kind": "barline"})

    # -- internal -----------------------------------------------------

    def _write_note(self, finalization_t, pitch_class, octave, duration_hops, bpm_estimate, chord_name):
        hop_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
        duration_seconds = duration_hops * hop_seconds
        onset_t = finalization_t - duration_seconds
        beats = (duration_seconds * bpm_estimate / 60.0) if bpm_estimate else None
        event = {
            "t": round(onset_t, 3),
            "kind": "note",
            "pc": pitch_class,
            "octave": octave,
            "label": _label(pitch_class, octave),
            "duration_hops": duration_hops,
            "duration_seconds": round(duration_seconds, 3),
            "duration_class": duration_tracker.duration_class_for_beats(beats),
            "bpm_estimate": round(bpm_estimate, 1) if bpm_estimate else None,
            "chord_name": chord_name,
        }
        self._write(event)

    def _write(self, obj):
        self._fh.write(json.dumps(obj) + "\n")
        self._fh.flush()  # small, append-only, infrequent -- see README's cost discussion
        self.events_written += 1
