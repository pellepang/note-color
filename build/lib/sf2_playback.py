"""SF2 soundfont playback via FluidSynth (map #99, build ticket #117,
implementing #102's research).

**This is the only module in this repo permitted to import `fluidsynth`**
(`pyfluidsynth`), exactly as `batch_transcribe.py`/`rhythm_reanalysis.py`
are the only ones permitted to import `librosa` and `score_writer.py`/
`score_editor_state.py` the only ones permitted to import `music21`.
Unlike those, the import here is *lazy* and behind one cached probe,
`sf2_availability()`: everything in this module that decides something
(the availability report, soundfont discovery, the voice handle, the
engine's note/program/render bookkeeping) is importable and
unit-testable on a machine with no FluidSynth at all, and only
`SF2Engine._make_synth()` -- the one place a real `Synth` is
constructed -- ever touches the library. `pyfluidsynth` reports both
"package not installed" and "system `libfluidsynth` not found" as the
same `ImportError` at import time (#102), which is why one probe covers
both; the result is cached because the answer cannot change within a
process and a patch browser will ask repeatedly.

**Pull model only -- `Synth.start()` is never called** (#102's
load-bearing finding). `start()` would open FluidSynth's *own* audio
driver on its own thread, a second output device competing with this
app's `sounddevice` streams. Without it FluidSynth is a pure renderer
owning no device: `fluid_synth_write_s16()` hands back a block on
demand, which `SF2Voice.render()` mixes into `sound_engine.SoundEngine`'s
existing callback buffer. FluidSynth's own defaults are already
`config.PLAYBACK_SAMPLE_RATE`/`PLAYBACK_BLOCK_SIZE` (44100/512), so
nothing resamples and nothing re-blocks. Measured by #102: 0.951 ms per
512-frame block at 64 voices with reverb and chorus on -- 8.2% of the
11.61 ms budget -- and a note-on is audible in the *next* block, the
same one-block bound `sound_engine.py` already documents for the
oscillator voices. `tests/test_sf2_playback.py` re-measures this
against whatever soundfont it finds (see docs/DECISIONS.md for the
numbers reproduced at build time).

**FluidSynth owns its own voices, so an `SF2Voice` is a handle, not a
generator.** This is the case #105 cited for keeping `Voice` a Protocol
rather than one concrete class: `Synth` renders every sounding note into
one mixed buffer, so N `SF2Voice`s cannot each render "their" audio.
Exactly one of them pulls the shared block per callback -- the
*primary*, defined as the oldest still-live, still-unfinished voice this
engine has handed out -- and the rest are no-ops for that block. The
registry holding them is a list of `weakref.ref`s specifically so a
voice the voice manager *steals* (which drops the record outright,
without a note-off -- `sound_engine.VoiceManager.allocate()`) disappears
from it deterministically under CPython refcounting, instead of
lingering as a primary that is never rendered again. The matching
`weakref.finalize` is what stops that stolen note ringing inside
FluidSynth forever; it is generation-checked so it can never silence a
*newer* note that has since retaken the same (channel, key).

**Two polyphony numbers that must not be conflated.** `synth.polyphony`
(`config.SF2_POLYPHONY`) is FluidSynth's *internal* cap and counts
FluidSynth voices -- a stereo or layered preset spends two or more per
key (#102) -- with its own stealing inside the library. The voice
manager's `[preferences].polyphony_*` budget (#112) counts *notes* this
app has handed out and is enforced entirely outside FluidSynth. Both
apply at once; neither knows about the other.

**stderr is redirected while a `Synth` is constructed** (`silence_stderr()`).
Constructing one enumerates audio drivers and writes ~16 ALSA/SDL lines
straight to fd 2 on a typical Linux box, which would scribble over any
raw-ANSI view in this app. `fluid_set_log_function` is not bound by
`pyfluidsynth` 1.4.0 and the ALSA lines are libasound's, not
FluidSynth's, so an fd-level redirect is the only mitigation that works
-- verified in #102 and re-verified by this module's tests.

**No soundfont is bundled.** `discover_soundfonts()` searches the
standard system locations plus this app's own samples directory, and
`resolve_soundfont()` layers a patch's bare `[sf2].soundfont` name and
the `[preferences].soundfont_path` setting over that. "No soundfont
found" is reported the same way "no library" is: a plain `SF2Error`
subclass a caller shows as status, never a crash.

Per this repo's "pure logic unit-tested, real I/O smoke-tested"
convention, every decision in here is exercised against a fake synth
with no FluidSynth involved, and the real-library tests skip cleanly
when `pyfluidsynth`/`libfluidsynth` or a soundfont is absent.
"""

import contextlib
import ctypes
import glob
import os
import threading
import weakref
from typing import NamedTuple

import numpy as np

import config
from config_store import store

SOUNDFONT_EXTENSIONS = (".sf2", ".sf3")

# The standard system locations #102 surveyed. `.sf3` banks are listed
# after `.sf2` ones by `discover_soundfonts()` because FluidSynth reads
# them only when built against libsndfile (#102 open question 5).
SYSTEM_SOUNDFONT_DIRS = (
    "/usr/share/soundfonts",
    "/usr/share/sounds/sf2",
    "/usr/share/sounds/sf3",
    "/usr/local/share/soundfonts",
)


class SF2Error(RuntimeError):
    """The library is fine but something this engine needs is not: a
    soundfont FluidSynth refused to load, a program that isn't in it, a
    closed engine asked to play. Base class for the two below, so a
    caller can catch all three at once and show them as status."""


class SF2Unavailable(SF2Error):
    """`pyfluidsynth` or the system `libfluidsynth` is missing -- the
    degrade-to-unavailable case, raised only by `engine_for_patch()`;
    `sf2_availability()` reports the same fact without raising."""


class SoundfontNotFound(SF2Error):
    """No usable soundfont could be found or resolved (nothing bundled,
    nothing discovered, no `soundfont_path` preference)."""


# --------------------------------------------------------------------------
# Availability probe
# --------------------------------------------------------------------------

class Sf2Availability(NamedTuple):
    available: bool
    reason: str      # "" when available; a one-line human explanation otherwise


_probe_result = None
_probe_lock = threading.Lock()


def _import_fluidsynth():
    """The single import site. Wrapped in `silence_stderr()` even though
    importing alone is quiet today -- the library is dlopen()ed at
    import, and a future build enumerating drivers there would otherwise
    scribble on a raw-ANSI view."""
    with silence_stderr():
        import fluidsynth
    return fluidsynth


def sf2_availability(_importer=None):
    """Whether SF2 playback can work in this process, as a cached
    `Sf2Availability`. One `try/except` covers both failure modes because
    `pyfluidsynth` raises the same `ImportError` for a missing package and
    for a missing system library (#102); an `OSError` covers a library
    that exists but cannot be loaded. Never raises. `_importer` is a
    test seam -- passing one bypasses (and never touches) the cache."""
    global _probe_result
    with _probe_lock:
        if _probe_result is not None and _importer is None:
            return _probe_result
        importer = _importer or _import_fluidsynth
        try:
            importer()
        except (ImportError, OSError) as exc:
            result = Sf2Availability(False, _unavailable_reason(exc))
        except Exception as exc:                        # pragma: no cover - defensive
            result = Sf2Availability(False, f"FluidSynth could not be loaded ({exc})")
        else:
            result = Sf2Availability(True, "")
        if _importer is None:
            _probe_result = result
        return result


def _unavailable_reason(exc):
    if isinstance(exc, ModuleNotFoundError):
        return "pyfluidsynth is not installed (pip install -e .[sf2])"
    text = str(exc)
    if "FluidSynth library" in text:
        return "system libfluidsynth not found (install the fluidsynth package)"
    return f"FluidSynth could not be loaded ({text})" if text else "FluidSynth could not be loaded"


def sf2_available():
    return sf2_availability().available


def reset_availability_cache():
    """Tests only: forget the cached probe and the log-silencing latch."""
    global _probe_result, _log_silenced
    with _probe_lock:
        _probe_result = None
    _log_silenced = False


# --------------------------------------------------------------------------
# stderr redirect and downmix
# --------------------------------------------------------------------------

@contextlib.contextmanager
def silence_stderr():
    """Redirects fd 2 to /dev/null for the duration of the block.

    An fd-level redirect rather than `contextlib.redirect_stderr`
    because the noise is written by C libraries (libasound, SDL) that
    never see Python's `sys.stderr` at all. Restores the original fd
    even if the body raises. Process-wide for its (short) duration --
    another thread's stderr write in that window is lost too, which is
    accepted: construction is a one-off, and nothing in this app writes
    to stderr from a thread while a view is up.
    """
    try:
        saved = os.dup(2)
    except OSError:                      # no fd 2 at all (rare, but not fatal)
        yield
        return
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(null)
        os.close(saved)


# FluidSynth's own log levels (`enum fluid_log_level`). PANIC/ERR are
# deliberately left alone -- they are rare and mean something is really
# broken -- while WARN and below are the chatty ones that would scribble
# on a raw-ANSI view mid-performance.
FLUID_LOG_LEVELS_TO_SILENCE = (2, 3, 4)   # WARN, INFO, DBG

_log_silenced = False


def silence_fluidsynth_log(fluidsynth=None):
    """Installs a null log handler for FluidSynth's chatty log levels,
    once per process. Returns True if it took effect.

    `silence_stderr()` covers construction, but FluidSynth also logs at
    *runtime*: exceeding `synth.polyphony` emits one
    "Failed to allocate a synthesis process" line per stolen voice,
    straight to fd 2, from inside `noteon()`. Measured here at build
    time -- 64 keys against `SF2_POLYPHONY` 64 produced 21 such lines in
    one test. Wrapping every `noteon()` in `silence_stderr()` instead
    would mean six syscalls per note *and* a process-wide stderr blackout
    on every note-on, which is far more invasive than one call at setup.

    #102 correctly found `fluid_set_log_function` is not *bound* by
    `pyfluidsynth` 1.4.0; it is still reachable on the `CDLL` the module
    already holds open, which is what this uses. Best-effort on purpose:
    a build that doesn't export the symbol just keeps its warnings, a
    cosmetic degradation, never an error (see docs/DECISIONS.md).
    """
    global _log_silenced
    if _log_silenced:
        return True
    fluidsynth = fluidsynth or _import_fluidsynth()
    try:
        lib = fluidsynth._fl
        setter = lib.fluid_set_log_function
        null_handler = ctypes.cast(None, ctypes.c_void_p)
        for level in FLUID_LOG_LEVELS_TO_SILENCE:
            setter(ctypes.c_int(level), null_handler, null_handler)
    except Exception:
        return False
    _log_silenced = True
    return True


def downmix(block, frames):
    """FluidSynth's interleaved int16 stereo block -> mono float32 in
    -1..1, the shape `sound_engine.SoundEngine`'s callback buffer wants.

    Mono because every audio path in this project is mono today (map
    #99's "Stereo" is explicitly still-unspecified fog); averaging the
    two channels rather than taking the left one keeps a hard-panned
    preset audible at half level instead of silent.
    """
    stereo = np.asarray(block[:2 * frames], dtype=np.float32)
    return (stereo[0::2] + stereo[1::2]) * (0.5 / 32768.0)


# --------------------------------------------------------------------------
# Soundfont discovery (nothing bundled -- #102's recommendation)
# --------------------------------------------------------------------------

def soundfont_search_dirs(samples_dir=None):
    """Where `discover_soundfonts()` looks, in priority order: this app's
    own samples directory first (a soundfont dropped next to the
    sampler's own samples is the most deliberate placement), then the
    user's XDG data dir, then the system locations, then a Homebrew
    prefix if one is set. Missing directories are fine -- the glob just
    finds nothing there."""
    if samples_dir is None:
        from patch_format import samples_dir as _samples_dir
        samples_dir = _samples_dir()
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    dirs = [samples_dir, os.path.join(data_home, "soundfonts"), *SYSTEM_SOUNDFONT_DIRS]
    brew = os.environ.get("HOMEBREW_PREFIX")
    if brew:
        dirs.append(os.path.join(brew, "share", "soundfonts"))
    return dirs


def discover_soundfonts(dirs=None):
    """Every `.sf2`/`.sf3` file in `dirs` (default `soundfont_search_dirs()`),
    flat and non-recursive like every other file glob in this repo,
    ordered by directory priority, then `.sf2` before `.sf3` within one
    directory, then by name. Duplicated paths are dropped."""
    found = []
    seen = set()
    for directory in (soundfont_search_dirs() if dirs is None else dirs):
        matches = []
        for ext in SOUNDFONT_EXTENSIONS:
            for pattern in (f"*{ext}", f"*{ext.upper()}"):
                matches.extend((ext, p) for p in glob.glob(os.path.join(directory, pattern)))
        for _, path in sorted(matches, key=lambda item: (item[0], os.path.basename(item[1]).lower())):
            if path not in seen and os.path.isfile(path):
                seen.add(path)
                found.append(path)
    return found


def preferred_soundfont_path():
    """`[preferences].soundfont_path` from config.toml, or "" -- read
    through `config_store` on every call so a hand edit applies live."""
    value = store.preference("soundfont_path", "")
    return os.path.expanduser(str(value)) if value else ""


def resolve_soundfont(name="", preference=None, dirs=None):
    """Which soundfont file to load, or `None`.

    Order: a bare `name` (a patch's `[sf2].soundfont`, reduced to its
    basename the way `patch_format.Sf2Selection` already does) is looked
    up in the search dirs and, if not found there, nothing else is
    substituted -- a patch that names a bank it can't find must not
    silently play a different one. With no name, the `soundfont_path`
    preference wins if it points at a file, else the first discovered
    bank. `preference=None` means "read config.toml"; pass "" to
    ignore it (tests).
    """
    search_dirs = soundfont_search_dirs() if dirs is None else dirs
    if name:
        base = os.path.basename(name)
        for directory in search_dirs:
            candidate = os.path.join(directory, base)
            if os.path.isfile(candidate):
                return candidate
        return None
    pref = preferred_soundfont_path() if preference is None else preference
    if pref and os.path.isfile(pref):
        return pref
    found = discover_soundfonts(search_dirs)
    return found[0] if found else None


def sf2_status(name="", preference=None, dirs=None):
    """One short status-line phrase: `"unavailable (<reason>)"`, `"no
    soundfont found"`, or the resolved bank's basename."""
    availability = sf2_availability()
    if not availability.available:
        return f"unavailable ({availability.reason})"
    path = resolve_soundfont(name, preference, dirs)
    return os.path.basename(path) if path else "no soundfont found"


# --------------------------------------------------------------------------
# The voice handle
# --------------------------------------------------------------------------

class SF2Voice:
    """One note handed out by `SF2Engine`. Satisfies
    `sound_engine.Voice`; see this module's docstring for why only the
    primary voice actually renders.

    `finished` is a *timed release tail*, not a report from FluidSynth:
    the C API exposes no per-note "this voice has finished" signal
    (`get_active_voice_count()` is a total, and one key can spend two or
    more FluidSynth voices in a layered preset). So a released voice
    holds its slot for `release_seconds` and is then reclaimed. Erring
    long wastes a little of the voice manager's budget; erring short cuts
    the *shared* block's tail only when this was the last live voice --
    while any other note is live the block is still pulled and FluidSynth
    finishes the sound on its own.
    """

    def __init__(self, engine, channel, key, velocity, sample_rate, release_seconds=None):
        self.engine = engine
        self.channel = channel
        self.key = key
        self.velocity = velocity
        self.sample_rate = sample_rate
        self.release_seconds = (config.SF2_RELEASE_TAIL_SECONDS
                                if release_seconds is None else release_seconds)
        # Stamped by `SF2Engine.note_on()` under its lock, and the only
        # thing identifying *which* note-on this handle stands for:
        # FluidSynth's `noteoff(chan, key)` cannot name one. Read back by
        # `SF2Engine.release()` so an older voice's note-off is dropped
        # instead of silencing a newer note that retook the same key.
        self.generation = None
        self._released = False
        self._release_frames = 0

    # -- Voice Protocol ----------------------------------------------------

    @property
    def released(self):
        return self._released

    @property
    def finished(self):
        if not self._released:
            return False
        return self._release_frames >= self.release_seconds * self.sample_rate

    def amplitude(self):
        """A ranking signal for `sound_engine.select_steal_index()`, not
        a measurement -- FluidSynth exposes no per-note level. Full
        velocity while held, fading linearly across the release tail so
        the stealing policy prefers the most-decayed released note."""
        if not self._released:
            return self.velocity
        total = max(1.0, self.release_seconds * self.sample_rate)
        return self.velocity * max(0.0, 1.0 - self._release_frames / total)

    def note_off(self):
        """Idempotent, per the Protocol -- a second note-off must not
        restart the release tail."""
        if self._released:
            return
        self._released = True
        self.engine.release(self)

    def render(self, out, frames):
        """Pulls the shared block if this is the primary voice, *then*
        advances the release tail -- in that order, so a lone releasing
        voice still pulls the block it finishes on rather than being
        skipped as already-finished."""
        if frames <= 0:
            return
        self.engine.render_voice(self, out, frames)
        if self._released:
            self._release_frames += frames


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

class SF2Engine:
    """`sound_engine.Engine` over one FluidSynth `Synth` and one loaded
    soundfont.

    One engine per *patch* (a soundfont plus a bank/preset selection),
    not one per process: two SF2 patches sounding at once would be two
    engines, each with its own `Synth`. That costs one bank's worth of
    RAM per engine (31-148 MB, #102's measurements) but keeps "a patch is
    a sound" true, and multi-timbral use inside one engine is available
    for free anyway via `select_program()` on a second channel -- which
    is exactly why `NoteOn` carries `channel`.

    `synth` (and `writer`) are injectable so every decision in this class
    is testable against a fake with no FluidSynth present; `_make_synth()`
    is the only place that touches the real library.
    """

    def __init__(self, soundfont_path=None, bank=0, preset=0, sample_rate=None,
                 gain=None, polyphony=None, reverb=None, chorus=None,
                 synth=None, writer=None, release_seconds=None):
        self.sample_rate = int(sample_rate or config.PLAYBACK_SAMPLE_RATE)
        self.gain = float(config.SF2_GAIN if gain is None else gain)
        self.polyphony = int(config.SF2_POLYPHONY if polyphony is None else polyphony)
        self.reverb = bool(config.SF2_REVERB if reverb is None else reverb)
        self.chorus = bool(config.SF2_CHORUS if chorus is None else chorus)
        self.release_seconds = release_seconds
        self.bank = int(bank)
        self.preset = int(preset)
        self.soundfont_path = None
        self.sfid = None
        self.soundfont_name = ""

        self._lock = threading.Lock()
        self._live = []                  # weakref.ref[SF2Voice], allocation order
        self._generations = {}           # (channel, key) -> monotonically rising int
        self._next_generation = 1
        self._programmed = set()         # channels already program_select()ed
        self._buffer = None
        self._buffer_frames = 0
        self._last_out = None             # weakref to the last `out` buffer pulled into
        self.blocks_rendered = 0
        self.closed = False

        self.synth = synth if synth is not None else self._make_synth()
        self._writer = writer if writer is not None else self._make_writer()
        if soundfont_path:
            self.load_soundfont(soundfont_path)

    # -- construction ------------------------------------------------------

    def _make_synth(self):
        """The one call that touches the real library. Wrapped in
        `silence_stderr()` because construction is what emits the ALSA/SDL
        spew (#102); `Synth.start()` is deliberately never called."""
        availability = sf2_availability()
        if not availability.available:
            raise SF2Unavailable(availability.reason)
        fluidsynth = _import_fluidsynth()
        silence_fluidsynth_log(fluidsynth)
        settings = {
            "synth.polyphony": self.polyphony,
            "synth.reverb.active": int(self.reverb),
            "synth.chorus.active": int(self.chorus),
        }
        with silence_stderr():
            return fluidsynth.Synth(gain=self.gain, samplerate=float(self.sample_rate), **settings)

    def _make_writer(self):
        """A `writer(frames, buffer)` that fills `buffer` with `frames`
        interleaved int16 stereo frames. For a real `Synth` this is the
        raw `fluid_synth_write_s16` into a preallocated buffer rather
        than `Synth.get_samples()`, which allocates a fresh one per call
        -- #102 measured that as only a ~5% saving, but keeping
        allocations out of the real-time callback is worth it on
        principle. A fake synth with a `write_s16(frames, buffer)`
        method is used as-is."""
        if hasattr(self.synth, "write_s16"):
            return self.synth.write_s16
        fluidsynth = _import_fluidsynth()
        handle = self.synth.synth

        def writer(frames, buffer):
            fluidsynth.fluid_synth_write_s16(handle, frames, buffer, 0, 2, buffer, 1, 2)

        return writer

    @classmethod
    def from_patch(cls, patch, soundfont_path, **kwargs):
        """A `patch_format.Patch` with `engine = "sf2"` -> an engine.
        `soundfont_path` is resolved by the caller (`resolve_soundfont()`
        / `engine_for_patch()`) rather than here, so construction never
        has to know about the samples directory or the preference."""
        return cls(soundfont_path=soundfont_path, bank=patch.sf2.bank,
                   preset=patch.sf2.preset, **kwargs)

    def load_soundfont(self, path):
        """Loads a bank and selects its program on channel 0. Raises
        `SF2Error` on `FLUID_FAILED` (-1) rather than letting a silent
        engine look like a working one -- #102's open question 5: an
        `.sf3` bank fails exactly this way on a `libfluidsynth` built
        without libsndfile."""
        if not path or not os.path.isfile(path):
            raise SoundfontNotFound(f"soundfont not found: {path}")
        with silence_stderr():
            sfid = self.synth.sfload(str(path))
        if sfid is None or sfid == -1:
            raise SF2Error(f"FluidSynth could not load {os.path.basename(path)} "
                           "(unreadable, or an .sf3 bank on a libfluidsynth "
                           "built without libsndfile)")
        self.sfid = sfid
        self.soundfont_path = path
        self.soundfont_name = os.path.basename(path)
        self._programmed.clear()
        self.select_program(0, self.bank, self.preset)
        return sfid

    def select_program(self, channel, bank=None, preset=None):
        """Bank+preset selection for one channel (SF2's **Program**, in
        `CONTEXT.md`'s vocabulary). FluidSynth accepts a program that
        isn't in the bank (it falls back to whatever it finds), so this
        never raises for a bad selection -- `program_name()` is the way
        to check whether one really exists."""
        bank = self.bank if bank is None else int(bank)
        preset = self.preset if preset is None else int(preset)
        if self.sfid is None:
            raise SF2Error("no soundfont loaded")
        with silence_stderr():
            self.synth.program_select(int(channel), self.sfid, bank, preset)
        self._programmed.add(int(channel))

    def program_name(self, bank=None, preset=None):
        """The preset's own name from the bank, for a patch browser. An
        empty string when the program isn't in this soundfont -- a
        browser wants a blank row, not an exception."""
        if self.sfid is None:
            return ""
        bank = self.bank if bank is None else int(bank)
        preset = self.preset if preset is None else int(preset)
        try:
            with silence_stderr():
                name = self.synth.sfpreset_name(self.sfid, bank, preset)
        except Exception:
            return ""
        return name or ""

    # -- Engine Protocol ---------------------------------------------------

    def note_on(self, event, sample_rate=None):
        """`sound_engine.Engine.note_on`. `event.velocity` is 0..1 here
        (this repo's normalized convention) and is converted back to
        MIDI's own 1..127 at this edge, which is where FluidSynth's
        vocabulary begins (a MIDI velocity of 0 would be a note-off)."""
        if self.closed:
            raise SF2Error("engine is closed")
        channel = int(event.channel)
        key = max(0, min(127, int(event.pitch)))
        velocity = max(0.0, min(1.0, float(event.velocity)))
        midi_velocity = max(1, min(127, int(round(velocity * 127))))
        if channel not in self._programmed and self.sfid is not None:
            self.select_program(channel)
        voice = SF2Voice(self, channel, key, velocity, int(sample_rate or self.sample_rate),
                         release_seconds=self.release_seconds)
        with self._lock:
            generation = self._next_generation
            self._next_generation += 1
            self._generations[(channel, key)] = generation
            voice.generation = generation
            self._live = [ref for ref in self._live if ref() is not None]
            self._live.append(weakref.ref(voice))
        # Finalizer, not `__del__`: it must capture plain ints only, never
        # the voice itself (which would keep it alive and defeat the point).
        weakref.finalize(voice, self._release_generation, channel, key, generation)
        self.synth.noteon(channel, key, midi_velocity)
        return voice

    # -- shared rendering --------------------------------------------------

    def live_voices(self):
        """Every voice handed out that is still referenced somewhere
        (the voice manager, or a caller), oldest first. Prunes dead refs
        as a side effect."""
        with self._lock:
            live = [(ref, ref()) for ref in self._live]
            live = [(ref, voice) for ref, voice in live if voice is not None]
            self._live = [ref for ref, _ in live]
            return [voice for _, voice in live]

    def primary_voice(self):
        """The one voice that pulls the shared block this callback: the
        oldest still-live, still-unfinished voice. `None` when nothing is
        sounding."""
        for voice in self.live_voices():
            if not voice.finished:
                return voice
        return None

    def render_voice(self, voice, out, frames):
        """Called by every `SF2Voice.render()`; exactly one of them pulls
        and mixes per callback, so N sounding notes still cost exactly one
        `fluid_synth_write_s16`.

        Two independent "this is the block's one pull" signals, either
        sufficient: (a) `voice` is the primary (oldest live unfinished),
        which is always the first voice the manager renders when every
        live voice is in the manager; (b) `out` is a buffer this engine
        has not seen before (a `weakref` identity check -- a dead ref can
        never alias a new array), which is true of every callback of
        `sound_engine.SoundEngine` since it zeroes a fresh block each
        time. (b) covers a stray reference held *outside* the manager
        (a status line's `snapshot()`, a test) that would otherwise make
        the primary a voice nobody renders; (a) covers a caller that
        reuses one buffer across callbacks. They cannot double-pull: a
        manager voice older than the primary is impossible, so whichever
        renders first consumes both signals at once.
        """
        if self.closed:
            return
        new_buffer = self._last_out is None or self._last_out() is not out
        if not new_buffer and voice is not self.primary_voice():
            return
        try:
            self._last_out = weakref.ref(out)
        except TypeError:
            self._last_out = None
        out[:frames] += self.render_block(frames).astype(out.dtype, copy=False)

    def render_block(self, frames):
        """`frames` mono float32 samples of everything FluidSynth is
        currently sounding. Safe to call directly (an offline caller, or
        a test) -- the primary-voice rule only governs who calls it from
        inside the callback."""
        if frames <= 0 or self.closed:
            return np.zeros(max(0, frames), dtype=np.float32)
        raw = self._render_raw(frames)
        self.blocks_rendered += 1
        return downmix(raw, frames)

    def _render_raw(self, frames):
        """The interleaved int16 stereo block, as FluidSynth wrote it.
        Split out from `render_block()` so the downmix arithmetic is
        tested independently of the library."""
        if self._buffer is None or self._buffer_frames < frames:
            self._buffer = ctypes.create_string_buffer(frames * 2 * 2)
            self._buffer_frames = frames
        buffer = self._buffer
        self._writer(frames, buffer)
        return np.frombuffer(buffer, dtype=np.int16, count=2 * frames)

    # -- note-off / teardown -----------------------------------------------

    def release(self, voice):
        """The note-off `SF2Voice.note_off()` delegates here.

        Passes the voice's *own* generation, not whatever is currently
        newest for its (channel, key) -- reading the live value here
        would make the check in `_release_generation()` compare a number
        against itself and always pass, so releasing a superseded voice
        would cut off the newer note holding that key.
        """
        return self._release_generation(voice.channel, voice.key, voice.generation)

    def _release_generation(self, channel, key, generation):
        """Sends a note-off only if `generation` is still the newest
        note-on for this (channel, key).

        Without the check, a stolen voice's finalizer -- which can run at
        any moment, in any thread -- could silence a *newer* note that
        has since retaken the same key, since FluidSynth's own
        `noteoff(chan, key)` has no way to name which note it means.
        `synth.threadsafe-api` defaults to 1 (#102), so calling it from
        the finalizer's thread is safe.
        """
        if self.closed or generation is None:
            return False
        with self._lock:
            if self._generations.get((channel, key)) != generation:
                return False
            del self._generations[(channel, key)]
        try:
            self.synth.noteoff(channel, key)
        except Exception:
            return False
        return True

    def all_notes_off(self, channel=None):
        """Releases every held key (on one channel, or all). Returns the
        number of channels told."""
        with self._lock:
            channels = ({channel} if channel is not None
                        else {chan for chan, _ in self._generations})
            if channel is None:
                self._generations = {}
            else:
                self._generations = {k: v for k, v in self._generations.items()
                                     if k[0] != channel}
        for chan in channels:
            with contextlib.suppress(Exception):
                self.synth.all_notes_off(chan)
        return len(channels)

    def active_voice_count(self):
        """FluidSynth's own live *voice* count (not notes -- see the
        module docstring). 0 for a fake synth without the method."""
        getter = getattr(self.synth, "get_active_voice_count", None)
        if getter is None or self.closed:
            return 0
        try:
            return int(getter())
        except Exception:
            return 0

    def close(self):
        """Idempotent, and safe to call on a half-constructed engine --
        same convention as `session_recorder.SessionRecorder.close()`."""
        if self.closed:
            return
        self.closed = True
        with self._lock:
            self._live = []
            self._generations = {}
        synth, self.synth = self.synth, None
        self._writer = None
        if synth is not None:
            with contextlib.suppress(Exception), silence_stderr():
                synth.delete()


# --------------------------------------------------------------------------
# Patch -> engine
# --------------------------------------------------------------------------

def engine_for_patch(patch, dirs=None, preference=None, **kwargs):
    """A `patch_format.Patch` with `engine = "sf2"` -> a ready
    `SF2Engine`, or one of the three `SF2Error`s: `SF2Unavailable` (no
    library), `SoundfontNotFound` (no bank -- the patch's named one is
    missing, or with none named, nothing was discovered), or a plain
    `SF2Error` (the bank exists but FluidSynth refused it). Never any
    other exception for a "this machine can't do SF2" reason, so a
    caller has exactly one thing to catch."""
    availability = sf2_availability()
    if not availability.available and "synth" not in kwargs:
        raise SF2Unavailable(availability.reason)
    name = patch.sf2.soundfont
    path = resolve_soundfont(name, preference=preference, dirs=dirs)
    if path is None:
        if name:
            raise SoundfontNotFound(f"soundfont {name!r} not found in any search directory")
        raise SoundfontNotFound("no soundfont found (install one, or set [preferences].soundfont_path)")
    return SF2Engine.from_patch(patch, path, **kwargs)
