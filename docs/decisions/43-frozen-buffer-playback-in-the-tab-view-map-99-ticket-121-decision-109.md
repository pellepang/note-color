# Frozen-buffer playback in the `tab` view (map #99, ticket #121, decision #109)

Decision #109 settled the shape of this feature (frozen playback only, no
live sonification; `Enter` to start and stop; marked range if set, else
what is visible; the full detected stack in chord mode; `tab` only). This
entry records only the implementation calls that decision left open.

**Where "what is on screen" comes from.** `TabDisplay.render()` decides
which history entries fit by walking newest-to-oldest against a width
budget, and that walk was previously inlined in `render()`'s body.
Playback's default scope is *exactly* that set, so rather than
re-deriving it (a fixed column count, say), the walk was extracted into a
pure `select_visible_entries()` plus a `column_width_for()` helper, and
`render()` and a new `TabDisplay.visible_entries()` method both call it.
This is the whole reason playback can never disagree with what the user
is looking at across a terminal resize, an `L` legend toggle, a notehead
style change, or a scrollback offset -- all four change how many columns
are on screen, and all four are already inputs to that one function. The
terminal-size lookup stays in the method; everything with a decision in
it is in the pure function, and is unit-tested there.

**A marked range is not filtered against visibility.** With `[`/`]` both
placed, playback covers every note column in that time window whether or
not it is currently drawn. A mark is an explicit statement about a region
of history, and the user may well have scrolled somewhere else since
placing it; intersecting the two would make the feature's scope depend on
a scroll position the user did not think they were choosing. This matches
how `_handle_reanalysis_key()` already treats a marked range (it narrows
the whole rolling buffer, not the visible tail).

**Onsets come from recorded timestamps, lengths from `duration_class`.**
Two different sources, deliberately. A column's own `.t` reproduces the
real gaps between what was played -- rushed passages and pauses survive,
the same reason `virtualnote replay` paces itself by recorded gaps rather
than a metronome. A note's *length*, though, cannot come from the gap to
the next column: the next column may be silence, or a different voice
entirely. The measured `duration_class` is the only length this pipeline
ever produced, so that is what sounds, converted against whatever tempo
the status line is currently showing (the reanalysis-corrected estimate
if `R` has run, else the live one, else
`config.TAB_PLAYBACK_DEFAULT_BPM`). A note whose duration never finalized
falls back to `duration_tracker.DEFAULT_DURATION_CLASS` -- the same
resolution `render()` already draws for it, so what is heard matches the
glyph that is drawn rather than quietly disagreeing with it.

The converted length is clamped into
`[TAB_PLAYBACK_MIN_NOTE_SECONDS, TAB_PLAYBACK_MAX_NOTE_SECONDS]`. The
tempo estimate feeding it is explicitly approximate (this repo already
documents barline drift for the same reason), and an absurd estimate at
either end would otherwise turn a thirtysecond into a click or a whole
note into a ten-second drone -- a clamp, not a correction, in the same
spirit as `settings_display.parse_numeric_input()`'s clamp-not-wrap rule
for bounded quantities.

**Fixed velocity.** Every played note uses
`config.TAB_PLAYBACK_VELOCITY`. Nothing in this pipeline measures a
per-note attack strength -- `rms` is a whole-frame measure, not a note's
own -- so deriving a dynamic from it would be dressing up data that was
never captured, which is the same objection #109 raised against
re-voicing a chord from its matched name.

**A throwaway thread, not the render loop.** Playback waits on a daemon
thread spawned by `_handle_playback_key()`, the shape
`_handle_reanalysis_key()` already established. Running it inside the
render loop instead would quantise every note onset to one frame
(50ms at `TAB_FPS`), which is audible; blocking the loop would stop the
Enter that ends playback from ever being read. The thread sleeps toward
each onset in short bounded slices (`_wait_until()`) rather than one long
sleep per gap, so a stop is acted on within a slice, and each slice
recomputes against the real clock so per-note drift can't accumulate.
Note-*offs* need no thread at all: each is handed to
`SoundEngine.schedule_note_off()`, resolved against the audio callback's
own frame clock (#105 decision 1's "a caller that knows a duration
arranges its own note-off").

**Unfreezing stops playback.** What is being played is precisely "what is
frozen on screen", and that stops being a stable thing the moment columns
start scrolling again -- so `Space` while playing sets the same stop flag
a second `Enter` does. This is consistent with the "no catch-up on
unfreeze" convention every other frozen-only piece of state here already
follows (`scroll_offset`, the corrected tempo, the marks themselves).
Nothing else about the frozen view is disturbed by playback: it reads the
history, the marks and the scroll offset, and writes none of them.

**A missing sound engine is a status-line message, not a crash.**
`SessionState.ensure_sound_engine()` can raise (`synth_engine.
SynthUnavailable` when SciPy, an optional `[synth]` extra, is absent).
The failure is caught and shown as `play=unavailable(...)`: a missing
optional dependency must not take down a view whose actual job is drawing
notes. For the same reason an empty scope (a screen of barlines and
silence columns) never reaches for the engine at all, so it can't fail
for a press that had nothing to play anyway.

**Verified numerically and structurally only.** Scope selection, chord
expansion, onset re-basing, duration conversion/clamping and the
start/stop/failure handshake are unit-tested (`tests/test_tab_playback.py`,
new cases in `tests/test_terminal_tab_display.py` and
`tests/test_main.py`) against fake engines with no audio device opened.
Nobody has yet listened to it: the timing thread's real-world accuracy,
and whether a screen of detected notes actually sounds like what was
played, both need a real TTY and working audio output.
