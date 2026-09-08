# SF2 engine: pull-model FluidSynth behind an optional `[sf2]` extra (map #99, ticket #117, research #102)

**`[sf2]` is a third extra, deliberately not folded into `[synth]`.**
`pyfluidsynth` is itself a pure-Python `py3-none-any` ctypes wheel —
nothing compiles on install, so #32's original "wheel/dependency risk"
objection to FluidSynth genuinely does not apply. What *does* apply is
the system `libfluidsynth` it dlopen()s: this is the only optional
dependency in this project that can fail for a reason `pip` cannot see
or fix, which is a different failure mode from a pure-Python extra and
therefore gets its own opt-in rather than riding along with one. The
audiences are disjoint anyway — `[batch]` is offline analysis, `[synth]`
is the built-in synthesis path, `[sf2]` is soundfont playback.

**Pull model only; `Synth.start()` is never called.** This is the
finding the whole engine is built on. `start()` would open FluidSynth's
*own* audio driver on its own thread — a second output device competing
with this app's `sounddevice` streams, and a second clock, which is
exactly the drift #24/#32 rejected. Without it, FluidSynth owns no
device at all and is a pure renderer: `fluid_synth_write_s16()` hands
back a block on demand, which the engine downmixes into
`sound_engine.SoundEngine`'s existing callback buffer. Verified
numerically at build time rather than assumed: `synth.audio_driver` is
`None` on a constructed engine.

**One `fluid_synth_write_s16` per callback, not per voice — so
`SF2Voice` is a handle, not a generator.** FluidSynth renders every
sounding note into one mixed buffer, so N voices cannot each render
"their" audio; this is precisely the case #105 cited for keeping `Voice`
a Protocol rather than one concrete class. Exactly one voice — the
*primary*, the oldest still-live unfinished one — pulls the shared block
per callback and the rest are no-ops. `render_voice()` accepts two
independent "this is the block's one pull" signals, either sufficient:
the caller *is* the primary, or `out` is a buffer this engine has not
seen before (a `weakref` identity check, true of every
`SoundEngine._callback`, which zeroes a fresh block each time). The
second signal covers a stray reference held outside the voice manager
that would otherwise leave the primary a voice nobody renders; the first
covers a caller reusing one buffer. They cannot double-pull, since a
manager voice older than the primary is impossible.

**The voice registry holds `weakref`s, and note-off is
generation-checked.** `VoiceManager.allocate()` steals a voice by
dropping its record outright, with no note-off — which for an engine
that owns its own voices would leave the note ringing inside FluidSynth
forever. A `weakref.finalize` per voice sends the missing note-off under
CPython refcounting. That introduces the hazard the check exists for:
FluidSynth's `noteoff(chan, key)` cannot name *which* note-on it means,
so a stolen voice's finalizer — which runs at an arbitrary moment, in an
arbitrary thread — could silence a *newer* note that has since retaken
the same key. Every voice is therefore stamped with a monotonic
generation at note-on and releases only if it is still the newest for its
`(channel, key)`. A bug found by this ticket's own test: `release()`
originally read the *current* newest generation rather than the voice's
own, which made the check compare a number against itself and always
pass, defeating the guard entirely. The voice now carries its own.

**Two polyphony numbers that must not be conflated.**
`config.SF2_POLYPHONY` is FluidSynth's *internal* cap and counts
FluidSynth **voices** — a stereo or layered preset spends two or more per
key — with its own stealing inside the library. #112's
`[preferences].polyphony_*` budget counts **notes** this app has handed
out and is enforced entirely outside FluidSynth. Both apply at once and
neither knows about the other; they are deliberately not reconciled,
because a soundfont's voices-per-key ratio is a property of the bank, not
something this app can know in advance.

**stderr is silenced twice, by two different mechanisms, because there
are two different noises.** #102 found construction writes ~16 ALSA/SDL
driver-enumeration lines to fd 2, which would scribble over any raw-ANSI
view. Those come from libasound and SDL, which never see Python's
`sys.stderr`, so only an fd-level redirect (`silence_stderr()`) works.
Building this ticket surfaced a *second*, unhandled half of the same
hazard: FluidSynth also logs at **runtime**, emitting one "Failed to
allocate a synthesis process" line per stolen voice from inside
`noteon()` whenever `synth.polyphony` is exceeded — measured here as 21
lines from a single 64-key test, long after construction. Wrapping every
`noteon()` in `silence_stderr()` would cost six syscalls per note *and*
impose a process-wide stderr blackout on every note-on, far more invasive
than the one-off construction case that redirect was reasoned about for.
#102 correctly reported that `fluid_set_log_function` is not *bound* by
`pyfluidsynth` 1.4.0; it is nonetheless reachable on the `CDLL` the
module already holds open, so `silence_fluidsynth_log()` installs a null
handler once per process for WARN/INFO/DBG. PANIC and ERR are left
reporting: they are rare and mean something is genuinely broken, and the
engine surfaces real failures through return codes (`sfload` →
`FLUID_FAILED`) rather than the log anyway. Best-effort by design — a
build not exporting the symbol keeps its warnings, a cosmetic
degradation, never an error.

**No soundfont is bundled — banks are discovered.** #102 measured usable
banks at 31–148 MB and they are loaded fully into RAM, which is not
something to put in a repo or a wheel. `discover_soundfonts()` searches
this app's own samples directory first (the most deliberate placement),
then XDG data, then the standard system locations, then a Homebrew
prefix, layered under a `[preferences].soundfont_path` setting and a
patch's own bare `[sf2].soundfont` name. A patch naming a bank that
cannot be found resolves to nothing rather than silently substituting a
different one — playing the wrong instrument is worse than reporting
none. "No soundfont found" and "no library" are reported the same way, as
status through `sf2_status()`, mirroring how `M`'s loopback switch
reports a failed `pactl` inline rather than crashing.

**One cached probe covers both failure modes.** A missing `pyfluidsynth`
and a missing system `libfluidsynth` surface as the *same* `ImportError`
at import time, before any state exists (a third — library present but
unloadable — as `OSError`), so `sf2_availability()` catches both with one
`try/except` and caches the result: the answer cannot change within a
process, and a patch browser will ask repeatedly. This extends the
`librosa`/`music21` sole-importer idiom with one step — unlike those, the
import here is *lazy*, so every decision in the module (discovery,
resolution, the voice handle, all engine bookkeeping) is importable and
unit-testable on a machine with no FluidSynth at all.

**`tinysoundfont` was re-checked at implementation time, as #102 asked,
and not adopted.** Its packaging position is unchanged: newest release
2025-06-03, wheels only to cp312, so it still compiles from source on
this repo's Python 3.14 and still carries a hard `pyaudio` dependency
duplicating the `sounddevice` stack. Its measured speed advantage is
real but irrelevant at 6% of budget. No silent switch.

**Verified numerically, with the machine muted.** Against real
`libfluidsynth` 2.6.0 + `pyfluidsynth` 1.4.0 and a real FluidR3_GM.sf2
(148 MB, the same bank #102 measured): a silent block before any note
(`|x|max < 1e-3`); a non-silent, correctly-shaped `float32` block the
*very next* block after note-on, confirming the one-block onset latency
the oscillator voices already have; RMS decaying to under half after
note-off; exactly one pull per `SoundEngine._callback` across multiple
sounding voices; **0 driver status flags**; nothing written to fd 2
either during construction or when overrunning polyphony by 32 notes.
Render cost at 64 voices with reverb and chorus **on**: **0.675–0.733 ms
mean per 512-frame block, 5.8–6.3% of the 11.61 ms budget** — #102's
0.951 ms / 8.2% reproducing comfortably. **Not verified:** that any of it
sounds correct. The machine was muted for the whole build; timbre,
bank quality and release smoothness need a listening pass. Also still
unverified from #102's own open list: macOS/Windows, and coexistence with
a live `InputStream` on a real device.
