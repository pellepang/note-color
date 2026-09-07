"""Synthetic jazz corpus with exact ground truth (map #123, issue #144).

Generates `(audio, score, truth)` triples: a jazz arrangement -- drums,
walking bass, comped chords, melody -- together with the exact notes that
produced it. The ground truth is correct **by construction** rather than
by annotation, which is what makes it usable as a regression suite.

WHAT THIS IS FOR, AND WHAT IT IS NOT
-----------------------------------
This is **a regression and diagnostic harness, never the accuracy claim.**

It is the same thing Slakh2100 already is, and #124's standing caveat --
stated by MulTTiPop's own authors -- is that **good Slakh numbers do not
transfer to real recordings**. The measured gap is ~60% onset F1 on
synthetic audio against **37.87%** on real commercial band audio. Numbers
from this generator will be *higher still*, because Slakh at least uses
professional sample libraries where this uses oscillators and filtered
noise: no room, no mic bleed, no compression, no mastering, no performer.

Two traps this design has to live with, recorded rather than solved:

- **A generator's own errors are invisible.** If the swing here is
  actually straight eighths, a converter that transcribes straight scores
  100% forever. The truth is only as honest as the generator's fidelity
  to real playing, and nothing inside this file can check that.
- **Chord ground truth is circular.** The "correct" chord is correct
  because it was chosen here. Real jazz has substitutions, rootless
  voicings and upper structures a root-position generator never produces.

So: use it to answer "did this change break something?" and "which stage
failed?", which it answers well and cheaply. Do not use it to answer "how
accurate is this converter?", which it cannot answer at all.

WHY THE REPO'S OWN SYNTH
------------------------
Pitched parts render through `playback.render_offline()` and drums reuse
`scripts/acoustic_pipeline_test.py`'s existing kick/snare/hi-hat models
(a real pitch sweep on the kick, a tonal "poc" on the snare) rather than
new ones. No new dependency, and it dogfoods instruments this project
already ships.
"""

from dataclasses import dataclass, field
import numpy as np

import config

# --- Jazz vocabulary ------------------------------------------------------
#
# Semitone offsets from the chord root. Deliberately small: this is a
# generator, not a theory engine, and a wider vocabulary would mostly
# widen the circularity noted above rather than make the corpus more
# honest.
CHORD_INTERVALS = {
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
    "dom7": (0, 4, 7, 10),
    "min7b5": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9),
}

#: (root pitch class, quality) per bar. ii-V-I is the spine of the idiom;
#: the blues gives dominant-heavy material with a different harmonic
#: rhythm, which is a genuinely different test rather than a longer one.
PROGRESSIONS = {
    "ii-V-I": [(2, "min7"), (7, "dom7"), (0, "maj7"), (0, "maj7")],
    "ii-V-i-minor": [(2, "min7b5"), (7, "dom7"), (0, "min7"), (0, "min7")],
    "blues-F": [
        (5, "dom7"), (10, "dom7"), (5, "dom7"), (5, "dom7"),
        (10, "dom7"), (10, "dom7"), (5, "dom7"), (5, "dom7"),
        (0, "dom7"), (10, "dom7"), (5, "dom7"), (0, "dom7"),
    ],
    "rhythm-changes-A": [
        (0, "maj7"), (9, "min7"), (2, "min7"), (7, "dom7"),
        (0, "maj7"), (9, "min7"), (2, "min7"), (7, "dom7"),
    ],
}


@dataclass
class Part:
    """One instrument's notes, as `(onset_beats, pitch_class, octave,
    duration_beats, velocity)` tuples. Beats rather than seconds so a
    tempo change is expressible without rewriting every part."""

    name: str
    notes: list = field(default_factory=list)


@dataclass
class SynthTrack:
    """A generated arrangement and everything known about it."""

    parts: list
    drums: list                 # (onset_beats, kind) with kind in kick/snare/hihat
    chords: list                # (onset_beats, root_pc, quality, duration_beats)
    tempo_bpm: float
    beats_per_bar: int
    beat_unit: int
    swing: bool
    seed: int
    name: str = "synthetic"

    def total_beats(self):
        ends = [n[0] + n[3] for part in self.parts for n in part.notes]
        ends += [d[0] + 0.25 for d in self.drums]
        return max(ends) if ends else 0.0

    def duration_seconds(self):
        return self.total_beats() * 60.0 / self.tempo_bpm


def swing_offset(onset_beats, swing):
    """Delay every off-beat eighth, the defining feel of the idiom.

    A 2:1 triplet ratio -- the off-beat lands two-thirds of the way
    through the beat rather than halfway. Straight when `swing` is False.

    Worth being explicit that this is where the "generator errors are
    invisible" trap bites hardest: if this is wrong, every swing number
    the corpus produces is wrong in the same direction and nothing here
    can detect it. Real swing is also not a fixed ratio -- it varies with
    tempo and player -- so even a correct implementation is a caricature."""
    if not swing:
        return onset_beats
    beat = np.floor(onset_beats)
    frac = onset_beats - beat
    if abs(frac - 0.5) < 1e-6:
        return beat + 2.0 / 3.0
    return onset_beats


def walking_bass(progression, beats_per_bar, rng, octave=2):
    """One note per beat, stepping between chord tones.

    A walking line is the clearest possible test of note-level
    transcription -- monophonic, evenly spaced, low register -- which is
    exactly why it is worth having as a separate part rather than folded
    into the comping."""
    notes = []
    for bar, (root, quality) in enumerate(progression):
        intervals = CHORD_INTERVALS[quality]
        for beat in range(beats_per_bar):
            # Root on the downbeat, chord tones elsewhere: a real walking
            # line does more, but landing the root on 1 is the part that
            # makes the harmony legible.
            interval = intervals[0] if beat == 0 else intervals[rng.integers(1, len(intervals))]
            pitch = (root + interval) % 12
            oct_shift = octave + ((root + interval) // 12)
            notes.append((bar * beats_per_bar + beat, pitch, oct_shift, 0.9, 0.85))
    return Part("bass", notes)


def comped_chords(progression, beats_per_bar, rng, octave=4, swing=False):
    """Chord stabs, syncopated the way a comping instrument plays them
    rather than on every downbeat -- which also makes them a harder and
    more realistic test than block chords on 1."""
    notes = []
    for bar, (root, quality) in enumerate(progression):
        bar_start = bar * beats_per_bar
        hits = sorted({0.0, float(rng.choice([1.5, 2.0, 2.5]))})
        for hit in hits:
            onset = swing_offset(bar_start + hit, swing)
            duration = 0.8
            for interval in CHORD_INTERVALS[quality]:
                pitch = (root + interval) % 12
                oct_shift = octave + ((root + interval) // 12)
                notes.append((onset, pitch, oct_shift, duration, 0.55))
    return Part("chords", notes)


def melody_line(progression, beats_per_bar, rng, octave=5, swing=False, density=2):
    """A melody built from chord tones and passing notes, `density` notes
    per beat. The part a Real Book chart is mostly made of (#141), so its
    accuracy is the one that matters most for that output."""
    notes = []
    step = 1.0 / max(density, 1)
    for bar, (root, quality) in enumerate(progression):
        intervals = CHORD_INTERVALS[quality]
        beat = 0.0
        while beat < beats_per_bar - 1e-9:
            interval = int(rng.choice(intervals))
            if rng.random() < 0.25:
                interval += int(rng.choice([-1, 1]))   # passing tone
            pitch = (root + interval) % 12
            oct_shift = octave + ((root + interval) // 12)
            onset = swing_offset(bar * beats_per_bar + beat, swing)
            notes.append((onset, pitch, oct_shift, step * 0.9, 0.7))
            beat += step
    return Part("melody", notes)


def drum_pattern(n_bars, beats_per_bar, swing=False):
    """A plain jazz ride/kick/snare feel.

    Drums exist here for #130's timing-oracle question, not for notation:
    v1 writes no notated drum part, so what matters is whether their
    presence helps or hurts beat tracking and whether they leak into the
    pitched stems."""
    hits = []
    for bar in range(n_bars):
        base = bar * beats_per_bar
        for beat in range(beats_per_bar):
            hits.append((base + beat, "hihat"))
            if swing:
                hits.append((swing_offset(base + beat + 0.5, True), "hihat"))
        hits.append((base, "kick"))
        if beats_per_bar >= 4:
            hits.append((base + 2, "snare"))
    return sorted(hits)


def make_track(
    progression="ii-V-I",
    tempo_bpm=140.0,
    beats_per_bar=4,
    beat_unit=4,
    swing=True,
    density=2,
    parts=("bass", "chords", "melody"),
    with_drums=True,
    repeats=2,
    seed=0,
    name=None,
):
    """Build one arrangement. Deterministic from `seed`, so a regression
    is reproducible rather than merely observed once."""
    rng = np.random.default_rng(seed)
    chart = PROGRESSIONS[progression] * repeats

    built = []
    if "bass" in parts:
        built.append(walking_bass(chart, beats_per_bar, rng))
    if "chords" in parts:
        built.append(comped_chords(chart, beats_per_bar, rng, swing=swing))
    if "melody" in parts:
        built.append(melody_line(chart, beats_per_bar, rng, swing=swing, density=density))

    chords = [
        (bar * beats_per_bar, root, quality, float(beats_per_bar))
        for bar, (root, quality) in enumerate(chart)
    ]
    drums = drum_pattern(len(chart), beats_per_bar, swing) if with_drums else []

    return SynthTrack(
        parts=built, drums=drums, chords=chords, tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar, beat_unit=beat_unit, swing=swing, seed=seed,
        name=name or f"{progression}-{int(tempo_bpm)}bpm-{'swing' if swing else 'straight'}",
    )


def render_audio(track, sample_rate=None, drum_level=0.5):
    """Render a `SynthTrack` to one mono float32 buffer.

    Pitched parts go through `playback.render_offline()` -- this repo's
    own oscillator+ADSR instrument -- and drums are mixed in from
    `acoustic_pipeline_test.py`'s existing models. Nothing new is
    synthesized here that the project did not already have."""
    from playback import render_offline

    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    seconds_per_beat = 60.0 / track.tempo_bpm

    timed = []
    for part in track.parts:
        for onset_b, pitch_class, octave, duration_b, velocity in part.notes:
            timed.append((onset_b * seconds_per_beat, pitch_class, octave,
                          duration_b * seconds_per_beat, velocity))

    audio = render_offline(timed, sample_rate=sample_rate)
    total = max(audio.size, int(track.duration_seconds() * sample_rate) + sample_rate)
    mixed = np.zeros(total, dtype=np.float32)
    mixed[: audio.size] = audio

    if track.drums:
        drum_audio = render_drums(track, sample_rate)
        n = min(mixed.size, drum_audio.size)
        mixed[:n] += drum_level * drum_audio[:n]

    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.99:
        mixed *= 0.99 / peak
    return mixed


def render_drums(track, sample_rate=None):
    """Drum hits alone, as their own buffer -- separately addressable so a
    test can measure what drums do to beat tracking by adding or removing
    exactly this."""
    import importlib.util
    import os

    sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "_acoustic_synth", os.path.join(here, "scripts", "acoustic_pipeline_test.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    makers = {
        "kick": lambda: module.synth_kick(sr=sample_rate),
        "snare": lambda: module.synth_snare(sr=sample_rate),
        "hihat": lambda: module.synth_hihat(sr=sample_rate),
    }
    seconds_per_beat = 60.0 / track.tempo_bpm
    total = int((track.duration_seconds() + 1.0) * sample_rate)
    out = np.zeros(total, dtype=np.float32)
    cache = {}
    for onset_beats, kind in track.drums:
        if kind not in cache:
            cache[kind] = makers[kind]()
        hit = cache[kind]
        start = int(onset_beats * seconds_per_beat * sample_rate)
        end = min(start + hit.size, total)
        if start < total:
            out[start:end] += hit[: end - start]
    return out
