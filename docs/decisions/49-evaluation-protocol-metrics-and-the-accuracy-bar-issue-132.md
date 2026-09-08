# Evaluation protocol, metrics and the accuracy bar (issue #132)

Map #123's **evaluation-before-algorithm** rule made concrete. The metric
half is #142's research applied; the corpora, the alignment route, the
error-analysis shape and the bar are settled here.

### Corpora: two suites, plus one the repo never sees

**Committed regression suite** (reproducible, licence-clean, fetchable by
script):

| Corpus | Role | Licence |
|---|---|---|
| **MulTTiPop** (3.5 h) | The only real band-mix corpus with aligned ground truth that exists | CC BY 4.0 |
| **Slakh2100** (104 GB) | Large synthetic proxy for iteration and ablation | CC BY 4.0 |
| **GMD / E-GMD** | The drum transcriber and the beat grid it feeds | CC BY 4.0 |
| **ASAP** | Score-layer validation — the *only* corpus pairing real audio with a human-written MusicXML | CC BY-NC-SA 4.0 |

ASAP is **non-commercial**, and it is used anyway: #139 category 4 permits
NC material for private evaluation, since audio never enters the repo and
never ships. It is solo piano and therefore the wrong genre — its job is
narrow and it should not be cited for anything else: it is the only place
a `musicdiff`/MV2H notation harness can be **validated against a real
human score at all** before being pointed at band material. Slakh carries
its own standing caveat, stated by MulTTiPop's authors and endorsed by
#124: **good Slakh numbers do not transfer to real recordings.**

**Real-world genre check**: the project owner's own audio paired with
human-written scores, spread across the genres this converter targets.
**Numbers recorded in the repo, files never in it** — the audio is
commercial music and the scores are third-party publications. Measurements
derived from them are facts and are publishable; the material is not.

The corpus this project would most like does not exist: **no MusicXML
reference for band material exists anywhere** (#124 §1.4). Either a
handful of hand-notated bars over MulTTiPop segments gets produced, or the
notation layer is scored `musicdiff`-against-our-own-earlier-output — a
*regression* metric, not an accuracy one. That distinction must be stated
every time a notation number is quoted; #133 carries the choice.

### Metrics: a triple, never averaged

Reporting one number would hide the thing that matters. **F1 is not the
headline metric here** — it weights a hallucinated note and a missed note
equally, and for output a human corrects in this repo's own editor they
are not equal.

1. **F0.5, with P and R always beside it.** β=0.5 weights precision twice
   as heavily as recall. It is one line over what `mir_eval` already
   returns and stays legible to anyone who knows F1. **β is a stated
   editorial choice, not a measurement** — "this project holds a
   hallucinated note to be about twice as expensive as a missed one" — and
   it moves if a real editing session says otherwise.
2. **Ghost rate and miss rate, per bar, separately, never merged.** A
   ghost must first be *found*, which means reading every bar against the
   recording; a miss is discovered by the listening pass that would have
   happened anyway and fixed with one keystroke. Per **bar** because that
   is the unit a human scans — a 2-notes-per-bar ballad and a
   16-notes-per-bar riff at equal F1 are not equal work.
3. **Editor operations to correct** — the metric only this project can
   compute. `score_editor_display.py` already exposes a closed, countable
   mutation set (`note_toggle`, `transpose_note_at_cursor`,
   `cycle_duration`, `clear_to_rest`, `insert_column_at`,
   `delete_column_at`), and `load_score()` already turns a reference
   MusicXML into an `EditorScore`. The minimum operations transforming
   output into reference is a literal measure of effort in the units the
   user actually spends. It prices what F1 cannot see: **right pitch,
   wrong duration is one `cycle_duration` press**, while a wrong pitch is
   a delete plus a place — and a wrong time signature is one operation,
   not a hundred wrong barlines.

Underneath: `mir_eval` 0.8.2 at its documented defaults for note events
(reported *with* the defaults, since every published number in #124 §5
used them); `musicdiff` 5.2's OMR-NED for notation, which reads the
MusicXML this repo already writes; MV2H's **Meter** and **Note Value**
sub-metrics once a score layer exists, those being the only citable
numbers for the two things this map commits to guessing.

**Not reported as a headline: MUSTER `MeanER`.** #124 established via
arXiv 2608.04511 that it correlates with *playback* preference (ρ=0.79)
far more than notation similarity (ρ=0.38), so calling it "notation
accuracy" would be wrong.

### Alignment

A human-written score has no timestamps. **MV2H's `-a` DTW flag** is the
ready-made answer, with Nakamura's MIT-licensed alignment tool as the
alternative. `musicdiff` sidesteps the problem entirely — it compares
notation to notation and needs no timeline at all, which is a large part
of why it is Tier 1.

### Error analysis

The map's brief is a statistical breakdown, not a score. Every evaluation
run emits, alongside the triple: a breakdown by **genre, instrument,
tempo, polyphony density and SNR**, and the **bounds where accuracy breaks
down** rather than one averaged figure. Per-stem numbers are reported
separately and never pooled into a single mix-level number, since
MulTTiPop's own exact-vs-harmonic/percussive split moves YourMT3+ by 12
points and pooling would hide exactly that.

A **per-stem matching rule** has to be stated before any multi-track
number is quoted: which output stem is scored against which reference
part. #124 flags this as un-set, and an unstated rule is how a number
becomes meaningless.

### The bar

Stated **before** any algorithm is chosen, so the target cannot drift to
meet the result — the map's own requirement.

**The product-level bar, which decides usable/not-usable:** correcting a
converted score must cost **fewer than half the editor operations of
entering it from scratch**. Measured with metric (3). This is the only bar
that means anything, because a converter that costs more effort to fix
than to bypass is worth nothing regardless of its F1, and it is stated in
the units the owner actually spends.

**Per output style**, since #129 established these are different problems
with different ceilings:

| Output | Bar | Field context |
|---|---|---|
| **Real Book chart** (melody + chords, #141) | chord accuracy ≥ **75%** MajMin; melody **F0.5 ≥ 0.65** | field ceiling 75–80% chords; 71.07 F1 published for real pop vocals |
| **Full multi-track notation** | **measured and reported, not gated** | ~38% is roughly the field ceiling; a bar here would be theatre |

That asymmetry is deliberate and is the honest position: the Real Book
path is held to a real standard because the field can meet one; full
band-mix notation is measured and published in this project's own voice
without pretending a threshold makes it good.

**Regression bar:** no change ships that lowers F0.5 or raises ghost rate
on the committed suite. This one is absolute and needs no judgement.
