# 74. The log knob's floor is a lift-off rung, not a shared lower bound (issue #238)

#236 gave `ShortDelay` its drawer entry, node type and panel, and left one
thing behind on purpose: its `time` knob could not be turned through the
part of its range it exists for. `time` runs from 0.1ms, and the
sub-millisecond end is where the module stops being a delay and becomes the
comb a flanger is made of — but `step_value()`'s log path clamped to
`config.SYNTH_PARAM_LOG_FLOOR` (1ms), so one press down from a millisecond
landed on the spec minimum, one press back up returned to a millisecond,
and nothing in between was reachable by hand. Both extremes worked; the
sweep a user actually turns did not exist.

#236 left it alone because the clamp is shared. Every log-scaled knob in
the app reads that constant, so a fix is a change to all of them — which
is why this is a decision and not a one-line patch.

## 1. What the constant was actually for

The old rule was one expression, in two places:

```python
floor = max(float(spec.low), config.SYNTH_PARAM_LOG_FLOOR)
```

and the bug is entirely in `max`. Read `config.py`'s own comment and the
constant's purpose is narrow and precise: *a ratio step can never leave
zero on its own, so the first press has to jump.* It answers one question —
**where does a knob whose minimum is zero land on its first press up?** —
and that question only exists for a spec whose minimum *is* zero. A spec
whose minimum is already positive has no lift-off problem at all:
multiplying 0.0001 by 1.3 works exactly as well as multiplying 1.0 by 1.3.

`max()` silently promoted a lift-off rung into a shared lower bound. For
every spec in the app but one that promotion was invisible, because their
minima are either exactly zero (the envelope stages, the LFO rates, glide)
or already at or above a millisecond (the Delay's time, the mod envelope's
stages, the canvas LFO's rate). `ShortDelay`'s `time` is the first spec
that legitimately lives below the constant, and it is the one the bug
amputated.

So the fix is to say what was meant:

```python
def log_floor(spec):
    low = float(spec.low)
    return low if low > 0.0 else float(config.SYNTH_PARAM_LOG_FLOOR)
```

The constant keeps its job, its value and its meaning. What changes is that
it now speaks only for the specs it was written about.

## 2. Why not the other three fixes

**Lower the shared constant** (to 0.0001, say). This was the obvious move
and it is the worst of the four. It does not just extend one knob's range —
it changes where *every zero-minimum knob* lands on its first press up. An
amp envelope's attack would leave zero at 0.1ms instead of 1ms, spending
nine more presses crossing a sub-millisecond span that is inaudible on an
attack stage, and the LFO's rate would lift off at 0.0001 Hz — one cycle
every two and three-quarter hours. It pays for one knob's bottom end by
putting useless travel at the bottom of a dozen others, and it still leaves
the rule wrong in principle, merely wrong somewhere less visible.

**Give `ParamSpec` its own `log_floor` field.** Correct, and more machinery
than the problem has. The spec already carries the answer in `low`; a
second field would be a place for it to disagree with itself, and every
existing spec would need a value it does not need. Rejected as a cure for a
question the data already answers — but noted as the escape hatch if a spec
ever genuinely wants a lift-off rung other than its own minimum.

**Derive the ratio per spec so a fixed number of presses spans the range.**
This is the most tempting reframing — "every knob crosses in 40 presses" is
a nice property — and it is the one that breaks something real. The filter
cutoff's ratio is `SYNTH_PARAM_CUTOFF_RATIO`, a semitone, chosen in
decision #107 point 5 for a musical reason: one press is the same musical
distance at 100 Hz as at 10 kHz, and a sweep is heard in semitones. A rule
that recomputed that ratio to hit a press count would throw that away to
make the knob shorter. Press count is a *consequence* worth checking, not a
target worth designing to.

## 3. TUI and GUI: the same rule, one copy of it

The clamp existed twice — `tui/synth_params.py`'s `step_value()` and
`gui/synth_view.py`'s `_rotation_for()`. They are not the same computation:
one decides what the next press produces, the other where the knob's hand
points. They do want the same floor, and they have to, because a knob whose
presses reach 0.1ms while its hand pins every sub-millisecond value to the
hard left is a knob that lies about what it is doing. So `log_floor()` is a
function in `synth_params.py` — beside the `ParamSpec` it takes and the
`step_value()` that is its main caller — and the GUI imports it rather than
re-deriving it.

That is also why `_rotation_for()` was left where it is rather than moved:
the angle range (-135..+135) and the choice-spec index fraction are Qt
knob-drawing concerns with no place in a pure model module. What moved is
the one line the two genuinely share.

## 4. The press count, checked rather than assumed

The property worth having is that a log knob takes a *hand-turnable* number
of presses to cross its range: enough that the value someone wants does not
fall between two presses, few enough that finding it is not a chore.
`tests/test_synth_params.py` asserts it generically over every log spec in
both the TUI panel and the Synth View's knob tables — **between 20 and 130
ordinary presses**, in both directions, ending exactly on the spec's own
ends.

The ceiling is really a statement about `Shift`. At ten ordinary presses
per coarse one (`SYNTH_PARAM_COARSE_STEPS`), 130 means no log knob is ever
more than thirteen coarse presses end to end. The filter cutoff sits near
that ceiling at 120 — deliberately, for §2's reason — and everything else
lands between 24 and 65. Short Delay's `time` is 24 presses across its full
0.1ms–50ms range, where before it was 16 with the bottom two-and-a-half
octaves missing.

A second generic test asserts the shape rather than the count: **no press
inside a log knob's range may be anything but a clean multiply**. The one
permitted discontinuity is the lift-off off zero and the landing back onto
it, and only for a spec whose minimum is zero. Together those two are what
stop a future spec from quietly reintroducing the cliff this ticket
removed, in some knob nobody thinks to turn by hand.

## 5. What this does not do

- **Nothing was verified by ear.** The machine is muted. Every claim here
  is a number under test — step counts, monotonicity, no-jump, the knob
  hand's angles increasing across the comb range. Whether 0.1ms–1ms on this
  module *sounds* like a flange, and whether 24 presses is the right
  feel for that sweep, are the two questions the tests cannot answer and
  the owner's ears can.
- **No knob's ratio changed**, and no spec's `low` or `high` changed. The
  only behavioural difference anywhere in the app is on `ShortDelay`'s
  `time`: it was the single spec whose effective floor the old `max()` was
  raising. That was confirmed by sweeping every log spec under both rules,
  not by reading them.
- **The `Delay`'s own `time` still starts at 1ms** and is untouched. Its
  minimum happens to equal the constant, which is a coincidence this
  decision does not rely on either way — after this change it steps from
  its own `low` like everything else.
- **`SYNTH_PARAM_LOG_FLOOR` keeps its value.** A reader who concluded from
  #238 that the constant was wrong would be reading it right and fixing it
  wrong: the constant was fine, the `max()` around it was not.

## Index

See `docs/DECISIONS.md`.
