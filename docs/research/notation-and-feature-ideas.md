# Research: alternative terminal notation representations, and feature ideas

## Question

The `tab` view's visual output (a scrolling grand-staff with real
noteheads, duration glyphs, and barlines — see `terminal_tab_display.py`,
`staff_map.py`) works and the user likes the aesthetic. But the *internals*
that produce it are fiddly: per-column mutable dicts pushed one at a time,
combining-Unicode-mark composition (`STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH`
glued onto a notehead) whose real terminal-cell width had to be reverse-
engineered against `pyte` (issue #82, `_char_display_width()`), hand-rolled
diatonic-step math for staff placement (`staff_map.diatonic_step()`), and a
font-rendering limitation with no code-level fix (the treble clef's
bottom-clipping — see "Known limitations" in `CLAUDE.md` and the issue #20
investigation in `docs/DECISIONS.md`). Getting *editing/tweaking this
further* right increasingly means understanding zero-advance combining-mark
grapheme rules, not music.

This doc asks: (1) are there fundamentally different ways to represent
notation in a terminal that stay "true to music notation" while being
easier for a hobbyist to hand-edit and extend going forward, and (2) what
concrete new features would make this app more useful, given its stated
real-time/portable/terminal-only goals.

Sources consulted: this repo's `CLAUDE.md`, `docs/DECISIONS.md`
(specifically the notehead-style/legend/dimming/barline-drift/issue #82
entries), `terminal_tab_display.py`, `staff_map.py`, `config_store.py`
(read in full for this doc), plus external research on ABC notation
([Wikipedia](https://en.wikipedia.org/wiki/ABC_notation),
[abc:standard:v2.1](https://abcnotation.com/wiki/abc:standard/v2.1/)) and
braille music notation
([NLS Music Notes](https://blogs.loc.gov/nls-music-notes/2023/01/braille-music-basics-accidentals-time-and-key-signatures/),
[RNIB](https://www.rnib.org.uk/living-with-sight-loss/education-and-learning/braille-tactile-codes/braille-music/)).

---

## Part 1: notation-representation concepts

### Concept A — ABC notation as the live data model, current staff as one *renderer* of it

**What it is.** [ABC notation](https://en.wikipedia.org/wiki/ABC_notation)
is a 30+-year-old plain-text music notation format, designed from the start
to be both human-typeable and machine-parseable — it's the de facto
standard for folk/traditional tune archives (thousands of tunes on
[abcnotation.com](https://abcnotation.com)). A tune is a header of single-
letter fields (`X:` reference number, `T:` title, `M:` meter, `L:` unit
note length, `K:` key) followed by a body line of letters `A`–`G` for
pitches (lowercase = one octave up, `,`/`'` shift further), digits/`/`
suffixes for duration relative to `L:`, `^`/`_`/`=` for sharp/flat/natural,
and `|` for barlines. E.g. a C-major scale in quarter notes, 4/4:

```
X:1
T:Live capture 2026-09-01 14:32
M:4/4
L:1/4
K:C
C D E F | G A B c |
```

The proposal: instead of `TabDisplay` accumulating a `deque` of per-column
dicts (`TabEntry`/`BarlineEntry`, mutated in place as durations finalize —
see `terminal_tab_display.py`'s `_open_notes` bookkeeping), the live
pipeline appends to a growing **ABC body string** (or an equivalent flat
list of `(pitch, duration, decorations)` tuples that serialize 1:1 to ABC
tokens). Rendering the scrolling staff becomes: take the last N tokens,
run a ~40-line ABC-token-to-staff-row mapper (pitch letter + octave marks
→ `staff_row()`, same math as today; duration digit → duration glyph, same
lookup as today) over them.

**Terminal mockup** (this is *already* close to what ships — the point is
what sits underneath it):

```
tempo=118bpm  time=4/4  notes=symbol  legend=on  src=mic  sens=1.0

𝄞 ┬─────────────────────────────────────────────
  │      𝄆♩       𝄆♩              𝄆♩
  │  ♩       ♩         𝄆♩    ♩
──┼──────────────────────|────────────────
  │
𝄢 ┴─────────────────────────────────────────────
      C4    D4    E4     |    F4    G4

abc-so-far: "C D E F | G A" ...
```

**How "true to music notation" is it?** Genuinely more than the current
approach in one specific sense: the *underlying representation itself* is
recognized music notation (not just the rendered pixels/glyphs on screen)
— which means the exact same in-memory data can be handed to any existing
ABC tool (transposition, playback, PDF/PNG rendering via `abcm2ps`,
MusicXML conversion) with zero translation layer. The on-screen staff
rendering doesn't get any more accurate than today's (same grand staff,
same glyphs) — this concept changes what's *underneath* the rendering, not
the rendering itself.

**How the user would tweak it.** This is the concept's real payoff. ABC's
own header fields are already the exact "small textual key = value, edit
by hand" shape `config.toml` already trains the user on:

```toml
# config.toml, hypothetically extended
[notation]
export_format = "abc"          # or "musicxml", "off"
abc_header_T = "Live session"  # T: field template
abc_header_M = "4/4"           # mirrors --time-signature
```

More importantly, because the live token stream *is* ABC (or one step from
it), the user's future tweaks — "I want triplets shown differently," "I
want a repeat-sign shorthand for a held note," "I want grace notes" — are
mostly just "add a case to a ~10-line token→glyph dict," the same shape as
today's `FLAG_GLYPHS`/`_NAME_STYLE_DURATION_SUFFIXES` dicts already are.
The difference is the token *vocabulary* is now a documented external
standard the user can look up (`abcnotation.com`'s reference), rather than
this project's own bespoke duration-class strings
(`"dotted-sixteenth"`, etc. — still fine internally, but not a standard
anyone else's tooling speaks). It also opens a genuinely new, low-effort
feature for free: `virtualnote transcribe song.wav --export-abc out.abc`
writing a file every existing ABC editor/player can open.

**Cost.** A real, if bounded, rewrite: `TabDisplay`'s internal history
needs to become (or be built alongside) a token sequence, and duration/
barline finalization needs to append/rewrite tokens rather than mutate
column dicts. Not a drop-in — a deliberate architecture change to
`terminal_tab_display.py`'s data model, on the order of the #55 rhythm
work itself.

---

### Concept B — ASCII guitar/chord-chart tab style: one line per string/voice, monospace-native

**What it is.** Traditional ASCII guitar tab (as seen on any tab archive
site) is a set of horizontal monospace lines, one per string, with fret
numbers placed at the time position they're played:

```
e|-----------------3-----5-----|
B|-------3-----5---------------|
G|---2-----4--------------------|
D|-------------------------------|
A|-------------------------------|
E|-------------------------------|
```

Applied to this app (which has no fretboard, just detected pitch), the
natural adaptation is a **piano-roll-as-text**: one text row per pitch
class or octave band, time flowing left→right, a marker character at each
row a note is sounding on, duration shown by run-length (dashes) rather
than a duration glyph:

```
tempo=118bpm  time=4/4
C5 |-----------------------------|
B4 |-----------------------------|
A4 |-----------------●───────────|
G4 |-----●───────────────────────|
F4 |-----────────────────────────|
E4 |●────────────────────────────|
D4 |-----------------------------|
C4 |-----------------------------|
   0s        1s        2s        3s
```

Each row is exactly one octave-band or pitch-class lane; a held note draws
as a run of `─` from onset to release, a fresh onset as `●`. This is
structurally the closest of all five concepts to what a plain Python
script can generate with `str` slicing and no Unicode combining-mark
subtlety at all — every cell is exactly one printable character, one
column, always. No `_display_width()`/`wcwidth` reverse-engineering
required (compare: issue #82's entire barline-drift saga existed *only*
because of the current design's combining marks).

**How "true to music notation" is it?** Honestly, less than the current
staff or ABC — this is a piano-roll/tracker convention (see Concept C),
not staff notation. A reader who reads sheet music doesn't get pitch
*names* for free the way a staff gives via vertical position + a legend;
they'd need the row labels (already planned above) to read pitch at all,
and there's no visual encoding of octave doubling, key signature, or
enharmonic spelling the way a staff's letter/space system carries
implicitly. It is, however, *unambiguous about time* in a way the current
tab view's onset-driven, drift-prone barline placement isn't (see the
Known Limitations note on approximate barline placement) — because it's
laid out against real elapsed seconds rather than a beat-accumulator
guess.

**How the user would tweak it.** This is the strongest "simple to edit"
candidate of the five. The whole rendering is: for each visible time-slice
column, for each active pitch-lane row, pick one of three characters
(`●`/`─`/` `) plus a color. A `config.toml`-style `[notation.pianoroll]`
table could plausibly expose the marker characters themselves as literal
strings (`onset_char = "●"`, `sustain_char = "─"`), the same "small
declarative override, no restart" pattern `[colors]` already uses for
hue — genuinely just string substitution, no glyph-metrics knowledge
needed to change. Row granularity (per-pitch-class vs. per-octave-band)
would be one more numeric-preference knob alongside `tab_scrollback_
seconds` in the existing Settings screen pattern.

**Cost.** Smaller than Concept A — this is closer to a new render mode
than a data-model change; `TabDisplay`'s existing `entries`/timestamps
already carry everything needed (`e.t`, per-note `pitch_class`/`octave`,
now-finalized `duration_class` or measured on/off times). Realistically
buildable as an additional `notehead_style`-style toggle or a wholly
separate view mode, without touching duration tracking or chord
recognition at all.

---

### Concept C — Tracker-style step-sequencer grid (MOD/XM/IT convention)

**What it is.** Music tracker software (ProTracker, FastTracker, Impulse
Tracker, and every "tracker" descendant) represents a song as a grid: rows
= fixed time steps (e.g. 1/16 note each), columns = channels/voices, each
cell a fixed-width text token like `C-4 01 40` (note-name+octave,
instrument number, volume/effect). Applied here (monophonic + up to
`CHORD_MAX_NOTES=6` polyphonic voices):

```
Row  Ch1      Ch2      Ch3      Ch4      Ch5      Ch6
--------------------------------------------------------
000  C-4 ..   E-4 ..   G-4 ..   ...      ...      ...
001  ...      ...      ...      ...      ...      ...
002  ...      ...      ...      ...      ...      ...
003  D-4 ..   F-4 ..   A-4 ..   ...      ...      ...
004  |bar|
005  ...      ...      ...      ...      ...      ...
```

Each row is a fixed hop/beat subdivision (this app already has a natural
one: the analysis hop, or a snapped subdivision of the live tempo
estimate); a cell holds a note only on the hop it re-attacks, `...` means
"still sustaining or silent" (tracker convention: no explicit sustain
marker at all — a note plays until the next non-blank cell in that
channel or an explicit note-off).

**How "true to music notation" is it?** The least of the five in the
traditional sense — trackers are their own 40-year-old convention, not
staff notation, and don't encode pitch spelling, key, or duration-as-
note-value at all (duration is implicit in row spacing × tempo). Its
honesty is different: it is *maximally true to how a grid-quantized
digital pipeline like this one actually measures things* — each row really
is one hop or beat-fraction, which is exactly this app's own internal
sampling grid (`config.WINDOW_SIZE`, `hop_seconds`). It would read
"correct" to anyone who's used FastTracker/Renoise/OpenMPT, an audience
that plausibly overlaps this app's own hobbyist users more than sheet-
music readers do.

**How the user would tweak it.** Very editable, but in a different
direction than ABC: the whole feature space is column layout (voices side
by side vs. stacked) and cell-token format (`C-4` vs `C4` vs the app's own
existing chord-symbol conventions) — a `TRACKER_CELL_FORMAT` string
template in `config.py`, or a `[notation.tracker]` `config.toml` table
with a `row_hops` (quantization) knob, would cover most future tweaks with
plain string formatting, no Unicode/font concerns whatsoever (every glyph
is plain ASCII).

**Cost.** Similar to Concept B — a new render path over existing data, no
data-model change required for the monophonic case; chord mode (already
carrying a `note_stack` of up to 6 simultaneous notes) maps onto multi-
channel columns fairly directly. The one real complexity is: unlike
`TabDisplay`'s current variable-width, onset-driven columns, a tracker
grid genuinely wants fixed-size rows, which means picking (and living
with) a quantization grid rather than rendering each detected onset
exactly where it landed.

---

### Concept D — Braille music cells as a compact duration+pitch encoding (glyph substitution only, not a new architecture)

**What it is.** [Braille music](https://www.rnib.org.uk/living-with-sight-loss/education-and-learning/braille-tactile-codes/braille-music/)
encodes *both* pitch (upper 4 of 6 dots → which of 7 note letters) and
duration (lower 2 of 6 dots → note value) in a single 6-dot cell, with
separate octave-marker cells preceding a note when the octave changes.
Unicode has a full Braille Patterns block (`U+2800`–`U+28FF`, one
codepoint per one of the 256 possible dot combinations) — meaning a
braille music cell is always exactly **one codepoint, one terminal
column, zero combining-mark composition** — the polar opposite of the
current notehead+stem+flag+dot four-codepoint cluster that caused issue
#82's whole barline-drift investigation.

**Terminal mockup** (using real braille music cell values — pitch+duration
combined per Recommendation of the international braille music code):

```
tempo=118bpm  time=4/4
⠐⠹ ⠐⠝ ⠨⠹ ⠐⠓  |  ⠨⠹ ⠐⠝ ⠐⠹ ⠐⠳  |
C4 D4 E4 F4     G4 A4 B4 C5
```

(Each pair here is an octave-marker cell + note cell; the letters below
are a legend row this app would still render, since almost no sighted
terminal user reads braille music cells fluently — same spirit as today's
letter-column legend for the staff.)

**How "true to music notation" is it?** In a narrow, literal sense, very —
it's an actual internationally standardized music notation (used for real
sheet music by blind musicians), not an approximation. But it's the wrong
kind of "true": it's optimized for *tactile* reading, not *visual* sighted
reading, and its dot-pattern-to-pitch mapping is opaque to a sighted
reader without memorizing the code (unlike a staff, where vertical
position alone conveys pitch to anyone who's seen sheet music before).
Practically, this app's target user reads a screen, not fingertips — using
braille cells as the *visual* notation would be a novelty, not a
readability win, and doesn't fix anything about the treble-clef-clipping
or dimming/age-fade features that already work.

**How the user would tweak it.** Trivially — it's the smallest possible
change of the five: swap `NOTEHEAD_GLYPH`/`STEM_GLYPH`/`FLAG_GLYPHS`/
`DOT_GLYPH`'s composed-cluster approach for a single lookup table
`(pitch_letter, duration_class) -> one braille codepoint`, i.e. delete the
combining-mark machinery `_char_display_width()` exists to work around
entirely, replacing four-codepoints-that-a-terminal-renders-as-two-cells
with one-codepoint-that-is-always-one-cell. This *directly* eliminates the
issue #82 class of bug (barline drift from width miscounting) at the
root, since there is no longer any zero-advance combining mark in the
notehead cell to miscount.

**Cost.** Small and surgical if adopted purely as a glyph-set swap (not a
data-model change) — but it trades a real problem (combining-mark width
math) for a real regression in at-a-glance readability for a sighted user
who doesn't already know braille music. Best framed as a *technical* fix
to consider borrowing from (the "one codepoint per note-cell, no
combining marks" idea), not a wholesale notation style to ship as-is.

---

### Concept E — Named-JSON/YAML session log as the source of truth, current staff kept as one view over it

**What it is.** Not a *visual* notation style at all — a data-model-only
proposal, closest in spirit to what `config_store.py` already established
as this project's own precedent for "user-editable, hot-reloaded, plain
text." Instead of (or alongside) `TabDisplay.session_history`'s
in-process list of namedtuples, every finalized note/chord/barline is
appended to a flat, append-only line-oriented log:

```
{"t": 0.00, "kind": "note", "pc": 0, "octave": 4, "duration": "quarter"}
{"t": 0.52, "kind": "note", "pc": 4, "octave": 4, "duration": "quarter"}
{"t": 1.04, "kind": "barline"}
{"t": 1.06, "kind": "chord", "notes": [[0,4],[4,4],[7,4]], "name": "C", "duration": "half"}
```

(JSON Lines — one self-describing object per line, human-diffable, no
schema migration ever required, trivially `tail -f`-able while the app
runs.)

**How "true to music notation" is it?** Orthogonal to the question, by
design — this concept doesn't change what's *rendered*, only what's
*recorded*, and it's explicitly not meant to compete with Concepts A–D as
a rendering style. It's listed because it's the cheapest, lowest-risk step
toward "easier to tweak," and because several of this doc's Part 2 feature
ideas (recording/playback, export, stats) all need *some* durable session
record, and right now none exists — `session_history` lives only in
`TabDisplay`'s in-memory list for the process's lifetime, gone the instant
the process exits except for the ANSI `dump_ansi()` text file.

**How the user would tweak it.** Maximally simple, and it's the one
concept requiring zero new glyph/Unicode/staff-math knowledge at all:
JSON Lines is editable with any text editor, `jq`, or a five-line Python
script — a user wanting a new export format writes a ~20-line converter
reading this log, rather than touching `terminal_tab_display.py`'s render
loop or duration-glyph composition at all. It also composes cleanly with
Concept A: an ABC exporter is just "read the JSONL log, emit ABC tokens,"
no coupling to the live render path required.

**Cost.** Very low to add (a few lines in `main.py`'s already-existing
finalization call sites — `finalize_duration()`, `push_barline()`), but by
itself it changes *nothing* about the "fundamentally different, more
music-notation-true rendering" half of the ask — it only unblocks
future tweaking/export work. Best understood as infrastructure the other
concepts (especially A) and several Part 2 features (recording, export,
stats) would all sit on top of, not a competing rendering style.

---

## Part 1 summary table

| Concept | "Truer to real notation"? | Editability for a hobbyist | Rendering-side cost | Data-model cost |
|---|---|---|---|---|
| A. ABC as source of truth | Yes — the model itself is a real, standard notation format | High — small documented token vocabulary, external tools too | Low (reuses today's staff renderer) | High (rearchitects history storage) |
| B. Piano-roll-as-text | No — different (but honest) convention | Highest — 3 literal characters, no Unicode subtlety | Low–medium (new render mode) | Low (reuses existing per-note data) |
| C. Tracker grid | No — a different 40-year-old convention | High — plain ASCII cell templates | Low–medium (new render mode) | Low, except quantization choice |
| D. Braille cells | Yes, literally — but wrong audience (tactile, not visual) | High for the glyph table itself | Low (glyph-set swap) | None |
| E. JSONL session log | N/A — infrastructure, not a rendering style | Highest — plain text, any tool | None (additive) | Low (append-only log) |

---

## Recommendation

**Adopt E now, as pure infrastructure — a JSON-Lines session log alongside
the existing `dump_ansi()` text dump — regardless of anything else this
doc recommends.** It's a few lines, it's strictly additive (doesn't touch
`TabDisplay`'s render path or any existing test), and every other useful
thing this doc proposes (ABC export, session playback, practice-mode
scoring, stats) needs *some* durable record of what was actually played,
which does not exist today beyond a per-run ANSI text file with no
structured fields. This is the same "small, boring, unlocks everything
else" move `config_store.py` itself already represents for settings.

**Then pursue A (ABC as source of truth) as the actual notation-
architecture change, but scoped as an *additional* export/import path
first, not an immediate rewrite of `TabDisplay`'s live rendering.**
Concretely: keep `terminal_tab_display.py`'s current staff renderer and
combining-mark notehead glyphs exactly as they are for the live view (they
work, the user likes them, and a live rewrite risks re-litigating issue
#82's whole hard-won width-measurement fix for no rendering-quality gain)
— but build a converter from the new JSONL log (Concept E) to ABC text,
and from ABC text back into `TabDisplay` push calls for `virtualnote
transcribe`'s output. This gets the "true to music notation, in a
standard others' tools understand" win and the "small text-token
vocabulary that's easy to extend by hand" editability win, without
touching the one part of the render pipeline that's actually working and
already hardened against a real, documented bug class (issue #82). If,
after living with that for a while, the user still wants the *live*
scrolling view itself to be ABC-token-driven rather than column-dict-
driven, that becomes a much lower-risk follow-up ticket once the ABC
token vocabulary and conversion logic already exist and are already
tested against real transcription output.

**Why not B, C, or D as the primary recommendation**, explicitly weighed
against this project's own stated conventions:

- B (piano-roll) and C (tracker) are both *easier to build and edit* than
  the current staff, genuinely — but they're a strictly less traditional-
  music-notation-literate representation than what's already shipped
  (CLAUDE.md's own framing: "more true to music notation," not "easier to
  build regardless of notation-fidelity"). They're better filed as
  *additional* view modes (a third `notehead_style`-equivalent choice, or
  a new `virtualnote tab --style pianoroll`) than as a replacement for the
  staff the user already said they like the aesthetic of.
- D (braille cells) is worth stealing from technically — the "one
  codepoint, zero combining marks" property is a genuine, targeted fix for
  the issue #82 bug class — but shipping it as the *visible* notation
  style would make the view less readable for this app's actual (sighted)
  audience, which cuts against "more true to music notation" in the sense
  that actually matters here (legible to someone who reads sheet music).

**On the Rich/Textual question this doc was asked to weigh explicitly:**
neither is recommended, and the reasoning should be explicit rather than
just "the raw-ANSI convention says no." This project's raw-ANSI-everywhere
convention has exactly one deliberate, scoped exception — the Settings
screen's `blessed` app, settled by a real grilling (#37/#39) specifically
because that screen needed genuine interactive form controls (field
navigation, "capture the next keypress," clamped numeric entry) that raw
ANSI genuinely can't do cleanly. None of Concepts A–E introduce that kind
of interactive-form requirement — they're all still "redraw a fixed
region every frame," exactly the shape raw ANSI already handles well and
`TabDisplay.render()` already does today. Pulling in Rich or Textual for
a *rendering* change (not a new interactive form) would mean adopting a
whole framework's abstraction layer (widgets, layout engines, its own
diffing model) to solve a problem — "make notation easier to hand-edit" —
that a data-model change (JSONL log, ABC token vocabulary) already solves
more directly, more portably (this app's own stated Pi-class-hardware
constraint means every new dependency is a real wheel/install-risk
question, the same reasoning that already ruled out `aubio`/`librosa` on
the live path), and without touching a rendering pipeline that already
works and is already hardened against a real, hard-won bug (#82). If a
*future* feature genuinely needs interactive widgets (e.g. Feature 3's
practice-mode scoring UI, if it grows a live settings-like config screen),
that's the moment to reconsider `blessed` (already a dependency, already
precedented) before reaching for a heavier framework — not a reason to
adopt one now for notation rendering.

---

## Part 2: feature ideas

Each idea is scoped against this app's stated goals (real-time,
Pi-class-portable, terminal-only, "make this app more useful") and, where
relevant, notes whether it depends on Part 1's Concept E (JSONL session
log) as a prerequisite.

### 1. Session recording + playback (`virtualnote replay session.jsonl`)

**What.** Every `tab`-view session already produces an on-quit ANSI dump;
this extends that into a genuinely *replayable* artifact. With Concept E's
JSONL log in place, add a `virtualnote replay <file>` subcommand that
re-drives `TabDisplay` (the exact same `push`/`push_notes`/`push_barline`/
`finalize_duration` calls the live pipeline makes) from the recorded
timestamps instead of live audio — effectively a deterministic re-run of
what was actually played, at real speed or a `--speed 2x` multiplier.

**Why it fits.** No new audio/detection code at all — pure playback of
already-recorded data through an existing render path. Directly useful for
a hobbyist reviewing "what did I actually play" without re-recording, and
for showing someone else a session without needing them present live.

**Scope/complexity.** Small once Concept E exists (a timestamp-driven
event-replay loop, maybe 100–150 lines); without it, this feature has no
data to replay from at all.

**Upside/downside.** Upside: cheap, safe (read-only, no audio pipeline
changes), immediately demoable. Downside: only as good as what got logged
— doesn't capture raw audio, so it can't "sound" the session back, only
show its notation.

---

### 2. Export to ABC / MusicXML / standard MIDI file

**What.** `virtualnote transcribe song.wav --export-abc out.abc` (and,
more ambitiously, `--export-midi out.mid` via a small hand-rolled Standard
MIDI File writer — SMF format 0 is simple enough not to need a new
dependency: a handful of variable-length-quantity-encoded delta-time +
note-on/note-off byte events). Exports whatever `batch_transcribe.
transcribe()` or a live session (via Concept E's log) produced into a
format real notation software (MuseScore, Finale, any DAW) can open.

**Why it fits.** Closes a real, concrete gap: right now nothing this app
detects survives past the process (beyond a plain-text ANSI dump) in any
form another tool can consume. This is the single highest-leverage
"connect this hobby project to the wider music-software world" feature on
this list, and ABC export specifically falls out almost for free if
Concept A's token vocabulary already exists internally.

**Scope/complexity.** ABC export: small (a JSONL/column-history → ABC
text-line serializer). MIDI export: small-to-medium (SMF byte format is
well-documented and genuinely simple to hand-roll, no library needed —
consistent with this project's own "hand-roll rather than add a wheel-risk
dependency" convention already established for YIN). MusicXML: larger
(verbose XML schema) — lowest priority of the three.

**Upside/downside.** Upside: real interoperability, no new runtime
dependency needed for MIDI specifically. Downside: export quality is only
as good as this app's own pitch/duration/tempo detection accuracy, which
CLAUDE.md itself already documents as "varies run-to-run" — an exported
file inherits any wrong note/duration same as the live view does.

---

### 3. Practice mode: play-along scoring against a target melody

**What.** Load a short target (an ABC string, or a simple pitch-sequence
file) and, while the user plays along live, score each detected note
against the expected one at that beat position — hit/miss/early/late,
shown as a running accuracy readout in the status line or a per-note
color cue (green = matched, red = missed/wrong pitch) layered onto the
existing `tab` view.

**Why it fits.** This is the most "new capability, not just new export"
idea on the list, and it's a natural extension of machinery that already
exists: onset detection, pitch/chord recognition, and duration tracking
are all already computed every hop (see Architecture) — practice mode is
"compare what's already detected against a loaded target," not new
detection work.

**Scope/complexity.** Medium: needs a simple target-file format (ABC
again is a strong fit — a short target tune is exactly what ABC already
represents well) and a scoring/alignment algorithm (even a naive
"nearest-expected-beat" match, no full dynamic-time-warping, would cover a
first version). Config/keybind wiring (loading a target file, a `Practice`
menu entry) follows the existing `virtualnote <view>`/Settings-screen
patterns closely.

**Upside/downside.** Upside: genuinely differentiates this from "just a
visualizer" — turns it into a practice tool, likely the single most
motivating feature for a hobbyist musician user. Downside: real
complexity risk in the scoring/alignment logic (rhythmic tolerance, what
counts as "close enough," how to handle a wrong note that's also
early/late) — likely needs its own round of live iteration/tuning the way
chord-mode and rhythm-mode thresholds already did, not a one-shot ship.

---

### 4. Historical play stats (`virtualnote stats` or a Credits-screen-style summary)

**What.** Aggregate simple stats across saved sessions (Concept E logs) —
total practice time, most-played notes/keys, a rough "how in-tune/on-time
were you" summary, session count over time. Rendered as a static raw-ANSI
screen, same convention as the existing Credits screen (`credits_
display.py`) — no interactivity needed, so no `blessed` exception
required here either.

**Why it fits.** Zero new detection work — pure aggregation over data this
app would already be logging once Concept E ships. Low-risk, high
"makes the hobby project feel alive over time" payoff.

**Scope/complexity.** Small: a stats-computation module reading a
directory of JSONL logs, plus a `credits_display.py`-shaped static render
screen. Genuinely a weekend-sized feature once Concept E exists.

**Upside/downside.** Upside: cheap, safe, additive, no real design risk.
Downside: only as motivating as the underlying data is interesting — a
single hobbyist playing solo may not generate enough session variety for
the stats to feel meaningful without playing for a while first.

---

### 5. Multi-track / multi-instrument session view

**What.** Extend the `tab` view (or a new mode) to show two independently
detected sources side by side or overlaid — e.g. mic + `--source loopback`
simultaneously (a duet with a backing track, or vocal-over-instrument)
rather than the current single active source at a time (`M` toggles
between them, doesn't run both).

**Why it fits.** The architecture already treats source selection as
swappable state (`AudioCapture.restart()`, `SourceState`) — running two
`AudioCapture`+analysis-thread pairs concurrently and merging their
`RenderItem`s into one `TabDisplay` (as two colored voices, or two
side-by-side staves) is a natural, if nontrivial, extension of the
existing threading model (Architecture: "three threads, connected by
non-blocking queues at every boundary").

**Scope/complexity.** Large: doubles the audio-capture/analysis-thread
machinery, needs careful queue/state design to keep the "no stage can ever
stall another" guarantee intact for two independent pipelines, and the
render side needs real layout work (two staves, or two colored note
streams sharing one). The single biggest-scope idea on this list.

**Upside/downside.** Upside: a real, distinct capability (ensemble/duet
visualization) nothing else on this list offers. Downside: meaningfully
larger than every other idea here — likely its own multi-ticket project
(map-issue-and-children shaped, like #47→#48-54 was for rhythm), not a
single scoped ticket; probably lowest priority unless there's a concrete
duet/ensemble use case driving it.

---

### 6. Loop/section markers for review

**What.** While frozen (`Space`) in `tab` view, let the user mark a
start/end point in the currently-scrolled-back history (reusing the
existing Left/Right scrollback from issue #77) and have `R`'s non-causal
rhythm re-analysis, or a future export (Feature 2), scope to just that
marked range instead of the whole rolling buffer.

**Why it fits.** Almost entirely wiring on top of two features that
already exist (freeze + scrollback from issue #23/#77) — no new detection,
no new rendering primitive, just a start/end timestamp pair threaded
through already-existing `erase_barlines()`/`correct_duration()`-style
range-scoped calls (which already take `start_t`/`end_t` half-open
intervals, per `terminal_tab_display.py`).

**Scope/complexity.** Small — two new keybinds (mark start/end), a small
amount of state in `main.py` alongside the existing `ReanalysisState`, and
routing an optional range into calls that already support one.

**Upside/downside.** Upside: cheap, composes cleanly with existing
freeze/scrollback/reanalysis features rather than adding a parallel
mechanism. Downside: fairly narrow value on its own — mostly useful as
supporting infrastructure for Features 1/2/4 (scoping a replay/export/
stats query to "just that phrase I played"), less compelling standalone.

---

## Notes on scope discipline

Every idea above is written to reuse existing architecture (the three-
thread pipeline, `TabDisplay`'s push/finalize API, `config_store.py`'s
overlay pattern, the existing freeze/scrollback/reanalysis machinery)
rather than propose new subsystems where an existing one already covers
the need — consistent with this project's own documented preference for
narrowly-scoped, evidence-based changes over speculative rearchitecture
(see how issues #67/#68/#74/#75's chord/multipitch fixes were each scoped
to one root-caused failure mode rather than a general rewrite). Concept E
(the JSONL session log) is the one piece of infrastructure that unlocks
the most other value here (Features 1, 2, 4, and partially 3) for the
least cost, and is the concrete first step this doc would suggest actually
picking up, ahead of any bigger notation-architecture decision.
