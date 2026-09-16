# 72. MIDI hardware input: note-on/off, velocity, sustain, mod wheel, pitch bend (issue #173)

Research for this ticket (#233, `docs/research/midi-hardware-input.md`) had
already settled the library, the portability story, the threading model and
what expression the graph engine can already accept. This is that plan
built: a MIDI keyboard now plays the Synth View, up to the engine's 16
voices (#180's cap, unchanged by this ticket -- see "What was deliberately
not touched" below), with real velocity, a working sustain pedal, and mod
wheel/pitch bend reaching the modulation layer decisions 66-68 already
built.

## 1. The library, and the dependency

`python-rtmidi` directly, not `mido` -- the research's own recommendation,
unchanged: the translation from raw status/data bytes to `PolyGraph.
note_on()`/`note_off()` calls is small enough that mido's backend-
abstraction layer buys nothing this project needs, and one fewer hop
matters for reasoning about which thread is calling in (§3 below).

**Licence, checked at the source per decision 47's rule** (the research
document already did this and it is not re-derived here): `python-rtmidi`
is MIT (Christopher Arndt/SpotlightKid); the RtMidi C++ library it wraps is
MIT (Gary P. Scavone), with a non-binding courtesy clause asking for
patches upstream. Same category as every other dependency in
`pyproject.toml`. No licence-driven objection, and nothing here resembles
decision 56 §10's pedalboard/JUCE reversal -- there is no embedded
GPL-licensed framework riding along.

**Declared as its own extra, `[midi]`, following the project's existing
`[sf2]`/`[synth]` convention exactly**: a Cython/C++ extension the rest of
the app must run correctly without, imported lazily (only from
`audio/midi_input.py`, only inside `ensure_started()`/`list_ports()`).
Prebuilt wheels cover manylinux x86_64/aarch64 from PyPI and piwheels
armv6l/armv7l for Raspberry Pi OS's own pip -- CLAUDE.md's Pi-to-desktop
range is a wheel install almost everywhere, not a per-platform compile,
exactly as the research found. Added to the `all` extra alongside the
others.

## 2. Lifecycle: a third independently-lazy device, with two owners

The research recommended `SessionState.ensure_midi_input()`, mirroring
`ensure_sound_engine()`. That is built (`audio/session.py`) -- but reading
the actual code that hosts the Synth View turned up a wrinkle the research
had not needed to resolve: **`visualnote studio` (`gui/app.py`'s `main()`)
never constructs a `SessionState` at all.** `gui/app.py.start_audio()`
builds its own `SoundEngine` directly and hands it to `ProjectPlayer` --
the identical "this entry point is standalone" shape CLAUDE.md already
documents for `virtualnote replay --play`, which "never constructs a
SessionState at all... being a standalone entry point." `SynthView` is
opened from `gui/studio.StudioWindow`, which is that same standalone
process.

So this ticket gives MIDI input **two lifecycle owners, matching the two
places a live view already exists in this project**, not one:

- **`SessionState.ensure_midi_input()`** (`audio/session.py`) -- idempotent
  and lazy, exactly like its `ensure_sound_engine()` sibling, for the
  session-based terminal world CLAUDE.md's "Process/session lifecycle"
  section describes. Nothing in the terminal tools asks for live MIDI yet
  (none of them host a canvas a MIDI keyboard could play), so this exists
  as the matching third accessor the moment one does, not as dead
  scaffolding -- it is unit-tested (`tests/test_session_midi.py`) exactly
  like its sibling, including that a session which never calls it never
  opens a port.
- **`SynthView`'s own `MidiInput`** (`gui/synth_view.py`) -- the real,
  reachable path. One instance per `SynthView` window, constructed in
  `__init__`, opened lazily on the view's *first* `showEvent()` (the same
  "not on construction" convention that method already uses for
  `_claim_voice_budget()`/`_apply_patch()`), and closed in `closeEvent()`.
  This does **not** outlive the window the way `SoundEngine` outlives every
  tool switch: `SynthView` is destroyed on close (`WA_DeleteOnClose`,
  `studio.py`'s `_on_synth_view_destroyed()`) and rebuilt from scratch on
  next open, so there is no persistent process-wide owner for a port to
  survive in between -- tying the device 1:1 to the window's own lifetime
  is simpler than inventing one, and correct, because nothing else in the
  process ever wants to read this port.

Both converge on the same guarantee CLAUDE.md already states for audio
output: a tool that never opens the Synth View never opens a MIDI port,
and opening one never implies opening the mic or the sound engine.

## 3. Threading: decision 70's mechanism reused, not re-derived

Verified directly against RtMidi's own source (not assumed): the ALSA
backend's `MidiInAlsa::openPort()` calls `pthread_create()` for a dedicated
reader thread that invokes the registered Python callback synchronously,
at ordinary `SCHED_OTHER` priority -- the identical starting point decision
70 found for the audio *output* callback before its fix. `audio/
midi_input.py`'s `MidiInput._ensure_realtime_priority()` therefore calls
`sound_engine._try_realtime_scheduling()` **directly**, not a re-derived
copy of it: same `os.sched_setscheduler(0, SCHED_FIFO, ...)` call from
inside the callback's first invocation (no Python `Thread` object exists
ahead of time to configure), same three-way degradation
(`PermissionError`/other `OSError`/no `os.sched_setscheduler` at all)
collapsing to the same one-line `print(f"[midi] ...")` status, same
"never let an exception escape the callback" rule. `tests/
test_midi_input.py`'s `test_the_callback_reuses_decision_70s_realtime_
scheduling_mechanism` pins that it is the *same* function, not a copy that
could drift.

**Off the reader thread, onto the Qt UI thread, before anything Qt- or
engine-touching runs.** `MidiInput` calls `on_message(raw_bytes)` from
RtMidi's own thread. `gui/synth_view.SynthView` connects that straight to
`midiMessageReceived.emit` -- a Qt signal whose cross-thread `emit()` is
automatically delivered through a queued connection when the emitting
thread differs from the receiver's own. `_on_midi_message()` (which runs
`MidiDispatcher.handle_message()`) therefore always executes on the same
Qt thread every keyboard-band key event already does, never on RtMidi's
reader thread. This was a deliberate design decision, not an oversight
research left open: `poly.PolyGraph.note_on()`/`note_off()` have no lock at
all (unlike `sound_engine.VoiceManager`, which does) -- calling them from a
third, uncoordinated OS thread while the audio callback thread reads
`voice.active`/`voice.note` and the Qt thread's own keyboard dispatch might
be mutating the same voice list at the same instant would have been a new,
real data race this ticket should not introduce. Marshalling onto the Qt
thread first means MIDI's call into `bridge.note_on()`/`note_off()` carries
the exact same (pre-existing, unchanged) UI-thread-vs-audio-thread hazard
every keyboard-band note already carries, and nothing worse.

## 4. Two input sources: a reference-counted hold, not a race

**The problem is specific to `PolyGraph.note_off()`'s own contract**:
"release every voice sounding this pitch" (poly.py's own docstring) --
correct when only one source could ever hold a pitch, wrong the instant a
second one can hold it independently. If the computer keyboard is holding a
C4 and a MIDI keyboard's C4 note-off arrives, a bare `bridge.note_off(60)`
would silence both.

**Fixed at the dispatch layer, not inside `poly.py`.** `audio/note_hold.
NoteHoldGate` is a small, plain (`{pitch: {source_label, ...}}`) reference
count: `press(pitch, source)` records a hold, `release(pitch, source)`
returns `True` only once nothing else holds that pitch -- the caller's cue
that the underlying `bridge.note_off()` may actually run. One instance is
shared between the computer keyboard's own graph-path dispatch
(`_on_note_preview`/`_on_note_released` in `gui/synth_view.py`, which now
call `press`/`release` around the `bridge.active` branch they already had)
and `MidiDispatcher` (constructed with `source_label="midi"`). Chosen over
teaching `PolyGraph.note_off()` a holder concept because that pitch-based
contract is shared by every other call site in the project and reshaping it
would ripple outward for one narrow fix; a reference count in front of the
one call site that needed it is the smaller, more honest change.

**The legacy (non-graph) engine path already had no such collision.**
`sound_engine.VoiceManager.release_voice(voice_id)` releases exactly the
voice it is handed, never "every voice at this pitch" -- so MIDI's fallback
to that path (whenever `bridge.active` is False) needs no gate at all; two
sources holding the same pitch there are simply two independent voice ids,
each released independently, which was already correct before this ticket.

**Retriggering, not deduplication.** Both sources pressing the same pitch
at once produces two real voices (a real re-strike each), not a single
shared one -- matching how a MIDI note-on retriggers on real hardware
rather than silently no-oping a second press. The gate only ever answers
"is anything still holding this," never "should this press make a sound."

**Held notes survive a graph rebuild.** `_rebuild_graph()` already
re-triggered every `_voice_by_key` note the computer keyboard was holding
(a rebuild replaces all sixteen voices); this ticket extends the identical
re-trigger to `_midi_voices`, so a MIDI chord held while a cable is dragged
gets the same "changes sound rather than stopping it" treatment the
keyboard's own chords already had.

## 5. Expression

**Velocity** -- a non-event, exactly as the research found: MIDI's 0-127
byte divides by 127 into `NoteContext.velocity`'s existing 0..1 range
(`parse_message()`), read every block by `AmpEnvelope`/`ModEnvelope`
unchanged.

**Sustain pedal (CC64)** -- entirely a dispatch-layer policy
(`MidiDispatcher`), no contract change, as the research predicted. A
physical key-up while the pedal is down moves the pitch into a
`_sustained` set rather than releasing it; the pedal lifting releases
everything still in that set (through the same `NoteHoldGate`-checked
path); a re-strike while sustained removes the pitch from the set instead
(a live note again, not a pedal-held one) so the pedal lifting afterwards
does not double-release or release a note nothing is holding.

**Pitch bend** -- routed onto `oscillator.py`'s existing `fine` destination
(cents, already `modulatable`), via `patch_bridge.PatchBridge.
apply_pitch_bend()`. Two choices worth naming:

- **v1 scales MIDI's bend travel down to `fine`'s own declared range**
  rather than widening that range: a full bend (+/-1.0 normalized) reaches
  +/-100 cents (one semitone), not MIDI's own typical +/-200-cent default
  (`PITCH_BEND_RANGE_CENTS` in `audio/midi_input.py`, named and reasoned
  about explicitly rather than silently). The research flagged this as "a
  real decision but small"; widening `fine`'s range instead is a
  one-constant change for later if the compressed feel is wrong in
  practice, deliberately deferred rather than guessed at from a machine
  with no MIDI hardware to feel it on.
- **It is applied by direct `poly.set_parameter()` call, not through
  `PatchBridge.set_parameter()`'s ordinary knob path** -- the ordinary path
  writes into `self._parameters`, which every `rebuild()` re-applies; a
  transient performance gesture must not survive a cable edit as a
  permanent detune. It is added to the user's own Fine-knob value, not
  replacing it, so turning the knob and bending the wheel compose.

**Mod wheel (CC1)** -- `audio/graph/modules/midi_cc.ExternalCc`, a new
`POLY_ONCE` module exposing one `mod_out` port whose value is whatever
`set_value()` last set, held flat for the block -- the "small new module"
the research named, patched exactly like an LFO or Mod Envelope through
the same `ModRoute`/`ParamBlock` machinery, no contract change. Registered
in `patch_bridge.MODULE_FACTORIES`/`MOD_SOURCE_PORTS` under `"midi_cc"` and
proven working end-to-end at the engine layer (`tests/
test_patch_bridge.py`'s `test_external_cc_modulates_a_patched_destination`
patches it straight onto an oscillator's `fine` and reads back a live
modulation buffer).

**The one deliberately incomplete piece: no canvas drawer entry exists yet
for `midi_cc`.** Adding one is a `gui/patch_graph.py` `NodeSpec` -- a file
this ticket's own instructions name as owned by another agent for the
ticket's duration, so it is out of scope by construction, not by choice.
Everything up to the canvas is real and tested (`gui/synth_view.py`'s
`MONO_TYPES` already carries `"midi_cc"` so the canvas and the engine will
already agree on its default side the moment a drawer entry exists);
`set_external_cc()` is a documented no-op until then, which is the correct
degrade for "the mod wheel has nowhere on this patch to go yet," not a
crash or a silent lie.

**Aftertouch is out of scope, per the issue** -- the research found no
clean destination (overloading `velocity` would conflate strike force with
continuous pressure), and building one is left for whenever a use for it
is named.

## 6. What was deliberately not touched

- **`synth_engine.py`'s fixed path** -- decision 56 §7's boundary,
  unchanged; MIDI plays the patch-graph Synth View exclusively (through
  `PatchBridge`) or the legacy `SoundEngine.note_on()` path when no graph
  is playing, exactly mirroring the computer keyboard's own two routes.
  Neither is new plumbing.
- **The 16-voice cap itself (#180's own follow-up note on this issue)** --
  the issue's own scope line says "up to the engine's 16 voices," and #180
  named re-measuring the cap for pedal-driven polyphony as real but
  separate work (its own re-measurement in a live `sounddevice` callback,
  not a benchmark). Not reopened here.
- **Sample-accurate note timing** -- the same 11.6ms block-boundary
  granularity QWERTY notes already have (`envelope.py`'s own docstring);
  making it sample-accurate is a `PORT_EVENT` contract change touching
  every per-note module, the research's own explicit non-goal for this
  ticket.
- **Device selection UI, hot-plug polling, and a channel picker** -- named
  by the research (§5) as UI design work, not decided by it. This ticket
  runs omni (any channel plays, a constructor argument away from a filter)
  and opens the first port `get_ports()` returns; `MidiInput.list_ports()`
  exists for a future picker to call, not built here.
- **MIDI output, MIDI learn, MIDI clock/sync** -- explicitly out of the
  issue's stated scope.

## 7. Degradation, exhaustively

Every path collapses to a one-line `print(f"[midi] ...")` status (the same
convention `sound_engine.py`/`audio_capture.py` already use) and an
unchanged synth -- never a crash, never a stuck note:

| Situation | What happens |
|---|---|
| `python-rtmidi` not installed | `ensure_started()` catches `ImportError`, reports once, `available=False`. `pip install 'note-color[midi]'` is named in the message. |
| No MIDI device present | `get_ports()` returns empty, reported, `available=False`. |
| Permission denied opening the port | Broad `except Exception` around `open_port()` (matching `gui/app.py.start_audio()`'s own "deliberately broad" convention), reported, `available=False`. |
| Device unplugged mid-play | No push notification exists in RtMidi's own API (checked, not assumed, per the research); nothing here polls for it yet (named as future UI work, §6). What *is* handled: `closeEvent()`/a future explicit stop calls `MidiDispatcher.reset()` before `MidiInput.stop()`, releasing every pitch this dispatcher itself holds (respecting `NoteHoldGate` -- a pitch the keyboard is still holding is not released) -- no stuck note on the paths this ticket controls. |
| A malformed/truncated MIDI message | `parse_message()` defaults missing bytes to 0 rather than indexing past the end; `handle_message()` wraps the whole apply in a bare `except Exception: pass`. |
| The RtMidi callback itself raises (a bug, not a device problem) | Caught in `MidiInput._callback()`, counted (`callback_error_count`), never escapes into RtMidi's C trampoline. |
| No MIDI hardware at all (the common case) | Nothing about existing behaviour changes. The Synth View's status bar gains one label (`midi=none`), informational only -- CLAUDE.md's own "no MIDI hardware means no change in behaviour" is read as "no change in *sound* or *input handling*," which a status label is not. |

## 8. Testability without hardware

Three layers, two of which need neither a device nor Qt:

- `parse_message()` -- pure function, byte lists in, `MidiEvent` out.
- `MidiDispatcher` -- pure Python against a plain fake `sink` object
  (`note_on`/`note_off`/optionally `pitch_bend`/`mod_wheel`); every
  scenario the issue's own "Rules" section names (note-on/off, velocity,
  sustain hold and release, pitch bend, two-source interaction, every
  degradation path) is exercised this way in `tests/test_midi_input.py`.
- `MidiInput` -- a fake `rtmidi` module substituted into `sys.modules`
  (`sys.modules["rtmidi"] = None` forces the "not installed" branch
  deterministically regardless of what happens to be installed on the CI
  machine; a small `FakeRtMidiIn` class stands in for a real port with
  configurable `get_ports()`/`open_port()` behaviour for every other
  branch) plus direct calls to `_callback()`, mirroring
  `test_sound_engine.py`'s own `engine._callback(...)` convention.

`gui/synth_view.py`'s own integration is covered too
(`tests/test_synth_view.py`): MIDI note-on/off through the real graph with
real velocity, a keyboard hold surviving a MIDI release and vice versa,
Panic and window-close releasing MIDI-held notes, the legacy-engine
fallback, pitch bend reaching the bridge, and the status label reading
"none" with no `python-rtmidi` installed.

Full suite green throughout: 2531 passed / 4 skipped at this decision's
landing commit (up from 2453/4 at the ticket's start).

## What fought the spec, and what is left open

- **No MIDI hardware and no Raspberry Pi were available for this
  implementation either** -- the same gap the research already disclosed,
  now inherited rather than newly created. Every degradation path is
  tested against a fake device; the actual feel of a real controller
  (velocity curve, bend range expectations, hotplug timing) is unverified.
  See the report back to the owner for the exact try-it-yourself steps.
- **The Synth View's actual host has no `SessionState`** -- not something
  the research anticipated, discovered by reading `gui/app.py` directly
  while implementing. Resolved with two lifecycle owners (§2) rather than
  forcing one shape onto both worlds.
- **The mod wheel's canvas drawer entry is genuinely incomplete** -- not a
  bug, a file-ownership boundary for this ticket's duration. Everything
  behind it is built and tested; `gui/patch_graph.py` needs one `NodeSpec`
  addition to finish the wiring, named for whoever owns that file next.
- **Pitch bend's compressed +/-100-cent range is a real, named compromise**
  -- see §5. Left for a future ticket to revisit once it can be felt on
  real hardware.
