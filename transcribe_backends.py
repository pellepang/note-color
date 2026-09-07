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
        #: The reason on its own, without the install line appended -- so a
        #: caller that wants to lay the two out itself (a status line, an
        #: indented hint) is not left splitting `str(exc)` on a newline.
        self.message = message
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


#: Shortest span worth naming as a chord. Below this a "chord" is an
#: artifact of where the analysis window happened to end, not something
#: anyone played.
MIN_CHORD_SPAN_SECONDS = 0.2


class TemplateChordEstimator:
    """Chord spans from this repo's own chroma folding and ~360-template
    matcher -- no new dependency, no download, no extra.

    #142 identified this as the **no-extra tier** for #141's Real Book
    output, sitting below the neural estimator (`BTC-ISMIR19`, MIT code
    with weights committed in an MIT repo, independently re-run at 81.59%
    MajMin on 485 real pop/rock songs). The two obvious alternatives are
    both licence traps under #139: Chordino/NNLS-Chroma is GPL-2.0, and
    `autochord` is Apache-2.0 running *on* that GPL plugin, so it inherits
    the refusal.

    Expect meaningfully less than BTC's number: `oss-landscape-chord-
    multipitch.md` puts the field's MajMin ceiling at 75-80% and notes
    that plain chroma+templates specifically is "a weaker baseline than
    NNLS-chroma/Chordino". This is the tier that works with nothing
    installed, and #132's harness is what decides how far behind it
    actually is.

    **Segmentation is beat-synchronous when a `BeatGrid` is given**, one
    span per beat, which is the right unit for a lead sheet and is why
    `ChordEstimator.estimate()` takes `beats` at all -- chords change on
    beats, and averaging chroma across a beat is both more stable and
    cheaper than a fixed window that straddles two of them. With no grid
    it falls back to fixed `window_seconds` blocks.

    Adjacent spans naming the same chord are merged, so a chord held for
    four beats is one span rather than four -- what `<harmony>` (#131) and
    a human reader both want.
    """

    def __init__(
        self,
        window_seconds=0.5,
        threshold=None,
        bass_cutoff_hz=None,
        min_span_seconds=0.0,
    ):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.bass_cutoff_hz = bass_cutoff_hz
        self.min_span_seconds = min_span_seconds

    def _segments(self, duration_seconds, beats):
        """(start, end) pairs to analyse -- beat-synchronous if a grid is
        available and usable, fixed windows otherwise."""
        if beats is not None and len(beats.beat_seconds) >= 2:
            edges = [t for t in beats.beat_seconds if 0.0 <= t <= duration_seconds]
            if len(edges) >= 2:
                spans = list(zip(edges, edges[1:]))
                # The tail after the last beat is real audio and may hold
                # the final chord -- a beat grid ends at the last detected
                # beat, not at the end of the file. But only when it is
                # long enough to be a chord: a bare `> 0` test appends a
                # sliver whenever the last beat lands near the end, and a
                # 0.02-second span produced a spurious "C13/F" on the
                # first real end-to-end run. Half a beat is the shortest
                # span worth naming.
                tail = duration_seconds - edges[-1]
                median_beat = float(np.median(np.diff(edges))) if len(edges) > 1 else 0.0
                if tail > max(median_beat * 0.5, MIN_CHORD_SPAN_SECONDS):
                    spans.append((edges[-1], duration_seconds))
                return spans
        step = max(self.window_seconds, 1e-3)
        starts = np.arange(0.0, duration_seconds, step)
        return [(float(s), float(min(s + step, duration_seconds))) for s in starts]

    def estimate(
        self, audio: np.ndarray, sample_rate: int, beats: Optional[BeatGrid] = None
    ) -> list:
        # Local imports for consistency with every other backend here, and
        # because chord_templates/chroma pull in this repo's own config.
        import chord_templates
        import chroma as chroma_module
        from pitch_detect import compute_spectrum

        audio = np.asarray(audio, dtype=np.float64)
        if audio.size == 0:
            return []
        duration_seconds = audio.size / float(sample_rate)

        match_kwargs = {}
        if self.threshold is not None:
            match_kwargs["threshold"] = self.threshold
        bass_kwargs = {}
        if self.bass_cutoff_hz is not None:
            bass_kwargs["cutoff_hz"] = self.bass_cutoff_hz

        raw = []
        for start, end in self._segments(duration_seconds, beats):
            lo = int(start * sample_rate)
            hi = min(int(end * sample_rate), audio.size)
            segment = audio[lo:hi]
            if segment.size < 32:
                continue
            spectrum = compute_spectrum(segment)
            chroma_vector = chroma_module.fold(spectrum, sample_rate)
            bass_chroma = chroma_module.fold_bass(spectrum, sample_rate, **bass_kwargs)
            result = chord_templates.match(chroma_vector, bass_chroma=bass_chroma, **match_kwargs)
            # A segment nothing matches stays blank rather than being given
            # the previous chord or a guess -- the same "render blank rather
            # than a guess" posture chord_templates.match() itself takes.
            if result is None:
                continue
            raw.append(ChordSpan(start, end, result.name, confidence=float(result.similarity)))

        return self._merge(raw)

    def _merge(self, spans):
        """Fuse adjacent spans naming the same chord, then drop anything
        shorter than `min_span_seconds`.

        Merging happens first: four consecutive beats of C become one
        two-second C, which then survives a minimum-length filter that
        each individual beat would have failed."""
        if not spans:
            return []
        merged = [spans[0]]
        for span in spans[1:]:
            last = merged[-1]
            if span.name == last.name and abs(span.start_seconds - last.end_seconds) < 1e-6:
                confidence = None
                if last.confidence is not None and span.confidence is not None:
                    # Length-weighted, so a long confident span isn't
                    # dragged down by one marginal beat joining it.
                    a = last.end_seconds - last.start_seconds
                    b = span.end_seconds - span.start_seconds
                    total = a + b
                    confidence = (
                        (last.confidence * a + span.confidence * b) / total if total else None
                    )
                merged[-1] = ChordSpan(last.start_seconds, span.end_seconds, last.name, confidence)
            else:
                merged.append(span)
        if self.min_span_seconds > 0:
            merged = [
                s for s in merged if (s.end_seconds - s.start_seconds) >= self.min_span_seconds
            ]
        return merged


class BeatThisTracker:
    """Beat This! (ISMIR 2024) behind the `BeatTracker` Protocol -- #130's
    beat and downbeat source. MIT code **and** MIT weights, CPU by
    default, verified by #127 to resolve on Python 3.14.

    Expect ~0.89 beat F1 / ~0.78 downbeat F1 on genre-diverse pop/rock and
    ~0.63 beat F1 on expressive/rubato material (#127). Downbeats are what
    barlines are made of (#130 places a barline at each predicted
    downbeat), so the second number is the one that bounds notation
    quality.

    **`drum_stem` is accepted and ignored, and that qualifies #130.**
    Reading `beat_this` 1.1.0's own source (`inference.Audio2Beats`)
    settles something the research did not: it takes exactly one signal
    (`__call__(signal, sr)`), with no extra-input channel anywhere in
    `Spect2Frames`/`Audio2Frames`. #127's measured drum-stem gain
    (downbeat F1 0.699 -> 0.775) comes from **Beat Transformer**, which is
    a multi-channel architecture; it does not transfer to a single-input
    model. So with this tracker the timing-oracle role is currently
    unrealised -- the Protocol's "a tracker that cannot use it ignores it"
    path, taken for real. Mixing a boosted drum stem back into the input
    would be an unmeasured heuristic, and this map does not do those; see
    docs/DECISIONS.md.

    `dbn=False` deliberately: the DBN post-processor is madmom, which #130
    declined because it needs a git pin on Python 3.14, and Beat This!'s
    own argument for its minimal post-processing is that the DBN's
    55-215 BPM and constant-meter priors break on real material -- which
    is exactly the tempo-drift case #130 cares about.
    """

    INSTALL_HINT = "pip install -e .[convert]"

    def __init__(self, checkpoint="final0", device="cpu", float16=False):
        self.checkpoint = checkpoint
        self.device = device
        self.float16 = float16
        self._tracker = None

    def _load(self):
        if self._tracker is not None:
            return self._tracker
        try:
            from beat_this.inference import Audio2Beats
        except ImportError as exc:
            raise ConversionUnavailable(
                "Beat tracking needs Beat This! (and CPU torch).",
                self.INSTALL_HINT,
            ) from exc
        # Weights download on first use. They are MIT, so unlike Demucs'
        # (#139 category 2) this needs no terms prompt.
        self._tracker = Audio2Beats(
            checkpoint_path=self.checkpoint, device=self.device, float16=self.float16, dbn=False
        )
        return self._tracker

    def track(
        self, audio: np.ndarray, sample_rate: int, drum_stem: Optional[np.ndarray] = None
    ) -> BeatGrid:
        tracker = self._load()
        # Beat This! resamples internally to its own 22050 Hz
        # (`Audio2Frames.signal2spect`), so no resampling is done here --
        # doing it twice would be strictly worse.
        beats, downbeats = tracker(np.asarray(audio, dtype=np.float32), int(sample_rate))
        return BeatGrid(
            beat_seconds=tuple(float(t) for t in np.asarray(beats).ravel()),
            downbeat_seconds=tuple(float(t) for t in np.asarray(downbeats).ravel()),
        )


# --- basic-pitch (map #123, #129's per-stem note detector) -----------------
#
# Constants read from the model's own ONNX signature and from Basic
# Pitch's `constants.py`, not from prose about them. See
# vendor/basic_pitch/README.md for the full signature and why the graph is
# committed rather than pip-installed.

#: The model is trained at this rate; input at any other rate is resampled.
BASIC_PITCH_SAMPLE_RATE = 22050
#: 2 seconds at 22050 Hz less one 256-sample FFT hop.
BASIC_PITCH_WINDOW_SAMPLES = 43844
#: 86 fps x 2 s.
BASIC_PITCH_FRAMES_PER_WINDOW = 172
BASIC_PITCH_FFT_HOP = 256
BASIC_PITCH_FPS = BASIC_PITCH_SAMPLE_RATE // BASIC_PITCH_FFT_HOP  # 86
#: Windows overlap by this many output frames, half trimmed from each end
#: on unwrapping, so a note spanning a window boundary is not cut in two.
BASIC_PITCH_OVERLAP_FRAMES = 30
#: Bin 0 of the 88-bin note/onset posteriorgrams is MIDI 21 (A0).
BASIC_PITCH_MIDI_OFFSET = 21

_VENDORED_MODEL = "vendor/basic_pitch/nmp.onnx"


@dataclass(frozen=True)
class Posteriorgrams:
    """basic-pitch's three raw output matrices, unwrapped to whole-signal
    length and trimmed to the real audio duration.

    Exposed as a value rather than kept private because #143 found this is
    the *useful* access point: frame-level averaging across perturbed
    passes, and the per-note confidence that decoding computes from
    `note`, both need these before any thresholding happens. A backend
    that only ever returned decoded notes would throw that away -- which
    is precisely what basic-pitch's own `predict()` does."""

    note: np.ndarray      # (n_frames, 88)   frame activation
    onset: np.ndarray     # (n_frames, 88)   onset activation
    contour: np.ndarray   # (n_frames, 264)  3 bins per semitone
    fps: float = float(BASIC_PITCH_FPS)

    def frame_times(self) -> np.ndarray:
        return np.arange(self.note.shape[0], dtype=float) / self.fps


def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Band-limited resampling to the model's own rate.

    Uses `soxr` when present -- it arrives with `beat-this` in the
    `[convert]` extra, so in a real converter install it always is -- and
    otherwise an FFT method (rfft, truncate or zero-pad the spectrum,
    irfft) which is band-limited by construction.

    **Not linear interpolation**, which is what this function did when
    first written and which was wrong in a way that would not have shown
    up in its own test. Downsampling 44.1 kHz to 22.05 kHz by
    interpolation applies no anti-aliasing, so everything above 11 kHz
    folds back into the audible band -- cymbals and hiss landing on top of
    real pitches. A pure sine test passes happily either way, which is
    exactly why the defect survived; real music would have been quietly
    degraded. 44.1 kHz is the common case for the files this converter
    exists to read, so this path is not hypothetical."""
    if from_rate == to_rate or audio.size == 0:
        return np.asarray(audio, dtype=np.float32)

    try:
        import soxr

        return np.asarray(
            soxr.resample(np.asarray(audio, dtype=np.float32), from_rate, to_rate),
            dtype=np.float32,
        )
    except ImportError:
        pass

    target_n = int(round(audio.size * to_rate / float(from_rate)))
    if target_n <= 0:
        return np.zeros(0, dtype=np.float32)
    spectrum = np.fft.rfft(np.asarray(audio, dtype=np.float64))
    n_keep = target_n // 2 + 1
    if n_keep <= spectrum.size:
        spectrum = spectrum[:n_keep]          # downsample: discard above the new Nyquist
    else:
        spectrum = np.concatenate([spectrum, np.zeros(n_keep - spectrum.size, dtype=complex)])
    resampled = np.fft.irfft(spectrum, n=target_n) * (target_n / float(audio.size))
    return resampled.astype(np.float32)


def _window_audio(audio: np.ndarray, hop_size: int) -> np.ndarray:
    """-> (n_windows, BASIC_PITCH_WINDOW_SAMPLES, 1), zero-padding the
    final short window. Mirrors basic-pitch's own `window_audio_file()`."""
    windows = []
    for start in range(0, max(audio.size, 1), hop_size):
        window = audio[start : start + BASIC_PITCH_WINDOW_SAMPLES]
        if window.size < BASIC_PITCH_WINDOW_SAMPLES:
            window = np.pad(window, (0, BASIC_PITCH_WINDOW_SAMPLES - window.size))
        windows.append(window)
        if start + BASIC_PITCH_WINDOW_SAMPLES >= audio.size:
            break
    return np.asarray(windows, dtype=np.float32)[..., np.newaxis]


def _unwrap(stacked: np.ndarray, original_samples: int) -> np.ndarray:
    """(n_windows, frames, bins) -> (n_frames, bins).

    Trims half the overlap from each window's start and end, concatenates,
    then cuts to the number of frames the *original* audio actually spans
    -- otherwise the zero padding on the final window reappears as
    trailing silence the decoder would happily read as real frames."""
    if stacked.ndim != 3:
        raise ValueError(f"expected (n_windows, frames, bins), got {stacked.shape}")
    half = BASIC_PITCH_OVERLAP_FRAMES // 2
    trimmed = stacked[:, half:-half, :] if half else stacked
    flat = trimmed.reshape(-1, trimmed.shape[2])
    n_real = int(np.floor(original_samples * (BASIC_PITCH_FPS / BASIC_PITCH_SAMPLE_RATE)))
    return flat[:n_real, :]


class BasicPitchModel:
    """Runs the vendored `nmp.onnx` and returns raw `Posteriorgrams`.

    Separate from any note-decoding backend on purpose. #143's central
    finding was that this model exposes exactly what an ensembling or
    confidence scheme needs -- three posteriorgrams on a fixed
    86.13 fps x 88-semitone grid anchored at A0, with note creation as a
    plain function over them -- and that YourMT3-style seq2seq models
    structurally cannot. Keeping this half addressable is what preserves
    that option; folding it into a `transcribe()` that only ever returns
    notes would discard it.

    Loads the graph once and reuses the session, since `InferenceSession`
    construction is not free and a converter runs this per stem."""

    INSTALL_HINT = "pip install -e .[convert]"

    def __init__(self, model_path=None, providers=None):
        self.model_path = model_path
        self.providers = providers or ["CPUExecutionProvider"]
        self._session = None

    def _resolve_path(self):
        import os

        if self.model_path is not None:
            return str(self.model_path)
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, *_VENDORED_MODEL.split("/"))

    def _load(self):
        if self._session is not None:
            return self._session
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ConversionUnavailable(
                "Note detection needs onnxruntime.", self.INSTALL_HINT
            ) from exc
        import os

        path = self._resolve_path()
        if not os.path.exists(path):
            raise ConversionUnavailable(
                f"The basic-pitch model is missing from this install ({path}). "
                "It is committed to the repo, so this usually means an incomplete checkout."
            )
        self._session = ort.InferenceSession(path, providers=self.providers)
        return self._session

    def posteriorgrams(self, audio: np.ndarray, sample_rate: int) -> Posteriorgrams:
        session = self._load()
        audio = _resample(np.asarray(audio).ravel(), int(sample_rate), BASIC_PITCH_SAMPLE_RATE)
        original_samples = audio.size
        if original_samples == 0:
            empty = np.zeros((0, 88), dtype=np.float32)
            return Posteriorgrams(empty, empty, np.zeros((0, 264), dtype=np.float32))

        overlap_samples = BASIC_PITCH_OVERLAP_FRAMES * BASIC_PITCH_FFT_HOP
        hop_size = BASIC_PITCH_WINDOW_SAMPLES - overlap_samples
        # Half the overlap is prepended as silence so the first real sample
        # sits at the centre of the first window's kept region -- without
        # it every note's onset would land half an overlap early.
        padded = np.concatenate(
            [np.zeros(overlap_samples // 2, dtype=np.float32), audio]
        )
        windows = _window_audio(padded, hop_size)

        input_name = session.get_inputs()[0].name
        note_out, onset_out, contour_out = [], [], []
        for window in windows:
            note, onset, contour = session.run(
                ["StatefulPartitionedCall:1",
                 "StatefulPartitionedCall:2",
                 "StatefulPartitionedCall:0"],
                {input_name: window[np.newaxis, ...]},
            )
            note_out.append(note)
            onset_out.append(onset)
            contour_out.append(contour)

        return Posteriorgrams(
            note=_unwrap(np.concatenate(note_out), original_samples),
            onset=_unwrap(np.concatenate(onset_out), original_samples),
            contour=_unwrap(np.concatenate(contour_out), original_samples),
        )


#: basic-pitch's own decoding defaults, carried over so a number reported
#: against this backend is comparable with a published one. #142's metric
#: work argues these should eventually move -- a higher onset threshold
#: buys precision at zero compute, and precision is what "editing effort"
#: rewards -- but that is a measured change for #132 to make, not a
#: default to quietly differ on.
BASIC_PITCH_ONSET_THRESHOLD = 0.5
BASIC_PITCH_FRAME_THRESHOLD = 0.3
BASIC_PITCH_MIN_NOTE_MS = 127.70
#: Frames of sub-threshold energy tolerated inside a note before it ends.
BASIC_PITCH_ENERGY_TOLERANCE = 11


def _local_maxima_mask(matrix: np.ndarray) -> np.ndarray:
    """Strict local maxima down each column (axis 0).

    Equivalent to `scipy.signal.argrelmax(matrix, axis=0)` at its default
    `order=1`/`comparator=np.greater`, endpoints excluded. Written out
    rather than imported because SciPy lives behind this repo's `[synth]`
    extra (#111) and adding it to `[convert]` for four lines of comparison
    would be a real dependency for no reason."""
    mask = np.zeros(matrix.shape, dtype=bool)
    if matrix.shape[0] < 3:
        return mask
    mask[1:-1] = (matrix[1:-1] > matrix[:-2]) & (matrix[1:-1] > matrix[2:])
    return mask


def _infer_extra_onsets(onsets: np.ndarray, frames: np.ndarray, n_diff: int = 2) -> np.ndarray:
    """Add onsets implied by a sharp rise in frame energy.

    A re-articulation on a note already sounding often produces little
    onset activation but a clear jump in frame energy; without this, two
    struck notes at one pitch merge into one long note. Takes the
    elementwise *minimum* across several difference lags so a slow swell
    does not register, rescaled to the onset matrix's own range."""
    if frames.shape[0] <= n_diff:
        return onsets
    diffs = []
    for n in range(1, n_diff + 1):
        padded = np.concatenate([np.zeros((n, frames.shape[1])), frames])
        diffs.append(padded[n:, :] - padded[:-n, :])
    frame_diff = np.min(diffs, axis=0)
    frame_diff[frame_diff < 0] = 0
    frame_diff[:n_diff, :] = 0
    peak = float(np.max(frame_diff))
    if peak <= 0:
        return onsets
    frame_diff = float(np.max(onsets)) * frame_diff / peak
    return np.max([onsets, frame_diff], axis=0)


def decode_notes(
    grams: "Posteriorgrams",
    onset_threshold: float = BASIC_PITCH_ONSET_THRESHOLD,
    frame_threshold: float = BASIC_PITCH_FRAME_THRESHOLD,
    min_note_ms: float = BASIC_PITCH_MIN_NOTE_MS,
    infer_onsets: bool = True,
    melodia_trick: bool = True,
    min_midi: Optional[int] = None,
    max_midi: Optional[int] = None,
    energy_tolerance: int = BASIC_PITCH_ENERGY_TOLERANCE,
) -> list:
    """`Posteriorgrams` -> `list[TranscribedNote]`.

    A faithful port of Basic Pitch's `output_to_notes_polyphonic`
    (Apache-2.0; `LICENSE`/`NOTICE` vendored under `vendor/basic_pitch/`),
    rather than a simpler decoder of this project's own. Two reasons:
    it is the decoder the model was trained and published against, so a
    number measured here is comparable with the 0.709 MAESTRO F1 #125
    quotes; and a hand-rolled thresholder would differ from it in ways
    that would then be indistinguishable from the model's own errors when
    #132 starts attributing blame.

    The algorithm, in short: find onset peaks above `onset_threshold`;
    walk each forward while frame energy holds above `frame_threshold`,
    tolerating `energy_tolerance` frames of dropout; consume that energy
    (and its immediate pitch neighbours, which is what stops one note
    spawning three); then, if `melodia_trick`, repeatedly take whatever
    energy is left over and grow a note outward from it in both directions
    -- that last pass is what recovers notes whose onset the model missed.

    The per-note `amplitude` -- mean frame activation across the note --
    becomes `confidence`. #143 found basic-pitch computes exactly this and
    then discards it into MIDI velocity, making it a **free** per-note
    confidence signal, and one that measured within ~1 point of AUROC of
    MC-Dropout at 50 forward passes. It is also written to `velocity`,
    because it is the only loudness proxy available and that is
    basic-pitch's own convention -- but treating it as a dynamic is a
    convention, not a measurement, and callers should not read it as one.
    """
    frames = np.array(grams.note, dtype=np.float64, copy=True)
    onsets = np.array(grams.onset, dtype=np.float64, copy=True)
    n_frames, n_bins = frames.shape
    if n_frames == 0:
        return []

    # Frequency constraint, applied before anything else so a bound note
    # cannot consume energy a valid one needed.
    if min_midi is not None:
        lo = max(0, int(min_midi) - BASIC_PITCH_MIDI_OFFSET)
        frames[:, :lo] = 0.0
        onsets[:, :lo] = 0.0
    if max_midi is not None:
        hi = min(n_bins, int(max_midi) - BASIC_PITCH_MIDI_OFFSET + 1)
        frames[:, hi:] = 0.0
        onsets[:, hi:] = 0.0

    if infer_onsets:
        onsets = _infer_extra_onsets(onsets, frames)

    fps = grams.fps
    min_note_frames = int(round(min_note_ms / 1000.0 * fps))
    max_bin = n_bins - 1

    peaks = _local_maxima_mask(onsets)
    peak_values = np.where(peaks, onsets, 0.0)
    onset_frames, onset_bins = np.where(peak_values >= onset_threshold)
    # Backwards in time: a later note's energy is claimed before an earlier
    # one can absorb it, which is what keeps a repeated pitch separate.
    onset_frames = onset_frames[::-1]
    onset_bins = onset_bins[::-1]

    remaining = np.array(frames, copy=True)
    events = []  # (start_frame, end_frame, midi, amplitude)

    for start, bin_idx in zip(onset_frames, onset_bins):
        if start >= n_frames - 1:
            continue
        i = start + 1
        k = 0
        while i < n_frames - 1 and k < energy_tolerance:
            k = k + 1 if remaining[i, bin_idx] < frame_threshold else 0
            i += 1
        i -= k  # back up to the last frame that was actually above threshold
        if i - start <= min_note_frames:
            continue
        remaining[start:i, bin_idx] = 0
        if bin_idx < max_bin:
            remaining[start:i, bin_idx + 1] = 0
        if bin_idx > 0:
            remaining[start:i, bin_idx - 1] = 0
        events.append((start, i, bin_idx + BASIC_PITCH_MIDI_OFFSET,
                       float(np.mean(frames[start:i, bin_idx]))))

    if melodia_trick:
        while float(np.max(remaining)) > frame_threshold:
            mid, bin_idx = np.unravel_index(int(np.argmax(remaining)), remaining.shape)
            remaining[mid, bin_idx] = 0

            i = mid + 1
            k = 0
            while i < n_frames - 1 and k < energy_tolerance:
                k = k + 1 if remaining[i, bin_idx] < frame_threshold else 0
                remaining[i, bin_idx] = 0
                if bin_idx < max_bin:
                    remaining[i, bin_idx + 1] = 0
                if bin_idx > 0:
                    remaining[i, bin_idx - 1] = 0
                i += 1
            end = i - 1 - k

            i = mid - 1
            k = 0
            while i > 0 and k < energy_tolerance:
                k = k + 1 if remaining[i, bin_idx] < frame_threshold else 0
                remaining[i, bin_idx] = 0
                if bin_idx < max_bin:
                    remaining[i, bin_idx + 1] = 0
                if bin_idx > 0:
                    remaining[i, bin_idx - 1] = 0
                i -= 1
            begin = i + 1 + k

            if end - begin <= min_note_frames:
                continue
            events.append((begin, end, bin_idx + BASIC_PITCH_MIDI_OFFSET,
                           float(np.mean(frames[begin:end, bin_idx]))))

    notes = [
        TranscribedNote(
            onset_seconds=start / fps,
            offset_seconds=end / fps,
            pitch_midi=int(midi),
            velocity=float(np.clip(amplitude, 0.0, 1.0)),
            confidence=float(np.clip(amplitude, 0.0, 1.0)),
        )
        for start, end, midi, amplitude in events
    ]
    notes.sort(key=lambda note: (note.onset_seconds, note.pitch_midi))
    return notes


class BasicPitchTranscriber:
    """`BasicPitchModel` + `decode_notes()` behind the `NoteTranscriber`
    Protocol -- #129's per-stem note detector.

    Holds the model so the ONNX session is built once and reused across
    stems, and exposes `posteriorgrams()` alongside `transcribe()` so a
    caller wanting the raw matrices (frame-level ensembling, #143) does not
    have to reach around this class to get them."""

    def __init__(self, model=None, **decode_kwargs):
        self.model = model or BasicPitchModel()
        self.decode_kwargs = decode_kwargs

    def posteriorgrams(self, audio: np.ndarray, sample_rate: int) -> "Posteriorgrams":
        return self.model.posteriorgrams(audio, sample_rate)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> list:
        return decode_notes(self.posteriorgrams(audio, sample_rate), **self.decode_kwargs)


# --- Demucs source separation (map #123, #126/#129) -----------------------

#: Verbatim summary of the terms a user is agreeing to before Demucs'
#: weights are fetched. #126 verified this from the maintainer directly:
#: the CODE is MIT, the WEIGHTS are not, and the Hugging Face repos they
#: come from carry no licence field at all.
DEMUCS_WEIGHTS_TERMS = (
    "Demucs' source-separation weights are NOT open source.\n"
    "  The code is MIT, but its authors state the weights are\n"
    "  \"provided only for scientific purposes\", and the repositories they are\n"
    "  downloaded from state no licence at all.\n"
    "  Roughly 81 MB will be downloaded on first use.\n"
    "  note-color itself is MIT and does not redistribute them."
)

#: Setting that pre-accepts the above, so a batch run is not blocked by a
#: prompt nobody is there to answer (#139). Consent recorded deliberately
#: in a config file is stronger evidence of informed acceptance than a "y"
#: typed to dismiss something, not weaker.
ACCEPT_TERMS_PREFERENCE = "accept_model_terms"


def model_terms_accepted(terms=DEMUCS_WEIGHTS_TERMS, prompt=None, store=None):
    """Whether the user has accepted a model's non-open weight terms.

    #139 category 2: weights this project does not redistribute may be
    fetched, but **never silently** -- the user is told what they are
    agreeing to first. Returns True if `[preferences].accept_model_terms`
    is set, otherwise asks once; on a non-interactive stream it declines
    rather than hanging, and says how to pre-accept."""
    import sys

    if store is None:
        from config_store import store as store
    if store.preference(ACCEPT_TERMS_PREFERENCE, False):
        return True

    ask = prompt or input
    print("\n" + terms)
    if not sys.stdin.isatty():
        print(
            "  Not an interactive terminal, so nothing was downloaded.\n"
            f"  To accept in advance, set [preferences].{ACCEPT_TERMS_PREFERENCE} = true\n"
            "  in note-color's config.toml."
        )
        return False
    try:
        answer = ask("  Download these weights? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return str(answer).strip().lower() in ("y", "yes")


def prepare_separator_input(audio, sample_rate, model_rate, channels):
    """Mono audio at the caller's rate -> (channels, samples) at the
    model's rate.

    Demucs is trained on stereo, so a mono source is duplicated across
    channels rather than passed as one -- the model rejects the latter.
    Pure and array-only so it is testable without torch installed, the
    same split this repo applies everywhere its logic sits behind a heavy
    dependency."""
    mono = np.asarray(audio, dtype=np.float32).ravel()
    resampled = _resample(mono, int(sample_rate), int(model_rate))
    return np.repeat(resampled[np.newaxis, :], int(channels), axis=0)


def stems_from_estimates(estimates, sources, model_rate, sample_rate):
    """(n_sources, channels, samples) at the model's rate -> {name: mono
    array at `sample_rate`}.

    Converting back to this project's own mono-at-`sample_rate`
    convention here means every downstream stage sees the shape it already
    expects, rather than each learning Demucs' output format."""
    estimates = np.asarray(estimates)
    stems = {}
    for index, name in enumerate(sources):
        mono = estimates[index].mean(axis=0)
        stems[name] = _resample(mono, int(model_rate), int(sample_rate))
    return stems


class DemucsSeparator:
    """Demucs (`htdemucs`) behind the `Separator` Protocol.

    #126 measured this on the target machine: **~2.1x real time** (a
    4-minute song in ~8.3 minutes, ~14% of the map's hourly budget), 1.3 GB
    peak RSS, 4 stems. It is also, by a wide margin, the most expensive
    stage of the pipeline -- basic-pitch transcription measured ~32-38x
    real time here, so separation dominates the budget by roughly 15x.
    Any speed dial worth having (#134) varies *this*, which is also why
    #126 recommended `--overlap` rather than a lighter model as the fast
    end. `mdx_extra_q` is explicitly not offered: measured slowest and
    hungriest of the four-stem models despite the smallest weight file.

    Whether separation improves *note* accuracy is #129's H1 and remains
    unmeasured by anyone; #142's one real datapoint is +0.20 points. It
    earns its place here regardless, because a multi-track deliverable
    structurally needs per-instrument audio.

    The weights are fetched only after the user accepts their terms
    (#139) -- they are research-use-only, unlike everything else in this
    stack."""

    INSTALL_HINT = "pip install -e .[convert]"

    def __init__(self, model_name="htdemucs", device="cpu", overlap=0.25, shifts=0,
                 terms_check=None):
        self.model_name = model_name
        self.device = device
        self.overlap = overlap
        self.shifts = shifts
        self.terms_check = terms_check or model_terms_accepted
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch  # noqa: F401  -- checked here so a missing torch
            from demucs.pretrained import get_model  # refuses cleanly too
        except ImportError as exc:
            raise ConversionUnavailable(
                "Source separation needs Demucs (and CPU torch).", self.INSTALL_HINT
            ) from exc
        if not self.terms_check():
            raise ConversionUnavailable(
                "Demucs' weights were not accepted, so no separation was run."
            )
        self._model = get_model(self.model_name)
        self._model.to(self.device)
        self._model.eval()
        return self._model

    def separate(self, audio: np.ndarray, sample_rate: int) -> dict:
        model = self._load()
        import torch

        from demucs.apply import apply_model

        model_rate = int(getattr(model, "samplerate", 44100))
        channels = int(getattr(model, "audio_channels", 2))

        prepared = prepare_separator_input(audio, int(sample_rate), model_rate, channels)
        with torch.no_grad():
            estimates = apply_model(
                model, torch.from_numpy(prepared[np.newaxis, ...]), device=self.device,
                overlap=self.overlap, shifts=self.shifts, progress=False,
            )
        return stems_from_estimates(
            estimates[0].cpu().numpy(), model.sources, model_rate, int(sample_rate)
        )
