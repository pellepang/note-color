# SF2 soundfont playback via FluidSynth: binding, packaging, mixing, and assets

Research for issue [#102](https://github.com/pellepang/note-color/issues/102),
a child of the sound-engine map [#99](https://github.com/pellepang/note-color/issues/99),
which accepts an SF2 player as the third engine alongside the existing
subtractive synth (`playback.py`) and a not-yet-built hand-rolled sampler,
"shipped as an optional dependency that degrades to *unavailable* when the
system library is missing."

Decision [#32](https://github.com/pellepang/note-color/issues/32) had earlier
deferred FluidSynth on wheel/dependency-risk grounds, on the strength of
[#28](https://github.com/pellepang/note-color/issues/28)'s desk research.
That research was desk-only — no binding was installed, no measurement taken.
This doc replaces the desk estimate with **measured numbers from a real
`libfluidsynth` 2.6.0 + `pyfluidsynth` 1.4.0 setup on this machine**, plus a
head-to-head against the leading alternative (`tinysoundfont`, built from
source on this repo's own Python 3.14).

## Question

1. What is the current state of `pyfluidsynth` — maintenance, wheels, Python
   version support — and is the system `libfluidsynth` a hard requirement on
   Linux/macOS/Windows?
2. What are the alternatives (`tinysoundfont`, `sf2utils`, pure-Python SF2
   parsing), and do any of them remove the system-library requirement?
3. What is the API shape for note-on/note-off/program-change, and can it be
   driven per-event from Python at the latency this map needs?
4. How would its audio output be mixed with note-color's own voices — does
   FluidSynth own its own output stream, or can it render into a buffer we mix?
5. Where do soundfont assets come from, how large is a usable GM bank, and what
   are the licences of the commonly-bundled ones?
6. What does "degrades to unavailable" look like *in code*, following the
   `librosa`/`music21` precedent this repo already uses?

## Answer

### 1. `pyfluidsynth` is alive, pure-Python, and needs the system library

**Maintenance and packaging (PyPI JSON API, read directly):**

| | value |
|---|---|
| Latest version | `pyfluidsynth` 1.4.0 |
| Uploaded | 2026-05-30 (three months before this research) |
| Distribution | `pyfluidsynth-1.4.0-py3-none-any.whl` — a **pure-Python wheel**, no compiled extension |
| `requires-python` | `>=3.10` |
| Runtime deps | `numpy` (already a note-color dependency) |
| Upstream | https://github.com/nwhitehead/pyfluidsynth |

Upstream CI (`.github/workflows/ci.yml`) runs the test suite on **Python
3.14, 3.14t and 3.15t** across `ubuntu-latest`, `ubuntu-24.04-arm`,
`macos-latest` (arm), `macos-26-intel` and `windows-latest`, with extra
Ubuntu jobs on 3.12 and 3.10. This is a maintained project on current
Pythons, not a stale one — a materially different picture from #28's
"reintroduces wheel risk" framing, because **there is no wheel to risk**: the
binding is ctypes, so `pip install pyfluidsynth` never compiles anything.

**The system library is a hard requirement, and it is checked at import
time.** `fluidsynth.py` calls `find_libfluidsynth()` at module scope (line 84)
and immediately `CDLL(lib)`; the helper tries `find_library()` over
`fluidsynth fluidsynth-3 libfluidsynth libfluidsynth-3 libfluidsynth-2
libfluidsynth-1`, then a `HOMEBREW_PREFIX` fallback for Apple-silicon
non-Homebrew Pythons, and otherwise:

```python
raise ImportError("Couldn't find the FluidSynth library.")
```

Verified by running the import with and without the library on `LD_LIBRARY_PATH`:
without it, `import fluidsynth` raises exactly that `ImportError`, at import,
with nothing half-initialised. That is the single most useful fact for
question 6 — see §6.

Install commands per OS, taken from upstream CI (i.e. what the maintainer
actually tests against), not from a blog:

| OS | command |
|---|---|
| Linux (Debian/Ubuntu) | `sudo apt-get install fluidsynth` |
| Linux (Arch, this machine) | `pacman -S fluidsynth` (2.6.0-1, LGPL-2.1-or-later) |
| macOS | `brew install fluid-synth` |
| Windows | `choco install fluidsynth` (or the official release ZIP on the FluidSynth releases page) |

Note that `pip install fluidsynth` (no `py`) installs an **unrelated, dead
2012 package** ("Fluidsynth bindings", 0.2, last uploaded 2012-02-28, single
sdist) — worth a line in any install doc, since it is an easy misread of the
import name (`import fluidsynth`) back into a package name.

**Licence discrepancy, flagged rather than resolved.** `pyfluidsynth`'s
`pyproject.toml` declares `license = "MIT"` (and PyPI shows MIT), but the
`LICENSE` file shipped in the same wheel is **GNU LGPL-2.1**, and the module
docstring says "Released under the LGPL". FluidSynth itself is LGPL-2.1-or-later.
Practically this is low-stakes for note-color — the binding links
`libfluidsynth` dynamically via ctypes (the replaceable-library condition LGPL
§6 wants is satisfied by construction), note-color is not statically linking
anything, and this repo currently ships **no `LICENSE` file at all**, so there
is no declared licence for an LGPL obligation to conflict with. It is still
worth knowing that the dependency's own licence metadata contradicts its
licence file.

### 2. Alternatives: `tinysoundfont` is the real one; `sf2utils` is a parser, not a player

**`tinysoundfont` (a.k.a. tinysoundfont-pybind)** — same author as
`pyfluidsynth` (Nathan Whitehead), pybind11 bindings around
[TinySoundFont](https://github.com/schellingb/TinySoundFont), **MIT**,
self-contained: **no system library at all**. `pyfluidsynth`'s own README
points at it for users who "don't need all the features of FluidSynth". It
reads `.sf2`, `.sf3` and `.sfo`.

Its packaging is the mirror image of `pyfluidsynth`'s: binary wheels (so no
system library), but **the newest release is 0.3.7, uploaded 2025-06-03, with
wheels only up to cp312** — no cp313/cp314 wheels exist. On this repo's own
interpreter (Python 3.14.6) `pip install tinysoundfont` therefore falls back
to the sdist and **compiles from source**. That was tested here and *does*
succeed (scikit-build-core + pybind11 + a C++ toolchain, plus a from-source
build of its hard `pyaudio` dependency, ~2 minutes) — but "needs a C++
toolchain on the current Python" is precisely the failure mode this repo
avoided `aubio` for, just relocated. It also hard-depends on `pyaudio`
(unconditional `Requires-Dist`), a second audio I/O stack alongside the
`sounddevice`/PortAudio one note-color already has.

Measured head-to-head, same 31MB GeneralUser GS bank, same 512-frame block at
44100 Hz, this machine:

| | `pyfluidsynth` 1.4.0 + libfluidsynth 2.6.0 | `tinysoundfont` 0.3.7 |
|---|---|---|
| Module import | 167 ms (ctypes prototypes), +11 MB RSS | 49 ms |
| `sfload` (31.3 MB sf2) | 68 ms, +38 MB RSS | 93 ms, **+91 MB RSS** |
| Render, 0 voices | 0.094 ms/block | 0.002 ms/block |
| Render, 8 voices | 0.211 ms/block | 0.040 ms/block |
| Render, 32 voices | 0.538 ms/block | 0.161 ms/block |
| Render, 64 voices | 0.951 ms/block (8.2% of the 11.61 ms budget) | 0.396 ms/block (3.4%) |
| Output format | interleaved **int16** stereo (NumPy array) | interleaved **float32** stereo (memoryview) |
| Effects | reverb + chorus built in | none |
| System library | **required** | none |
| Licence | LGPL-2.1 lib; binding metadata conflicts (§1) | MIT throughout |

TinySoundFont is genuinely cheaper per block (it is a much simpler engine —
no reverb/chorus, no dynamic voice-quality scaling) and hands back float32,
which is what note-color's mixer wants. It costs ~2.4x the RAM per bank
(it expands samples on load) and, today, a source build on Python 3.13+.

**`sf2utils` (1.0.0, 2024-01-11) is not a candidate for playback.** It is a
pure-Python SF2 *parser* — it reads the RIFF structure, presets, instruments,
sample headers and generator/modulator lists, and does no synthesis at all.
It is also **GPLv3+**, the only copyleft-with-teeth option in this survey.
It would only ever be relevant as a way to feed the map's *hand-rolled
sampler* engine with SF2-extracted samples, i.e. re-implementing SF2 playback
(loop points, key/velocity zones, envelopes, filters, modulators) in NumPy —
which is a whole synthesizer, not a binding, and drags GPLv3 into a repo that
currently has no licence at all.

Pure-Python SF2 rendering with no parser library at all is not seriously
worth costing: the SF2 spec's generator/modulator model is the bulk of what
FluidSynth *is*.

### 3. API shape: MIDI-native, per-event, no scheduler required

`pyfluidsynth`'s `Synth` class is a thin OO wrapper over the C API and is
already the exact event vocabulary map #99 committed to ("MIDI-shaped from day
one … note-on/note-off/velocity/channel as first-class concepts"):

```python
fs   = fluidsynth.Synth(gain=0.2, samplerate=44100.0, **settings)
sfid = fs.sfload("/path/to/bank.sf2")        # -> int soundfont id
fs.program_select(chan, sfid, bank, preset)  # or program_change(chan, prg)
fs.noteon(chan, key, vel)                    # key/vel are MIDI 0..127
fs.noteoff(chan, key)
fs.cc(chan, ctrl, val)                       # 7 volume, 10 pan, 64 sustain, 91 reverb, 93 chorus
fs.pitch_bend(chan, val)                     # +/-8192
fs.all_notes_off(chan) / fs.all_sounds_off(chan)
fs.get_active_voice_count()
fs.sfpreset_name(sfid, bank, preset)         # -> str, for a patch browser
```

Channels default to 256 (`synth.midi-channels`), polyphony to 256
(`synth.polyphony`, range 1–65535), sample rate to 44100.0 — all settable at
construction via `**kwargs` and readable back with `get_setting()`.

**Per-event latency is bounded by the render block, not by the binding.**
Measured: after `noteon()`, the *very next* `get_samples(512)` call returns
audio whose first sample is already non-zero (first non-zero frame index = 0).
So a note triggered from the render/UI thread is audible at the start of the
next audio callback — the same one-block bound (`PLAYBACK_BLOCK_SIZE` /
`PLAYBACK_SAMPLE_RATE` ≈ 11.6 ms) that `playback.LiveScheduler`'s docstring
already documents for the existing oscillator voices. No new latency tier.

**Cross-thread event delivery is safe.** `synth.threadsafe-api` defaults to 1
("Controls whether the synth's public API is protected by a mutex or not",
per the official settings reference) and was confirmed as `1` at runtime here.
Stress-tested directly: ~900 `noteon`/`noteoff` pairs fired from a control
thread while a second thread called `get_samples(512)` in a tight loop —
no errors, no crashes. That is exactly the shape of note-color's existing
`LiveScheduler` (caller thread appends, audio callback pulls), so the
existing threading model transfers unchanged. If the map ever wants the
last few percent of CPU back, `synth.threadsafe-api=0` is available but must
not be set while events cross threads.

There is also a `Sequencer` class (`note()`, `note_on()`, `timer()`,
`process()`) wrapping FluidSynth's own scheduler. **It should not be used
here**: `playback.py`'s existing decision (map #24, mirrored in
`docs/DECISIONS.md`) is that the *caller's* already-paced loop owns timing so
audio cannot drift against the visuals. Adding FluidSynth's independent clock
would reintroduce exactly the second-clock problem that decision rejected.

### 4. Mixing: FluidSynth renders into a buffer on demand — it does not have to own a stream

This is the question with the most load-bearing answer, and it is a clean yes.

`Synth.start(driver=...)` opens FluidSynth's *own* audio driver on a
background thread (ALSA/PulseAudio/JACK/CoreAudio/WASAPI/SDL3). **Do not call
it.** `pyfluidsynth`'s own docstring is explicit: *"If you don't call this
function, use `get_samples()` to generate samples."* Without `start()`,
FluidSynth is a pure pull-model renderer with no device, no driver thread, and
no device conflict with the `sounddevice.InputStream` the live mic path
already holds.

`Synth.get_samples(len=1024)` calls `fluid_synth_write_s16` and returns a
NumPy array of **interleaved int16 stereo** samples of length `2 * len`
(confirmed: `dtype=int16, shape=(1024,)` for `len=512`). It fits directly
inside `playback.LiveScheduler`'s existing `OutputStream` callback:

```python
# inside the existing callback, alongside the oscillator voices
fluid_block = synth.get_samples(frames)                       # int16, 2*frames
mixed[:] += fluid_block.reshape(frames, 2).astype(np.float32) / 32768.0
```

Three facts make this a genuinely comfortable fit:

- **The sample rates already match.** `config.PLAYBACK_SAMPLE_RATE = 44100`
  and `config.PLAYBACK_BLOCK_SIZE = 512` are *exactly* FluidSynth's own
  defaults (`synth.sample-rate` 44100.0; `audio.period-size` 64/512). No
  resampling, no block-size adaptation. (The live *capture* path's 22050 Hz
  is untouched — that mismatch is map #99's separate open question and this
  engine does not add to it.)
- **The CPU cost is small.** 64 simultaneous voices with the 148 MB FluidR3_GM
  bank cost **0.951 ms mean / 1.838 ms p95 / 3.408 ms max** per 512-frame
  block, i.e. 8.2% of the 11.61 ms real-time budget on this machine; 32 voices
  cost 4.6%; 8 voices 1.8%. Turning reverb/chorus off and capping polyphony at
  64 measured *no* improvement here (0.686 ms vs 0.662 ms at 64 voices) — the
  #28 desk research's "disable reverb/chorus, cap polyphony" tuning advice is
  Pi-tier advice, and map #99 has explicitly dropped the Pi constraint for
  this subsystem. Keep the effects on.
- **Allocation in the audio callback is avoidable but barely matters.**
  `get_samples()` allocates a fresh `create_string_buffer` per call. Calling
  the underlying `fluidsynth.fluid_synth_write_s16(fs.synth, N, buf, 0, 2,
  buf, 1, 2)` into a preallocated ctypes buffer with a `np.frombuffer` view
  measured 0.154 ms vs 0.162 ms per block — a ~5% saving, worth doing in the
  callback purely to keep allocations out of the real-time path, not for speed.

**One real TUI-specific hazard, found by running it.** Constructing a `Synth`
(specifically `new_fluid_settings()`, which enumerates audio drivers) writes
**16 lines of ALSA/SDL warnings straight to stderr** on this machine:

```
ALSA lib pcm.c:2722:(snd_pcm_open_noupdate) [error.pcm] Unknown PCM cards.pcm.rear
...
fluidsynth: warning: SDL3 not initialized, SDL3 audio driver won't be usable.
```

In a raw-ANSI full-screen view (`tab`, the score editor, the menu) that would
scribble over the rendered frame. Runtime warnings happen too — exceeding
`synth.polyphony` emits `fluidsynth: warning: Failed to allocate a synthesis
process. (chan=…,key=…)` per stolen voice. `pyfluidsynth` 1.4.0 does **not**
bind `fluid_set_log_function`, and the ALSA lines come from libasound rather
than FluidSynth anyway, so no library-level hook can suppress them. The
mitigation that was verified working here is an OS-level fd-2 redirect around
construction (and ideally held for the synth's whole life):

```python
@contextlib.contextmanager
def _silence_stderr():
    old, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 2); yield
    finally:
        os.dup2(old, 2); os.close(null); os.close(old)
```

Confirmed: with the redirect, construction and `sfload` are silent and preset
lookup still works. Also worth knowing for the polyphony warning:
**`synth.polyphony` counts voices, not notes** — a stereo or layered preset
spends 2+ voices per key, so a 64-voice cap was exceeded by 60 held keys in
testing.

### 5. Soundfont assets: three usable size tiers, all MIT-or-permissive

Measured on this machine (load time and resident memory are what matter — the
whole bank's sample data goes into RAM; nothing is mmapped or streamed):

| Bank | File size | `sfload` | RSS added | bank-0 presets |
|---|---|---|---|---|
| FluidR3_GS.sf2 (GS variation supplement, not standalone) | 3.2 MB | 3 ms | ~0 MB | 0 |
| GeneralUser GS v1.471 | 31.3 MB | 68 ms | 38 MB | 128 |
| FluidR3_GM.sf2 | 148.4 MB | 122 ms | 111–147 MB | 128 (+ bank 128 "Standard" drum kit) |

Load times are all well under a perceptible pause, so bank choice is a
memory-and-download question, not a latency one.

Where they come from, and their licences:

- **FluidR3_GM** (Frank Wen, 2000–2008) — **MIT**, with the shipped
  `COPYING`/`README` acknowledgement block required in derivative works.
  Packaged nearly everywhere: Arch `soundfont-fluid` (125 MB download,
  `custom:MIT`), Debian/Ubuntu `fluid-soundfont-gm`. The de-facto default GM
  bank on Linux and what `fluidsynth`-using apps usually find already present.
- **MuseScore's banks** (in the MuseScore repo, `share/sound/`) —
  `FluidR3Mono_GM.sf3` (23.7 MB) and `MS Basic.sf3` (51.3 MB), both **MIT**,
  both descended from FluidR3 with the same attribution requirement ("The
  acknowledgements and copyright notices above must be included in any
  derivative work"). `.sf3` is SF2 with Ogg-Vorbis-compressed samples —
  roughly a 6x file-size saving. **FluidSynth reads `.sf3` only when built
  against libsndfile**; the Arch 2.6.0 build used here links
  `libsndfile.so.1`, `libvorbis`, `libogg` and carries Ogg-Vorbis SF3 strings,
  so it does. That is a build-option dependency, not a guarantee, so an
  `.sf3` bank must not be the *only* supported format.
- **GeneralUser GS** (S. Christian Collins) — current v2.0.1 under
  "License v2.0": *"You may use GeneralUser GS without restriction for your
  own music creation, private or commercial … Please feel free to use it in
  your software projects, and to modify the SoundFont bank or its packaging."*
  Not an OSI licence, but explicitly permissive about bundling. Its own
  licence text carries an honest caveat that some samples' provenance is not
  100% certain — relevant only if note-color were ever commercially
  distributed.
- **freepats-general-midi** (Arch, 255 MB download) — **GPL3 / CCPL**. Free
  as in freedom but the largest and the only copyleft bank here; no reason to
  prefer it over FluidR3.

**Recommendation on assets: bundle nothing.** A 23–148 MB binary blob in a git
repo whose largest current asset is a `.py` file is a poor trade, and this
project already gitignores generated artifacts on principle. Instead: search
the standard system locations (`/usr/share/soundfonts/`,
`/usr/share/sounds/sf2/`, `~/.local/share/soundfonts/`, and the Homebrew
prefix's own `share/soundfonts` on macOS -- the last unverified here) plus an
explicit
`[preferences].soundfont_path` in `config.toml`, and report "no soundfont
found" the same way a missing library is reported. On this machine, installing
`fluidsynth`'s companion `soundfont-fluid` package puts FluidR3_GM at
`/usr/share/soundfonts/FluidR3_GM.sf2` — i.e. the same one install step that
satisfies the library usually satisfies the asset too.

### 6. "Degrades to unavailable", concretely

The existing precedent (`librosa` in `batch_transcribe.py`/
`rhythm_reanalysis.py`, `music21` in `score_writer.py`/`score_editor_state.py`)
is two rules: **(a)** the third-party import lives at module top level in
exactly the modules permitted to have it, and **(b)** callers import *those
modules* locally, inside the function that needs them, so the cost is paid
only on the feature's own code path. Note what the precedent does **not**
have: any `try/except ImportError`. `librosa`/`music21` are declared in the
`[project.optional-dependencies] batch` extra and are simply required for
those features; missing them is a crash, not a degradation.

SF2 needs one step more, because the map's contract is *degrade*, not
*require* — and `pyfluidsynth`'s import-time `ImportError` (§1) is what makes
that step cheap. Both failure modes — Python package absent, **and** system
library absent — surface as the same exception, at import, before any state
exists. A third mode (library found but unloadable: ABI mismatch, wrong
architecture) surfaces from `CDLL` as `OSError`, so catch both.

The shape that follows this repo's conventions:

```python
# sf2_playback.py -- the ONLY module permitted to import fluidsynth,
# mirroring batch_transcribe.py (librosa) and score_writer.py (music21).
import fluidsynth          # top-level here, nowhere else

# ... SF2Engine wrapping Synth: load(), note_on(), note_off(), render(frames)
```

```python
# the caller (voice manager / synth tool), never at module scope:
def sf2_available() -> tuple[bool, str]:
    """(usable?, human-readable reason) -- safe to call from a render loop."""
    try:
        import sf2_playback           # noqa: F401  (pulls in fluidsynth)
    except ImportError as exc:        # package OR libfluidsynth missing
        return False, f"SF2 playback unavailable: {exc}"
    except OSError as exc:            # library present but unloadable
        return False, f"SF2 playback unavailable: {exc}"
    return True, ""
```

with, in `pyproject.toml`, a **third** extra alongside `batch` (deliberately
not folded into it — `batch` is offline analysis, this is playback, and they
have disjoint audiences):

```toml
sf2 = ["pyfluidsynth>=1.4"]   # sf2_playback.py only; also needs the system
                              # libfluidsynth (apt/brew/pacman/choco install)
```

Two details worth getting right in the implementation ticket:

- **Probe once and cache the result on the session/engine object**, not per
  frame — the ~167 ms import and the ALSA driver enumeration should happen at
  most once per process, and the fd-2 redirect of §4 must wrap it.
- **Report the reason, do not just hide the engine.** "SF2 unavailable
  (install `fluidsynth`)" in the patch browser is far better UX than an SF2
  entry that silently is not there, and it costs one string. This mirrors how
  `M`'s loopback switch reports a failed `pactl` inline in the status line
  rather than crashing or vanishing.

## Recommendation

**Adopt `pyfluidsynth` + system `libfluidsynth`, pull-model only
(`get_samples()`, never `start()`), mixed into the existing
`playback.LiveScheduler` callback, behind an `sf2` extra and a cached
availability probe. Bundle no soundfont; discover one.**

The reasoning, against what #32 actually decided:

1. #32 deferred FluidSynth on *wheel/dependency risk* grounds. Measured, that
   risk is not where #28 thought it was: `pyfluidsynth` is a pure-Python
   ctypes wheel that installs anywhere `pip` runs and is CI-tested on
   Python 3.14/3.15. The real cost is a **system package install**, one
   command per platform, which the "degrades to unavailable" contract already
   accounts for by design.
2. It is a comfortable fit for the existing audio architecture — same 44100/512
   as `config.PLAYBACK_*`, one-block onset latency identical to today's
   oscillator voices, a thread-safe API matching the existing
   caller-appends/callback-pulls shape, and no second output device.
3. The measured CPU cost (8.2% of the block budget at 64 voices, with reverb
   and chorus on) is a non-issue on the laptop-class hardware map #99 scoped
   this subsystem to.

`tinysoundfont` is the runner-up and is *close*: MIT throughout, no system
library, float32 output, and measurably cheaper per block. It loses on
packaging today — no wheels past cp312 means a from-source C++ build on this
repo's own Python 3.14, plus a hard `pyaudio` dependency duplicating the
`sounddevice` stack — and on RAM (~2.4x per bank). It is worth **re-checking
at implementation time**: if cp313/cp314 wheels have shipped by then, the
trade flips towards it hard enough to be worth a second look, because "no
system library at all" beats any amount of graceful degradation. `sf2utils` is
not a candidate (parser only, GPLv3+).

## What's still open (out of this ticket's scope)

1. **No macOS or Windows verification.** Everything measured here is Linux
   (Arch, `libfluidsynth` 2.6.0). The cross-platform claims rest on upstream
   CI covering those platforms, not on this session running there. In
   particular the `HOMEBREW_PREFIX` fallback path and Windows' `add_dll_directory`
   handling are read-in-source, not exercised.
2. **Coexistence with the live capture stream is untested** — the map already
   lists this as open. Nothing here opened a real `OutputStream` at all
   (rendering was measured by pulling blocks directly), so "FluidSynth's
   blocks mixed into a live `OutputStream` while `AudioCapture`'s
   `InputStream` is open" remains unproven in practice, even though the
   pull-model design removes the obvious device conflict.
3. **Voice-stealing/polyphony policy under the voice manager.** FluidSynth
   does its own voice allocation and stealing inside `synth.polyphony`,
   independently of whatever the map's voice manager does for the other two
   engines. Reconciling "one voice manager" with an engine that manages its
   own voices is a design question for the voice-manager ticket, not a
   packaging one.
4. **Whether the SF2 engine should get its own MIDI channels or share
   note-color's own note-identity model.** `program_select(chan, ...)` is
   per-channel, so multi-timbral use (a drum kit and a piano at once) is
   free — but only if the voice manager's event model carries channel through,
   which map #99 says it must ("velocity is the tell").
5. **SF3 support cannot be assumed.** It depends on how the user's
   `libfluidsynth` was built (libsndfile at build time). A bank-loading path
   should handle `sfload` returning `-1` (`FLUID_FAILED`) gracefully rather
   than assuming a readable file loads.

## Sources

Primary sources only; each was read or executed directly in this session.

- `pyfluidsynth` 1.4.0 wheel contents — `fluidsynth.py` (1395 lines) and the
  shipped `LICENSE`, downloaded from PyPI and read directly (not the README's
  description of them). `find_libfluidsynth()` at lines 55-84, `Synth` at
  764+, `get_samples()`/`fluid_synth_write_s16_stereo()` at 750-760/1112-1120,
  `Sequencer` at 1314+.
- PyPI JSON API (`https://pypi.org/pypi/{pyfluidsynth,tinysoundfont,sf2utils,fluidsynth}/json`)
  — versions, upload dates, wheel/sdist file lists, licence metadata,
  `requires-python`.
- `https://raw.githubusercontent.com/nwhitehead/pyfluidsynth/master/.github/workflows/ci.yml`
  — tested OS/Python matrix and per-OS `libfluidsynth` install commands.
- `https://raw.githubusercontent.com/nwhitehead/pyfluidsynth/master/pyproject.toml`
  and `.../LICENSE` — the MIT-vs-LGPL metadata discrepancy.
- FluidSynth official settings reference, `https://www.fluidsynth.org/api/fluidsettings.xml`
  — `synth.threadsafe-api` (default 1), `synth.polyphony` (256, 1–65535),
  `synth.sample-rate` (44100.0), `synth.reverb.active`/`synth.chorus.active`
  (both 1), `audio.period-size` (64 non-Windows).
- Direct measurement on this machine, Arch `fluidsynth` 2.6.0-1 (package
  extracted locally, `LD_LIBRARY_PATH`-loaded; nothing installed system-wide)
  against Python 3.14.6 + `pyfluidsynth` 1.4.0: import cost, `sfload` time and
  RSS for three banks, per-block render cost at 0/1/4/8/16/32/64 voices,
  note-on-to-first-sample latency, cross-thread event stress test,
  preallocated-buffer vs `get_samples()` comparison, stderr-spew reproduction
  and fd-2-redirect mitigation, and the missing-library `ImportError`.
- `tinysoundfont` 0.3.7 sdist — `src/tinysoundfont/synth.py` (`generate()`
  float32 contract at 399-431), `pyproject.toml` (`dependencies = ["pyaudio"]`,
  scikit-build-core/pybind11 build), `NOTICE`/`LICENSE` (MIT); built from
  source and benchmarked on Python 3.14.6 in this session.
- MuseScore repository contents API, `share/sound/` — `FluidR3Mono_GM.sf3`
  (23,712,790 bytes), `MS Basic.sf3` (51,278,610 bytes), and the full text of
  `FluidR3Mono_License.md` / `MS Basic_License.md` (MIT + required
  acknowledgements, quoting Frank Wen's original FluidR3 `COPYING`/`README`).
- GeneralUser GS v2.0.1 "License v2.0" full text via ScanCode LicenseDB
  (`https://scancode-licensedb.aboutcode.org/generaluser-gs-2.0.LICENSE`);
  bank size from the `JustEnoughLinuxOS/generaluser-gs` mirror
  (v1.471, 31,281,186 bytes) via the GitHub contents API.
- `pacman -Si fluidsynth soundfont-fluid freepats-general-midi` on this
  machine — versions, licences, download/installed sizes.
- `ldd`/`strings` on `libfluidsynth.so.3` — libsndfile/libvorbis/libogg
  linkage confirming Ogg-Vorbis `.sf3` support in this build.
- This repo, read directly: `pyproject.toml` (`[project.optional-dependencies]
  batch`), `config.py` lines 360-371 (`PLAYBACK_SAMPLE_RATE` 44100,
  `PLAYBACK_BLOCK_SIZE` 512), `playback.py`'s module docstring,
  `batch_transcribe.py:48` / `rhythm_reanalysis.py:37` / `main.py:1620,1672`
  (the isolated-import precedent), and issues #28/#32/#99/#102 via `gh`.
