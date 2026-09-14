# The quality dial, the conversion cache and long-run UX (issue #134)

The dial was supposed to trade an hour of compute for accuracy. Measuring the
pipeline first — map #123's own rule — changed what the ticket was about: there
is no hour to trade yet, because nothing in the stack spends one.

Full measurement protocol, per-pass numbers and install findings:
`docs/research/convert-pass-costs.md`.

### What a conversion actually costs

Measured on the target box (i5-7300U, 2 cores / 4 threads, no GPU), 45 s clips,
the whole `[convert]` stack co-installed in one venv:

| Pass | RTF | Peak RSS |
|---|---|---|
| `htdemucs` separation, default overlap | **1.28–1.41×** | 1300 MB |
| `htdemucs --overlap 0.05` | 1.06× | 1319 MB |
| Beat This! beats + downbeats | **0.17×** | 528 MB |
| `nmp.onnx`, per stem | **0.04×** | 108 MB |
| `transkun` v2, default 8 s hop | **1.52×** | 1482 MB |

For a 4-minute song: piano mode ≈6.8 min, band mode ≈6.4–6.9 min, band with
`htdemucs_ft` ≈32 min (#126's figure, not re-measured). **Separation dominates
band mode and everything else is rounding error** — all four stems transcribe in
36 seconds.

Two corrections to earlier tickets fall out of this. `htdemucs` measured
**1.28–1.41× against #126's 2.02–2.14× on the same machine**, ~35 % faster,
presumably a torch/demucs version change; and **transkun's cost is
content-dependent** (+46 % at the default hop once there are notes to decode),
so #126's "timing is content-independent" covers the separator only.

### The ruling that reshaped the ticket

The project owner's call on those numbers: **~6 minutes is the *fast* setting**,
not the top of a quality dial. Map #123 allows ~1 hour per song, so roughly 50
minutes of budget is unspent and nothing currently spends it.

So the dial ships with **two positions, both of which do something measurably
different**, and the top is earned rather than asserted:

| Position | What it does | 4-minute song |
|---|---|---|
| **Fast** | `htdemucs` default overlap, per-stem transcription | ≈6.5 min |
| **Quality** | `htdemucs_ft` | ≈32 min |
| ~~Max~~ | **Ships with #215, not before** | — |

**Max is not shipped empty.** A third position that runs identically to the
second is a lie in the interface, and "a dial that maps onto nothing real is
worse than no dial" is this ticket's own brief. #215 exists to earn it.

### Two dial axes that were measured and rejected

**`--overlap` on the separator.** #126 recommended it as the dial's fast end. It
saves 80 seconds on a 4-minute song. Not worth a position a user has to
understand.

**`transkun`'s segment hop.** The intuitive "look more carefully" knob is
actively harmful, and this is the ticket's one measured accuracy result — 495-note
rendered piano, MIDI-exact ground truth, `mir_eval` ±50 ms onsets:

| hop | cost | notes | P | R | F1 | F0.5 | duplicates |
|---|---|---|---|---|---|---|---|
| 16 s | 0.64× | 482 | 1.0000 | 0.9737 | 0.9867 | 0.9946 | 0 |
| **8 s (default)** | 1.52× | 495 | 1.0000 | 1.0000 | **1.0000** | **1.0000** | 0 |
| 4 s | 3.06× | 617 | 0.8023 | 1.0000 | 0.8903 | 0.8353 | **120** |

Below the model conf's own hop, transkun emits **both** copies of every note in
an overlapped region. Twice the compute for a score with 120 doubled notes,
which under #132's precision-weighted F0.5 is the most expensive error class
there is. **The hop is pinned to the conf default and exposed nowhere**; the
adapter-level dedupe that makes the seam robust regardless is **#216**.

Caveat kept attached to those numbers wherever they are quoted: synthetic
GM-piano audio, one seed, MIDI-exact ground truth — an easy best case, which is
why the default row reaches 1.000. The claim is the **ordering and the
duplication mechanism**, not the absolute figures.

### Determinism, and what it means for #215

Every model in the stack is **bit-exact across runs** — transkun byte-identical
MIDI, `nmp.onnx` bit-identical posteriorgrams, Beat This! identical arrays.
Repeating an unchanged input is therefore unanimous by construction. Any
consensus scheme must **perturb the input**, which is exactly the rate-based
slow-down the owner proposed and #143 independently recommends for its exact
invertibility. Recorded here because it is a standing constraint on #215, not a
result of it.

### The cache

**A bounded, derived, deletable cache** at
`$XDG_CACHE_HOME/note-color/convert/`, holding separated stems and each pass's
symbolic output. Map #123 chartered this in principle; the boundary it left open
is settled in one line:

> **Nothing in a score file ever points into the cache.** Deleting the whole
> directory can make the next run slow. It can never corrupt a project.

That is the property that makes the carve-out from map #24's no-raw-audio-
persistence rule safe. #24 was protecting the score of record, and the score of
record remains a `.musicxml` and nothing else.

**Keyed per pass, not per run**:
`sha256(audio) + pass name + the settings that produced it + the model version`.
A changed model or setting simply misses and recomputes — an upgrade can never
hand back a stale stem as if it were fresh. The Fast/Quality relationship then
falls out with no special-casing: the beat grid's key does not mention the
separator, so it **hits across both settings**; the stems' key does, so it
misses. Re-running Fast at Quality therefore saves ~40 seconds of a 32-minute
run — real, but not why the cache exists. **Its actual jobs are making a repeat
of the same settings instant, and making a crash or a cancel cost one pass
instead of the whole run.**

**Bounded by size, not age**: a ~5 GB default cap with least-recently-used
eviction, plus an explicit clear command. Stems run ~160 MB per 4-minute song,
so twenty songs fill ~3 GB. An age cap punishes someone who converts one album a
month; a size cap punishes nobody.

### Long-run UX

A **per-pass progress indication with an ETA**, which is honest here for a
reason that rarely holds: separation cost is content-independent and the file's
duration is known up front, so the estimate is arithmetic on measured real-time
factors rather than a guess. Demucs already prints its own progress bar.
**Ctrl-C cancels at a pass boundary**, so a cancelled run leaves its completed
passes in the cache and resuming is free. No dedicated TUI view — `convert` is a
batch command the user walks away from.

**The quality choice is a dialog in VisualNote Studio**, with the terminal path
taking `--fast`/`--quality` and asking inline when run bare — the same shape
#129 already decided for input mode (asked once at load, flags override), and it
keeps `convert` scriptable.

**First-run order is refusal, then terms, then quality.** `ConversionUnavailable`
fires first if the extra is not installed (#129's posture), then #139's
weights-terms prompt for the 132 MB fetched at first use, then the dialog. The
user is never asked to choose between two settings that cannot run yet.

### What this opens

- **#215** — the top of the dial: multi-pass consensus over perturbed inputs,
  and better weights. Its own finding, recorded there: the band path runs a
  **36,037-parameter** model where the piano path runs a **14,087,028**-parameter
  one, chosen for its licence rather than its accuracy. Spending the unused 50
  minutes on a bigger model is one adapter behind an existing seam, where
  consensus is a new algorithm — so weights get measured first.
- **#216** — the transkun seam-duplicate dedupe.
- Three install pins the `[convert]` extra must carry, all found by building it:
  `torchaudio` from the CPU index (the PyPI build is CUDA-linked and kills
  transkun), `audioop-lts` (transkun → pydub → `audioop`, gone from the stdlib in
  3.13), and `soundfile` (Beat This! 1.1.0's loader calls `torchaudio.load`,
  which no longer works on torchaudio 2.11). The full stack otherwise co-installs
  clean on 3.14 in a 1.6 GB venv plus 132 MB fetched at first use.
