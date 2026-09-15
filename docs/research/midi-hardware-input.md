# MIDI hardware input: library choice, portability, threading, timing, and the expression it brings

Research for [#233](https://github.com/pellepang/note-color/issues/233), which precedes
[#173](https://github.com/pellepang/note-color/issues/173) (Synth View: no MIDI hardware
input, and QWERTY notes have no velocity/sustain/mod-wheel). [#226](https://github.com/pellepang/note-color/issues/226)
(`docs/research/keyboard-piano-rollover.md`) already established that the owner's five-note
ceiling is the computer keyboard's own USB HID report shape — a hardware fact no software can
lift — and that MIDI is the only path that removes it, because a MIDI note-on carries its own
note number with no shared 6-slot array to exhaust. This document does not re-argue that; it
starts from "we're doing MIDI" and asks how.

Two things make this worth more than polyphony alone, both named in #233: the Synth View's
modulation layer (decisions [66](../decisions/66-the-modulation-layer-lfo-scalar-or-buffer-params-and-the-poly-boundary-issue-208.md)–[68](../decisions/68-the-canvas-plays-modulation-the-depth-ring-and-the-dual-output-jack-issue-208.md))
now has real destinations and depth for a mod wheel to feed, and `docs/decisions/70-...md`'s
scheduling work on the audio *output* thread turns out to be directly reusable for the MIDI
*input* thread — see §3.

No MIDI hardware is attached to this machine and no Raspberry Pi is available. Everything
about real device behaviour (hotplug timing, actual jitter, a specific controller's report
shape) is reasoned from documentation, not measured, and is flagged as such — see "What I
could not verify."

---

## 1. The library question

### The options

- **`python-rtmidi`** — a Cython wrapper around Gary Scavone's C++ **RtMidi**, giving raw
  MIDI in/out with both a polling (`get_message()`) and a callback (`set_callback()`) API.
- **`mido`** — a pure-Python MIDI *message* library (parsing, `Message` objects, `.mid` file
  I/O) that delegates real port I/O to a swappable **backend**; its own docs name RtMidi as
  "the default and recommended backend", with PortMidi, `pygame.midi`, `rtmidi-python`, and an
  experimental ALSA `amidi`-shell-out backend as alternatives (mido docs,
  [Backends](https://mido.readthedocs.io/en/stable/backends/index.html)). In practice, "install
  mido" on Linux/desktop means "install mido and python-rtmidi" (`pip install mido[ports-rtmidi]`,
  per mido's [installation docs](https://mido.readthedocs.io/en/stable/installing.html)).
- **Raw ALSA sequencer access** — talking to `/dev/snd/seq` directly, either via `python-alsaaseqmidi`-style
  ctypes bindings or a hand-rolled wrapper. Linux-only by construction; no portability story to
  macOS/Windows at all, and it duplicates exactly what RtMidi's Linux backend already does
  underneath. Not evaluated further as a real option — it buys nothing python-rtmidi doesn't
  already give, at the cost of a project-maintained C binding.

### Licence

Checked at the source, not from memory, per decision 47's rule and the pedalboard/JUCE
reversal in decision 56 §10:

- **`python-rtmidi`**: MIT (`LICENSE.md`, copyright Christopher Arndt/SpotlightKid,
  2012–2023, on the [PyPI project page](https://pypi.org/project/python-rtmidi/) and the
  package's own licence file).
- **RtMidi** (the C++ library it wraps): MIT, copyright Gary P. Scavone
  ([`thestk/rtmidi`'s `LICENSE`](https://github.com/thestk/rtmidi/blob/master/LICENSE)), with
  one non-binding courtesy clause asking anyone who modifies it to send patches upstream — a
  request, not an obligation, and it does not change the licence category.
- **`mido`**: MIT (`LICENSE`, copyright Ole Martin Bjørndalen, on
  [`mido/mido`](https://github.com/mido/mido)).

All three are clean MIT, the same category as `numpy`/`sounddevice`/`pygame-ce`'s LGPL/`blessed`
already in `pyproject.toml`. Nothing here reintroduces the JUCE/pedalboard problem decision 56
§10 reversed — that was GPL-3.0 leaking in through an embedded framework; RtMidi is a small,
directly-licensed MIT library with no such passenger. **No licence-driven objection to any of
these three.**

### C extension / build cost

`python-rtmidi` is a compiled Cython extension, not pure Python. This matters for the Pi-to-desktop
range CLAUDE.md commits to:

- **manylinux wheels exist for `x86_64` and `aarch64`** (PyPI project files for `python-rtmidi`
  1.5.8), covering 64-bit desktop Linux and 64-bit Raspberry Pi OS (the default on Pi 3/4/5
  since Raspberry Pi OS "Bookworm").
- **piwheels** (`piwheels.org/project/python-rtmidi/`, the prebuilt-wheel service Raspberry Pi
  OS's `pip` is configured to use) carries **`armv6l` and `armv7l`** wheels too, i.e. 32-bit Pi
  OS and even a Pi Zero/1 (`armv6l`), across multiple Python versions including recent ones
  (3.11, 3.13 in the latest release). So in practice, on the actual target platform (Raspberry
  Pi OS's own pip, pointed at piwheels by default), this is **still a wheel install, not a
  source build** — the Cython/C++ compile cost that "a C extension needs building per platform"
  usually implies has already been paid upstream, on both ends of the Pi/desktop range.
- Where no wheel matches (an unusual architecture, or a piwheels outage), the source build needs
  a C++ toolchain and ALSA's development headers (`libasound2-dev` on Debian/Raspberry Pi OS) —
  a real but ordinary Linux build dependency, no different in kind from what `pyfluidsynth`'s
  `sf2` extra already documents needing (`libfluidsynth`) in this repo's own `pyproject.toml`.
- `mido` itself is pure Python (no compiled component); the C extension cost is entirely in
  `python-rtmidi`, which mido treats as a pluggable, optional backend.

### Callback vs. polling

`python-rtmidi` gives both. `MidiIn.set_callback(func)` runs `func` from RtMidi's own reader
thread whenever a message arrives (see §3); `get_message()` polls a queue instead. `mido`'s
RtMidi backend surfaces the same choice one layer up — its docs describe a "true blocking
`receive()` in Python 3 (using a callback and a queue)" — i.e. mido's own polling `receive()` is
already callback-fed underneath, not busy-polling the OS.

### Recommendation on the library

**`python-rtmidi` directly, not through `mido`.** Two reasons:

1. **mido buys message parsing this project would have to write anyway if it dropped mido**,
   but note-color's own event vocabulary (`NoteContext`, `poly.PolyGraph.note_on()/note_off()`)
   already *is* a parsed-message model — mido's `Message` objects would be translated into that
   shape immediately, at the door, and never seen again. The translation layer is a dozen lines
   against raw RtMidi status/data bytes (`0x90 note vel` for note-on, `0xB0 cc value` for CC,
   `0xE0` for pitch bend), not enough to justify a second dependency and its own backend
   indirection on top of the one library actually doing the I/O.
2. **One less abstraction between the app and the thread doing the reading** matters directly
   for §3's threading question — reasoning about "which OS thread calls into note-color" is
   cleaner one hop from RtMidi than two hops through mido's backend-selection layer, and mido's
   backend abstraction is aimed at a use case (swapping ALSA/JACK/PortMidi/pygame.midi
   transparently, `.mid` file work) this project doesn't need — it already knows it wants RtMidi
   and does not need to swap backends at runtime.

If a future need for `.mid` file import/export arises, mido is still the right tool for *that*
half (it is a message/file library first) and could be added then without touching the live
input path built on `python-rtmidi` — they are not mutually exclusive, just not both needed now.

---

## 2. Portability: Pi to desktop

- **Desktop Linux, PipeWire.** PipeWire exposes MIDI to legacy ALSA-sequencer clients (which is
  what RtMidi's Linux backend is) through an **ALSA-seq bridge node**
  ([PipeWire MIDI docs](https://docs.pipewire.org/page_midi.html)): the session manager
  (WirePlumber) creates one SPA node backed by `SPA_NAME_API_ALSA_SEQ_BRIDGE`, with a port per
  MIDI client/stream, so an RtMidi `MidiIn` opened against ALSA sees PipeWire-managed MIDI
  devices the same way it would see them under plain ALSA — no separate PipeWire-native MIDI
  API is needed for note-color to work on a PipeWire desktop. Two caveats from the same primary
  source, both worth carrying into implementation: the bridge node's creation is gated on
  `/dev/snd/seq` being accessible (it waits on an `inotify` watch for that), and "the session
  manager does not try to link control messages automatically" — i.e. a MIDI device shows up as
  a port to connect, not as something PipeWire auto-routes into note-color the way it
  auto-routes default audio. The user (or the app, via RtMidi's own `open_port()`) still picks
  which port to read.
- **Raspberry Pi.** No PipeWire assumption needed there either — Raspberry Pi OS runs a
  conventional ALSA stack, and RtMidi's ALSA backend talks to the kernel sequencer the same way
  regardless of what's mixing audio on top of it. §1 already covers the practical build story
  (piwheels has `armv6l`/`armv7l` wheels). A class-compliant USB-MIDI keyboard needs no special
  driver on Linux — `snd-usb-audio` handles the USB-MIDI class spec generically, the same driver
  path that already exists for any USB audio device; **not independently verified against a real
  Pi** (no Pi available here), but this is standard, widely-documented Linux USB-MIDI behaviour
  rather than a Pi-specific claim.
- **Should MIDI input follow `sounddevice`/PortAudio's shape, or differ?** Differ, and it should:
  `sounddevice` was chosen (decision, `pyproject.toml`) because PortAudio abstracts *host APIs*
  note-color's audio path needs to be host-API-agnostic across (ALSA, PipeWire, CoreAudio,
  WASAPI) for *sample streaming*, where block size and sample-rate negotiation genuinely differ
  per backend. MIDI has no equivalent negotiation surface — a note-on is a note-on on every
  backend — so RtMidi's own thinner "pick ALSA or JACK explicitly, or let it default" model
  (mido's docs mention `MIDO_BACKEND=mido.backends.rtmidi/LINUX_ALSA` as an example of that
  choice existing) is already the right level of abstraction; reaching for something
  PortAudio-shaped here would be solving a negotiation problem MIDI doesn't have.

---

## 3. Threading and timing

### Where MIDI input belongs among the existing threads/lifecycles

CLAUDE.md's three always-on threads (audio capture → analysis → render, joined by non-blocking
queues) are the *pitch/chroma/rhythm detection* pipeline and have nothing to do with sound
output or note input — `SessionState` already keeps that pipeline separate from `sound_engine`'s
own PortAudio callback thread, which `ensure_sound_engine()` starts lazily and independently
(decision #105, `ensure_sound_engine()`/`ensure_started()` in `src/notecolor/audio/session.py`).
MIDI input is a **fourth**, and architecturally a **sibling of `ensure_sound_engine()`, not of
the capture/analysis pipeline**: it is a device the Synth View wants and the score editor/mic-only
tools do not, the exact shape CLAUDE.md already describes for output ("a tool that only plays
never opens the mic, nor vice versa"). The natural extension is an `ensure_midi_input()` on
`SessionState`, idempotent like its two siblings, called only by the tool that wants live MIDI
(the Synth View), so a `tab`/score-editor session that never touches MIDI never opens a port —
and, symmetrically, opening a MIDI port never has to imply opening the sound engine or the mic.

### What thread the messages actually arrive on

Read directly from RtMidi's own C++ source rather than assumed: the ALSA backend's
`MidiInAlsa::openPort()` calls `pthread_create()` to spin up a dedicated reader thread
(`alsaMidiHandler`) that blocks on the ALSA sequencer and invokes the registered callback
synchronously from *that* thread — not from whatever thread called `open_port()`, and not from
any of note-color's existing three (`thestk/rtmidi`'s `RtMidi.cpp`, verified by reading the
`pthread_attr_setschedpolicy(&attr, SCHED_OTHER)` / `pthread_create(...)` call directly).
Concretely: **MIDI input is a fifth native OS thread**, alongside the capture thread, the
analysis thread, PortAudio's own callback thread (output), and now this reader thread (input) —
none of which are Python `threading.Thread` objects the app constructed; PortAudio and RtMidi
each spawn and own theirs.

That reader thread is created with `SCHED_OTHER` — ordinary time-sharing priority, not
realtime — which is the same starting point decision 70 (issue #232) found for the audio
*output* callback before it was fixed. **The same trick decision 70 used applies here almost
unchanged**: because `python-rtmidi`'s `set_callback()` invokes the Python callback synchronously
from inside that C pthread (via the Cython trampoline), a call to `os.sched_setscheduler(0, ...)`
made from *inside* the first invocation of that Python callback elevates that exact OS thread —
`pid=0` means "the calling thread" (`man 2 sched_setscheduler`), which decision 70's own comment
already establishes as the reason its version had to run from inside the callback rather than
being set up ahead of time. This is a real, concrete opportunity for #173's implementation to
reuse rather than re-derive, with the identical graceful-degradation shape decision 70 already
built (catch `PermissionError`/`OSError`, fall back silently, report a one-line status) — most
machines (including this one, per decision 70's own `ulimit -r` → 0 finding) will decline the
elevated priority, and that has to be a non-event, not a crash.

### Note-off and note timing granularity

**The gate-per-block granularity is inherent and already applies identically to QWERTY input**
— `envelope.py`'s own docstring is explicit that "the gate is read once per block... up to
11.6ms at 512 frames," and that is a fact about `sound_engine`/the graph engine's block-at-a-time
contract (rule 1), not about *which* input device produced the note-on/off. A MIDI note-on
timestamped to microsecond precision by RtMidi still only takes effect at the next block
boundary once it reaches `PolyGraph.note_on()`, exactly like a QWERTY keydown does today.

**Is that acceptable?** Yes, for a first version, on the same grounds CLAUDE.md's own latency
target already sets: 11.6ms of note-on/off jitter is an order of magnude under the "comfortably
under 150ms end-to-end" target, and it is well under generally-cited human just-noticeable-timing
thresholds for onset simultaneity (single-digit to low-teens milliseconds is the region where
timing differences start to become audible in isolation, and this sits at the edge of that
region, not far past it). MIDI does not make the *granularity* worse than it already is for
QWERTY notes; it makes the *stakes* higher because velocity/expression riding on the same event
now matters more, which is a different question (§4), not a timing one.

**What would `PORT_EVENT` have to become if 11.6ms were not acceptable?** Today `PORT_EVENT`
(`contract.py`) is defined only as "notes, carried as a `NoteContext` rather than a buffer" —
i.e. one already-resolved note state per block, for a hosted plugin to receive, with no
per-sample offset information at all. Contract rule 5 already flags the general gap this sits
inside: "Sample-accurate automation... is deliberately not here yet." Making note-on/off
sample-accurate would mean `PORT_EVENT` (or a new field alongside it) carrying an **ordered list
of timestamped events per block** — `(frame_offset, kind, data)` tuples, the same shape CLAP's
own event list uses — and every per-note module's `process()` would need to honor a mid-block
offset, not just a start-of-block gate. Concretely, `AmpEnvelope`/`ModEnvelope`'s
`DahdsrEnvelope.block_into()` would need to be callable in two pieces around an event's exact
sample (walk N1 samples in the pre-event stage, call `note_off()`, walk the remaining N-N1 in
the post-event stage) rather than being handed one gate value for the whole block. **That is a
contract-wide change affecting every per-note module, not something #173 can or should build
as a side effect of adding MIDI** — it is its own ticket, and the recommendation below scopes
#173 to *not* attempt it.

---

## 4. The expression that arrives with MIDI

Checked against what the engine (contract + built modules) can actually receive today, not
against what MIDI can send:

| Signal | Has somewhere to land today? | What it is |
|---|---|---|
| **Velocity** | **Yes, directly.** | `NoteContext.velocity` already exists and is read every block by both `AmpEnvelope` and `ModEnvelope`'s own `velocity` `ParamSpec` (`env.py`'s `scale = 1.0 - amount * (1.0 - note.velocity)`). MIDI velocity (`0x90` note-on's second data byte, 0–127) maps onto it with one division by 127. No contract change. |
| **Mod wheel (CC1)** | **Yes, via the existing modulation layer, with one new small module.** | `ParamBlock`'s scalar-or-buffer split (decision 66) and `graph.ModRoute` don't care *where* a `PORT_MOD` signal comes from — `LFO` and `ModEnvelope` are just two current sources. A `POLY_ONCE` "External CC" module exposing a `mod_out` port, fed by the MIDI thread writing into a small thread-safe shared value the module reads once per block, would patch into any of decision 67's already-broadened destination list (filter cutoff/resonance, oscillator `level`/`fine`, etc.) exactly like an LFO does — no contract change, a genuinely small module. |
| **Pitch bend** | **Yes, specifically via `oscillator.py`'s existing `fine` destination.** | `fine` (cents, -100..+100, already `modulatable` and already wired through `ParamBlock.mod_active`/`param_buffers` for per-sample precision — see `oscillator.py`'s `fine_live` branch) is exactly the bipolar, continuous, per-note-or-global destination pitch bend needs. A pitch-bend value (`0xE0`, 14-bit, centered at 8192) scaled into a mod-source buffer and routed to `fine` via a cable is a direct fit for machinery that already exists, though the *default range* MIDI pitch bend implies (commonly ±2 semitones = ±200 cents) exceeds `fine`'s declared ±100-cent knob range — either `fine`'s range widens, or pitch bend is scaled down to it, or bend gets its own wider-range destination. That range decision is real but small; it does not require new contract mechanism. |
| **Sustain pedal (CC64)** | **No contract change needed at all — it isn't a modulation-layer question.** | Sustain is "don't let a note-off take effect yet," which is a *dispatch-layer* policy (whatever code currently turns a QWERTY keyup or a MIDI note-off into a `PolyGraph.note_off()` call), not a per-parameter signal — it belongs beside `SynthKeyboardBand`/the note-input dispatch, tracking "keys physically released while the pedal is down" and deferring the `note_off()` call for them until CC64 goes back below 64. No `NoteContext`/contract field is implicated. |
| **Aftertouch (channel or poly)** | **No clean destination exists yet.** | The only per-note, continuously-read field is `NoteContext.velocity`, and it is already semantically "strike velocity," read by both envelopes' `velocity` knob specifically as that. Aftertouch is a different MIDI concept (continuous pressure after the strike) and routing it into the same field would conflate the two — technically live (the field is read every block, so an update would take effect at block granularity, same as everything else here), but not a good semantic fit. A first version should either not implement aftertouch, or give it its own mod-source module (the same "External CC" shape as the mod wheel) rather than overloading `velocity`. |

**Net finding for #233's question 4:** velocity is a non-event (already there); mod wheel and
pitch bend are real but small additions that reuse decision 66–68's modulation layer exactly as
built, with no contract change; sustain is a dispatch-layer concern outside the graph engine
entirely; aftertouch has nowhere good to land yet and should be scoped out of a first version
rather than jammed into `velocity`.

---

## 5. What the UI will have to answer

Not designed here, per the issue's own instruction — named only:

- **Device selection.** `python-rtmidi`'s `MidiIn().get_ports()` enumerates ALSA-seq clients
  visible *at the moment it's called* — a snapshot, not a live list. The UI needs a place to
  show that snapshot and let the user pick one (or "no MIDI input"), and a way to re-scan on
  demand.
- **Hot-plug.** Neither RtMidi's header nor its ALSA implementation documents any push
  notification for a device appearing/disappearing after a port list was already taken — this
  was checked directly in `RtMidi.h`/`RtMidi.cpp` and found absent, not merely unread. The
  practical answer used by comparable tools is periodic re-enumeration (poll `get_ports()` every
  few seconds and diff against the last snapshot), which the UI needs a place to trigger from
  and a way to surface ("a MIDI keyboard just appeared — use it?").
- **Channel handling.** MIDI has 16 channels; the UI has to decide (and let the user decide)
  between "omni" (any channel sounds) and a specific channel, the same choice most controllers'
  own default behavior assumes and most software mirrors.
- **Two live input sources at once.** With the computer keyboard (`SynthKeyboardBand`,
  `NOTE_CHANNEL`) and a MIDI controller both able to call into the same `PolyGraph.note_on()`/
  `note_off()`, the UI has to answer what happens when both are live simultaneously — do they
  layer (both can sound notes at once, sharing the 16-voice pool), does one take priority, and
  what happens if the same pitch is triggered by both at once (a `note_id` collision the voice
  pool's allocator has to resolve one way or the other, not silently pick one).
- **Whether MIDI-driven notes get the same status-bar honesty #226 recommended for QWERTY
  chord-size warnings** — probably not the same warning (MIDI doesn't have a rollover ceiling),
  but the same *principle*: don't imply a capability the current active input can't deliver
  (e.g. showing "velocity-sensitive" UI when the active source is the computer keyboard's flat
  `DEFAULT_VELOCITY`).

---

## What I could not verify

- **No MIDI hardware of any kind is attached to this machine.** Everything about a real
  controller's behaviour — actual note-on/off jitter, a specific keyboard's velocity curve,
  whether a given cheap controller sends 7-bit or (rarely) high-resolution pitch bend, hotplug
  timing in practice — is reasoned from RtMidi/PipeWire/USB-MIDI-class documentation, not
  measured. Flagged inline above wherever it applies.
- **No Raspberry Pi is available.** The `armv6l`/`armv7l` piwheels finding (§1) and the
  class-compliant-USB-MIDI-needs-no-special-driver claim (§2) are both documentation-based, not
  run on real Pi hardware. Both are ordinary, well-established Linux behaviour rather than
  exotic claims, but "verified on a real Pi" would still be stronger than "verified against
  piwheels' own listing and general USB-MIDI-class documentation."
- **RtMidi's actual note-on-to-callback latency** (as opposed to its documented thread model) was
  not measured — there is no hardware to generate an event to measure against, and no existing
  benchmark was found that isolates RtMidi's own overhead from the underlying kernel/ALSA path.
- **Ableton/Logic/FL Studio's own MIDI-input threading and latency handling** were out of scope
  for this document (#226 already surveyed those tools for QWERTY-piano behaviour); this
  document did not re-survey them for MIDI-specific behaviour since the question here is what
  *note-color's own* library/thread/contract choices should be, not a competitive feature
  comparison.

---

## Recommendation for #173

**Library:** `python-rtmidi` directly (MIT, same category as every current dependency), not
`mido` — the translation from raw status/data bytes to `PolyGraph.note_on()`/`note_off()` calls
is small enough that mido's extra backend-abstraction layer buys nothing this project needs,
and one fewer hop matters for reasoning about which thread is calling in. Revisit only if `.mid`
file import/export is ever wanted — a genuinely separate, later need mido would still serve well.

**Lifecycle:** a third independently-lazy device, mirroring `ensure_sound_engine()` exactly —
`SessionState.ensure_midi_input()`, idempotent, called only by the Synth View, so a tab/editor
session that never asks for MIDI never opens a port. `set_callback()`'s first invocation should
attempt the same realtime-scheduling elevation decision 70 built for the audio output thread
(`os.sched_setscheduler(0, SCHED_FIFO, ...)`, called from inside the callback since that pthread
has no Python `Thread` object to configure ahead of time), with the identical silent-degradation
contract (catch `PermissionError`/`OSError`, never let an exception escape into RtMidi's C
callback trampoline, report a one-line status once).

**Scope for a first version:** note-on/off (feeding the existing `PolyGraph.note_on()`/
`note_off()`, accepting the same 11.6ms block-boundary granularity QWERTY input already has —
*not* a `PORT_EVENT` sample-accurate rework, which is a separate, contract-wide ticket) plus
velocity (already has a landing spot, `NoteContext.velocity`) plus mod wheel and pitch bend as
small additions to the existing modulation layer (a `POLY_ONCE` "External CC"-style mod source
for CC1, and pitch bend routed to `oscillator.py`'s existing `fine` destination, checking its
±100-cent range against MIDI's typical ±200-cent bend default). Sustain pedal belongs at the
note-dispatch layer, not the graph, and can land in the same first version cheaply since it
needs no contract change. Aftertouch should be explicitly deferred — no clean destination exists
yet, and overloading `velocity` for it is not a fix worth taking. Device selection, hot-plug
polling, channel handling and dual-input-source arbitration (§5) are UI-design work #173 will
need to resolve but this document does not decide.

---

## Sources

- python-rtmidi — [PyPI project page](https://pypi.org/project/python-rtmidi/), `LICENSE.md` (MIT, Christopher Arndt/SpotlightKid)
- RtMidi (C++) — [`thestk/rtmidi`](https://github.com/thestk/rtmidi), `LICENSE` (MIT, Gary P. Scavone), `RtMidi.h`, `RtMidi.cpp` (`MidiInAlsa::openPort()`'s `pthread_create`/`SCHED_OTHER`, read directly)
- mido — [`mido/mido`](https://github.com/mido/mido), `LICENSE` (MIT, Ole Martin Bjørndalen); docs: [Backends](https://mido.readthedocs.io/en/stable/backends/index.html), [RtMidi backend](https://mido.readthedocs.io/en/stable/backends/rtmidi.html), [Installing](https://mido.readthedocs.io/en/stable/installing.html)
- piwheels — [python-rtmidi project page](https://www.piwheels.org/project/python-rtmidi/) (armv6l/armv7l wheel listing for Raspberry Pi OS)
- PipeWire — [MIDI Support](https://docs.pipewire.org/page_midi.html) (ALSA-seq bridge node, `/dev/snd/seq` permission gating, no automatic control-message linking)
- This repo — `CLAUDE.md` (three-thread pipeline, portability constraint, output lifecycle), `src/notecolor/audio/session.py` (`SessionState.ensure_sound_engine()`/`ensure_started()`), `src/notecolor/audio/sound_engine.py` and `docs/decisions/70-realtime-scheduling-for-the-audio-callback-thread-issue-232.md` (realtime-scheduling precedent reused in §3), `src/notecolor/audio/graph/contract.py` (`PORT_EVENT`, `NoteContext`, `ParamBlock`), `src/notecolor/audio/graph/modules/envelope.py` (gate-per-block granularity, `velocity`), `src/notecolor/audio/graph/modules/oscillator.py` (`fine` destination), `docs/decisions/47-...md` (licence rules), `docs/decisions/56-...md` §10 (pedalboard/JUCE/CLAP), `docs/decisions/66-...md`–`68-...md` (modulation layer, destinations, depth)
- `docs/research/keyboard-piano-rollover.md` (#226) — why MIDI is the path that removes the rollover ceiling
