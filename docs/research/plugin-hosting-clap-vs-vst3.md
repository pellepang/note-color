# Plugin hosting: CLAP vs VST3, and what a Python host actually costs

Research for [#217](https://github.com/pellepang/note-color/issues/217), part of
map [#179](https://github.com/pellepang/note-color/issues/179). Read this
alongside decision
[56 §10](../decisions/56-the-synth-view-becomes-a-real-cable-patched-modular-synth-issue-200-round-2.md)
(the current, candid state of the question) and decision
[59](../decisions/59-the-graph-module-contract-and-plugin-hosting-moved-into-the-design-issue-202.md)
(the contract shaped to receive a plugin). This is the third pass at the
format question — #145 chose CLAP on a licensing argument that decision 56
found dead; this research is what the ticket asked for instead of a fourth
round of received wisdom: primary sources, and an honest size.

**No code under `src/` was touched to produce this, and no audio device was
opened.** Everything below is header-reading, licence-reading, and
repository archaeology.

---

## 1. The real cost of a Python CLAP host

CLAP's advantage was never the licence (§3 below shows VST3 is now equally
permissive) — it is that the ABI is plain C, so the question "what would a
`ctypes`/`cffi` binding actually have to do" has a concrete, boundable
answer. Below is that answer, built from the
[free-audio/clap](https://github.com/free-audio/clap) headers on `main`.

### The unavoidable core: five structs, four entry points

A host that can load one plugin, feed it audio, and turn a knob needs:

1. **`clap_plugin_entry_t`** (`entry.h`) — the DSO's exported symbol
   (`clap_entry`, resolved by name — `ctypes.CDLL(...).clap_entry` is exactly
   the kind of lookup `ctypes` is built for). Three functions: `init(path)`,
   `deinit()`, `get_factory(id)`. `init`/`deinit` are refcounted by the
   plugin itself ("a static counter... performing actual init/cleanup only
   when the counter transitions between zero and one") — a host is expected
   to call them in pairs but the plugin has to tolerate not being the only
   caller, since several hosts (or a scan pass and a load pass) may share
   one process.
2. **`clap_plugin_factory`**, obtained via `get_factory` — enumerates and
   instantiates plugins by ID string. Thread-safe, unlike `init`/`deinit`.
3. **`clap_plugin_t`** — the instance itself. Twelve function pointers:
   `init`, `destroy`, `activate(sample_rate, min_frames, max_frames)`,
   `deactivate`, `start_processing`, `stop_processing`, `reset`, `process`,
   `get_extension`, `on_main_thread`, plus `desc` and `plugin_data`. Note
   this is **two more lifecycle calls than `contract.py`'s
   `activate`/`process`/`deactivate`**: CLAP separates *activation*
   (allocate, off the audio thread) from *start/stop processing* (audio
   thread, cheap, toggled when a voice goes idle/active without a
   reallocation). Our contract has no equivalent of `start_processing` —
   see §4.
4. **`clap_host_t`** — the callback side, which *we* implement and hand to
   the plugin at instantiation. Four thread-safe callbacks:
   `get_extension`, `request_restart`, `request_process`,
   `request_callback` (schedule `on_main_thread`, "typically... within
   33ms/30Hz"). A minimal host still has to implement this struct correctly
   — a plugin that calls `request_callback` and gets nothing back is a
   plugin that silently never finishes initializing.
5. **`clap_process_t`** (`process.h`) — built fresh every block: frame
   count, a `clap_input_events`/`clap_output_events` pair (sorted-by-time
   event lists the plugin reads/writes via `size()`/`get()`/`try_push()`,
   never owns), input/output `clap_audio_buffer_t` arrays, and
   `steady_time` (a monotonic sample counter, `-1` if unavailable) which a
   plugin may use for its own scheduling.

That is the audio-thread surface, and it is genuinely small — this matches
the issue's framing exactly. It is also the part `ctypes` handles best:
flat structs of function pointers, no vtables-within-vtables, no reference
counting on the audio path.

### The extension surface: not optional, just deferred

"Most features come from extensions" is not a footnote — a plugin with no
extensions can't be patched into anything. A usable instrument/effect host
needs at minimum:

- **`clap_plugin_audio_ports`** — `count()`/`get()` per direction, each
  port carrying `channel_count`, `port_type` ("mono"/"stereo"), and
  `in_place_pair` (whether input and output may share a buffer). Queryable
  only while the plugin is **deactivated**, which forces host discovery
  order: instantiate → query ports → decide buffer shapes → `activate()`.
  This is a real ordering constraint `contract.py`'s `ports()` (any-thread,
  any-time) does not have.
- **`clap_plugin_note_ports`** — the same shape for `PORT_EVENT`, with a
  `supported_dialects` bitfield (CLAP-native / MIDI / MIDI-MPE / MIDI2) and
  a `preferred_dialect`. A host has to pick a dialect and translate; our
  `NoteContext` (one note, `pitch`/`velocity`/`gate`) is not any of these —
  see §4.
- **`clap_plugin_params`** — `count`/`get_info`/`get_value`/`value_to_text`
  /`text_to_value` (all main-thread) plus **`flush`**, whose thread
  changes with plugin state: audio-thread while `active`, main-thread
  otherwise. `value_to_text`/`text_to_value` exist so a generic knob UI can
  show "2.3 kHz" without the host knowing what the parameter means —
  directly relevant to the GUI question in §4.
- **`clap_plugin_state`** — `save(stream)`/`load(stream)`, both
  main-thread, opaque bytes. "If a parameter value changes... the state is
  dirty" is stated as *implicit*, meaning a host cannot assume params-only
  round-tripping is equivalent to calling `save`/`load` — some plugins keep
  state outside their declared parameters entirely.
- **`clap_plugin_latency`** — one `get()`, callable only
  `main-thread & (being-activated | active)`, value fixed for the life of
  one activation ("can only change during `plugin->activate`"). This maps
  cleanly onto `contract.py`'s existing `latency_frames()` — the one place
  our contract already has a plugin-shaped hook with nothing behind it yet.
- **`clap_plugin_gui`** — see §4; not needed for a headless/generic-knobs
  host, mandatory for hosting the plugin's own window.

None of this is exotic, but "the scan/GUI/state surfaces are not [small]"
(the issue's own framing) is accurate: five more extensions, each with its
own struct family, each needing its own `ctypes.Structure` definitions
maintained against a spec that can add extensions the host has never heard
of (`get_extension` returning `NULL` is the correct, silent answer).

### Threading, concretely

CLAP's per-method thread tags are real engineering discipline, not just
documentation. The two that bite hardest for a Python host:

- **`process()` must never allocate, lock, or call back into the host
  synchronously** — this is the plugin's obligation, not the host's, but
  the host is what makes it *checkable*: CLAP defines a
  `clap_host_thread_check` extension (queried by the plugin) precisely so
  a plugin can ask "am I currently on the audio thread?" and get a truthful
  answer. A Python host has to track that fact per-call, which is one more
  small piece of state to get right, not a hard problem.
- **The GIL is the sharp edge, and it is ours, not CLAP's.** `process()`
  calling into Python at all — even just to shuttle a `ctypes` buffer
  pointer — means the plugin's audio-thread work now competes for the GIL
  with the UI thread's parameter edits and the render thread's own numpy
  work. CLAP's contract assumes a native audio thread with no such
  contention; nothing in the spec accounts for a GIL, because nothing in
  the spec expects Python. This is the real, CLAP-specific cost the issue
  asked to be named plainly: **hosting a CLAP plugin in-process from
  Python does not just require binding the ABI, it requires guaranteeing
  the binding call itself never blocks on the GIL for a plugin that expects
  hard real-time.** `ctypes` calls release the GIL for the duration of the
  native call (CPython does this for any C call through `ctypes`), which
  helps — the plugin's own `process()` runs without holding the GIL — but
  the *return* into Python to hand results to the rest of `contract.py`'s
  `process()` re-acquires it, and that reacquisition can stall behind
  whatever else is holding the GIL at that instant. This is measurable, not
  theoretical, and nobody has measured it for this specific shape (Python
  audio thread hosting a native CLAP plugin) — treat it as an open risk,
  not a settled cost.

### Sizing the minimal host

Concretely, in order: `entry.h` load + `get_factory` + instantiate (a day,
mostly `ctypes.Structure` boilerplate); `audio-ports` + `note-ports`
discovery (a day); `activate`/`process`/`deactivate` wired to
`contract.Module` with one plugin instance under test, silence in/silence
out (two to four days, most of it debugging struct layout mismatches
`ctypes` will not catch at compile time the way a C compiler would);
`params` read/write mapped onto `ParamBlock` (two to three days, because
`value_to_text` output is what a generic knob UI needs and CLAP delivers
per-sample event offsets `ParamBlock` does not yet carry, #208's problem);
`state` save/load as an opaque blob (a day, but see §4 for why it does not
fit decision 69's format cleanly); the thread-check extension and getting
`request_callback` actually delivering (a day). **Call it two to three
weeks of focused work to host one well-behaved open-source CLAP plugin
(effects only, no GUI, no note dialect beyond raw pitch/velocity/gate) as a
proof of concept**, before any of §4's structural gaps in `contract.py` are
closed. Hosting an arbitrary commercial plugin — one that queries
extensions the proof-of-concept never implemented, or that assumes a
native thread with no GIL — is open-ended past that point, the same way
"we support VST3" is open-ended for any real host.

---

## 2. VST3, re-examined now that the licence changed

### The licence, verified at the source

Fetched directly, not taken from the decision doc:

- `steinbergmedia/vst3sdk`, `LICENSE.txt` on `master`: plain MIT text,
  **"Copyright (c) 2026, Steinberg Media Technologies GmbH."**
- The [VST 3 Developer Portal licensing
  page](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License.html)
  states "Since version 3.8, VST 3 is licensed under MIT license" and "GPLv3
  and the Steinberg proprietary license are no longer available." The one
  residual obligation is trademark use of the VST name/logo, which is
  branding, not code licence.

This confirms decision 56 §10 rather than overturning it — the licence
argument really is dead, from a primary source, independently re-checked
here.

### The COM-style ABI, concretely — and a wrinkle the decision docs miss

VST3's core interfaces (`IAudioProcessor`, `IComponent`, `IEditController`,
`FUnknown`) are genuine COM: every interface carries a 16-byte `FUID`,
supports `queryInterface`/`addRef`/`release`, and is reference-counted
across module boundaries. Fetched from
`steinbergmedia/vst3_pluginterfaces`'s `ivstaudioprocessor.h`: activation
goes through `setupProcessing(ProcessSetup{processMode, symbolicSampleSize,
maxSamplesPerBlock, sampleRate})`, and each `process(ProcessData&)` call
carries `AudioBusBuffers` arrays plus parameter-change and event queues —
structurally similar in *content* to CLAP's `clap_process_t`, but reached
through `queryInterface` calls rather than a flat struct.

**What the decision docs did not check: Steinberg publishes a plain-C
version of this ABI, specifically for non-C++ hosts, and it has existed
since 2022.** [`steinbergmedia/vst3_c_api`](https://github.com/steinbergmedia/vst3_c_api)
is "automatically generated from the C++ API," and the [announcement
thread](https://forums.steinberg.net/t/new-vst-3-c-api-released/816413)
states the explicit purpose: "easily create bindings to other programming
languages, like Python, Rust and Kotlin." The header (`vst3_c_api.h`,
generated against SDK 3.8.1) is roughly 4,000 lines: 60+ interfaces
declared as C structs of function pointers (the vtable made explicit in C
rather than hidden in a C++ class), `SMTG_INLINE_UID` macros building the
16-byte `Steinberg_TUID` from four 32-bit words, and
`SMTG_STDMETHODCALLTYPE` conditionally expanding to `__stdcall` on Windows
and nothing elsewhere (a real, if small, platform-conditional detail a
`ctypes` binding has to get right per platform).

**Its licence, checked directly:** `LICENSE.txt` on `vst3_c_api` master is
**BSD-3-Clause**, copyright "(c) 2025, Steinberg Media Technologies GmbH" —
permissive, and *not* the GPLv3-or-Steinberg-proprietary dual licence the
2022 announcement thread describes; it has evidently been updated
alongside the SDK's own move to MIT (the repository still tracks the SDK,
per the 3.8.1 version embedded in the generated header).

**What this changes and what it doesn't.** This is a real, previously
unexamined path: a `ctypes` binding against `vst3_c_api.h` is not blocked by
C++ name-mangling or template instantiation the way binding the raw SDK
headers would be — it is C structs, all the way down, exactly the shape
`ctypes` is designed for. It does **not** remove the COM mechanics
themselves: a host built this way still has to implement `queryInterface`
by matching 16-byte UIDs, still has to manage reference counts correctly
(a leaked or premature `release` is a use-after-free, not a Python
exception), and still has to build the host-side callback interfaces
(`IComponentHandler`, `IHostApplication`, etc.) as C structs of function
pointers the plugin calls into — meaning **Python has to hand the plugin
real, ABI-stable function pointers**, which for `ctypes` means
`CFUNCTYPE`-wrapped Python callables kept alive for the object's whole
life (a well-known but fiddly `ctypes` pattern; it is exactly how
`comtypes` and `pywin32` implement real Windows COM from Python, so it is
proven to work, not speculative). Concretely: this is **still
substantially more work than CLAP** — CLAP has no `queryInterface`, no
UIDs, no refcounting on the audio path, one flat vtable per plugin instead
of a graph of them — but it is a *bounded* amount more, not an
in-principle wall. Estimate: what §1 scoped at two to three weeks for CLAP
would be five to eight weeks for VST3 via `vst3_c_api.h`, mostly the
`queryInterface`/refcounting/host-callback-object machinery, none of it
GPL-tainted.

**No evidence this path has been tried.** Searching PyPI, GitHub, and the
Steinberg forums turned up no Python binding built on `vst3_c_api.h`
specifically — the existing Python-reachable VST3 routes (`pedalboard`,
`DawDreamer`) both go through JUCE's C++ wrapper, not this C surface. This
is genuinely new ground, not a known-and-rejected option.

---

## 3. What has changed since October 2025

Checked directly rather than assumed current:

- **`pedalboard`** (Spotify): CLAP support is a standing, unresolved
  feature request — [issue
  #270](https://github.com/spotify/pedalboard/issues/270), open since
  November 2023, still open. `pedalboard` remains VST3 + Audio Unit only,
  still embeds JUCE, still GPL-3.0. No change.
- **No Python CLAP binding exists**, checked again now: nothing on PyPI,
  nothing found by GitHub code search for a `ctypes`/`cffi` CLAP host in
  Python. `python-clap` on PyPI is still the argparse-adjacent CLI package
  unrelated to the audio format (the decision doc's characterization
  holds; the package's own page did not load cleanly for this check, but
  nothing in its description or the broader search suggests an audio
  binding). This is the single fact this whole thread turns on, and it is
  still true a year later.
- **`clap-wrapper`** (`free-audio/clap-wrapper`) is active — a September
  2026 PR (#560) adds host/format identification so a wrapped plugin can
  tell which format it's being hosted as. This project wraps a CLAP
  plugin *as* VST3/AU/AAX for other hosts to load — the opposite direction
  from what note-color needs (we want to host a plugin, not present one),
  but it is evidence the CLAP ecosystem itself is maturing.
- **JUCE still has no first-party CLAP hosting.** The community
  `CLAPPluginFormat` project (`free-audio/clap-juce-extensions` and
  `jatinchowdhury18/juce_clap_hosting`) remains third-party and is
  explicitly described as "super-alpha." The JUCE team has acknowledged
  the request without committing to it. Since `pedalboard`/`DawDreamer`
  are JUCE-based, this is also why neither has picked up CLAP: the layer
  they'd need it from doesn't have it yet.
- **Carla** (`falkTX/Carla`), a real multi-format plugin host with
  experimental CLAP support in its source tree, is **GPL-2.0-or-later** —
  same licence problem as `pedalboard`/JUCE, and also architecturally a
  full DAW-shaped host (Qt frontend, JACK/native backends, LADSPA/DSSI/
  LV2/VST2/VST3/AU/CLAP) rather than an embeddable library, so it is not a
  "depend on it as a Python package" option even setting the licence
  aside. Worth naming as prior art for what a *complete* multi-format host
  looks like, and as a possible **out-of-process peer** (see §4) rather
  than an in-process dependency.
- **The genuinely new finding this round is `vst3_c_api.h`** (§2) — it
  predates October 2025 by three years but was not surfaced by either
  prior pass of this question, and its licence has itself moved to
  BSD-3-Clause in the same wave that made the main SDK MIT. This is the
  one finding here that could change the recommendation, and it is
  flagged as such below.

---

## 4. What `contract.py` would have to grow

Read against the file as it stands today (`src/notecolor/audio/graph/contract.py`,
not touched by this research):

- **Parameter delivery is block-rate; CLAP's is sample-accurate.**
  `ParamBlock` (decision 59 §3, #208's territory) delivers one value per
  parameter per block, with `advance()`'s one-pole smoothing filling the
  gap for our own modules. `clap_plugin_params.flush()` and the event
  lists in `clap_process_t` expect a per-sample-offset event stream — a
  plugin driving its own precise envelope from automation would not get
  that through today's `ParamBlock`, and #208's "offset queue drained at
  block start" extension (already scoped, per the contract's own
  docstring) is exactly what would need to exist before a plugin adapter
  could honestly claim sample-accurate automation. Nothing here is a
  surprise — the docstring already says this is deliberately deferred —
  but a plugin adapter is the first consumer that would actually need it,
  not a nice-to-have.
- **`PORT_EVENT` and `NoteContext` are a placeholder, not a note port.**
  `NoteContext` carries exactly one note (`note_id`, `pitch`, `velocity`,
  `frequency`, `gate`, `finished`) because our own modules are one voice
  per instance. CLAP's `note-ports` extension expects a *dialect*
  (CLAP-native, MIDI, MIDI-MPE, or MIDI2) and a stream of note on/off/
  choke/end plus note-expression events (per-voice pitch bend, pressure,
  brightness) addressed by `port, channel, key, note_id` with wildcard
  addressing. A hosted instrument plugin that wants MPE-style per-note
  expression has nowhere to put it in today's contract — this is a real
  gap, not a documentation gap, because `POLY_PER_NOTE` module semantics
  (one instance per voice, `new_instance()` cloning) do not obviously
  compose with a plugin that wants all its own polyphony handled inside
  one instance (which is how most instrument plugins actually work — one
  `clap_plugin_t` handling 16 voices itself, not 16 activated instances).
- **`latency_frames()` exists and is unused.** It is defined on `Module`
  and returns `0` by default, which is honest for our own modules
  (`descriptor().block_delay` is the guarantee that matters for the cycle
  rule) but there is no visible compensation logic anywhere consuming
  `latency_frames()` yet — a plugin adapter would be the first thing to
  actually report a nonzero value, and nothing downstream currently reads
  it. This needs building, not just wiring.
- **Plugin-side buffer requirements don't match our activation contract.**
  `Activation(sample_rate, max_block)` gives a module the *ceiling* it
  will ever be asked for. CLAP's `activate()` takes `(sample_rate,
  min_frames_count, max_frames_count)` — a *range* — because some plugins
  refuse to run below or above a declared block size. Our own engine
  always calls at a fixed block, so this has never mattered; a plugin
  adapter is the first caller that could get a legitimate "no" from
  `activate()`, which the contract has no vocabulary for today
  (`Activation.__post_init__` only validates positivity, not a plugin's
  own acceptable range).
- **State save/restore doesn't fit decision 69's format at all.**
  `graph_format.py`'s `[node.parameters]` sub-table is
  `{param_id: value}` — human-readable TOML, directly `ParamBlock.
  snapshot()`/`restore()`-shaped, chosen deliberately because that's what
  the contract already produces. CLAP's `state` extension is an **opaque
  byte stream** (`save`/`load` against a `clap_ostream`/`clap_istream`),
  explicitly not required to be equivalent to a plugin's declared
  parameters — "if a parameter value changes... the state is dirty" is
  stated as *implicit*, meaning some plugins keep meaningful state outside
  `params` entirely (a sample slot, an internal mode not exposed as a
  knob). A patch file that only stores `[node.parameters]` would silently
  lose that state on save/load for such a plugin. This is a structural
  choice decision 69 will have to make explicitly: either the format grows
  an opaque `state_blob` field (base64 in TOML, ugly but honest) alongside
  the readable parameter table, or hosted plugins are restricted to ones
  whose full state genuinely round-trips through declared parameters —
  which cannot be verified in general, only per plugin.
- **Plugin GUIs have no story, and the contract doesn't pretend otherwise.**
  This is real, and the issue is right to name it as a wall rather than a
  gap: `clap_plugin_gui` is either an embedded native window (Win32
  `HWND`/Cocoa `NSView`/X11 `XEmbed`; Wayland floating-only, no embedding
  at all) or a plugin-owned floating window. Embedding means Qt/PySide
  would have to host a *foreign* native window handle inside its own
  widget tree, per platform, with per-platform quirks (Wayland's
  no-embedding restriction is not a note-color problem to solve, it's a
  platform limitation every host hits) — genuinely nontrivial GUI work
  orthogonal to the audio graph entirely. The cheaper alternative the
  issue itself names — drive every parameter as a generic knob built from
  `clap_plugin_params.get_info()`/`value_to_text()` — is real, is
  consistent with `contract.py`'s existing `ParamSpec` shape (a plugin's
  parameter becomes one more `ParamSpec` on the canvas), and costs the
  user exactly what every generic-knob host costs its users: no custom
  visualizers, no manufacturer-branded UI, a knob that says "2.3 kHz"
  instead of a filter curve. This is the right default for a first cut
  regardless of which format is chosen.

---

## 5. The honest recommendation

**CLAP is still the right target format, and the reason hasn't changed
since decision 56 §10: it's the only one where a Python host is a bounded,
nameable amount of work rather than an open-ended one.** VST3's `vst3_c_api.h`
discovery in §2 is real and worth recording, but it does not overturn
this — it makes VST3 "also feasible, at roughly double the effort and with
COM mechanics that have more ways to get subtly wrong from `ctypes`,"
not "cheaper than CLAP." If Steinberg's plain-C surface had existed as the
*only* viable route to either format, this recommendation would flip; it
doesn't, because CLAP's ABI was designed for this use case from the start
and VST3's C surface is a bolt-on, however well done.

**Sizing, stated plainly because that is what this ticket asked for:**

- A proof-of-concept CLAP host — one well-behaved effect plugin, no GUI,
  block-rate parameters only, running inside a headless test harness with
  no audio device — is **two to three weeks** for someone who already
  knows this codebase, per §1's breakdown.
- Making that real inside the Synth View — wired through
  `patch_bridge.py`, surviving a save/load round-trip, with generic-knob
  parameter UI on the canvas — is **another one to two months**: the
  `ParamBlock`/#208 sample-accurate delivery, the note-port dialect
  question, `latency_frames()` actually feeding compensation, and decision
  69's format growing a state-blob path are none of them small on their
  own, and #202's own experience (`np.take`'s hidden allocation, found
  only by a dedicated memory test) says this project's own "no allocation
  in the callback" rule will find real violations inside whatever `ctypes`
  marshalling code gets written, the same way it found one in the
  oscillator.
- Instrument plugins (as opposed to effects) are harder again — the
  poly-voice mismatch named in §4 (one `clap_plugin_t` handling all its own
  voices vs. our `POLY_PER_NOTE`/`new_instance()` model) is a real design
  question, not an implementation detail, and is worth its own ticket
  before writing code.
- **Total shape: a real quarter, not a sprint, before a plugin can be
  patched into the Synth View the way the owner asked for** — consistent
  with decision 56 §10 calling this "a real project, not a checkbox," now
  with a number attached.

**Given the owner's standing instruction — "this should be the real deal,"
"I do not care how long this takes" — the honest answer is: do it, in
CLAP, staged exactly as §1's estimate breaks it down (proof-of-concept
first, Synth View wiring second, instruments third), and treat the GIL
contention question in §1 as the one open technical risk worth a small
timed spike before committing further, since it is the one thing in this
document that could not be checked from a header and could change the
shape of the whole approach (an out-of-process host, paying Ardour's own
named cost of context-switch latency, becomes the honest answer if in-
process GIL contention turns out to be real at note-color's block size —
worth noting that Ardour's numbers are for a 128-track DAW session and
this project's Synth View patches a handful of instances, so the
same tradeoff may land very differently here).** The alternative — doing
this "much later" — costs nothing except the door decision 56/59 already
built (the contract is genuinely plugin-ABI-shaped, per §4's specific
findings of what's *missing* rather than what's *wrong*) staying open
rather than being walked through.

---

## What I could not verify

- **Whether `ctypes`'s GIL release during a native call actually keeps a
  CLAP plugin's `process()` off the GIL for its whole duration in
  practice**, versus some subtlety in how `sounddevice`'s own callback
  thread interacts with `ctypes` call boundaries. This is stated as CPython
  behaviour from documentation, not measured in this project's own
  callback — §1 and §5 flag it explicitly as the one risk worth a timed
  spike rather than a documented fact.
- **Whether `vst3_c_api.h`'s BSD-3-Clause licence has actually been
  exercised by anyone building a real host or plugin from Python or Rust.**
  No such project was found. The repository's existence and licence are
  verified directly; its practical viability is not — nobody has proven
  the path works, only that nothing forbids trying it.
- **`python-clap`'s actual current PyPI page** did not load cleanly during
  this research; its description could not be re-confirmed as an
  argparse-only package from the PyPI page itself this round (the prior
  decision doc's characterization is repeated here, not independently
  re-verified from that specific page — GitHub/PyPI search for any CLAP
  audio binding under any name still turned up nothing, which is the
  fact that actually matters).
- **Carla's exact CLAP support maturity.** Search results describe it as
  present in the source tree and "experimental/work-in-progress"; this was
  not independently confirmed against Carla's own changelog or a release
  note.
- **The precise cost of an out-of-process CLAP host at note-color's scale**
  (a handful of plugin instances, not Ardour's 128-track/384-plugin
  reference case). Ardour's own published numbers
  (https://ardour.org/plugins-in-process.html) are for a load this project
  will likely never reach; no equivalent measurement exists at note-color's
  actual scale, so the out-of-process option's real cost here is
  reasoned-about, not measured.

---

## Sources

- Contract and decisions (this repo): `src/notecolor/audio/graph/contract.py`;
  [decision 56](../decisions/56-the-synth-view-becomes-a-real-cable-patched-modular-synth-issue-200-round-2.md)
  §10; [decision 59](../decisions/59-the-graph-module-contract-and-plugin-hosting-moved-into-the-design-issue-202.md);
  [decision 47](../decisions/47-mit-and-the-rules-for-third-party-models-and-weights-issue-139.md);
  [decision 69](../decisions/69-patch-persistence-for-an-arbitrary-graph-and-migrating-old-patches-issue-209.md).
- CLAP — [free-audio/clap](https://github.com/free-audio/clap) headers:
  `entry.h`, `process.h`, `host.h`, `events.h`,
  `ext/params.h`, `ext/gui.h`, `ext/state.h`, `ext/audio-ports.h`,
  `ext/note-ports.h`, `ext/latency.h` (all fetched from `main`);
  [clap-host](https://github.com/free-audio/clap-host) (MIT, reference
  host); [clap-wrapper PR #560](https://github.com/free-audio/clap-wrapper/pull/560).
- VST3 licence — `steinbergmedia/vst3sdk`'s `LICENSE.txt` (MIT, "Copyright
  (c) 2026, Steinberg Media Technologies GmbH"); [VST 3 Developer Portal —
  Licensing](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License.html).
- VST3 C API — [steinbergmedia/vst3_c_api](https://github.com/steinbergmedia/vst3_c_api),
  its `LICENSE.txt` (BSD-3-Clause, "(c) 2025, Steinberg Media Technologies
  GmbH"), its `vst3_c_api.h` header, and the [announcement
  thread](https://forums.steinberg.net/t/new-vst-3-c-api-released/816413).
- VST3 COM interfaces — `steinbergmedia/vst3_pluginterfaces`'s
  `vst/ivstaudioprocessor.h`.
- Python VST3/CLAP hosting landscape — [spotify/pedalboard](https://github.com/spotify/pedalboard)
  and its [issue #270](https://github.com/spotify/pedalboard/issues/270)
  (CLAP support request, open since 2023); [DBraun/DawDreamer](https://github.com/DBraun/DawDreamer);
  [falkTX/Carla](https://github.com/falkTX/Carla) (GPL-2.0-or-later).
- JUCE CLAP hosting status — [free-audio/clap-juce-extensions](https://github.com/free-audio/clap-juce-extensions);
  [jatinchowdhury18/juce_clap_hosting](https://github.com/jatinchowdhury18/juce_clap_hosting);
  [JUCE forum — FR: Support CLAP for plugins](https://forum.juce.com/t/fr-support-clap-for-plugins-host-client/51860).
  VST3 Rust bindings (permissive, for comparison) — [coupler-rs/vst3-rs](https://github.com/coupler-rs/vst3-rs)
  (MIT/Apache-2.0) vs. [RustAudio/vst3-sys](https://github.com/RustAudio/vst3-sys) (GPLv3).
- Out-of-process hosting tradeoffs — [Ardour: why no plugin crash
  protection](https://ardour.org/plugins-in-process.html); Bitwig's
  [Plug-in Hosting & Crash Protection](https://www.bitwig.com/learnings/plug-in-hosting-crash-protection-in-bitwig-studio-20/)
  and its [VST plug-in handling & options
  userguide](https://www.bitwig.com/userguide/latest/vst_plug-in_handling_and_options/).
