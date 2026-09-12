# Modular routing prior art: polyphony, feedback, cable types, graph cost, plugin formats, canvas UI

Research for the Synth View patching work under map [#179](https://github.com/pellepang/note-color/issues/179),
specifically the fog that decision
[55](../decisions/55-synth-view-runtime-desktop-first-16-voices-and-the-bus-that-was-already-there-issue-180.md)
(issues [#180](https://github.com/pellepang/note-color/issues/180) /
[#200](https://github.com/pellepang/note-color/issues/200) /
[#201](https://github.com/pellepang/note-color/issues/201)) left open: what a
*cable-based* routing model would have to commit to, if the project ever moves
past the reorderable-slots model those decisions settled on.

Target system for the comparisons below: Python + numpy, PySide6 GUI,
`sounddevice`, **512-frame blocks at 44100 Hz = 11.61 ms per callback**, 16-voice
polyphony. Decision 55 measured 16 voices of the current fixed-topology engine at
**3.57 ms/block (30.7% of budget)** on this machine, so a graph layer has roughly
**8 ms of headroom** to spend before it hits that decision's own ~70% revisit
trigger.

Sources are cited inline. Where I could not verify something from a primary
source, it says so.

---

## 1. Polyphony vs. cables — where the voice/mix boundary lives

The systems surveyed take **four distinct positions**, and the difference is the
single biggest design fork in this whole document.

### (a) Hide it — the cable carries all voices. VCV Rack.

VCV Rack has exactly one cable type. Monophonic cables are "just a special case
of polyphonic cables, having just 1 channel", and a cable can carry **2–16
channels** ([VCV Rack Manual — Polyphony](https://vcvrack.com/manual/Polyphony)).
Polyphony spreads by "chain reaction": patch a poly pitch signal into a
poly-capable module and its output becomes poly too, so a whole patch goes
polyphonic by configuring a single module (usually MIDI-CV's right-click channel
count).

Crossing the boundary is **never forbidden and never an error**. The
[Voltage Standards](https://vcvrack.com/manual/VoltageStandards) page specifies
what a *monophonic* module must do with a poly cable:

- audio inputs: "sum the voltages of all channels (e.g. with `Port::getVoltageSum()`)"
- CV or hybrid inputs: "use the first channel's voltage (e.g. with `Port::getVoltage()`)"

and for poly modules, channel count N comes from the **primary** input; secondary
inputs are broadcast if mono (M=1), used per-channel if M≥N, and zero-filled for
out-of-bounds engines if 1<M<N — handled by `Port::getPolyVoltage(c)`.

The engine source confirms the mixing rule at the cable level rather than the
module level. In `Engine_stepFrameCables`, several cables into one input are
summed, a mono output is summed into *all* input channels, and the input's
channel count becomes the max over its sources
([`src/engine/Engine.cpp`, VCV Rack v2](https://github.com/VCVRack/Rack/blob/v2/src/engine/Engine.cpp)).

UI-wise the boundary is shown only as a hint: "polyphonic cables appear thicker",
plus `Split` / `Merge` / `Sum` / `Viz` utility modules for explicit control.

**Cost of this model:** every port is an N-channel buffer, every module must be
written poly-aware, and "is this cable one voice or sixteen" is answerable only
by looking at the cable's thickness or probing it.

### (b) Forbid it — poly→mono is a hard error. Reaktor Primary.

Reaktor makes the boundary a **type error you can see**. From
[REAKTOR 6 — Building in Primary](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/REAKTOR_6_Building_in_Primary_English_0419.pdf)
(pp. 25–27):

> "It is possible to connect the output of a monophonic Module to the input of a
> polyphonic Module, but it is not possible to connect the output of a
> polyphonic Module directly to monophonic Module."
>
> "Connecting a polyphonic Module to a monophonic Module will produce an error
> and the connection will not work. A connection like this will be colored red
> and an '!' will mark the input of the monophonic Module."

Module icons carry the type: **single note = mono, double note = poly**. To cross
the boundary you must place a **Voice Combiner**, described as a module that
"merge[s] a polyphonic signal into a monophonic signal", and the manual states
outright that "the outputs of REAKTOR are monophonic… Therefore, Voice Combiners
are important to add before the final output of any Ensemble that uses
polyphonic signals."

Note the second-order rule: an Ensemble with voices=1 makes *everything*
effectively mono regardless of per-module settings, "because a polyphonic module
that only uses one voice is functionally the same as a monophonic Module."

**This is the most explicit, most teachable version of the boundary** — the graph
is statically typed by voice-ness, the error is caught at patch time, and the
summing node is a real, visible, nameable module.

### (c) Split the canvas in two. Massive X.

Massive X's Routing page is literally divided into a **Polyphonic Area** (the
wavetable oscillators, noise, filter, insert effects A/B/C, the PM Aux bus, the
feedback loop, modulation slots) and a **Monophonic Area** that "sums all
polyphonic voices" and applies three stereo effects X/Y/Z before the host output
([Massive X Manual — Routing](https://docs.native-instruments.com/ni-tech-manuals/massive-x-manual/en/routing)).
Polyphonic modules *must* route into the Monophonic Area for the path to
complete. Within each area, "outputs can be connected to any number of inputs and
vice versa."

So: free patching, but the voice/mix boundary is **spatial and unmissable**, and
the summing happens at a fixed place rather than in a user-placed node.

### (d) Split the *device*. Bitwig's Grid.

Bitwig ships two Grid devices rather than one. **Poly Grid** is an instrument
that "generally respond[s] to notes"; **FX Grid** is an effect whose patches
"usually respond to incoming audio"
([Bitwig User Guide — Welcome to The Grid](https://www.bitwig.com/userguide/latest/welcome_to_the_grid/),
[The Grid Devices](https://www.bitwig.com/userguide/latest/the_grid_devices/)).
The same module set is available in both; what differs is the voicing context the
patch runs inside, and the device-level Voices / voice-stacking settings.

The rationale I can verify from primary docs is the Thru behaviour rather than an
explicit statement about cost: Poly Grid has no Audio Through and passes audio
through automatically, while FX Grid mixes dry/wet via a Mix parameter
([Special Connections / Thru Signals](https://www.bitwig.com/userguide/latest/special_connections/)).
Secondary write-ups add that FX Grid has Auto-Gate to retire silent voices, which
I did **not** find in the primary user-guide pages I fetched — treat that as
unverified.

**Verification gap:** I could not retrieve the user guide's "Voicing 'FX Grid'"
and "Voicing 'Note Grid'" chapters (the pages I could fetch reference them but
don't include them), so the exact voice-allocation and summing semantics at the
Grid's output module are *not* confirmed from a primary source here.

### (e) Per-lane toggle. Phase Plant.

Phase Plant is not cable-patched at the effects stage — it has a generator area
and **three fixed effect lanes**, each with a **Poly button**. With Poly on, "the
effects processing is done for each voice separately instead of on all voices
mixed together"
([Kilohearts Docs — Phase Plant](https://kilohearts.com/docs/phase_plant)). The
docs state the cost plainly: on a POLY lane "each note played will require about
the same amount of CPU, which can quickly add up when playing chords."

This is the closest prior art to note-color's *current* shipped model
(`effects.EffectsChain` as an ordered list on a shared mix bus) — with the
addition of a per-lane poly switch and an explicit CPU warning attached to it.

### (f) Voices as patcher instances. Max/MSP `poly~`.

`poly~` wraps a patcher in 1–1023 instances and routes inlet signals to `in~`
objects inside each one. Voice allocation is least-recently-used by default
(`legacynotemode 0`); `target n` addresses one instance, `target 0` all of them;
`mute` disables DSP for a voice and saves CPU. Crucially: "signals sent to the
inlet of `out~` objects in each patcher instance are **mixed** if there is more
than one instance"
([Max 8 reference — `poly~`](https://docs.cycling74.com/legacy/max8/refpages/poly~)).

So the boundary is the `poly~` object's own boundary: inside is per-voice, outside
is mixed, and the summing is implicit at `out~`. It also supports per-instance
up/downsampling (`up 2`, `down 2`).

### Summary table for Q1

| System | Boundary shown? | Crossing poly→mono | Summing node |
|---|---|---|---|
| VCV Rack | Hinted (thicker cable) | Always legal; sum for audio, ch.1 for CV | Implicit in port read, or explicit `Sum`/`Merge` |
| Reaktor Primary | Yes — module icons + red error wire | **Forbidden** | Explicit Voice Combiner module |
| Massive X | Yes — two labelled areas of the canvas | Only into the Mono Area | Fixed, at the area boundary |
| Bitwig Grid | Yes — two different devices | N/A (different device) | Device output |
| Phase Plant | Yes — per-lane Poly button | Toggle the lane | Lane output |
| Max `poly~` | Yes — the object's walls | N/A | Implicit at `out~` |

**Nobody hides the boundary completely except VCV, and VCV pays for it by making
every module poly-aware and every port an array.**

---

## 2. Feedback loops — four genuinely different answers

| System | Direct feedback | Mechanism | Granularity |
|---|---|---|---|
| VCV Rack | Always allowed, no special case | Every cable is a unit delay by construction | **1 sample** |
| Reaktor Core | Allowed, auto-resolved | Compiler inserts an implicit z⁻¹ and colours the loop **orange** | **1 sample** |
| Bitwig Grid | **Refused at patch time** | Must insert a Long Delay module | **1 block** (audio buffer size) |
| Max/MSP + gen~ | Refused in the signal graph | `send~`/`receive~`, `tapin~`/`tapout~`, or gen~'s `history` | 1 signal vector, or 1 sample inside gen~ |
| JUCE `AudioProcessorGraph` | Not rejected, but **cut to silence** | Back edge reads a read-only empty buffer | n/a (loop does not actually close) |

### VCV Rack: the delay is unconditional, so feedback is free

Voltage Standards states each cable "introduces a 1-sample delay of its carried
signal from the output port to the input port"
([Voltage Standards](https://vcvrack.com/manual/VoltageStandards)). The engine
source shows why: `Engine_stepFrame` steps **all modules first** (across worker
threads, first-come-first-served module-to-thread allocation, i.e. *no
topological order at all*) and only then runs `Engine_stepFrameCables`, which
copies every output buffer into its destination input
([Engine.cpp](https://github.com/VCVRack/Rack/blob/v2/src/engine/Engine.cpp)).
`Engine::stepBlock(frames)` is just `for (int i = 0; i < frames; i++) Engine_stepFrame(this);`.

This is the cleanest architecture in the survey: **no cycle detection, no sort,
no error states, feedback patches just work**, and the price is a fixed one-sample
latency per hop and a hard commitment to sample-at-a-time execution.

### Reaktor Core: detect the cycle, insert the delay, colour the wire

From [REAKTOR 6 — Building in Core](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/REAKTOR_6_Building_in_Core_English_2015_11.pdf), §4.10:

> "REAKTOR Core allows the usage of the feedback loops in the Structures,
> provided each feedback loop contains at least one scalar (float or int) wire.
> The loop will be handled by automatically placing an implicit one-sample audio
> delay somewhere within the loop"

with the caveats that (a) it is "not guaranteed that REAKTOR Core will use at
maximum one implicit one-sample delay per detected feedback loop", (b) a loop
with no scalar wire is a **red error**, (c) a loop that an SR bus definition
depends on is also an error, and (d) for exact placement you use the `z^-1 fbk`
macro explicitly, after which "the compiler does not highlight the loop anymore"
because the macro is *nonsolid* and the cycle formally disappears.

The UI detail worth stealing: **the whole loop is highlighted orange**, and the
6.x changelog notes this was improved to highlight "the entire loop instead of
just the resolution point."

### Bitwig Grid: refuse the cable, sell a module

Direct feedback is rejected — the cable "disappears upon release." To make
feedback you insert **Long Delay**, which is "specially configured to allow
feedback and has a minimum delay time of one block size (see Audio Settings)"
([Special Connections](https://www.bitwig.com/userguide/latest/special_connections/)).
So Bitwig's feedback granularity is the **audio buffer**, not a sample — the
direct consequence of being a block-processing graph. At our 512/44100 that would
be 11.6 ms, far too coarse for comb/Karplus-Strong-style feedback.

### Max/MSP: the graph is acyclic; the escape hatch is a delay object

`gen~` "will not allow a feedback loop (since it represents a synchronous
process)"; the `history` operator provides "a single-sample delay (a Z⁻¹
operation)"
([Gen Overview](https://docs.cycling74.com/userguide/gen/_gen_overview/)). In
plain MSP the same rule holds at the object graph level, and `send~`/`receive~`
introduces **one signal vector** of delay when a loop is detected. That last
detail I could only source from Cycling '74 *forum* posts, not from the reference
page — flagging it as **not primary-verified**, though it is consistent with
`tapin~`/`tapout~`'s documented one-vector minimum.

### JUCE: the least good answer, worth knowing as an anti-pattern

`AudioProcessorGraph::canConnect` checks only `isConnectionLegal(...) && !isConnected(...)`
— channel validity and duplicates, **no cycle check**
([juce_AudioProcessorGraph.cpp, JUCE 8.0.0](https://github.com/juce-framework/JUCE/blob/8.0.0/modules/juce_audio_processors/processors/juce_AudioProcessorGraph.cpp)).
`createOrderedNodeList` builds an ancestry-ordered list by insertion, and
`findBufferForInputAudioChannel` handles a source whose buffer has not been
produced yet with `// if not found, this is probably a feedback loop` →
`bufIndex = readOnlyEmptyBufferIndex`. In other words JUCE lets you draw the
cycle and then **silently feeds silence into the back edge**. The widely-repeated
forum claim that "the graph stops rendering if you have feedback loops" does not
match the source; the real behaviour is a silent cut. Either way it is the
behaviour to avoid copying.

### The standard technique, stated plainly

Every system that supports real feedback does the same thing underneath: **break
the cycle with a unit delay**. The only real choices are *where* the delay lives
(everywhere, as in VCV; auto-inserted at a compiler-chosen point, as in Reaktor;
or in a user-placed module, as in Bitwig/Max) and *how big it is* (one sample vs.
one block). Patch-time cycle detection is used only to decide which of those three
to do — none of the surveyed systems detects a cycle and simply gives up.

---

## 3. Modulation vs. audio cables

Two camps, and the split is not where you might expect.

**One cable type, one rate (VCV Rack).** Audio, CV, gates and triggers are all
just voltages on the same cable, and Voltage Standards defines the conventions
(±5 V audio, 0–10 V unipolar CV, 1 V/octave pitch, 10 V triggers) rather than the
type system. The only in-band distinction is the guidance that a mono module
should *sum* a poly audio input but take *channel 1* of a poly CV input — which
means the module, not the cable, decides what a signal "is". Reported tradeoff:
maximum flexibility (any LFO is an oscillator and vice versa), at the cost of the
patch never telling you what a wire carries.

**Typed, colour-coded signals at one rate (Bitwig Grid).** The Grid has five
signal classes, each with a colour
([On Grid Signals](https://www.bitwig.com/userguide/latest/on_grid_signals/)):

- **Logic (yellow)** — bistate; "any signal level at or above `+0.5` is treated as high logic"
- **Phase (purple)** — unipolar 0 to just below 1, wrapping (1.02 → 0.02)
- **Pitch (orange)** — bipolar, 0 = middle C, ±0.1 per octave
- **Untyped (red/turquoise)** — the common case
- **Secondary untyped (blue)** — secondary control inputs

All of them are the same *technical* cable: every patch cord is a **stereo pair**,
and all Grid signals run at **four times the project sample rate (400%)** so that
audio-rate modulation is well-behaved. The genuinely separate thing in Bitwig is
the *Modulator* system outside the Grid: "modulators… are mono and operate at
your current sample rate" — i.e. Bitwig's device-level modulation is a
lower-rate, mono, different mechanism from Grid patch cords.

**Typed by data type, not by role (Max/MSP).** Max distinguishes **event cords**
from **signal cords** by "stripe patterns and colors", along with MC, Jitter
matrix, GL texture and geometry cords
([Patch Cords](https://docs.cycling74.com/userguide/patch_cords/)). This is a
*technical* split — messages are asynchronous, signals are vectors — not an
audio-vs-modulation split. Reaktor does the same: its Core Cells were unified so
that "the input and output ports of the Core Cells can be switched between audio
and event types" (Building in Core changelog).

**Reported tradeoffs.** Nobody in this survey separates modulation from audio
*technically* for signal-flow reasons; where a separation exists it is either
(a) cosmetic typing to catch mistakes and aid reading (Bitwig's five colours), or
(b) a genuine event-vs-signal execution-model split (Max, Reaktor). The one real
cost Bitwig pays for uniformity is stated in its own docs: everything running at
4× sample rate and in stereo, always, whether the wire needed it or not.

**Direct implication for a numpy engine:** the Bitwig approach — one cable type,
colour-coded by declared semantic role, same buffer representation — gets the
legibility benefit for free. The one distinction that *would* pay for itself in
numpy is **scalar-vs-vector**: a modulation value that is constant over a block
can be a Python float rather than a 512-element array, which is measurably
cheaper (see §4). That is a *runtime* optimisation, not necessarily a second
cable type, and VCV/Bitwig give no precedent for exposing it to the user.

---

## 4. Graph execution cost

### No usable published figures for the Python case

I found **no published benchmark** comparing dynamic-graph vs. fixed-topology
audio DSP in an interpreted language. The nearest primary statements are
qualitative:

- gen~'s value proposition is exactly this overhead: "a chain of Gen objects
  compiles down into one single meta-object, **removing the usual overhead that
  Max encounters when passing messages and signals between objects**", and it
  "includes an optimization that takes into account the update rate of each
  operator, so that any calculations that do not need to occur at sample
  rate… instead process at a slower rate"
  ([Gen Overview](https://docs.cycling74.com/userguide/gen/_gen_overview/)).
- Reaktor Core's manual makes the same point about latches: "the compiler…
  treats it in special, optimized way. Depending on the circumstances, using a
  Latch Macro at a given position in a Structure will produce more efficient code
  than without using it" (Building in Core §4.5).
- Phase Plant's docs quantify only the polyphony axis, not the graph axis:
  per-voice effect lanes cost ~1× per note.

Treat any number quoted elsewhere as unverified. What follows is **measured
locally** instead.

### Measured on this machine (Python 3.14.7, numpy 2.5.2, 512-frame float32)

Block budget: **11.61 ms**.

| Operation (512 float32) | Cost |
|---|---|
| Empty Python function call | 0.054 µs |
| `a + b` | 0.67 µs |
| `a * b` | 0.61 µs |
| `np.multiply(a, b, out=a)` | 0.54 µs |
| `np.tanh(b)` | 1.43 µs |
| `np.sin(b)` | 1.32 µs |
| `scipy.signal.lfilter` (biquad) | 8.05 µs |
| Pure-Python per-sample loop over 512 samples (one mul + one add) | 32.7 µs (≈64 ns/sample) |

Three conclusions fall straight out:

1. **Graph dispatch overhead is a non-issue.** A Python call is 0.054 µs against a
   0.54 µs numpy op — roughly 10% overhead per op, and a module is several ops. A
   16-voice × 8-module graph is 128 module invocations; even at a generous 3 µs
   each that is **0.38 ms, ~3% of the block budget**. Dynamic dispatch is *not*
   what would kill a Python node graph. Decision 55's own numbers agree: the
   voices themselves already cost 3.57 ms at 16.
2. **Per-module allocation is the real cost, and it is avoidable.** The gap between
   `a * b` (0.61 µs, allocates) and `np.multiply(..., out=a)` (0.54 µs) is small at
   512 frames but it is pure garbage pressure in the callback — and CLAUDE.md
   already forbids allocation in the audio callback. A graph runtime should
   pre-allocate one buffer per edge at patch-compile time, exactly as JUCE's
   `RenderSequenceBuilder` does with its `getFreeBuffer` / buffer-reuse scheme.
3. **Sample-at-a-time is the cliff.** 64 ns/sample in pure Python means a single
   trivial per-sample node costs 32.7 µs per voice per block; × 16 voices =
   **0.52 ms for one node**, and a realistic feedback node (a state-variable
   filter, say) is 5–10× that. **A VCV-style engine — every cable a 1-sample
   delay, the whole graph stepped per sample — is flatly impossible here**: it
   would be 16 × 512 × (modules) Python iterations per block.

### Block-at-a-time vs. sample-at-a-time, for graphs with feedback

This is the crux, and the prior art maps onto it cleanly:

- **VCV** chose sample-at-a-time globally. It gets 1-sample feedback everywhere,
  for free, and it can only afford that because it is C++ with SIMD-friendly
  fixed-size `float[16]` ports.
- **Bitwig** chose block-at-a-time and therefore had to make feedback cost a whole
  buffer and be gated behind a special module.
- **Reaktor Core and gen~** chose to *compile*, which lets them be logically
  sample-at-a-time while being physically block-at-a-time: the per-sample loop is
  generated machine code, and only the loop body is per-sample.

A Python/numpy engine has exactly the Bitwig shape and none of the escape
hatches. So the honest position for note-color is:

> **Block-at-a-time graph, block-granular feedback (11.6 ms), with any
> sample-accurate feedback confined *inside* a single compiled module** — i.e. a
> delay/comb/filter module whose internals are `scipy.signal.lfilter` or a numba/C
> kernel, not something the user assembles from cables. This is the Bitwig
> "Long Delay" answer, arrived at for the same reason Bitwig arrived at it.

Note that `scipy.signal.lfilter` at 8.05 µs is the precedent that makes this work:
an IIR with sample-accurate internal feedback, running at 1/4000th of the block
budget, because the recursion happens in C.

---

## 5. Plugin formats — **the CLAP-only decision needs revisiting**

### The headline: VST3 is now MIT-licensed

The premise behind "CLAP-only" (recorded in [#145](https://github.com/pellepang/note-color/issues/145)
and restated in [#200](https://github.com/pellepang/note-color/issues/200))
was, in substance, that VST3's dual GPLv3-or-Steinberg-proprietary licence is
incompatible with an MIT project, while CLAP's MIT is not. **That premise expired
in October 2025.**

From the Steinberg VST 3 Developer Portal
([VST 3 License](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License.html)):

> "Since version 3.8, **VST 3** is licensed under MIT license."
>
> "Licensing under **GPLv3** and the **Steinberg proprietary license** is no
> longer available."
>
> "Neither fees nor memberships are required. No need to sign any documents."
> The licence "is perpetual and does not expire."

Verified directly against the SDK repository: `LICENSE.txt` on
`steinbergmedia/vst3sdk` master is a plain MIT licence, "Copyright (c) 2026,
Steinberg Media Technologies GmbH", and the same MIT text is in the
`vst3_pluginterfaces`, `vst3_base` and `vst3_public_sdk` submodules. The commit
history shows the licence file changing at "VST SDK 3.8.0" (2025-10-20).
Corroborated by [Sound On Sound](https://www.soundonsound.com/news/steinberg-adopt-mit-license-vst3)
and [KVR](https://www.kvraudio.com/news/steinberg-moves-vst-3-sdk-to-mit-open-source-license-asio-now-gplv3-65179).
The one residual obligation is **trademark**: using the VST name/logo still
requires following Steinberg's guidelines, which is a branding matter, not a code
licence.

### Licence comparison, current

| Format | Licence | Verified from |
|---|---|---|
| **CLAP** | MIT | [free-audio/clap](https://github.com/free-audio/clap), GitHub API reports `mit`; last push 2026-07-28 |
| **VST3** | MIT since SDK 3.8 (Oct 2025) | `LICENSE.txt` on steinbergmedia/vst3sdk master; dev portal |
| **LV2** | ISC | [lv2/lv2](https://github.com/lv2/lv2) `COPYING` |

All three are now permissive. **Licence is no longer a discriminator.**

### What hosting one actually requires

**CLAP** is the cheapest to host by a wide margin, and this is its real
advantage, not the licence:

- Pure **C ABI**. A host includes `clap/clap.h`, resolves the exported entry point
  from `entry.h`, and calls through plain C function-pointer structs. There is no
  C++ ABI, no COM-like interface layer, no code generation.
- "Most features come from extensions, which are in fact C interfaces" queried by
  id from the host object, so a minimal host can implement `audio-ports`,
  `params`, `note-ports` and ignore everything else.
- "Each extension method includes a clear thread specification" — the threading
  contract is in the header, which matters a lot for a host with a Python GUI
  thread and a `sounddevice` callback thread.
- ABI stability: "a plugin binary compiled with CLAP 1.x can be loaded by any
  other CLAP 1.y."
- Reference implementations exist: [clap-host](https://github.com/free-audio/clap-host)
  ("a very simple host") and [clap-plugins](https://github.com/free-audio/clap-plugins).

(All quotes from the [free-audio/clap README](https://github.com/free-audio/clap).)

**VST3** requires a C++ host: COM-style `FUnknown` interfaces, `IComponent` /
`IAudioProcessor` / `IEditController` separation, the SDK's own hosting classes.
MIT or not, you cannot reach it from `ctypes`.

**LV2** is the second-easiest: a plain C API plus a Turtle/RDF manifest the host
must parse. Cheap ABI, non-trivial metadata layer, and a much smaller commercial
plugin ecosystem.

### Can any of them be hosted from Python, realistically?

**Short answer: VST3 yes (with a licence catch), CLAP no (nothing exists).**

- **`pedalboard`** (Spotify) — `load_plugin()` supports **VST3 on macOS/Windows/Linux
  and Audio Units on macOS only**; **CLAP is not supported**
  ([API reference](https://spotify.github.io/pedalboard/reference/pedalboard.html)).
  Mature: v0.9.25 on PyPI, 6.3k stars, actively pushed (2026-09). **But it is
  GPL-3.0** (GitHub API + PyPI metadata) — because it embeds JUCE. Depending on
  it would impose GPLv3 on note-color, which is precisely the outcome the original
  CLAP-only decision was trying to avoid. The irony is complete: VST3 itself is
  now MIT, but the only mature Python route to it is not.
  Its docs also say nothing about realtime or thread-safety guarantees, which for
  an in-callback host is a serious gap.
- **`DawDreamer`** — VST3 + AU + FAUST, 1.3k stars, actively maintained
  (2026-09), also **GPL-3.0**, and architecturally an offline/render-graph tool
  rather than a realtime callback host.
- **CLAP from Python** — I searched PyPI and the GitHub repository search and
  found **nothing**: no bindings, no ctypes wrapper, no host. (`python-clap` on
  PyPI is an argparse wrapper, unrelated.) The only adjacent thing is
  [WebCLAP/browser-test-host](https://github.com/WebCLAP/browser-test-host).
  CLAP's C-only ABI means a `ctypes`/`cffi` binding is *feasible* — arguably the
  most feasible of the three — but it would be **written from scratch by this
  project**, and it would have to marshal audio buffers and event queues across
  the Python/C boundary inside a 11.6 ms callback.

### Does CLAP-only still hold?

**The conclusion survives, but the reasoning has to be replaced.** Recommended
restatement for the decision record:

- ~~"VST3's licence is incompatible with MIT"~~ — **false since SDK 3.8**. Delete it.
- "CLAP is the only format whose ABI a Python host can plausibly bind to without
  a C++ layer" — **true, and now the load-bearing reason.**
- Cost, stated honestly: **there is no Python CLAP host today.** Choosing CLAP
  means writing one. The realistic shape is a small C shim (or a `cffi` binding)
  behind the `sound_engine.py` `Engine`/`Voice` Protocol seam that
  [#145](https://github.com/pellepang/note-color/issues/145) already reserves —
  which is to say, opening that seam, the thing decision 55 deliberately left
  closed.
- The cheap interim alternative, if plugin hosting ever becomes urgent before
  that seam opens, is `pedalboard` for **offline/render** paths only (where
  `transcribe --play` already pre-renders), accepting GPLv3 for that optional
  dependency — the same "optional dependency that degrades to unavailable"
  pattern `docs/research/sf2-fluidsynth-playback.md` established. **This would be
  a licence change for anyone distributing that path** and should not be done
  casually.

---

## 6. Canvas UI: how the big systems fight cable clutter

### VCV Rack — global sliders, not per-cable management

The View menu exposes exactly two cable knobs, both confirmed in the manual and
the source:

- **Cable opacity**: "Sets the transparency level for patch cables. Double-click
  to reset to **50%**."
- **Cable tension**: "Sets the relative length of patch cables. Double-click to
  reset to **50%**." Tension 0 = hanging loose/curved, 1 = straight line.

([MenuBar](https://vcvrack.com/manual/MenuBar); the settings header confirms
`cableOpacity` "Opacity of cables in the range [0,1]", `cableTension`
"Straightness of cables in the range [0,1]. Unitless and arbitrary", plus
`cableColors`, `cableLabels`, `cableAutoRotate` —
[include/settings.hpp](https://github.com/VCVRack/Rack/blob/v2/include/settings.hpp)).

The default palette is **five colours**, hardcoded in `settings::resetCables()`:
`#f3374b` red, `#ffb437` yellow, `#00b56e` green, `#3695ef` blue, `#8b4ade`
purple ([src/settings.cpp](https://github.com/VCVRack/Rack/blob/v2/src/settings.cpp)).
Cables cycle through them as you patch; users extend the list by editing
`cableColors` in `settings.json`. There is a parallel `cableLabels` array — colours
can be *named*, which is the cheap version of a legend.

Two behaviours worth copying:

- **Hover-to-reveal**: even with cables dimmed to invisible, hovering a jack
  displays that connection.
- **Lock module positions**, "Prevents accidentally dragging modules."

Rendering style: hanging bezier/catenary, physically skeuomorphic. **No
auto-layout at all** — modules are placed by the user in a 1-D rack row, and
placement carries no semantic meaning.

### Reason — flip the rack, and three tiers of hiding

Cables live on the **back** of the rack; you flip with **Tab**, the Flip Rack
button, or View → Flip Rack
([Working with the Rack](https://docs.reasonstudios.com/reason14/working-with-the-rack)).
Reason's Hide Cables setting has graded modes (from the Settings/Routing pages):
auto-routed cables drawn transparent so manual ones stand out; non-selected
devices' cables transparent; or **no cables at all, connections shown as coloured
dots in the jacks**. That last mode is the most aggressive clutter answer in this
survey and is worth noting as an option.

Reason is also the only system here with real **auto-layout**: adding a device
"attempts to automatically route it in a logical way", with Shift to suppress
auto-routing, and rack ordering governed by a Rack Sorting mode (Rack per Track /
Auto-Group Devices / No Grouping). Folded devices can't be re-cabled, but
"drag a cable to it and hold it there for a moment" auto-unfolds them.

### Max/MSP — routing style is a user preference

Max supports **curved (default), segmented, and straight** cords; Shift-clicking
an outlet inverts the preference for one cord; segmented cords are drawn by
clicking each bend point ([Patch Cords](https://docs.cycling74.com/userguide/patch_cords/)).
Clutter tools: per-cord **Hide on Lock** / **Show on Lock**, per-cord colour via
right-click → Color or the Format Palette's Patchline Color, and **Arrange →
Auto Align / Route Patcher Cords** to tidy segmented connections automatically.
Cord *type* is conveyed by stripe pattern + colour (event vs. signal vs. MC vs.
Jitter vs. GL).

### Bitwig Grid — colour by signal semantics

The Grid's clutter strategy is the typing system from §3: five signal classes,
five colours, so a dense patch is readable by hue rather than by tracing. I did
**not** find primary documentation of Grid-specific cable-hiding, opacity, or
auto-layout controls; the Grid does snap modules to a grid layout, but I could
not confirm an automatic placement feature from the user guide.

### Synthesis for a PySide6 canvas

The consistent, cheap wins across all four:

1. **A small fixed palette that cycles as you patch** (VCV: five colours, cycling,
   user-editable, optionally labelled) beats per-cable manual colouring.
2. **A global opacity/dim control plus hover-to-reveal** (VCV) is the highest
   value-per-line-of-code clutter feature in the survey.
3. **Colour by declared signal role** (Bitwig) is the highest value-per-line for
   *legibility* rather than density.
4. **Bezier with adjustable tension** is the near-universal default; orthogonal /
   segmented routing appears only in Max, as an option, and requires either manual
   bend points or an auto-router. For a first cut, bezier is the safe choice.
5. **Auto-layout is rare.** Only Reason does it, and only because its rack is
   1-dimensional and device roles are known. A free 2-D canvas in this project
   should expect user placement, and should instead invest in auto-*routing* a
   sensible default connection when a module is dropped (Reason's "attempts to
   route it in a logical way", Shift to suppress).

---

## What I could not verify

- **Bitwig's "Voicing 'FX Grid'" / "Voicing 'Note Grid'" chapters.** The user
  guide pages I could fetch reference them but do not contain them, so the exact
  voice-allocation and output-summing semantics of the Grid devices are
  unconfirmed here. FX Grid's **Auto-Gate** appears only in secondary sources.
- **Max's `send~`/`receive~` one-signal-vector feedback delay.** Consistent across
  multiple Cycling '74 *forum* threads and consistent with `tapin~`/`tapout~`'s
  documented one-vector minimum, but I did not find it stated on a reference page.
- **Sources that disagree:** the JUCE forum's claim that `AudioProcessorGraph`
  "stops rendering if you have any feedback loops" contradicts the JUCE 8.0.0
  source, which substitutes a read-only empty buffer for the back edge. I trust
  the source.
- **Grid cable-hiding / auto-layout controls**: no primary documentation found;
  absence of evidence only.
- **No published benchmark** exists (that I could find) for dynamic-graph vs.
  fixed-topology audio DSP overhead in an interpreted language. §4's numbers are
  my own measurements on this machine, not literature.

## Sources

- VCV Rack Manual — [Polyphony](https://vcvrack.com/manual/Polyphony), [Voltage Standards](https://vcvrack.com/manual/VoltageStandards), [Menu Bar](https://vcvrack.com/manual/MenuBar)
- VCV Rack v2 source — [src/engine/Engine.cpp](https://github.com/VCVRack/Rack/blob/v2/src/engine/Engine.cpp), [src/settings.cpp](https://github.com/VCVRack/Rack/blob/v2/src/settings.cpp), [include/settings.hpp](https://github.com/VCVRack/Rack/blob/v2/include/settings.hpp)
- Bitwig User Guide — [Welcome to The Grid](https://www.bitwig.com/userguide/latest/welcome_to_the_grid/), [The Grid Devices](https://www.bitwig.com/userguide/latest/the_grid_devices/), [On Grid Signals](https://www.bitwig.com/userguide/latest/on_grid_signals/), [Special Connections](https://www.bitwig.com/userguide/latest/special_connections/)
- Native Instruments — [REAKTOR 6: Building in Primary (PDF)](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/REAKTOR_6_Building_in_Primary_English_0419.pdf), [REAKTOR 6: Building in Core (PDF)](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/REAKTOR_6_Building_in_Core_English_2015_11.pdf), [Massive X Manual — Routing](https://docs.native-instruments.com/ni-tech-manuals/massive-x-manual/en/routing)
- Cycling '74 — [poly~ reference](https://docs.cycling74.com/legacy/max8/refpages/poly~), [Gen Overview](https://docs.cycling74.com/userguide/gen/_gen_overview/), [Patch Cords](https://docs.cycling74.com/userguide/patch_cords/)
- Kilohearts — [Phase Plant docs](https://kilohearts.com/docs/phase_plant)
- Reason Studios — [Working with the Rack](https://docs.reasonstudios.com/reason14/working-with-the-rack), [Routing Audio and CV](https://docs.reasonstudios.com/reason13/routing-audio-and-cv)
- JUCE 8.0.0 source — [juce_AudioProcessorGraph.cpp](https://github.com/juce-framework/JUCE/blob/8.0.0/modules/juce_audio_processors/processors/juce_AudioProcessorGraph.cpp)
- Plugin formats — [free-audio/clap](https://github.com/free-audio/clap), [clap-host](https://github.com/free-audio/clap-host), [VST 3 Developer Portal — Licensing](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License.html), [steinbergmedia/vst3sdk](https://github.com/steinbergmedia/vst3sdk), [lv2/lv2](https://github.com/lv2/lv2), [Sound On Sound — Steinberg adopt MIT License for VST3](https://www.soundonsound.com/news/steinberg-adopt-mit-license-vst3), [KVR — Steinberg Moves VST 3 SDK to MIT](https://www.kvraudio.com/news/steinberg-moves-vst-3-sdk-to-mit-open-source-license-asio-now-gplv3-65179)
- Python hosts — [spotify/pedalboard](https://github.com/spotify/pedalboard) ([API reference](https://spotify.github.io/pedalboard/reference/pedalboard.html)), [DBraun/DawDreamer](https://github.com/DBraun/DawDreamer)
