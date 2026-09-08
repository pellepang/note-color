# Kitty keyboard protocol in `RawKeys`: per-view opt-in, DA1-sentinel fallback, focus-loss release synthesis (map #99, ticket #118)

Landed from issue #101's prototype (`prototypes/kitty-key-release/`,
branch `prototype/kitty-key-release`), which the project owner confirmed
live in real kitty: held keys sustain, and three simultaneous keys
release independently and out of order. Neither is possible without
genuine key-release events, which no ordinary terminal reports — a held
key and a key struck repeatedly are the same signal there, so a
sustaining instrument has no note-off to work with at all. This is the
input half of the sound engine (map #99); nothing consumes it yet.

**Flags 27 (`1|2|8|16`), not flag 2 alone.** The obvious reading is that
flag 2 (`report event types`) is the whole feature. It isn't: a key that
the terminal would otherwise send as plain text keeps being sent as plain
text on press, and a bare text byte has nowhere to carry an event type —
so no release is ever reported for exactly the letter keys a QWERTY piano
is played on. Flag 8 (`report all keys as escape codes`) is what forces
those keys through the `CSI … u` encoding where a release can be
expressed. Flag 1 disambiguates, and flag 16 (`associated text`) lets the
*terminal*, not this code, decide what a keystroke means as text under
whatever keyboard layout is installed — which is what lets
`kitty_keys.legacy_token()` hand existing callers the same plain
characters they get today (Shift+a is `"A"` because the terminal said so,
not because this code guessed).

**The DA1 sentinel is what makes the fallback safe.** `CSI ? u` (the
protocol query) is answered only by a terminal that speaks the protocol;
one that doesn't answers *nothing*, which turns capability detection into
"wait for a reply that may never come." Sending `CSI c` (Primary Device
Attributes) immediately after it fixes that, because every VT-lineage
terminal in existence answers DA1: if the DA1 reply arrives and no
protocol reply came before it, the terminal definitively lacks support,
and it settles *immediately* rather than waiting out
`config.KITTY_NEGOTIATION_TIMEOUT`. The timeout only ever covers
something pathological (a pty with nothing on the far end), and is
asserted bounded in `tests/test_rawkeys.py`. The other half of the same
requirement is that negotiation must not eat input: a keystroke typed
while the probe is in flight is recovered from `CapabilityProbe.leftover`
and queued as ordinary input rather than dropped. Both fallback paths —
DA1-only and answers-nothing — are tested harder than the success path,
because they are the ones every non-kitty user takes.

**Per-view opt-in (`want_kitty=False` by default), not process-wide.**
Negotiation costs a terminal round trip; pushing the mode once at process
start would mean every `|` back-to-menu round trip inherits it, and the
detection cost would be paid where nothing benefits. More importantly,
`want_kitty=False` *is* today's behaviour byte-for-byte — no probe is
written, no mode is pushed, `poll()` takes the same code path it always
did — so all 11 existing construction sites and 15 `poll()` sites needed
no changes at all. A view that actually wants held notes (the synth tool)
opts in; everything else pays nothing. This is the same posture `P`/`M`'s
render-thread-local flags already take: opt into the cost where it buys
something.

**`poll()`'s contract is unchanged, via a legacy-token shim.** With flags
27 pushed, an ordinary letter arrives as `CSI 97;1:1 u` rather than the
byte `a`, which would silently break every caller comparing against
single characters. `kitty_keys.legacy_token()` maps the richer stream
back down: a release returns nothing *and `poll()` keeps draining rather
than returning None* (a note-off must never make a menu look like nothing
was pressed); auto-repeat maps to the same token a press does, so holding
Down on the tool picker still scrolls; a bare modifier press maps to
nothing, so Credits/Stats' "press any key" screens aren't dismissed by a
stray Shift. `parse_key_event()` is a strict generalisation of
`main._parse_csi_params()` rather than a parallel parser — every field
after the first is optional and defaults to "unmodified press", so
today's bare `ESC [ A` and issue #98's `ESC [ 1;2 A` are the degenerate
cases of the same grammar. `tests/test_kitty_keys.py` cross-checks the
two functions against each other for every arrow form rather than
trusting that by inspection.

**Three structural changes to the class, all forced.** (1) `poll()`
drains through an internal queue, because one read can now legitimately
yield several events — a chord's releases arrive in one burst — whereas
before, at most one token per call was possible. (2) `fd`/`out_fd` became
constructor parameters instead of `sys.stdin.fileno()` read inline: not
required by the protocol, required by testability, and it is what lets
`tests/test_rawkeys.py` exercise every byte path against an `os.pipe()`
(plus one genuine `pty.openpty()` pair) in an environment with no TTY.
Worth doing regardless. (3) `restore()` must pop the keyboard mode on
every path including exceptions, or the user's shell inherits a terminal
reporting every keystroke as an escape code.

**Focus loss synthesises releases rather than being reported.** If the
window loses focus mid-hold, the release for a key still physically down
is delivered to whichever window has focus *now* — never to us — and the
note hangs forever, the single worst failure mode of a held-note
instrument. `RawKeys` therefore enables focus reporting (DECSET 1004,
which predates the kitty protocol and is widely supported) alongside the
keyboard mode, tracks which keys it believes are held, and on `CSI O`
injects synthetic RELEASE events for each into its own queue. Doing it
this way rather than exposing a focus callback means focus loss is
*indistinguishable* from the user letting go of every key: any consumer
of `poll_event()` gets correct note-offs with no code of its own, and
`release_all()` is additionally callable directly on view exit. The
prototype had `release_all()` tested but uncalled; this is the wiring
issue #101 flagged as the one line still missing.

**Reporting the pushed flags, not the queried ones.** The query answers
with the terminal's flag state *before* the push, which is 0 in a fresh
kitty — so reporting it reads as "the protocol isn't active," the exact
opposite of the truth. Reaching that branch at all proves support (only a
protocol-speaking terminal answers `CSI ? u`; one that doesn't settles as
unsupported through the DA1 sentinel). `kitty_flags` is what was pushed;
`kitty_flags_before` keeps the queried value, which is the honest answer
to "what was already active". This was found during #101's live
verification and is carried forward here deliberately — a future session
debugging "is the protocol on?" would otherwise be sent in exactly the
wrong direction.

**One accepted behaviour change, currently latent.** With the protocol
active, Escape arrives as `CSI 27 u` rather than a bare ESC byte — and
today a bare ESC returns `None` from `poll()` (the burst-timeout path),
which makes `score_editor_picker.py`'s `key == "\x1b"` cancel branch
unreachable. Under the protocol it starts working. Verified benign: both
of that module's Escape branches return `None` (cancel back to the
picker/menu), which is what Escape should do and plainly what the author
intended. It is latent rather than live today, since the picker
constructs `RawKeys()` with the default `want_kitty=False`; it
materialises only for a view that opts in, and is covered by an explicit
test so it can't surprise a later reader.

**What is not verified.** tmux passthrough is untested — the failure mode
is degradation, not breakage (tmux answers DA1, so the probe settles as
unsupported and a view falls back to `FixedDurationKeys`). And the
fixed-duration fallback is honestly not a good instrument: with no
releases, "held" and "struck repeatedly" are the same signal, so a held
key extends its note (each auto-repeat press pushes the deadline out) at
the cost of merging a genuine fast repeat of the same note into one.
There is no way around that at the terminal level; documenting it is the
whole mitigation.
