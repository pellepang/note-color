"""ABC notation <-> note-event converter (prototype, research demo for
docs/research/notation-and-feature-ideas.md's Concept A).

Note-event shape here (`ProtoNote`) is deliberately the *ABC-relevant
subset* of `batch_transcribe.NoteEvent` (`onset_hop`, `onset_time`,
`pitch_class`, `octave`, `duration_hops`, `chord_name`) -- just
`pitch_class`/`octave`/`duration_class`, since ABC serialization only
needs pitch + a snapped note-value name, not hop-granular timing.
`from_note_events()` below is the adapter showing how a *real*
`batch_transcribe.NoteEvent` list becomes a `ProtoNote` list: it derives
`duration_class` from `duration_hops`/`hop_seconds`/`bpm` via
`duration_tracker.duration_class_for_beats()`, the exact same computation
`score_writer.py`'s `_duration_quarter_length()` already performs (see
that module, ~line 148) -- so this prototype's conversion path is provably
the same arithmetic the real batch pipeline already uses, not a
reinvention.

Key finding (see README.md's "System architecture reasoning" section for
the full discussion): `music21` -- already a dependency via
`score_writer.py` -- has a real, working ABC *reader*
(`music21.converter.parse(text, format='abc')`, used by
`abc_to_note_events()` below) but **no ABC writer**
(`music21.converter.subConverters.ConverterABC` registers zero
`registerOutputExtensions`; calling `stream.write('abc')` raises
`SubConverterException: This subConverter cannot show or write: no output
extensions are registered for it` -- confirmed directly against this
venv's music21 10.5.0). So `note_events_to_abc()` below hand-rolls the
(small, ~6-line-per-token) ABC body/header serialization itself, and only
reaches for `music21` to *validate* what it wrote by parsing it straight
back (`note_events_to_abc(..., validate=True)`, the default) -- "use
music21 where practical" turned out to mean "for reading," not "for
writing," for this particular format.
"""

import sys
from collections import namedtuple
from fractions import Fraction
from pathlib import Path

from music21 import converter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for
# color_map/duration_tracker -- this prototype deliberately reads (never
# writes) real project modules rather than duplicating their tables.

from color_map import NOTE_NAMES_FIFTHS
# duration_tracker._DURATION_CLASSES is the one authoritative (beats, name)
# table this whole app already snaps durations against (duration_class_for_
# beats() is its public forward lookup: beats -> nearest name). We need the
# reverse direction (name -> exact beats) to compute an ABC length ratio,
# so we read the same private table directly rather than inventing a
# second copy of it that could drift -- read-only use, fine for a
# research prototype that doesn't ship.
from duration_tracker import DEFAULT_DURATION_CLASS, _DURATION_CLASSES, duration_class_for_beats

ProtoNote = namedtuple("ProtoNote", "pitch_class octave duration_class")
# A rest is represented as a bare `None` in an event list (not its own
# type) -- ABC's own rest token (`z`) carries a duration but no pitch, so
# `None` (no pitch_class/octave) already says everything a rest needs;
# giving it a duration_class would need a 4th field just for this one case.
# The (pitch_class=None) convention also matches `terminal_tab_display.
# TabEntry.notes` dicts, which already use `pitch_class: None` for "no
# note sounding" in a column (see that module's render()).
Bar = namedtuple("Bar", [])  # sentinel: a barline in the event stream

_BEATS_BY_DURATION = {name: beats for beats, name in _DURATION_CLASSES}


def _abc_length(duration_class):
    """duration_class name -> ABC length suffix relative to `L:1/4` (this
    prototype always emits `L:1/4`, i.e. one ABC unit == one quarter note
    == one "beat" in this app's own existing convention -- see
    score_writer.py's `_QUARTER_LENGTHS` comment: "quarterLength is
    numerically identical to duration_tracker.py's beats unit
    everywhere in this codebase"). `"quarter"` (1 unit) maps to `""` --
    ABC's own implicit-default-length convention, a bare letter with no
    suffix at all."""
    beats = _BEATS_BY_DURATION[duration_class]
    frac = Fraction(beats).limit_denominator(32)
    if frac.denominator == 1:
        return "" if frac.numerator == 1 else str(frac.numerator)
    if frac.numerator == 1:
        return f"/{frac.denominator}"
    return f"{frac.numerator}/{frac.denominator}"


def _abc_pitch(pitch_class, octave):
    """(pitch_class, octave) -> an ABC pitch token. Accidental prefix
    (`^`=sharp, `_`=flat) comes straight from `NOTE_NAMES_FIFTHS`'
    flat-biased spelling -- the same spelling `staff_map.py`/
    `score_writer.py`/`terminal_tab_display.py`'s *name* style all already
    use, so an ABC-spelled note never disagrees with what this app's own
    staff/tab view would call the same pitch. Octave: ABC's convention is
    octave 4 == a bare uppercase letter, octave 5 == a bare lowercase
    letter, each octave further up/down adds one `'`/`,` -- confirmed
    directly against this venv's music21 by round-tripping `_B,` (-> B-3)
    and `c` (-> C5), so this matches this app's own octave numbering with
    no offset translation needed."""
    name = NOTE_NAMES_FIFTHS[pitch_class]
    letter, accidental_suffix = name[0], name[1:]
    accidental = {"b": "_", "#": "^"}.get(accidental_suffix, "")
    if octave >= 5:
        return f"{accidental}{letter.lower()}{chr(0x27) * (octave - 5)}"
    return f"{accidental}{letter}{',' * (4 - octave)}"


def with_barlines(notes, beats_per_bar=4):
    """Insert `Bar` sentinels into a flat `notes` list (ProtoNote / None
    for rest) every time cumulative beats reaches a multiple of
    `beats_per_bar` -- mirrors `main.py`'s own live beat-accumulator
    (`_hop_beats()`/`run_terminal_tab()`'s barline trigger) in spirit, just
    computed over a whole finished event list instead of hop-by-hop."""
    out = []
    acc = 0.0
    for note in notes:
        out.append(note)
        beats = _BEATS_BY_DURATION[note.duration_class] if note is not None else 1.0
        acc += beats
        if acc >= beats_per_bar - 1e-9:
            out.append(Bar())
            acc = 0.0
    if out and not isinstance(out[-1], Bar):
        out.append(Bar())
    return out


def note_events_to_abc(events, title="Live capture", time_signature=(4, 4), key="C",
                        reference_number=1, validate=True):
    """`events` (ProtoNote / None-for-rest / Bar, e.g. `with_barlines()`'s
    output) -> ABC text. Hand-rolled serialization -- see the module
    docstring for why (music21 has no ABC writer). `validate=True` (the
    default) parses the text straight back via `abc_to_note_events()`
    before returning, raising if music21 can't read what was just
    written -- a cheap, real correctness check for a hand-rolled
    serializer producing a format this app doesn't otherwise validate."""
    numerator, denominator = time_signature
    tokens = []
    for e in events:
        if isinstance(e, Bar):
            if tokens and tokens[-1] == "|":
                continue  # no doubled barlines from an already-trailing Bar
            tokens.append("|")
        elif e is None:
            tokens.append("z")
        else:
            tokens.append(_abc_pitch(e.pitch_class, e.octave) + _abc_length(e.duration_class))
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
    abc_text = header + body + "\n"

    if validate:
        abc_to_note_events(abc_text)  # raises on malformed ABC -- see docstring
    return abc_text


def abc_to_note_events(abc_text):
    """ABC text -> a flat list of ProtoNote / None (rest) / Bar, via
    `music21.converter.parse(text, format='abc')` -- this direction
    *does* work natively in music21 (unlike writing, see the module
    docstring), so this function is a thin, mostly-bookkeeping wrapper
    around it rather than a hand-rolled parser. Walks each measure in
    parsed order (so barlines land between measures, matching
    `with_barlines()`'s own placement), converting each note/rest via
    `.pitch.pitchClass` (already the same 0..11 semitone numbering this
    app's own `pitch_class` uses -- confirmed directly: `_B,` round-trips
    to pitchClass 10, matching `NOTE_NAMES_FIFTHS[10] == "Bb"`) and
    `.duration.quarterLength` (fed through
    `duration_tracker.duration_class_for_beats()`, since quarterLength
    and this app's "beats" are the same unit -- see the module
    docstring)."""
    score = converter.parse(abc_text, format="abc")
    events = []
    parts = list(score.parts) if score.parts else [score]
    part = parts[0]
    measures = list(part.getElementsByClass("Measure"))
    iterable = measures if measures else [part]
    for i, measure in enumerate(iterable):
        for element in measure.notesAndRests:
            if element.isRest:
                events.append(None)
            elif element.isChord:
                # ABC chords (`[CEG]`) aren't emitted by note_events_to_abc()
                # today (monophonic-only prototype scope), but parsing one
                # back shouldn't crash -- take the chord's highest note,
                # same "one note represents the column" simplification the
                # terminal preview's column model already assumes.
                top = max(element.notes, key=lambda n: n.pitch.midi)
                duration_class = duration_class_for_beats(element.duration.quarterLength) \
                    if element.duration.quarterLength else DEFAULT_DURATION_CLASS
                events.append(ProtoNote(top.pitch.pitchClass, top.pitch.octave, duration_class))
            else:
                duration_class = duration_class_for_beats(element.duration.quarterLength) \
                    if element.duration.quarterLength else DEFAULT_DURATION_CLASS
                events.append(ProtoNote(element.pitch.pitchClass, element.pitch.octave, duration_class))
        if measures and i < len(measures) - 1:
            events.append(Bar())
    if measures:
        events.append(Bar())
    return events


def from_note_events(note_events, hop_seconds, bpm):
    """Adapter: a real `batch_transcribe.NoteEvent` list (see that
    module's `NoteEvent` namedtuple, ~line 64) -> a `ProtoNote` list, via
    the exact `duration_hops -> beats -> duration_class` computation
    `score_writer.py`'s `_duration_quarter_length()` already performs (same
    formula, `duration_hops * hop_seconds * bpm / 60.0`) -- proving this
    prototype's note-event shape really is compatible with the real batch
    pipeline's output, not just superficially similar."""
    out = []
    for ev in note_events:
        beats = (ev.duration_hops * hop_seconds * bpm / 60.0) if bpm else None
        out.append(ProtoNote(ev.pitch_class, ev.octave, duration_class_for_beats(beats)))
    return out
