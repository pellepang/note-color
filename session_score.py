"""Adapter: session-log events (as read by `session_player.load_events()`)
-> a `batch_transcribe.TranscriptionResult`-shaped object, so
`score_writer.write_score()` -- which only ever consumes that shape -- can
write a MusicXML score straight from a recorded `.jsonl` session log
without `score_writer.py` needing to learn a second input shape.

This is issue #89's (map #57's live-score-saving child ticket) resolved
implementation. Its investigation found the gap real for non-causal
duration/tempo *re*-refinement -- `session_recorder.py` logs only
already-finalized note events (`t`, `pc`, `octave`, `duration_seconds`,
`bpm_estimate`, `chord_name`, ...), never the raw per-hop magnitude/onset
arrays `duration_tracker.DurationTracker.finalize_noncausal()`/
`rhythm_reanalysis.py` need to redo that work, and there's no way to
reconstruct one from the other after the fact -- but small for
score-writing itself, since `score_writer.write_score()` only ever reads
already-finalized `onset_hop`/`duration_hops`/`pitch_class`/`octave`/
`chord_name`/`bpm`/`chroma_histogram` fields, exactly what a session log
already has. See that issue's resolution comment for the full writeup.

Deliberately its own small module rather than folded into either side:
`session_player.py` stays a pure log-reading/grouping module with no
`batch_transcribe`/`score_writer` knowledge, and `score_writer.py` stays a
pure music21-consuming writer with no session-log-schema knowledge.
Mirrors `abc_export.py`'s own `from_session_log()` -- one small adapter
per consumer of the log, not a shared "session log means X" module.
"""

from statistics import median

from batch_transcribe import NoteEvent, TranscriptionResult
from session_player import group_columns

# score_writer.py only ever multiplies onset_hop/duration_hops by
# hop_seconds to recover real seconds (see write_score()'s own
# offset_beats computation and _duration_quarter_length()) -- fixing
# hop_seconds=1.0 and feeding each logged note's own already-real-seconds
# `t`/`duration_seconds` straight through as onset_hop/duration_hops makes
# that arithmetic reduce to the identity. No synthetic "hop index" is
# invented; a session log never had one (see session_recorder.py's schema
# doc -- it logs onset time directly, not a hop index).
HOP_SECONDS = 1.0


def from_session_log(events):
    """`events`, as loaded by `session_player.load_events()`, -> a
    `batch_transcribe.TranscriptionResult` `score_writer.write_score()`
    can consume directly, with zero changes to that module (issue #89's
    resolution). Each logged note's own already-finalized timing is
    treated as final -- no non-causal duration/tempo *re*-refinement is
    attempted, since the raw per-hop data that would need is not in the
    log (see module docstring). Same "provisional, not chased further
    without a concrete complaint" posture this codebase already takes
    toward causal rhythm data elsewhere (CLAUDE.md's Known Limitations).

    `result.mono_notes` is always `[]` -- `write_score()` never reads it
    (only `result.notes`, the polyphonic list; a solo note is already a
    one-note "chord" there, see that module's docstring), and a session
    log's mono-note vs. chord-tone events aren't reliably separable after
    the fact anyway: both share the exact same event shape, distinguished
    only by whether `chord_name` happens to be non-None that hop, which
    isn't the same thing as "this note was part of a chord" (chord
    recognition always runs regardless of display mode -- see main.py's
    Key design decisions -- so a genuinely solo mono note can still carry
    a non-None `chord_name` if chord_smoother matched something that hop).
    """
    notes = []
    for kind, t, group in group_columns(events):
        if kind != "notes":
            continue  # v1 session logs never emit barline events (session_recorder.py's own docstring);
            # skipped defensively here the same way abc_export.from_session_log() does, in case that changes.
        chord_name = next((event.get("chord_name") for event in group if event.get("chord_name")), None)
        for event in group:
            notes.append(
                NoteEvent(
                    onset_hop=t,
                    onset_time=t,
                    pitch_class=event["pc"],
                    octave=event["octave"],
                    duration_hops=event["duration_seconds"],
                    chord_name=chord_name,
                )
            )

    bpm_values = [event["bpm_estimate"] for event in events if event.get("bpm_estimate")]
    bpm = median(bpm_values) if bpm_values else None

    # Coarse chroma-histogram proxy for guess_key_signature() (issue #89's
    # resolution): sum each logged event's own pitch class, weighted by how
    # long it sounded, across every mono note *and* every individually-
    # logged chord tone -- cruder than batch_transcribe's real
    # harmonic-weighted chroma.fold() sum (no harmonic content, no
    # per-hop resolution), but score_writer.guess_key_signature() already
    # falls back to "no key signature" below its own confidence threshold
    # (#61's "blank rather than a wrong guess" posture), so a coarser
    # signal degrades to no guess rather than a bad one.
    chroma_histogram = [0.0] * 12
    for event in events:
        if event.get("kind") == "barline":
            continue
        chroma_histogram[event["pc"]] += event["duration_seconds"]

    return TranscriptionResult(
        notes=notes,
        mono_notes=[],
        bpm=bpm,
        hop_seconds=HOP_SECONDS,
        chroma_histogram=chroma_histogram,
    )
