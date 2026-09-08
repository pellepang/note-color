# MIT, and the rules for third-party models and weights (issue #139)

Charted by map [#123](https://github.com/pellepang/note-color/issues/123)'s
research as critical path rather than housekeeping: the offline
audio-to-score converter works by downloading other people's trained
neural networks, and several of the strongest candidates carry terms
(GPL-3.0, CC BY-NC, "scientific purposes only", or nothing stated at all)
that cannot be evaluated against a project which has no licence of its
own. Until this repo said what it was, "may we use these weights?" had no
answer.

### The project is MIT

`Copyright (c) 2026 Pelle Ørevik Evensen`, declared in `LICENSE`, in
`pyproject.toml` via PEP 639 (`license = "MIT"` + `license-files`, which
is why `build-system.requires` moved to `setuptools>=77`), and stated on
the Credits screen.

The brief was "free for me and for people installing and using it," with
donations on top. MIT is the direct expression of that, and it matches
every dependency already chosen — numpy, sounddevice, blessed, wcwidth,
librosa, music21, scipy and pyfluidsynth are all BSD/MIT/ISC. Donations
are unaffected: MIT does not prevent asking for money, it prevents
*requiring* it, and `DONATION_URL` was never a payment gate.

**The status quo was the opposite of the goal.** A public repo with no
LICENSE is not permissive by default — it is all rights reserved. Nobody
could legally fork or redistribute note-color before this file existed.

Apache-2.0 was considered and rejected: its patent grant is close to
ceremonial for a music-notation tool, and it costs a longer file for
nothing this project needs. Copyleft (GPL/AGPL) was rejected because it
would restrict exactly the people the brief wants unrestricted; the one
thing it would have bought — free use of GPL-3.0 transcription code — is
handled by the rule below instead.

One licence covers the whole repo, `docs/research/` included. Contributions
are inbound = outbound (a PR arrives under MIT), with no CLA: a CLA buys
the option to relicense commercially later, which is the opposite of the
stated intent, and it deters casual contributors.

### Four categories, not one rule

"Licence" is not one axis, because four kinds of third-party material
reach this project by genuinely different routes, and only the first is
something this project distributes:

1. **Code this repo ships.** Obligations bind directly. Must be
   permissive or weak copyleft. **Full GPL is refused outright** — even
   behind an optional extra, since shipping code written specifically to
   import a GPL module makes the combined work GPL, and that would
   convert this project by side door. LGPL is fine and already present:
   `pygame-ce` is LGPL-2.1 in the *core* dependencies today, which is
   precisely the use LGPL exists to permit. The cost of this rule is
   YourMT3+ (0.5938 F1, the strongest multi-instrument candidate #125
   found) — a model whose repo says GPL-3.0 while a third-party
   redistribution of the same code claims Apache-2.0, and whose CPU cost
   is unpublished. Not a strong thing to bend a licence for.
2. **Model weights fetched at runtime.** Not redistributed by this
   project, so a non-commercial or unstated term does not bind *us* — but
   it binds the user, and they must be told before it lands. The rule is
   therefore **download only after an explicit prompt naming the terms**,
   never silently. Bundling such weights (which would be redistribution)
   is refused; so is a silent fetch, which decides on the user's behalf.
   This is the same posture `sf2_playback.py` already takes with
   soundfonts: bundle nothing, and be explicit about what the user
   supplies. It is what keeps Demucs usable — MIT code, but weights the
   maintainer confirmed to #126 are "provided only for scientific
   purposes," from Hugging Face repos with no licence field at all.
3. **Training data.** Only in play if something is ever trained here.
   Never distributed; judged like category 4.
4. **Evaluation corpora.** Used privately to produce numbers. Audio never
   enters the repo and never ships. **Non-commercial material is
   acceptable here**, because private evaluation is not distribution and
   no licence term is implicated. Refusing it would forfeit most of the
   usable ground truth (MulTTiPop, GMD, Slakh) for no gain. Measurements
   *derived* from it — F1 numbers, error breakdowns — are facts and are
   freely publishable.

The boundary between 2/4 and 1 is the load-bearing part: **non-commercial
and unlicensed material may inform a decision; it may never ship inside
one.** Written down explicitly because the failure mode of a nuanced rule
is quiet erosion into "well, it's only an extra."

### Absent and contradictory licences

Three real cases, not hypotheticals: ADTOF-pytorch has no LICENSE file,
the Demucs weight repos have no licence field, and YourMT3 states two
different licences for the same code.

- **Absent** is treated as forbidden for anything shipped, permitted for
  private evaluation — the same line category 4 already draws. Reading
  "it's public, so it must be fine" as permission is the exact error that
  left this repo accidentally locked down.
- **Contradictory** takes the most restrictive reading, unless the
  upstream author clarifies. Asking is a real option and it works: that
  is how #126 got the Demucs answer.

### Consequences, recorded so they are not re-argued

- A prompt-gated model may still be the **default** — licence does not get
  to decide pipeline architecture, it only has to be satisfied, and a
  one-time prompt satisfies it either way. Whether separation belongs in
  the default path is an accuracy question for #124's harness, and #126
  found its benefit is currently *unmeasured*: no published study compares
  a transcriber with and without separation on a shared metric.
- A prompt that cannot be pre-answered is a wall in front of batch use, so
  a `[preferences].accept_model_terms` setting will pre-accept it,
  mirroring `soundfont_path`. Consent recorded deliberately in a config
  file is stronger evidence of informed acceptance than a `y` typed to
  dismiss something, not weaker.
- **Permissively-licensed weights may be committed to the repo under a
  1 MB ceiling**; anything larger downloads. This admits `basic-pitch`
  (Apache-2.0 code *and* weights, `nmp.onnx` is 230 KB against a git pack
  currently 1.62 MiB) — which matters because #125 found the pip package
  will not install on Python 3.14, so vendoring the ONNX file is the only
  route to it. The ceiling keeps anything of consequence (ByteDance's
  ~160 MB piano model) out of git history by rule rather than by
  judgement, since a binary committed once cannot be removed without
  rewriting history.
- MuScriptor's CC BY-NC 4.0 weights are gated behind a Hugging Face token
  plus licence acceptance, so they cannot be auto-fetched at all; under
  these rules they degrade to user-supplied, if ever wanted.
- madmom stays available: its source is 3-clause BSD and the DBN downbeat
  decoder loads no model, so the CC BY-NC-SA term on its `.npy` files
  never applies to the path #127 wants.
- Beat This! (MIT code *and* weights) and transkun (MIT, weights inside
  the wheel) are clean outright and need no prompt.

### Attribution

Permissive licences require their notice to travel with the code. The
Credits screen (`credits_display.py`) carries a name-and-SPDX-licence line
per component, which satisfies that for every current dependency —
all BSD/MIT/ISC/LGPL. A `THIRD_PARTY_LICENSES.md` with full texts is
deliberately *not* written yet: it earns its place with the first **model**
whose terms are non-obvious — an NC clause the user is agreeing to, or a
required attribution — rather than as ceremony around libraries whose
obligation a single line already discharges.
