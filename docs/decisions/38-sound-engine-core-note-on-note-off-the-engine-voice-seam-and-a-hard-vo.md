# Sound engine core: note-on/note-off, the Engine/Voice seam, and a hard voice cap (map #99, ticket #112, decision #105)

The rationale for *what* was built is decision #105 itself (`gh issue view
105`) and is not repeated here. This entry records the implementation
choices #105 left to the build, and the numbers the implementation was
verified against.

**Two modules, not one.** `sound_engine.py` holds the seam (the `NoteOn`
event, the `Engine`/`Voice` `typing.Protocol`s, `VoiceManager`,
`SoundEngine`); `tone_engine.py` holds the one concrete engine that ships
with it. This mirrors `detection_backends.py` only partly on purpose:
that module keeps its Protocols and its two adapters together because the
adapters are ten lines of argument-forwarding each, while a real engine is
a whole instrument (#113's subtractive synth brings mip wavetables, a
resonant filter, two envelopes and an LFO, plus SciPy behind an extra).
Keeping engines out of the seam's own module is what makes "swap the
default engine" a one-line change in `sound_engine._default_engine()`
rather than surgery.

**Why `ToneEngine` exists at all.** #112 is explicitly a seam ticket, but
a seam with nothing behind it cannot be tested end to end and would have
left `virtualnote replay --play` silent until #113 landed. `ToneEngine`/
`ToneVoice` are map #24's existing instrument — identical
`config.PLAYBACK_HARMONIC_WEIGHTS` stack, identical ADSR constants —
reshaped from "synthesize a whole note of known duration" into a
block-rendered voice with a resumable envelope, which is precisely the
per-voice state #103 said must survive across blocks (oscillator phase,
envelope stage and level, velocity). `playback.synthesize_note()` itself
could not be reused: it needs the note's total length up front, and under
note-on/note-off that length does not exist at note-on. The two are
verified to be the same instrument by a spectral test asserting the
rendered partials' proportions match `PLAYBACK_HARMONIC_WEIGHTS`.

**`schedule_note_off()` is caller-side sugar, not a second primitive.**
#105 rules out a duration-carrying primitive, and `Voice` accordingly
only ever learns note-on and note-off. But both existing callers *do*
know each note's duration, and making each one grow its own timer thread
would be worse in every way. The compromise: `SoundEngine.
schedule_note_off(voice_id, delay_seconds)` records a deadline resolved
by the audio callback's own frame clock — no thread, no sleep jitter,
accurate to one block by construction (11.6ms at the defaults), the same
"timing is an index computation, not a wall-clock one" property that
makes `render_offline()` sample-accurate. It is a scheduling convenience
on the engine, not a shape any `Voice` or `Engine` implementation ever
sees.

**Stealing reads "quietest among the released, oldest breaking ties".**
#105's wording ("oldest-released first, quietest among those") admits two
orderings; the implemented rank is `(amplitude, seq)` over the released
voices, because amplitude is the perceptual criterion — stealing an
inaudible tail is silent, while stealing a loud one that merely happens
to be older is not. Age is retained as the tie-break, and remains the
*only* criterion once every voice is still held (a held note's level is a
performance decision, not an aging signal, so ranking held notes by
loudness would steal whatever the player is leaning on). `seq` is a
monotonic allocation counter rather than a timestamp: immune to
wall-clock jitter and to list reordering during retirement.

**A stolen voice is dropped outright, not fast-released.** A ~5ms forced
fade would avoid the click that an abrupt cut can produce. It is not
implemented in v1 because it needs a `Voice`-level "release in N
samples" concept that every engine (FluidSynth included, whose voices
this repo does not own) would have to honour — a Protocol widening #105's
own reasoning warns against making before a second real engine exists to
design against. Revisit when #117's SF2 engine lands and can say what it
can actually do.

**Two polyphony preferences, not one.** #100 measured ~40 voices safe
standalone and ~24 with this app's real analysis work running in the same
process, so `[preferences]` carries `polyphony_standalone` and
`polyphony_with_detection` as two independent Settings-screen numeric
fields, and `SoundEngine` takes `detection_active` as a *callable* rather
than a bool — which budget applies depends on whether the analysis thread
is running at the moment a note is played, and that can change during one
process's life (menu → editor → a live view). `VoiceManager.polyphony`
re-reads through `config_store` on every note-on, the same mtime-checked
hot-reload every other live setting here uses, and floors at 1 (a zero
budget would mean every note is stolen instantly, i.e. silence rather
than a smaller instrument).

**`LiveScheduler` was deleted, not deprecated.** #105 says superseded;
leaving it in place would leave a second voice-mixing `OutputStream`
callback in the process, competing for exactly the device and GIL #100
identified as the binding constraint — and a second, duration-carrying
way to play a note, which is the thing #105 rejected. Its four unit tests
went with it; `render_offline()`/`play_offline()` are untouched, and
`tests/test_playback_callers.py` now asserts both that they still behave
and that `playback.LiveScheduler` no longer exists.

**Two lazy starts, not one.** `SessionState.ensure_sound_engine()` is
deliberately separate from `ensure_started()` rather than folded into it:
a tool can want audio input without output (every existing live view) or
output without input (the score editor, and #119's coming synth tool),
and folding them would make opening the editor turn on the mic's
"listening" indicator for nothing — the exact side effect issue #40's
lazy input start exists to avoid. `virtualnote replay --play` builds its
own `SoundEngine` instead of using `SessionState`'s, because that entry
point never constructs a `SessionState` at all (see `virtualnote.py`).

**Verified numerically, with the machine muted.** All 53 new unit tests
run without opening a device: the audio callback is called directly with
a plain NumPy buffer, so mixing, tanh clipping, frame-clock advance,
note-off deadline resolution and voice retirement are all asserted on
real sample values. `scripts/sound_engine_smoke.py` covers what only a
real device can report — PortAudio's own callback status flags. Measured
on this machine (44100Hz, 512-frame blocks, 11.61ms deadline): 24 voices
held for 3s → 3.16ms mean / 8.76ms p99 callback time (27% / 76% of
deadline), **0 driver status flags**, every voice reclaimed after
release; 40 voices → 3.83ms mean / 11.76ms p99 (33% / 101% of deadline),
still **0 status flags** — which is #100's "over-budget is not the same
as an xrun" finding reproducing exactly, and the reason the cap is hard
rather than load-driven; 20 note-ons against a cap of 8 → 8 voices held,
12 stolen, 0 status flags, i.e. the hard cap enforced on a real device,
with no note refused. **Not verified:** that any of it sounds correct.
The machine was muted for the whole build; timbre, click-on-steal
audibility, and release smoothness need a listening pass.
