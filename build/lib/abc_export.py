"""Hand-rolled ABC notation export (Concept A / Feature 2 in
docs/research/notation-and-feature-ideas.md), built as an additive export
path alongside the existing live `TabDisplay` renderer and MusicXML
`score_writer.py` -- not a replacement for either, and not a rewrite of
`TabDisplay`'s internal column-dict data model, per that doc's own
Recommendation section.

Ported and extended from `prototypes/abc-notation-view/abc_convert.py`,
which already confirmed the one real gotcha this needs to work around:
`music21` (already a `score_writer.py` dependency) can *read* ABC
(`music21.converter.parse(text, format="abc")` works) but has **no ABC
writer** -- `stream.write("abc")` raises `SubConverterException: this
subConverter cannot show or write`, confirmed directly against this
venv's music21. So this module hand-rolls the (small, ~1 line per token)
ABC header/body serialization itself and never imports `music21` at all
-- unlike `score_writer.py`, there's no music21 object graph to build or
validate here, just plain string assembly from data this app already
computes (`NOTE_NAMES_FIFTHS`'s flat-biased spelling,
`duration_tracker`'s duration-class vocabulary).

The prototype was monophonic-only (one `ProtoNote` per column); this
module extends that to polyphonic columns (chord mode's simultaneous
notes sharing one onset) via ABC's own chord-bracket syntax (`[CEG]`),
sharing the *longest* member's duration -- the same
`column_beats = max(...)` convention `main.run_batch_transcribe()` already
uses for its own barline accounting.

Two real input shapes are supported, both reduced to one shared
`columns` list (`note_events_to_abc()`'s own format -- see its
docstring) before serialization:

- `from_transcription_result()`: a `batch_transcribe.TranscriptionResult`
  (`virtualnote transcribe`'s output) -- notes grouped by `onset_hop`,
  same grouping `main.run_batch_transcribe()` performs for its
  `TabDisplay` columns.
- `from_session_log()`: a list of event dicts as read by
  `session_player.load_events()` from a `.jsonl` session-recording log --
  notes grouped by shared onset time `t`, via
  `session_player.group_columns()`.

Both reconstruct barlines via the same beats-accumulator walk
(`_with_barlines()`) `main.run_batch_transcribe()` already uses for the
live/batch dump path, since neither a `TranscriptionResult` nor a session
log carries its own barline events (session logs don't record barlines at
all in v1 -- see `session_recorder.py`'s docstring).

Not reconstructed: rests from silence gaps between notes. Only played
notes are represented (each source only logs sounding notes, never
silence spans), a deliberate v1 scope limit mirroring this project's
existing "note over completeness" posture elsewhere (e.g. session
recording's own barline-free v1 scope).
"""

from fractions import Fraction

from color_map import NOTE_NAMES_FIFTHS
from duration_tracker import _DURATION_CLASSES, duration_class_for_beats

_BEATS_BY_DURATION = {name: beats for beats, name in _DURATION_CLASSES}


def _abc_length(duration_class):
    """duration_class name -> ABC length suffix relative to `L:1/4` (this
    module always emits `L:1/4`, i.e. one ABC unit == one quarter note ==
    one "beat" in this app's own existing convention -- see
    score_writer.py's `QUARTER_LENGTHS` comment). `"quarter"` (1 unit)
    maps to `""`, ABC's own implicit-default-length convention."""
    beats = _BEATS_BY_DURATION[duration_class]
    frac = Fraction(beats).limit_denominator(32)
    if frac.denominator == 1:
        return "" if frac.numerator == 1 else str(frac.numerator)
    if frac.numerator == 1:
        return f"/{frac.denominator}"
    return f"{frac.numerator}/{frac.denominator}"


def _abc_pitch(pitch_class, octave):
    """(pitch_class, octave) -> an ABC pitch token. Accidental prefix
    (`^`=sharp, `_`=flat) comes straight from `NOTE_NAMES_FIFTHS`'s
    flat-biased spelling -- the same spelling `staff_map.py`/
    `score_writer.py`/`terminal_tab_display.py`'s *name* style all
    already use, so an ABC-spelled note never disagrees with what this
    app's own staff/tab view would call the same pitch. Octave: ABC's
    convention (bare uppercase = octave 4, bare lowercase = octave 5,
    each octave further up/down adds one `'`/`,`) lines up with this
    app's own octave numbering with no offset translation needed --
    confirmed by direct round-trip in the source prototype."""
    name = NOTE_NAMES_FIFTHS[pitch_class]
    letter, accidental_suffix = name[0], name[1:]
    accidental = {"b": "_", "#": "^"}.get(accidental_suffix, "")
    if octave >= 5:
        return f"{accidental}{letter.lower()}{chr(0x27) * (octave - 5)}"
    return f"{accidental}{letter}{',' * (4 - octave)}"


def note_events_to_abc(columns, title="note-color transcription", time_signature=(4, 4),
                        key="C", reference_number=1):
    """`columns`: an ordered list, each item either the literal string
    `"|"` (a barline) or a non-empty list of `(pitch_class, octave,
    duration_class)` tuples -- one note, or several sharing an onset (a
    chord-mode column), rendered as a plain pitch token or an ABC chord
    bracket (`[CEG]`) respectively. Pure string assembly -- no music21
    involvement, see module docstring for why."""
    numerator, denominator = time_signature
    tokens = []
    for col in columns:
        if col == "|":
            if tokens and tokens[-1] == "|":
                continue  # no doubled barlines from a boundary that already emitted one
            tokens.append("|")
            continue
        longest = max(col, key=lambda n: _BEATS_BY_DURATION[n[2]])
        length = _abc_length(longest[2])
        if len(col) == 1:
            pitch_class, octave, _ = col[0]
            tokens.append(_abc_pitch(pitch_class, octave) + length)
        else:
            pitches = "".join(_abc_pitch(pitch_class, octave) for pitch_class, octave, _ in col)
            tokens.append(f"[{pitches}]{length}")
    if not tokens or tokens[-1] != "|":
        tokens.append("|")
    body = " ".join(tokens)

    header = (
        f"X:{reference_number}\n"
        f"T:{title}\n"
        f"M:{numerator}/{denominator}\n"
        f"L:1/4\n"
        f"K:{key}\n"
    )
    return header + body + "\n"


def _with_barlines(columns_no_bars, beats_per_bar):
    """Insert `"|"` barline markers into a flat column list every time
    cumulative beats reaches a multiple of `beats_per_bar` -- mirrors
    `main.run_batch_transcribe()`'s own beats-accumulator barline
    placement, just walked over a whole finished column list instead of
    hop-by-hop."""
    out = []
    acc = 0.0
    for col in columns_no_bars:
        out.append(col)
        longest = max(col, key=lambda n: _BEATS_BY_DURATION[n[2]])
        acc += _BEATS_BY_DURATION[longest[2]]
        if acc >= beats_per_bar - 1e-9:
            out.append("|")
            acc = 0.0
    if out and out[-1] != "|":
        out.append("|")
    return out


def from_transcription_result(result, time_signature=(4, 4)):
    """`batch_transcribe.TranscriptionResult` -> a `columns` list for
    `note_events_to_abc()`. Groups `result.notes` by `onset_hop` (the
    same grouping `main.run_batch_transcribe()` performs for its
    `TabDisplay` columns) and inserts barlines via `_with_barlines()`."""
    numerator, denominator = time_signature
    beats_per_bar = numerator * (4.0 / denominator)

    by_hop = {}
    for note in result.notes:
        by_hop.setdefault(note.onset_hop, []).append(note)

    columns = []
    for onset_hop in sorted(by_hop):
        entries = []
        for n in by_hop[onset_hop]:
            beats = (n.duration_hops * result.hop_seconds * result.bpm / 60.0) if result.bpm else None
            entries.append((n.pitch_class, n.octave, duration_class_for_beats(beats)))
        columns.append(entries)
    return _with_barlines(columns, beats_per_bar)


def from_session_log(events, time_signature=(4, 4)):
    """A `session_player.load_events()`-shaped list of event dicts -> a
    `columns` list for `note_events_to_abc()`. Groups same-`t` note
    events into one column via `session_player.group_columns()` and
    inserts barlines via `_with_barlines()` -- session logs don't record
    barlines themselves in v1 (see `session_recorder.py`'s docstring:
    "Barlines are deliberately out of scope for v1"), so they're
    reconstructed here the same way batch transcription already
    reconstructs them."""
    from session_player import group_columns

    numerator, denominator = time_signature
    beats_per_bar = numerator * (4.0 / denominator)

    columns = []
    for kind, _t, group in group_columns(events):
        if kind != "notes":
            continue  # no barline events exist in a v1 session log; skip defensively if one ever does
        entries = [(e["pc"], e["octave"], e["duration_class"]) for e in group]
        columns.append(entries)
    return _with_barlines(columns, beats_per_bar)


def write_abc(columns, path, title="note-color transcription", time_signature=(4, 4), key="C"):
    """Serialize `columns` (as built by `from_transcription_result()` /
    `from_session_log()`) to ABC text and write it to `path`. Returns the
    text written, so a caller (or a test) doesn't need to re-read the
    file to check what was produced."""
    abc_text = note_events_to_abc(columns, title=title, time_signature=time_signature, key=key)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(abc_text)
    return abc_text
