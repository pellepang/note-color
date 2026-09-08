# Score editor audition, piano mode, playback, metronome, loop region (map #99, ticket #120, decision #108)

The score editor (issue #98) could write MusicXML but never made a
sound. This adds the four things #108 settled: hearing what you edit,
typing notes on the keyboard, playing a passage back, and a metronome to
play it against. New pure module: `score_audition.py`; every side effect
stays in `main.run_score_editor()`, the same split
`rhythm_reanalysis.recompute()` / `_handle_reanalysis_key()` already use.

**Piano entry is a mode, deliberately unlike the synth tool.** The
editor already binds `r i x u U z c w t , .` as commands, so letters
cannot simultaneously be notes -- decision #34's vim-style modal answer.
`piano_mode` (Shift+P) enters; the same key or `Esc` leaves; the status
line always carries `mode=edit`/`mode=piano`, because a mode you cannot
see is a mode you will be surprised by. The standalone synth tool is
mode-*free* for the mirror-image reason: there playing is the job, here
editing is, and the tool whose job is playing should never make you ask
permission to play.

**Press together = a chord, press in sequence = successive columns.**
That distinction is only observable with the kitty protocol's key-release
events (#101/#118), so this is the one view in the app whose `RawKeys` is
constructed with `want_kitty=True` -- per-view, not process-wide, so no
other screen pays the negotiation round trip. `score_audition.PianoEntry`
holds the whole rule: while any piano key is still down further presses
join the same column; once every key is up the next press starts the next
one. On a terminal with no releases, `advance_between_groups=False` takes
#108's explicit degraded path -- every press joins the current column and
the arrow keys move on. A timing heuristic was not used as a substitute:
guessing a chord from inter-key latency silently mangles either fast
melodies or slow chords, and the failure is invisible until you look at
what you wrote.

**Auto-repeat is not a second press.** With `FLAG_EVENT_TYPES` pushed the
terminal reports its own key repeat as a distinct `REPEAT` event, so only
a genuine `PRESS` enters a note. Treating a repeat as a press would
machine-gun the audition and push an undo snapshot dozens of times a
second for a key the user simply held down.

**The undo snapshot is taken before the column is appended.** One
snapshot per *chord* (the first press of a group), recorded ahead of any
mutation -- including the empty column a sequence-entered note is written
into. Recording after the append instead restores a score that already
carries that column, so undo appears to half-work: the note vanishes but
the column it lived in does not.

**Entered notes take the editor's `,`/`.` duration, never the hold
time.** Nothing here measures how long a key was down. Hold length is a
performance gesture; a score's note value is an editorial decision, and
this is an editor. A column appended by a run of piano entry inherits the
previous column's duration class (`new_column_duration()`), so a run of
eighths stays eighths without re-pressing `,` for each one.

**Audition: unconditional on placement, toggleable on movement.** You
should always hear what you just wrote, so placement never consults the
toggle. Cursor movement is different -- it is also how you get somewhere
-- so `audition_toggle` (Shift+A, default on) exists for anyone who wants
to navigate silently. Left/Right sounds the whole column, which is what
proofreading a melody actually uses; Up/Down sounds only a note genuinely
sitting on the row moved onto, because sounding the pitch of every empty
staff row would make vertical navigation a siren rather than feedback.

**Playback is expressed in beats, not seconds.** A `ScheduledColumn`
carries an *absolute* `start_beat` measured from the score's own first
column, not from wherever playback began. That is what keeps the
metronome's bar grid aligned with the score's real barlines when playback
starts mid-bar: a passage picked up on beat 3 still puts its downbeat on
the next real beat 1, rather than treating the cursor as beat one.
Seconds only appear at the edge, where the score's `tempo=` is applied.

**Playback runs in the render loop, unlike `tab`'s (#121).** #121 needed
a thread because its notes are unquantised detections whose onsets fall
anywhere, and 50ms of frame quantisation is audible against real playing.
The editor's columns are *written* note values against a chosen tempo, so
every onset already lands on a beat grid, and one editor frame
(1/`TERMINAL_FPS`) is the resolution the whole view already runs at.
Keeping it in the loop means "any key stops playback" needs no shared
flag, no thread, and no lock -- the loop already has the keystroke in
hand. The stopping keystroke is *consumed* by stopping and does not also
do its normal job: a panic key must not simultaneously edit.

**The loop region reuses `[`/`]`, and its rule is #121's rule.** No new
vocabulary: the editor's marks are the tab view's own `mark_range_start`/
`mark_range_end` bindings applied to column indices instead of history
timestamps, order-independent through the existing `_mark_range()`.
Playback covers "the marked range if one is set, else what follows the
cursor" -- deliberately the same shape #121 settled for the frozen
buffer. Where the two differ is only where their inputs differ: a cursor
*inside* a marked region starts playback there rather than at the region
start, since picking a marked section up from the middle is a thing an
editor is asked for and a scrollback buffer is not.

**The metronome is synthesised, not sampled.** A short high tone through
the same `SoundEngine` as everything else, a fifth higher on the downbeat
so bar one is audibly distinct. There is no sample library in this repo,
and map #99's sampler is about the user's own WAVs -- bundling a click
asset to avoid two `note_on()` calls would be a strange first sample to
ship. It clicks on the note value the time signature's denominator names
(4/4 once a quarter, 6/8 once an eighth), exactly as it reads on paper.

**Six remappable keybinds, four of them Shift+letter, matched exact
case.** Plain-letter space in the editor was nearly exhausted before
piano mode claimed a two-octave block of it, so `piano_mode`/
`play_from_cursor`/`metronome_toggle`/`audition_toggle` default to
`P`/`L`/`M`/`A` and join `undo`/`redo` in
`main._EDITOR_CASE_SENSITIVE_ACTIONS`. That is load-bearing rather than
tidy: `m` is B on the piano keyboard while `M` is the metronome, and a
case-insensitive match would make the two indistinguishable. For the same
reason `score_audition.is_piano_note_event()` requires *no* modifier held
(Caps Lock and Num Lock excepted -- neither is a deliberate chord), so a
modified press always falls through to normal keybind dispatch.

**Shift+Up/Shift+Down means the keyboard's octave in piano mode.** A
two-octave keyboard cannot otherwise reach the whole grand staff, and
nothing else in piano mode wants that binding; in edit mode it stays
issue #98's transpose. The current range is the one piece of piano state
you cannot otherwise see, so `oct=3-4` appears in the status line in
piano mode and nowhere else.

**Entering piano mode and playing back are both strictly non-dirtying.**
Mode switching touches no `EditorScore` state at all, and `_EditorPlayback`
reads the score and writes nothing -- unit-tested by deep-comparing the
score across a whole playthrough. Only actually writing a note is an
edit.

**A missing sound engine leaves a fully usable, silent editor.**
`_editor_sound_engine()` borrows the process-wide engine from
`SessionState` when the editor was reached from the live menu (so menu ->
editor -> synth never drops the audio device) and builds its own under
`virtualnote edit <path>`, which constructs no `SessionState` at all. Any
failure -- no SciPy (`[synth]`), no output device, PortAudio unhappy --
degrades to `engine=None`, and every audio call in `score_audition.py`
no-ops on it; the status line then carries `sound=unavailable`. This is
the opposite of the standalone synth tool's posture, which *does* refuse
to open without SciPy, and deliberately so: a synth that makes no sound
is not a synth, while a score editor that makes no sound is still a score
editor.

**Verified numerically and structurally only.** The keyboard map, mode
machine, press-together grouping, audition targets, beat schedule,
playhead, loop-region arithmetic, metronome grid and the two thin audio
helpers are unit-tested in `tests/test_score_audition.py`; the new
mutation helpers in `tests/test_score_editor_display.py`; and the loop's
own *dispatch* -- which keystroke becomes a note, which advances a
column, what lands in undo -- in `tests/test_main.py`, by driving
`run_score_editor()` headlessly against a scripted `RawKeys` stand-in and
a fake engine. Nobody has listened to any of it, and no real TTY has
negotiated the kitty protocol for this view: whether press-together
grouping feels right at speed, whether audition/metronome levels sit
well, and whether the (now considerably longer) status line still fits a
normal terminal all need a human at a real terminal with working audio.
