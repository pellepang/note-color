# Rhythm, meter, barline and drum-notation strategy (issue #130)

#127's research applied. The load-bearing move is separating what is
*inferred* from what is *convention*, and separating drums-as-oracle from
drums-as-notation.

### Beats and downbeats: Beat This!, and no madmom

**Beat This!** (MIT code *and* weights, CPU default, verified to resolve
on Python 3.14), already chosen in #129. Expect **~0.89 beat F1 / ~0.78
downbeat F1** on genre-diverse pop/rock, **~0.63 beat F1** on
expressive/rubato material.

**madmom is not adopted**, though #127 established it is licence-clean for
this use — its source is 3-clause BSD and its DBN downbeat decoder loads
no model, so the CC BY-NC-SA term on its `.npy` files never applies. The
blocker is installation: it does not build on Python 3.14 from PyPI and
needs a **git pin**, which is exactly the friction "easy install for
anyone, not just computer geeks" rules out. Its DBN would decide meter and
downbeats in one Viterbi pass, which is genuinely nicer than the simple
route below; it is recorded as a **measured refinement** to revisit behind
its own extra, not as a dependency.

### Time signature: a numerator is inferred, a denominator is a convention

This is the sharpest finding and it is not a shortcut.

- **The numerator is inferred** from the downbeat sequence — the mode of
  beats-between-downbeats — over the candidate set **{2, 3, 4, 6}**. A
  small candidate set is how the field itself works: madmom defaults to
  `[3, 4]`, PM2S's vocabulary is `{0, 2, 3, 4, 6}` with `0` meaning "some
  other value", and "Skip That Beat" (LAMIR 2024) counts bar-position
  resets exactly this way.
- **The denominator is not inferred. It is a convention**: 4, or 8 when
  the numerator is 6/9/12. 4/4 versus 2/2, and 3/4 versus 6/8, are
  *notational* distinctions on identical acoustic evidence, and **no
  system surveyed recovers them**. "Skip That Beat" says so outright: "The
  denominator is always 4 for the tracks we are using in this work."
  Writing a denominator as though it were measured would be a claim this
  project cannot support.
- **Confidence gate**: the inferred numerator is committed only when the
  modal value accounts for a supermajority of observed bars and enough
  bars were observed; otherwise **4/4**, which is right most of the time
  in this repertoire. Accuracy here is bounded above by downbeat F1
  (~0.78), and #127 is blunt that anything other than 4/4 vs 3/4 on
  Western pop-rock should be **assumed wrong and in need of correction**.
- **Meter changes mid-piece are not attempted** — nothing surveyed does
  it. One time signature per score.

**What "correctable guess" means concretely**, since map #123 reversed
#31's deferral on exactly that basis: the value is written to the file;
the project manifest records it as *inferred*, with its confidence and the
candidate it beat; and the correction path **already exists** — the score
editor's inline `t` header editor edits time signature in place. The guess
is committed because a downbeat grid is worth having even when the meter
label is wrong, **not** because meter inference works.

### Barlines at predicted downbeats, never from a beat accumulator

This is a deliberate departure from what this repo does live. `main.py`'s
`tab` view places barlines by accumulating beats against a tempo estimate,
which CLAUDE.md already calls "explicitly approximate". Offline, a barline
goes at **each predicted downbeat**.

The reason is structural, not incremental: **a downbeat is an event, an
accumulator is an extrapolation whose error compounds monotonically.** The
event formulation is the only one that survives a tempo change at all. The
tempo curve (#131) is read *back out* of the predicted beat times rather
than imposed on them — which is also Beat This!'s own argument for having
no DBN, since the DBN's 55–215 BPM and constant-meter priors break on real
material.

### Quantization: feed the grid in, leave the snapping alone

Three separable things, and only one of them changes:

1. **Quantize against the beat grid, not a global tempo.** This is the
   change. `duration_class_for_beats()`'s independent nearest-value snap
   **is not the weak link — its input is**: it currently snaps a duration
   measured in beats derived from a live tempo scalar. Beat This! supplies
   a real grid. Feed the grid in; **do not touch the snapping rule** until
   it is measured to be the problem.
2. **`score_writer.py`'s 32nd-note *offset* quantization stays** and is
   unrelated. It exists because music21 cannot express certain unquantized
   offsets as MusicXML rests — a format constraint, as its own docstring
   says. `log_import.py` already draws this distinction; musical
   quantization against a beat grid is a new layer over it, not a
   replacement.
3. **Tuplets get added** — see below.

### Tuplets: added, and additive only

Map #123 parked this here explicitly. `duration_tracker.DURATION_CLASS_ORDER`
holds ten plain and dotted powers of two and **no tuplet values at all**;
`log_import.py` documents deliberately omitting triplet grids "since
`DURATION_CLASS_ORDER` has no tuplet values to write them as"; issue #62
deferred tuplet detection in `score_writer.py`. **A converter that cannot
write a triplet misnotates 6/8 outright**, and 6/8 is on this map's own
candidate-meter list.

So tuplet duration classes are added — but **additively, and off by
default**, because map #123's scope explicitly excludes changes to the
live detection path. `DURATION_CLASS_ORDER` remains exactly the set the
live path snaps against; the tuplet values live in their own extension
that `duration_class_for_beats()` consults only when a caller opts in, so
the live pipeline's behaviour is unchanged byte-for-byte and only the
converter sees the larger vocabulary.

One scope relief worth recording: **swing does not need tuplets.** Jazz
lead sheets — the Real Book style #141 targets — notate swung eighths as
*straight* eighths under a "Swing" direction, not as triplet figures. So
the tuplet requirement is driven by genuine triplet figures and compound
meter, not by swing feel, which narrows what has to work.

### Drums: the oracle is in scope, the notation is not

Splitting these two roles is the useful move, because they have completely
different licence exposure and completely different cost:

- **As a timing oracle: in scope, and essentially free.** It needs a
  **drum stem**, which separation already produces, fed to the tracker as
  an extra input channel. Measured worth: **downbeat F1 0.699 → 0.775** on
  drum-heavy material. No ADT model, no non-commercial licence, no new
  weights.
- **As notation: out of scope for this map.** A notated drum part needs a
  real ADT model. **ADTOF is CC BY-NC-SA on code *and* data and cannot
  ship** under #139; ADTOF-pytorch has no LICENSE at all and derives from
  those weights. The only licence-clean route is **training on STAR Drums
  (CC BY 4.0)** — materially bigger than "adopt a model", and it graduates
  to its own effort rather than being assumed away here. Evaluating
  against ADTOF privately is fine and is not distribution (#139 category
  4).

So v1 **separates the drum stem, uses it for timing, records it in the
project manifest, and writes no notated drum part.** Saying that plainly
is better than emitting a drum track nobody measured.

Percussion-staff *rendering* is therefore moot for now. #128 verified the
format side round-trips cleanly (`Unpitched`, `PercussionClef`, colour on
unpitched notes); whether this repo's terminal renderers can draw a
percussion staff is untouched and stays open behind the notation question.

### One requirement this places on the harness

**CMLt/AMLt must be reported, not just F-measure.** Beat This!'s own GTZAN
numbers show beat F1 89.1 against beat CMLt 79.8, and downbeat F1 78.3
against downbeat CMLt 67.3 — *continuous* correct tracking is meaningfully
rarer than pointwise correct tracking. A score converter cares about
continuity specifically, because **one dropped bar shifts everything after
it**. `mir_eval.beat` implements all of them. Expressive/rubato material is
where this whole stack is weakest and the metric has to be able to say so.

### Beat This! cannot take the drum stem — #130's timing oracle qualified

Found while building the `BeatThisTracker` adapter, by reading
`beat_this` 1.1.0's own wheel rather than its paper.

#130 decided that the separated drum stem is fed to the beat tracker "as
an extra input channel", worth a measured **downbeat F1 0.699 → 0.775**
on drum-heavy material. That number is real, but it comes from #127's
citation of the **Beat Transformer** ablation — and Beat Transformer is a
*multi-channel* architecture. **Beat This! is not.**

`inference.Audio2Beats.__call__(signal, sr)` takes exactly one signal;
`Audio2Frames.signal2spect()` reduces a 2-D input by `signal.mean(1)`
(downmixing channels, not accepting stems) and raises on anything with
more dimensions. There is no extra-input path anywhere in
`Spect2Frames`/`Audio2Frames`.

So with the tracker #130 chose, **the drums-as-timing-oracle role is
currently unrealised.** `BeatTracker.track()` keeps its `drum_stem`
parameter and `BeatThisTracker` takes the Protocol's documented "a
tracker that cannot use it ignores it" path, for real rather than
hypothetically.

What was *not* done, deliberately: mixing a level-boosted drum stem back
into the tracker's input. It is the obvious workaround, it is one line,
and it is an **unmeasured heuristic** — there is no published evidence
that emphasising drums in a mono mix reproduces a multi-channel model's
gain, and this map's whole posture is that unmeasured mechanisms are
hypotheses rather than features. It is a clean experiment for #132's
harness (`track(mix)` vs `track(mix + k·drums)`), and belongs there.

Three things this does not change: separation still earns its place (the
multi-track deliverable structurally needs per-instrument audio, and
#142's H1b keeps the percussive/timing case alive), Beat This! is still
the right tracker (MIT code *and* weights, CPU, resolves on 3.14, and it
beat madmom's own ensemble with one model), and barlines still go at
predicted downbeats. What changes is the expected downbeat accuracy: plan
on Beat This!'s own ~0.78 F1 on pop-rock, with no drum-stem bonus on top.
