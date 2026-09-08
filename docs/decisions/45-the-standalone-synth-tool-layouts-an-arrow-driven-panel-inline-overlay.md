# The standalone synth tool: layouts, an arrow-driven panel, inline overlays (map #99, ticket #119, decision #107)

Decision #107 settled the shape of the `synth` tool outright — four
layouts cycled with Tab, a parameter panel above an always-visible input
layer, letters and numbers that always play, full velocity from QWERTY,
custom layouts in their own file, inline overlays for patch load/save and
sample import, and a status line that says plainly when notes are a fixed
length. This entry records only what *implementing* that settled the
questions #107 left to the build.

### The tracker keyboard lives in one place, and the seam is `score_audition.py`

Ticket #120 shipped the score editor's piano mode a few days before this
ticket landed, and it already contains the two-octave tracker keyboard
(`zsxdcvgbhnjm` / `q2w3er5t6y7u`) this tool needs — as
`score_audition.PIANO_LOWER_ROW`/`PIANO_UPPER_ROW` and the derived
`PIANO_KEY_SEMITONES` table, with `pitch_for_key()` on top. Shipping a
second hand-written copy here would have passed every test either module
has, right up until the day someone corrected one of them and not the
other. "The same keyboard, nearly" is a worse outcome than two openly
different ones.

**The seam is key → semitone, and it stays in `score_audition.py`;
`synth_layout.py` adds geometry on top.** That split is what makes the
sharing hold rather than being a courtesy import:

- `score_audition.py` never draws the keyboard — piano mode types into a
  score, so all it has ever needed is "which key is which semitone".
- The synth tool's entire input layer *is* a picture of the keyboard, so
  it additionally needs "and which display cell does that key sit in",
  with black keys physically between their white neighbours. That is the
  row templates in `synth_layout.py` (`" s d   g h j "` above
  `"z x c v b n m"`), and it is the only thing they carry: every pitch
  they resolve comes back through `_semitone_of()`, which is a modulo-12
  read of `score_audition`'s own table.

The direction (new module depends on shipped module) was chosen over
moving the constants into a third shared module: `score_audition.py` is
already tested and merged, importing it costs nothing (`config` plus
`duration_tracker`, no audio, no numpy), and a "shared keyboard constants"
module with two importers is a layer that earns its keep only once
something genuinely can't depend on `score_audition`. Nothing yet does.
`tests/test_synth_layout.py`'s
`test_every_key_sounds_the_pitch_score_audition_would_play` is the guard
that keeps this honest across all three base octaves — it asserts through
`score_audition.pitch_for_key()`, so a change to either module's idea of
the keyboard fails there rather than silently diverging.

Note the two tools deliberately *behave* oppositely with the same
keyboard, and #108 already settled why: in the score editor, piano entry
is a **mode** (letters are editor commands the rest of the time), while
here playing is the job and there is no mode at all. Sharing the layout
does not mean sharing the interaction, and neither module's docstring
pretends otherwise.

### Shift bindings are not remappable, unlike the app's other 22 keybinds

This app remaps nine live-view keybinds and thirteen score-editor ones
through `config.toml`'s `[keybinds]` and the Settings screen. The synth
tool's `Shift`+letter commands (`Shift+P` patches, `Shift+W` save,
`Shift+I` import, `Shift+M` panic, and the custom-layout binds) are
deliberately **not** added to `settings_display.KEYBIND_ACTIONS`.

The reason is the always-plays invariant. `settings_display.py`'s existing
reserved-key check (`is_valid_remap_key()`) can express "not `|`, not
`h`" — a fixed, tiny denylist. It has no way to express "not any letter,
ever, in this one tool", which is what safety would require here: a remap
onto a plain letter would silently take that letter away from playing
notes, breaking the one property #107 point 5 makes the tool's foundation.
A settings screen that can put the instrument into a state where a key
stops sounding is worse than no remapping at all. Same tier, and same
reasoning, as the score editor's hardcoded Shift+Arrow transpose (#98
follow-up).

The octave shift is on `Shift`+Up/Down for the same reason, arrived at by
elimination rather than preference: every plain letter and number is a
note, so the transpose cannot have a letter of its own, and the plain
arrows are already the parameter panel's. (The first draft of
`run_synth_tool()` shipped with `Shift`+Up/Down resolving to
`param_prev`/`param_next` — leaving `SynthToolState.shift_octave()`,
`config.SYNTH_OCTAVE_SHIFT_MAX` and the status line's `oct=` field
unreachable dead code while the help legend advertised the binding. It is
caught now by `test_synth_shift_up_down_transposes_the_whole_keyboard`,
and more generally by
`test_synth_legend_advertises_only_reachable_actions`, which asserts the
legend never promises a keybind the dispatcher does not implement.)

### A kit and a synth patch at once is a routing question, not a second sound path

Layout 2 (one octave of keys plus a row of pads) plays a sampler kit and a
synth patch simultaneously. `synth_tool.ChannelRouter` handles this as a
`sound_engine.Engine` that dispatches by MIDI channel — pads on channel 9,
MIDI's own drum channel, keys on channel 0 — rather than as a second code
path through the voice manager. That keeps the whole feature to a two-line
rule, and it is also exactly the shape a MIDI controller plugs into later
(map #99's standing "MIDI-shaped from day one" decision): a device sending
on channel 10 already lands on the drums with nothing further to write.

A missing engine yields `sampler.SilentVoice`, never `None` — the `Engine`
Protocol promises a `Voice`, and a null return would push a `None` check
down into the voice manager for every caller.

**Polyphony gets a third context, not a compromise on the existing two.**
Decision #105 made the voice cap a per-context `[preferences]` setting
because #100 measured the safe figure differing ~2x between "nothing else
running" (40) and "one thread doing this app's real analysis work" (24).
#107's implementation note flagged that layout 2 multiplies the budget,
and the risk it actually creates is worth naming precisely: the hard cap
already covers both engines' voices together, so the failure mode is not
*overrun* but **starvation** — a drum hit arriving to find every slot held
by sustained synth notes. `polyphony_synth_dual` (28) is the margin that
buys that off, exposed as its own Settings-screen numeric field alongside
the other two for exactly the reason those two are separate.

`SoundEngine.set_polyphony_override()` is an override rather than a third
branch inside `polyphony_for()` so that `sound_engine.py` stays ignorant
of what a layout is; the synth tool passes a *callable*, so the budget
follows Tab presses live rather than being sampled once at entry.

### Log-scaled parameters step by ratio, and the ends stay reachable

A cutoff sweep that adds a fixed number of Hz per press is unusable: it
crawls at the bottom of the range and leaps at the top, because brightness
and pitch are perceived logarithmically. `synth_params.SCALE_LOG` steps
multiply instead, so one press is the same musical distance everywhere —
for the filter cutoff, exactly a semitone
(`SYNTH_PARAM_CUTOFF_RATIO = 2**(1/12)`). Envelope times are log for the
same reason (5ms → 10ms matters as much as 1s → 2s).

Two ends of that need explicit handling, and both are real synth settings
rather than edge cases:

- A ratio step can never lift a value off zero, so the first press up
  jumps to `SYNTH_PARAM_LOG_FLOOR` (0.001).
- Stepping back down must *land* on the spec minimum rather than
  approaching it asymptotically forever, because "no attack at all" is a
  setting a player reaches for.

Numbers clamp and choice lists wrap — the same split
`settings_display.parse_numeric_input()` (a bounded physical quantity has
ends) and the score editor's Chord builder reels (a short ring of names
does not) already follow. Parameter *selection* clamps too: a long list of
knobs is a ruler, not a ring, and wrapping from the last row to the first
would be a surprise on every overshoot.

`Shift`+Left/Right is the coarse jump (`SYNTH_PARAM_COARSE_STEPS`, ten
ordinary presses) — #107 point 5's "Shift is the escape hatch" applied to
a sweep that would otherwise take a hundred presses to cross the filter's
range. There is deliberately no direct numeric entry: a synth parameter is
swept by ear, not typed, and a text field would be a state in which
letters stop playing.

### Which patch the panel edits, and why a kit shows only `[voice]`

`sections_for()` picks the panel's contents from the patch's own `engine`
field: a sampler kit gets `[voice]` alone, because its sound lives in its
zones — which are *files*, changed by importing a sample onto a pad — not
in a bank of knobs. Showing eight dead oscillator sections would
misrepresent what is actually adjustable.

`SynthToolState.panel_patch()` then decides *which* patch is on screen: a
pads-only layout with a kit loaded edits the kit (editing an oscillator
you cannot currently play would be a panel about nothing); every other
case, dual layouts included, edits the keys' patch, since the keys are the
half with knobs worth sweeping and the pads' sound is changed by importing
a different sample onto them.

Loading routes by engine rather than asking: a sampler patch becomes the
pads' kit, anything else the keys' sound. This is decision #106's "one
file kind, not three" paying off in practice — there is one browser, one
Enter, and no "which slot am I loading into?" question to put to the user.

### Typing in the save overlay both types and sounds

The most surprising consequence of the always-plays invariant, and it is
kept deliberately: in the save overlay, typing a patch name appends the
character *and* sounds the note. A text field that claimed the keyboard
would be exactly the state #107 point 5 rules out, and the overlay's own
body says so on screen ("typed keys still sound — letters always play")
rather than leaving it a surprise to discover. `Shift`+letter commands and
the arrows still resolve as actions first, so the overlay stays navigable.

### A custom layout's shape is fixed by whichever built-in it was copied from

`Layout.rebind()` replaces a slot's binding while keeping its row and
column, and returns `None` for a key the layout does not contain. #107
point 4 scopes custom layouts to *rebinding keys*, not to inventing new
rows — a layout describes your hands, and the built-ins are already the
arrangements worth starting from. Keeping the geometry fixed also means
the input layer never shifts under the user's hands mid-edit.

The bind target is "whichever key was played last", which is what lets
`Shift`+B and `<`/`>` work with no mode and no cursor: pointing at a key
is literally playing it, so you hear what is currently bound before
changing it, and the arrow keys stay the parameter panel's.

Custom layouts save to `~/.config/note-color/layouts/*.toml`, their own
directory, independent of patches — binding a key arrangement into a patch
would mean redoing the arrangement every time the sound changed, the same
mistake #106 already refused for MIDI CC numbers. `slugify()` is
deliberately conservative (anything but a letter, digit, dash or
underscore becomes a dash) so a name typed in the save overlay can never
escape its own directory.

### Pads tint by their sample, via a byte sum rather than `hash()`

#107 point 1 says pads tint by their assigned sample rather than a pitch
class — a pad has no pitch to be honest about, and tinting a snare by
"pitch class D#" would be a colour that means nothing.
`synth_layout.sample_hue_step()` is a plain UTF-8 byte sum modulo 12,
deliberately *not* Python's `hash()`, which is randomised per process: a
pad that changed colour on every launch would be worse than no colour at
all. The requirement is "different samples usually look different and one
sample always looks the same", which a byte sum meets; cryptographic
spread is not wanted here.

Note keys use `color_map.fifths_index()` unchanged, so a C on this
keyboard is the same colour as a detected C in every other view — the
whole reason for reusing the fifths palette rather than inventing a
keyboard palette. Only lightness moves between a key at rest
(`SYNTH_KEY_DIM_LIGHTNESS`) and a key sounding
(`SYNTH_KEY_LIT_LIGHTNESS`); hue and saturation are the key's identity,
exactly as `tab`'s age-fade already treats a note's colour.

`synth_display.KeyLights` gives each key its own `ColorAnimator` rather
than sharing one across the input layer. Several keys sound at once —
that is what a keyboard is — and a shared animator would smear every
simultaneous note into one average colour, the same reason chord mode
already keys `fill`'s band animators per note. `dt` is injected and no
clock is read, so the animation steps deterministically under test.

### Its own `shell.py` dispatch branch, for `edit`'s reason

The synth never opens the microphone, so routing it through
`main.run_session()` (which calls `session.ensure_started()`) would open a
mic for an instrument that has no use for one. But it also doesn't fit
`_NON_SESSION_SCREENS`' shape, whose four entries always loop straight
back to the menu: `run_synth_tool()` returns the same `"menu"`/`"quit"`
sentinel a real tool does, so its result has to be interpreted. That is
precisely `edit`'s situation (#98), and it gets precisely `edit`'s
solution — its own branch, taking `session` for one reason only: the
process-wide `SoundEngine` (#105), so menu → synth → editor → a live view
never tears down and reopens the output stream.

The `finally` block restores `sound.engine` and clears the polyphony
override unconditionally. The engine is process-wide, so leaving the synth
must hand it back exactly as it was found or every later view would play
through the synth's router.

### Verified without a TTY, and what that leaves open

The machine this was built on is muted and has no interactive terminal, so
nothing here has been *heard*. What is verified is the dispatch: ticket
#120's scripted-`RawKeys` pattern drives the real `run_synth_tool()` loop
headlessly against a fake sound engine, so "which keystroke becomes a note
on which MIDI channel", "what a Tab does to the voice budget", "does
switching layouts leave a note stuck" and "is the engine restored on the
way out" are all asserted rather than assumed. `render()` itself, the
audio path, and every timbre judgement remain smoke-tested-by-hand work,
per this repo's standing convention.

`run_synth_tool()`'s standalone path (`virtualnote synth` with no session,
which builds its own `SoundEngine` and opens a real output device) is the
one branch no test exercises — opening an audio device is exactly what the
test suite avoids everywhere else in this codebase.
