# Prototype: practice-mode scoring feel-check

Throwaway prototype answering the first of two design questions from
feature idea 3 in `docs/research/notation-and-feature-ideas.md` ("Practice
mode: play-along scoring against a target melody"): **does a reasonable
note-matching/scoring algorithm actually produce sensible-feeling scores
against realistic synthesized test data?**

Only this question is answered here. The second question ("what does it
look like on screen while playing, live") was not reached — a future
session should pick that up, reusing `prototypes/score-editor-cursor-
concept/`'s interactive-loop pattern (`main.RawKeys`, a switchable-variant
demo) the way this task was originally scoped to.

## What this does

Reuses (does not reimplement) the scoring/matching logic already built in
the sibling prototype `prototypes/session-log-and-practice-mode/`
(`practice_scorer.score_session()`/`print_report()` — a nearest-expected-
beat matcher). That prototype exercised it once, against one 5-note
melody mixing several deviation types across different notes. This one
instead runs **four separate whole-melody variants of the same 9-note
target** (a C-major scale up and back down), each isolating exactly one
of the four failure modes the feature idea names: perfectly correct, one
wrong note, rushed timing (a whole performance drifting increasingly
early), and a dropped note — one variable at a time, so it's easy to
judge by eye whether each specific failure mode scores the way a human
would expect.

## Finding worth flagging

Variants 1, 2, and 4 score exactly as expected (100%/89%/89% pitch
accuracy respectively, low timing deviation). **Variant 3 (rushed timing)
surfaces a real limitation in the existing nearest-expected-beat matcher**:
once the drift exceeds the tolerance window, the matcher starts pairing a
played note against the *wrong* target note (e.g. the target's `F4` at
t=3.00 gets matched against a played `E4` that actually arrived at
t=3.24, intended for the *next* target note) — this reads out as a run of
`wrong_pitch` results and a trailing `missed`, not as "this whole passage
was rushed." A human listening would hear "you're speeding up," not
"you played four wrong notes in a row." This is a real design gap in the
matching algorithm the sibling prototype built, not a bug in this
feel-check script — worth a real fix (e.g. a global tempo-drift detection
pass before per-note matching) before practice mode's scoring is treated
as trustworthy, flagged here for whoever picks this up next.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/practice-mode-concept/score_feel_check.py
```
