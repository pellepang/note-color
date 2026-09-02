# Kitty keyboard protocol -- key release for held notes (prototype)

Throwaway prototype for wayfinder ticket
[#101](https://github.com/pellepang/note-color/issues/101) (map
[#99](https://github.com/pellepang/note-color/issues/99), sound engine).

**The question.** Can `main.RawKeys` be extended to negotiate the kitty
keyboard protocol and deliver distinguishable key **press / repeat /
release** events, so a held QWERTY key sustains a note -- and does it
degrade cleanly to fixed-duration notes on a terminal that lacks it?

**The short answer: yes, and the fallback is the easy half.** The whole
extension is ~200 lines, the parser is a strict generalisation of
`main._parse_csi_params()` (so today's arrow handling is the degenerate
case of the same grammar, not a special case beside it), and existing
callers can be left byte-for-byte unaffected by mapping the richer event
stream back down to today's tokens. What has *not* been established here
is what real kitty puts on the wire when a human holds a key -- that needs
the human, in kitty. See **What is still unverified**.

Does not modify any file outside `prototypes/kitty-key-release/`. It only
*imports* (read-only) from the real app: `color_map.py` for the demo's
note colors, and `main._parse_csi_params()` in one test, to cross-check
legacy-token compatibility against the real parser rather than a copy of
it.

## Files

| File | What it is |
|---|---|
| `kitty_keys.py` | All the pure logic: negotiation byte sequences, `CapabilityProbe` (detection + fallback), `parse_key_event()` (the event encoding), `legacy_token()` (backward compatibility), and the two held-note policies `HeldKeys` / `FixedDurationKeys`. No I/O whatsoever. |
| `kitty_rawkeys.py` | `KittyRawKeys` -- what `main.RawKeys` would become. Same construct/`poll()`/`restore()` contract, plus `poll_event()`. |
| `test_kitty_keys.py` | 48 tests covering everything that does not need a terminal, including every fallback path and a full three-key chord driven over a real `os.pipe()`. |
| `demo.py` | **The thing to run in kitty.** A live QWERTY piano: decoded events scroll past, held keys hold their note bars open. |
| `pty_harness.py` | Runs `demo.py` under a *simulated* terminal (a real pty this script drives), proving the whole stack end to end over real file descriptors -- against the specification, not against kitty. |

## How to run it

From the repo root, using the project's own venv:

```bash
# The part a human has to do -- run this in kitty, then again in
# something else (xterm, gnome-terminal, a tmux pane) to see the fallback:
.venv/bin/python prototypes/kitty-key-release/demo.py

# Everything that can be checked without a terminal:
.venv/bin/python -m pytest prototypes/kitty-key-release/ -q

# End-to-end over a real pty, with this script playing the terminal:
.venv/bin/python prototypes/kitty-key-release/pty_harness.py
```

`demo.py` takes no arguments. Play `a w s e d f t g y h u j k` (one
octave, piano layout), `q` quits, Ctrl+C also works.

**In kitty, what to look for:** the header should read
`kitty keyboard protocol (flags=27)`. Hold one key -- you should see
exactly one green `press`, a stream of blue `repeat`, and one red
`release`, with the note bar lit for the whole hold and going out on the
release. Hold three keys at once and let go in a different order: three
independent bars, each closing on its own key. **In any other terminal:**
the header reads `fallback`, no `release` ever appears, and notes end on a
0.35 s timer.

## What was verified, and how

Run output from `pty_harness.py` (real, not massaged -- three notes held
down at once, on a pty answering `CSI ? 27 u`):

```
  probe written by demo.py:      yes
  pushed keyboard flags:         27
  popped keyboard mode on exit:  yes
    |   NEGOTIATED  kitty keyboard protocol (flags=27) -- held notes: press/release
    |   SOUNDING
    |     a   C4   ########################################
    |     d   E4   #####################################
    |     g   G4   #####################
    |     press   key=a   mods=0  cp=97
    |     press   key=d   mods=0  cp=100
    |     repeat  key=a   mods=0  cp=97
    |     press   key=g   mods=0  cp=103
```

...then releasing `d`, `a`, `g` in that order returns the display to
`(silence)`, and the same script against a pty answering only DA1 gives:

```
  pushed keyboard flags:         none (fallback)
    |   NEGOTIATED  fallback (negotiation=unsupported) -- fixed-duration notes: 0.35s
```

Verified by unit test (`test_kitty_keys.py`, 48 passing):

- **Negotiation sequences.** `CSI > 27 u` push / `CSI < 1 u` pop, and why
  the flags are 27 (`1|2|8|16`) rather than the obvious 2.
- **The parser.** press/repeat/release distinguishable; absent fields
  defaulting to press/no-modifiers; the modifier bitmask being the
  reported value *minus one*; associated text; arrows keeping their legacy
  final letters (`CSI 1;2:1 A`); `~`-form function keys; non-key CSI
  replies (DA1, cursor-position reports) rejected rather than
  mis-decoded.
- **The physical-key rule.** A note started with Shift held must stop when
  the key comes up even if Shift was released first, so `key` is the
  unshifted key code and the shifted spelling rides in `text`.
- **Backward compatibility.** `legacy_token()` is cross-checked against
  the *real* `main._parse_csi_params()` for every arrow form, and
  releases map to "nothing happened" so a menu never sees a phantom
  keypress.
- **Both held-note policies**, including several simultaneous keys,
  out-of-order releases, swallowed auto-repeat, a release for a key never
  pressed, a press whose release was lost (retrigger rather than a stuck
  note), and `release_all()`.
- **The fallback paths, which is where this breaks if it breaks:**
  a terminal that answers DA1 only, a terminal that answers *nothing*
  (bounded by timeout, provably under 0.5 s, then normal input works), a
  reply split across arbitrary read boundaries, and a keystroke typed
  *during* negotiation surviving rather than being eaten.

Verified end to end over real file descriptors (`pty_harness.py` and the
pipe-backed tests): cbreak entry, the probe actually being written, flags
actually being pushed, the mode actually being popped on exit, legacy
tokens (`p`, `UP`, `SHIFT_DOWN`) still arriving on a non-kitty terminal,
and a full three-key chord press/repeat/release driving `HeldKeys` to
exactly the right note-on/note-off sequence.

## What is still unverified

**Everything about real kitty.** This environment has no interactive TTY,
and kitty is the owner's terminal, not the agent's. Specifically:

1. That kitty answers `CSI ? u` with `CSI ? <flags> u` in practice, and
   accepts flags 27.
2. That a held key produces `press` then `repeat`\* then `release` with
   the codes assumed here -- in particular that `report all keys as escape
   codes` (flag 8) really is required to get releases for plain letters,
   which is the load-bearing reading of the spec.
3. Whether kitty's auto-repeat rate is fast enough to matter for a
   sustaining instrument (it should be irrelevant -- `HeldKeys` swallows
   repeats entirely -- but it is worth watching in the live demo).
4. Behaviour inside **tmux**, which is a real risk: tmux must pass the
   protocol through, and its own escape handling has historically been the
   thing that breaks this. The fallback path covers it correctly (tmux
   answers DA1, so the probe settles on "unsupported"), so the failure
   mode is degradation, not breakage -- but it is worth knowing which one
   the owner gets.
5. Focus loss while a key is held. `HeldKeys.release_all()` exists for
   exactly this and is unit-tested, but nothing yet *calls* it on a focus
   event -- kitty can report focus in/out (`CSI ? 1004 h`), and a real
   implementation should wire that up or risk a stuck note.

## What the extension costs `RawKeys`' existing callers

`RawKeys` is constructed in 11 places (`main.py` x4, `shell.py`,
`credits_display.py`, `stats_display.py`, `score_editor_picker.py`,
`prototypes_display.py` x2) and `poll()` is called from 15. The
compatibility story is deliberately "they change in no way at all", and
this prototype backs that up rather than asserting it:

- **`poll()` keeps its exact contract** -- one token or `None`, same
  tokens (`"p"`, `"A"`, `" "`, `"UP"`, `"SHIFT_UP"`, ...). `legacy_token()`
  does the mapping, and is cross-checked against `main._parse_csi_params()`
  itself in the test suite.
- **Release events are invisible to `poll()`.** It keeps draining rather
  than returning `None` on one -- otherwise a note-off would make a menu
  look like nothing was pressed.
- **Auto-repeat still feeds menus.** A `repeat` maps to the same token a
  `press` does, so holding Down on the tool picker keeps scrolling exactly
  as it does today.
- **Modifier keys stay silent.** With flag 8 the terminal reports Shift
  and Ctrl presses in their own right; `legacy_token()` returns `None` for
  them, so the "press any key to return" screens (Credits, Stats) are not
  dismissed by a stray Shift.
- **Three real costs, all small:**
  1. **`poll()` needs an internal queue.** One `read()` can now yield
     several events (a chord's releases arrive together), where today at
     most one token per call was possible. This is the only structural
     change to the class.
  2. **`fd` must become a constructor parameter** instead of
     `sys.stdin.fileno()` inline. Not required by the protocol -- required
     by testability: it is what let every byte-level path above be tested
     against an `os.pipe()` with no TTY in sight. Worth doing regardless.
  3. **`restore()` must also pop the mode**, on every path including
     exceptions, or the user's shell inherits a terminal that reports
     every keystroke as an escape code. `prototypes_display.py`'s
     restore-then-reconstruct dance around running a subprocess already
     has the right shape for this; it just has one more thing to undo.
- **One genuine behaviour change**, worth a deliberate decision rather
  than a shrug: today a bare Escape press returns `None` from `poll()`
  (the burst-timeout path), so `score_editor_picker.py:156`'s
  `key == "\x1b"` cancel branch is currently unreachable. Under the
  protocol, Escape arrives as `CSI 27 u` and that branch starts working.
  That is almost certainly what the author intended, but it *is* a change.

**Recommendation:** push the mode only while a view that needs it is
active (the `synth` tool, and note-entry in the score editor), not for the
whole process. `KittyRawKeys(want_kitty=False)` is exactly today's
behaviour, so every other view can opt out and pay nothing -- including
the ~0.25 s worst-case negotiation cost, which would otherwise be paid on
every `|` back-to-menu round trip and works directly against the "instant
transition" reason `|` exists.

## Honest downsides

- **Flags 27 is more than strictly needed** (flag 16, associated text, is
  a convenience for the compatibility shim). If real kitty turns out to
  behave differently under flag 8 than assumed, the parser is unaffected
  but `legacy_token()`'s text handling would need revisiting.
- **The fixed-duration fallback is not a good instrument.** Holding a key
  extends the note (each auto-repeat press pushes the deadline out), which
  approximates sustain, but the cost is that a genuine fast repeat of the
  same note merges into one. There is no way around this: the terminal
  reports no releases, so "held" and "struck repeatedly" are the same
  signal. Documenting it is the whole mitigation.
- **`pty_harness.py` tests the code against the spec, not the spec
  against kitty.** It is a real end-to-end run over real fds, and it would
  catch a regression -- but it cannot catch a misreading of the protocol
  document, because it was written from the same reading.
