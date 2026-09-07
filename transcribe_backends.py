"""Pluggable seam for map #123's offline audio-to-score converter (issue
#129).

The same seam `detection_backends.py` provides for the *live* path, for
the *offline* one -- and for the same reason. #129 settled that the
transcription stack itself is provisional: the models chosen there are
defaults to be replaced by whichever ones #132's harness measures as
better, and three of the pipeline's structural questions (does separation
help note accuracy, does a locked beat grid beat independent snapping,
does ensembling earn its cost) are explicitly recorded as *hypotheses* to
be tested rather than architecture to be assumed. None of that is
possible if swapping a model means surgery through a monolithic
`convert()`.

So this module owns the shapes and nothing else. Four `Protocol`s, one
per stage of the pipeline #129 describes:

    audio -> Separator      -> stems
    stem  -> NoteTranscriber -> notes
    audio -> BeatTracker     -> a beat/downbeat grid
    audio -> ChordEstimator  -> chord spans   (the Real Book half, #141)

Each mirrors the return shape its consumers already need, with nothing
model-specific in the Protocol itself -- the same discipline
`detection_backends.py` follows, and the same warning applies: do not pad
these with speculative parameters before a second real backend exists to
design against.

Concrete backends capture their model-specific configuration at
construction rather than threading it through every call, and **import
their heavy dependency lazily inside the method, never at module scope**
(`synth_engine.py`'s `_signal()` convention), so importing this module
costs nothing on an install that never converts anything.

Note events here are expressed in **seconds and MIDI pitch**, not in this
repo's hop-and-pitch-class terms. That is deliberate: the neural models
#129 selects emit absolute times, `batch_transcribe.NoteEvent`'s
`onset_hop`/`duration_hops` are an artifact of the DSP pipeline's own
block clock, and every evaluation metric #132 adopts (`mir_eval`,
`musicdiff`, MV2H) is defined over seconds. `DspNoteTranscriber` converts
at the edge.
"""

from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np


class ConversionUnavailable(Exception):
    """Raised when a backend's dependency, weights or soundfont is
    missing.

    #129 settled that the converter **refuses rather than degrades**: with
    nothing installed it names the install line instead of silently
    falling back to the live DSP pipeline. #124 measured the neural
    ceiling at ~37.87% onset F1 on real band audio and this repo's
    monophonic live detector is far below that, so a quiet fallback would
    hand someone a wrong four-track score of their favourite song and call
    it a transcription.

    Same posture and shape as `synth_engine.SynthUnavailable` (#111):
    carries the install line, so the message a user sees is actionable
    rather than a traceback."""

    def __init__(self, message, install_hint=None):
        self.install_hint = install_hint
        super().__init__(f"{message}\n{install_hint}" if install_hint else message)


@dataclass(frozen=True)
class TranscribedNote:
    """One note, in the units every downstream consumer actually wants.

    `velocity` and `confidence` are 0..1 and both optional in practice:
    not every model emits either. `confidence` is kept because #143 found
    `basic-pitch`'s decoder already computes a per-note mean frame
    amplitude and discards it into MIDI velocity -- a free per-note
    confidence signal, and the input to #129's "mark a low-confidence
    track" decision and to any editor surface that highlights notes worth
    checking."""

    onset_seconds: float
    offset_seconds: float
    pitch_midi: int
    velocity: float = 1.0
    confidence: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.offset_seconds - self.onset_seconds)


@dataclass(frozen=True)
class BeatGrid:
    """Beat and downbeat times in seconds.

    Downbeats are a subset of beats in principle but are *not* assumed to
    be one here -- a tracker may disagree with itself, and #130 places
    barlines at predicted downbeats specifically because a downbeat is an
    event rather than something extrapolated from a beat count.

    No tempo field: #130/#131 settled that tempo is a *curve* read back
    out of these times, not a scalar imposed on them."""

    beat_seconds: tuple[float, ...] = ()
    downbeat_seconds: tuple[float, ...] = ()

    def beats_per_bar(self) -> Optional[int]:
        """The modal number of beats between consecutive downbeats, or
        None when there are too few downbeats to say.

        This is #130's meter *numerator* inference in its simplest form --
        the "mode of beats-between-downbeats" route, chosen over madmom's
        DBN because madmom needs a git pin on Python 3.14. The caller
        applies the confidence gate and the {2,3,4,6} candidate set; this
        only reports what the grid says. The denominator is never inferred
        anywhere -- it is a convention (#130)."""
        if len(self.downbeat_seconds) < 3 or not self.beat_seconds:
            return None
        beats = np.asarray(self.beat_seconds, dtype=float)
        counts = []
        for start, end in zip(self.downbeat_seconds, self.downbeat_seconds[1:]):
            counts.append(int(np.count_nonzero((beats >= start - 1e-6) & (beats < end - 1e-6))))
        counts = [c for c in counts if c > 0]
        if not counts:
            return None
        return max(set(counts), key=counts.count)


@dataclass(frozen=True)
class ChordSpan:
    """One chord, sounding from `start_seconds` until `end_seconds`.

    A span rather than an event because that is what a lead sheet needs
    and what MusicXML `<harmony>` expresses (#131). `name` uses this
    repo's own jazz symbol spelling, as produced by
    `chord_templates.match()`."""

    start_seconds: float
    end_seconds: float
    name: str
    confidence: Optional[float] = None


class Separator(Protocol):
    def separate(self, audio: np.ndarray, sample_rate: int) -> dict:
        """-> {stem name: mono float array at `sample_rate`}. Stem names
        follow Demucs' own vocabulary ("drums", "bass", "vocals",
        "other"), since #129 picked it and every downstream routing rule
        is written in those terms."""
        ...


class NoteTranscriber(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> list:
        """-> list[TranscribedNote]."""
        ...


class BeatTracker(Protocol):
    def track(
        self, audio: np.ndarray, sample_rate: int, drum_stem: Optional[np.ndarray] = None
    ) -> BeatGrid:
        """-> BeatGrid. `drum_stem` is #130's timing oracle: a separated
        drum stem offered as an extra input channel, measured worth
        downbeat F1 0.699 -> 0.775 on drum-heavy material. A tracker that
        cannot use it ignores it rather than failing."""
        ...


class ChordEstimator(Protocol):
    def estimate(
        self, audio: np.ndarray, sample_rate: int, beats: Optional[BeatGrid] = None
    ) -> list:
        """-> list[ChordSpan]. `beats` lets an estimator segment on the
        beat grid rather than a fixed window; one that does not care
        ignores it."""
        ...


class DspNoteTranscriber:
    """This repo's existing DSP pipeline (`batch_transcribe.transcribe()`)
    behind the `NoteTranscriber` Protocol.

    Explicitly **not** a fallback for the converter -- #129 settled that
    `convert` refuses rather than degrading, and this backend is not
    reachable by default. It exists because #132's harness needs a
    *baseline arm*: every accuracy claim in map #123 is a comparison, and
    "better than what this project already had" is the comparison that
    decides whether any of it was worth building. Being able to run the
    old pipeline through the same seam, scored by the same metrics, is
    what makes that number honest.

    Converts at the edge from the hop-based `batch_transcribe.NoteEvent`
    to this module's seconds-and-MIDI `TranscribedNote`, using
    `sound_engine.midi_pitch()` rather than restating the tuning
    convention.

    `polyphonic=True` reads `result.notes` (the multipitch/chord path);
    False reads `result.mono_notes` (the YIN path). Both are always
    computed by `transcribe()`, so this is a selection, not a mode."""

    def __init__(self, time_signature=None, polyphonic=True):
        self.time_signature = time_signature
        self.polyphonic = polyphonic

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> list:
        # Lazy, like every backend here: batch_transcribe pulls in librosa,
        # which lives behind the [batch] extra and has no business being
        # imported by anything that merely inspects this module.
        try:
            import batch_transcribe
        except ImportError as exc:  # pragma: no cover -- needs [batch] absent
            raise ConversionUnavailable(
                "The DSP baseline backend needs librosa.",
                "pip install -e .[batch]",
            ) from exc
        import config
        from sound_engine import midi_pitch

        time_signature = self.time_signature or config.DEFAULT_TIME_SIGNATURE
        result = batch_transcribe.transcribe(audio, sample_rate, time_signature=time_signature)
        events = result.notes if self.polyphonic else result.mono_notes
        hop_seconds = result.hop_seconds

        notes = []
        for event in events:
            duration = (event.duration_hops or 0) * hop_seconds
            notes.append(
                TranscribedNote(
                    onset_seconds=float(event.onset_time),
                    offset_seconds=float(event.onset_time) + float(duration),
                    pitch_midi=midi_pitch(event.pitch_class, event.octave),
                    # This pipeline never measured a per-note attack
                    # strength -- the same reason tab_playback.py uses one
                    # fixed velocity (#121). Saying 1.0 is honest; making
                    # up a dynamic would not be.
                    velocity=1.0,
                    confidence=None,
                )
            )
        notes.sort(key=lambda note: (note.onset_seconds, note.pitch_midi))
        return notes
