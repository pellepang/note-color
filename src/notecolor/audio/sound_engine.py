"""The sound engine's core seam (map #99, build ticket #112, implementing
decision #105): the event model every sound source speaks, the two
`typing.Protocol`s every sound *producer* implements, the voice manager
that owns the polyphony budget, and the one process-wide `SoundEngine`
that owns the output stream.

Nothing in this module makes a sound on its own. It is the socket the
three engines of map #99 (subtractive synth #113, sampler #116, SF2
#117) and the four sources (QWERTY keys, editor audition, frozen-buffer
playback, a future MIDI device) all plug into.

**One vocabulary: note-on / note-off (#105 decision 1).** There is no
duration-carrying primitive here, deliberately. A caller that already
knows a note's length (`virtualnote replay --play`) issues a note-on and
separately arranges the matching note-off -- `SoundEngine.
schedule_note_off()` is caller-side sugar for exactly that arrangement
(a deadline counted by the audio callback's own frame clock), *not* a
second primitive in the voice model: a `Voice` still only ever knows
"start" and "stop". A note-on carries pitch (MIDI note number),
velocity, channel and a patch selection, all present from the first
commit even though nothing in this repo can yet supply a real velocity
(#105: retrofitting velocity later would touch every module, and #103
found it routes to two destinations).

**Two Protocols, mirroring `detection_backends.py` (#105 decision 2).**
`Engine` turns a `NoteOn` into a `Voice`; a `Voice` renders N samples,
accepts a note-off, and reports when it may be reclaimed. SF2 is the
forcing case for keeping `Voice` a Protocol rather than one concrete
class: FluidSynth owns its voices internally and renders a mixed buffer
(#102), so its voice object is a fundamentally different thing from the
synth's per-note oscillator/filter/envelope state.

**Voice stealing is a hard cap (#105 decision 3, from #100's
measurement).** The driver's ~3-block ring buffer hides overruns until
the engine has already xrun, so a load-driven policy's signal arrives
too late to act on -- the budget must therefore be enforced by counting
voices, not by watching timing. Policy: steal an already-released voice
first (the quietest of those, oldest breaking ties), and only touch a
still-held note when every voice is held (oldest first). A new note is
*never* refused: dropping the note the player just pressed is the most
audible possible failure.

**Polyphony is a setting, not a constant (#105 decision 4).** #100
measured ~40 voices safe standalone but only ~24 with one thread doing
this app's real analysis work (GIL contention, not CPU headroom), so the
budget is a `[preferences]` numeric field read live through
`config_store` -- two of them, one per context -- and `SoundEngine`
takes a *callable* budget rather than an int so a Settings-screen edit
takes effect on the next note without restarting anything, the same
hot-reload convention every other `config_store` consumer follows.

**One process-wide engine, lazily created (#105 decision 5).**
`SoundEngine.ensure_started()` is idempotent and opens the output stream
on first use, exactly as `main.SessionState.ensure_started()` does for
audio *input* (issue #40) -- `main.SessionState` owns one for the
process's whole life so switching tools never drops or reopens the audio
device. The standalone offline entry points (`transcribe`/`replay`),
which never construct a `SessionState` at all, build their own instead.

Block size stays `config.PLAYBACK_BLOCK_SIZE` (512): #100 measured
PipeWire reporting an identical 34.8ms stream latency at 128/256/512, so
a smaller block buys no latency at all and only tightens the callback
deadline.

Per this repo's "pure logic unit-tested, real I/O smoke-tested"
convention, everything that decides *what happens* -- the event model,
the stealing policy, `VoiceManager`'s allocate/release/render
bookkeeping, the note-off deadline arithmetic -- is pure and directly
unit-tested against fake voices with no audio device involved;
`SoundEngine._callback()` is exercised by calling it directly with a
plain NumPy buffer, and only `ensure_started()`/`stop()` need a real
`sounddevice.OutputStream`.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol

import os
import threading

import numpy as np

from notecolor.settings import config
from notecolor.settings.config_store import store
from notecolor.audio.effects import EffectsChain


# --------------------------------------------------------------------------
# Realtime scheduling for the audio callback thread (issue #232, decision 70)
# --------------------------------------------------------------------------

#: A fixed, modest `SCHED_FIFO` priority to ask for -- not tuned, just
#: comfortably inside the 1-99 range and well clear of the 90s kernel
#: watchdogs typically claim. JACK's own default (10) lands in the same
#: neighbourhood every pro-audio guide recommends, which is the point:
#: this is the standard ask, not a number picked for this machine.
AUDIO_THREAD_RT_PRIORITY = 10


def _try_realtime_scheduling():
    """Asks the OS for `SCHED_FIFO` on the *calling* thread and returns
    `(active, message)`.

    Deliberately reached for directly with `os.sched_setscheduler` rather
    than through `sounddevice`/PortAudio: neither exposes a scheduling or
    priority knob anywhere in their public API (checked -- `OutputStream`'s
    constructor takes no such argument, and PortAudio's own thread-priority
    hooks are host-API-internal, used automatically only by its JACK
    backend, not its ALSA/PipeWire ones this project runs on). There is
    also no Python `threading.Thread` object to call `setpriority` on ahead
    of time -- PortAudio spawns and owns the real OS thread that calls back
    into this module; the *first* call this function makes must therefore
    happen from inside that thread, i.e. from inside `_callback` itself, the
    only place this module could ever mean by "the audio thread". POSIX
    scheduling calls apply to whatever OS thread makes them; passing `pid=0`
    to `sched_setscheduler` means "the calling thread", not "the process"
    (`man 2 sched_setscheduler`), which is exactly what is wanted here.

    Never raises. The overwhelmingly common outcome on a default install is
    `PermissionError` -- Linux caps every thread's realtime priority at 0
    (`RLIMIT_RTPRIO`) unless an admin has configured otherwise (an
    `/etc/security/limits.d` rule, or membership of an `audio`/`realtime`
    group) -- and that is reported as the ordinary, expected case, not a
    fault requiring root: the synth must still play, just at normal
    scheduling, which is what every caller of this function already got
    before this existed. Also covers: a platform with no realtime
    scheduling exposed to Python at all (no `os.sched_setscheduler`, e.g.
    Windows), and any other `OSError` a kernel might raise."""
    if not hasattr(os, "sched_setscheduler"):
        return False, "realtime scheduling unavailable on this platform -- running at normal priority"
    try:
        lo = os.sched_get_priority_min(os.SCHED_FIFO)
        hi = os.sched_get_priority_max(os.SCHED_FIFO)
        priority = max(lo, min(hi, AUDIO_THREAD_RT_PRIORITY))
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
        return True, f"realtime scheduling active: SCHED_FIFO priority {priority}"
    except PermissionError:
        return False, (
            "realtime scheduling unavailable (no rtprio permission) -- running at "
            "normal priority; ask an administrator for an /etc/security/limits.d "
            "rtprio rule or audio/realtime group membership to enable it -- not "
            "required, the synth works either way"
        )
    except OSError as exc:
        return False, f"realtime scheduling unavailable ({exc}) -- running at normal priority"


def _detect_cpu_governor():
    """Best-effort read of cpu0's `scaling_governor` and returns
    `(governor_or_None, message_or_"")`. `None` covers every platform and
    kernel configuration without this sysfs file -- non-Linux entirely, a
    Linux kernel with no `cpufreq` subsystem (some minimal Pi/embedded
    images), or a container without `/sys` mounted through -- treated as
    "unknown", never as an error.

    Deliberately read-only (issue #232's second question): flipping a
    system-wide governor from inside an application needs root and would
    silently change every other process's power/performance tradeoff on
    the machine for as long as this one app happened to be running --
    exactly the kind of intrusive, hard-to-reverse side effect this
    project avoids elsewhere (`SessionState`/`SoundEngine` never touch a
    system setting either). Detecting `powersave` and saying so, once, is
    the whole of what this does."""
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            governor = f.read().strip()
    except OSError:
        return None, ""
    if governor == "powersave":
        return governor, (
            "CPU governor is 'powersave' -- the core can downclock between "
            "callbacks and cause timing spikes under load; 'performance' or "
            "'schedutil' avoids this (root, e.g. cpupower frequency-set -g "
            "performance) -- not changed automatically"
        )
    return governor, ""


# --------------------------------------------------------------------------
# Event model (#105 decision 1)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class NoteOn:
    """One note-on. MIDI-shaped on purpose (map #99's standing decision):
    `pitch` is a MIDI note number, `velocity` is 0..1, `channel` is a
    plain int, and `patch` names which sound to use (None = whatever the
    engine's default is). A MIDI device later becomes a *source* that
    constructs these, not a new code path.

    `velocity` is 0..1 rather than MIDI's own 0..127 because every
    consumer in this codebase (`playback.synthesize_note()`, the amp
    envelope, #103's `velocity_to_amp`/`velocity_to_filter`) wants a
    normalized scalar; `from_midi_velocity()` converts at the edge, which
    is where a real MIDI source will sit."""

    pitch: int
    velocity: float = 1.0
    channel: int = 0
    patch: Optional[str] = None

    @staticmethod
    def from_pitch_class(pitch_class, octave, velocity=1.0, channel=0, patch=None):
        """This repo's own (pitch_class, octave) pair -> a NoteOn. Uses
        the same standard MIDI tuning `playback.note_frequency()` already
        assumes (A4=440Hz, C4=midi 60)."""
        return NoteOn(midi_pitch(pitch_class, octave), velocity, channel, patch)

    @staticmethod
    def from_midi_velocity(pitch, midi_velocity, channel=0, patch=None):
        """A raw MIDI 0..127 velocity -> a NoteOn with velocity 0..1."""
        return NoteOn(pitch, max(0, min(127, midi_velocity)) / 127.0, channel, patch)


def midi_pitch(pitch_class, octave):
    """(pitch_class 0-11, octave) -> MIDI note number, the inverse of
    `pitch_class_octave()`. Same tuning convention as
    `playback.note_frequency()`."""
    return (octave + 1) * 12 + pitch_class


def pitch_class_octave(pitch):
    """MIDI note number -> (pitch_class, octave), for rendering surfaces
    that still think in this repo's own pitch-class/octave terms."""
    return pitch % 12, pitch // 12 - 1


def frequency_for(pitch):
    """MIDI note number -> Hz (A4=440)."""
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


# --------------------------------------------------------------------------
# The seam (#105 decision 2) -- mirrors detection_backends.py's convention
# --------------------------------------------------------------------------

class Voice(Protocol):
    """One sounding note (or, for the SF2 engine, one handle onto
    FluidSynth's own internally-owned voice). Rendering is *additive*:
    `render()` adds into the caller's block rather than returning a new
    array, so the voice manager mixes N voices with no per-voice
    allocation."""

    def render(self, out: np.ndarray, frames: int) -> None:
        """Adds this voice's next `frames` samples into `out` in place."""
        ...

    def note_off(self) -> None:
        """Begins this voice's release. Idempotent -- a second note-off
        for an already-releasing voice must not restart the release."""
        ...

    @property
    def released(self) -> bool:
        """True once note_off() has been accepted. The voice manager's
        stealing policy prefers released voices."""
        ...

    @property
    def finished(self) -> bool:
        """True once this voice has rendered its last sample and its slot
        may be reclaimed."""
        ...

    def amplitude(self) -> float:
        """Current output level, 0..1 -- the stealing policy's
        'quietest among the released' tie-break. Approximate is fine;
        it is a ranking signal, not a measurement."""
        ...


class Engine(Protocol):
    """Turns a note-on into a voice. The three engines of map #99
    (subtractive synth, sampler, SF2) each implement this."""

    def note_on(self, event: NoteOn, sample_rate: int) -> Voice:
        ...


# --------------------------------------------------------------------------
# Voice manager (#105 decisions 3 and 4)
# --------------------------------------------------------------------------

@dataclass
class ActiveVoice:
    """One allocated slot. `seq` is a monotonically increasing allocation
    counter -- the age ordering the stealing policy needs, immune to
    wall-clock jitter and to list reordering during retirement."""

    voice_id: int
    voice: object
    pitch: int
    channel: int
    seq: int


def select_steal_index(records):
    """Which of `records` (a list of `ActiveVoice`) to sacrifice for an
    incoming note, per #105 decision 3: an already-released voice first
    -- the quietest of those, oldest breaking ties -- and only a
    still-held note (oldest first) when every voice is held. Returns
    `None` for an empty list.

    Pure and separately testable precisely because it is the part with a
    judgment call in it; `VoiceManager.allocate()` owns only the locking
    and bookkeeping around it."""
    if not records:
        return None
    released = [i for i, record in enumerate(records) if record.voice.released]
    if released:
        return min(released, key=lambda i: (records[i].voice.amplitude(), records[i].seq))
    return min(range(len(records)), key=lambda i: records[i].seq)


class VoiceManager:
    """Owns the sounding voices and enforces the polyphony budget.

    `polyphony` is either an int or a zero-argument callable returning
    one -- a callable is what `SoundEngine` passes, so a Settings-screen
    edit of the `[preferences]` field applies to the very next note-on
    with no restart and no reload call anywhere (the same mtime-checked
    hot-reload shape `config_store` gives every other live setting).

    One `threading.Lock` guards every access, for the same reason
    `playback.LiveScheduler` uses one rather than `main.ReanalysisBuffer`'s
    GIL argument: `render_block()` is a read-modify-write across the whole
    voice list every callback (render each, then rebuild minus the
    finished ones), which is not a single atomic bytecode op.

    A stolen voice is dropped outright rather than fast-released -- see
    docs/DECISIONS.md for why a hard drop is the honest v1 here."""

    def __init__(self, polyphony=None):
        self._polyphony = polyphony if polyphony is not None else config.POLYPHONY_STANDALONE
        self._records = []
        self._lock = threading.Lock()
        self._next_id = 1
        self._next_seq = 0
        self.steal_count = 0

    @property
    def polyphony(self):
        """The live budget. Always at least 1 -- a zero or negative
        setting would otherwise mean 'every note is stolen immediately',
        which is silence rather than a smaller instrument."""
        value = self._polyphony() if callable(self._polyphony) else self._polyphony
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return config.POLYPHONY_STANDALONE

    def set_polyphony(self, value):
        """Replaces the budget with an int, a zero-argument callable, or
        None (restoring `config.POLYPHONY_STANDALONE`). Public because a
        *context* can change within one process's life without the engine
        being rebuilt -- the synth tool's layout 2 plays a kit and a synth
        patch from one cap and wants a lower figure than a single-engine
        view does (#107's implementation note), and it switches on a Tab
        press."""
        self._polyphony = config.POLYPHONY_STANDALONE if value is None else value
        return self._polyphony

    def active_count(self):
        with self._lock:
            return len(self._records)

    def snapshot(self):
        """A copy of the current records list -- for status lines and
        tests, never for mutation."""
        with self._lock:
            return list(self._records)

    def allocate(self, voice, pitch=0, channel=0):
        """Gives `voice` a slot, stealing first if the budget is already
        full. Never refuses (#105 decision 3). Returns the new voice's
        id."""
        with self._lock:
            budget = self.polyphony
            while len(self._records) >= budget:
                index = select_steal_index(self._records)
                if index is None:
                    break
                self._records.pop(index)
                self.steal_count += 1
            voice_id = self._next_id
            self._next_id += 1
            self._records.append(ActiveVoice(voice_id, voice, pitch, channel, self._next_seq))
            self._next_seq += 1
            return voice_id

    def note_off(self, pitch, channel=0):
        """Releases the oldest still-held voice matching `pitch`/`channel`
        -- MIDI's own resolution for the overlapping-same-pitch case, and
        the reason `release_voice()` exists alongside it for a caller
        holding an exact handle. Returns the released voice's id, or
        None."""
        with self._lock:
            matches = [r for r in self._records
                       if r.pitch == pitch and r.channel == channel and not r.voice.released]
            if not matches:
                return None
            record = min(matches, key=lambda r: r.seq)
            record.voice.note_off()
            return record.voice_id

    def release_voice(self, voice_id):
        """Releases exactly the voice `allocate()` returned this id for.
        A no-op (returning False) if it has already been stolen or
        finished -- a caller holding a stale handle is normal, not an
        error."""
        with self._lock:
            for record in self._records:
                if record.voice_id == voice_id:
                    record.voice.note_off()
                    return True
            return False

    def all_notes_off(self):
        """Releases every sounding voice (they still fade out through
        their own release stage). Returns how many were released."""
        with self._lock:
            count = 0
            for record in self._records:
                if not record.voice.released:
                    record.voice.note_off()
                    count += 1
            return count

    def clear(self):
        """Drops every voice immediately, no release -- teardown only."""
        with self._lock:
            self._records = []

    def render_block(self, out, frames):
        """Mixes every active voice additively into `out` and retires the
        finished ones. `out` must already be zeroed by the caller (the
        audio callback owns that buffer)."""
        with self._lock:
            for record in self._records:
                record.voice.render(out, frames)
            self._records = [r for r in self._records if not r.voice.finished]
            return len(self._records)


# --------------------------------------------------------------------------
# Polyphony budget (#105 decision 4)
# --------------------------------------------------------------------------

def polyphony_for(detection_active):
    """The live `[preferences]` budget for this context: #100 measured
    ~40 voices safe standalone but only ~24 with one thread running this
    app's real 2048-point-FFT analysis work, so the two contexts get two
    separate settings rather than one compromise value. Read through
    `config_store` on every call, so a Settings-screen edit applies
    immediately."""
    if detection_active:
        return store.preference("polyphony_with_detection", config.POLYPHONY_WITH_DETECTION)
    return store.preference("polyphony_standalone", config.POLYPHONY_STANDALONE)


# --------------------------------------------------------------------------
# The process-wide engine (#105 decision 5)
# --------------------------------------------------------------------------

class SoundEngine:
    """Owns one `sounddevice.OutputStream` and one `VoiceManager` for the
    process's whole life. `ensure_started()` is idempotent and lazy, so
    merely constructing one (as `main.SessionState` does eagerly, having
    no side effect to defer) never opens an audio device -- exactly the
    lifecycle issue #40 settled for audio input.

    `detection_active` is a bool or a zero-argument callable; it selects
    which of the two polyphony preferences applies, re-read on every
    note-on so a live view that starts detection mid-session tightens the
    budget without anything having to notice.

    Note-offs scheduled through `schedule_note_off()` are resolved
    against the audio callback's *own* frame clock rather than a timer
    thread: one fewer thread, no sleep jitter, and a deadline that is
    accurate to one block by construction (11.6ms at the defaults) --
    the same "timing is an index computation, not a wall-clock one"
    property that makes `playback.render_offline()` sample-accurate."""

    def __init__(self, engine=None, sample_rate=None, block_size=None, detection_active=False,
                 effects=None):
        self.sample_rate = sample_rate or config.PLAYBACK_SAMPLE_RATE
        self.block_size = block_size or config.PLAYBACK_BLOCK_SIZE
        self.detection_active = detection_active
        self.engine = engine if engine is not None else _default_engine()
        self.voices = VoiceManager(polyphony=self._polyphony)
        self.effects = EffectsChain()
        self.set_effects(effects)
        #: The patch graph (`audio/graph/poly.PolyGraph`) mixed in beside
        #: the voices, or None for "no graph". See `set_graph()`.
        self.graph = None
        #: Where a graph block is cast from float64 to the mix's float32,
        #: preallocated so the cast costs no allocation in the callback.
        self._graph_scratch = np.zeros(self.block_size, dtype=np.float32)
        self._stream = None
        self._frame_clock = 0
        self._block_listener = None
        #: Exceptions swallowed from the block listener. Surfaced in the
        #: transport bar beside xruns: a scheduler bug would otherwise show up
        #: only as "notes stopped happening", with no signal anywhere that
        #: something is wrong -- worse than the honest "no audio" path the
        #: rest of the app takes.
        self.block_listener_error_count = 0
        self._pending_offs = {}
        self._pending_lock = threading.Lock()
        self.callback_status_count = 0
        #: Set from inside `_callback`'s first invocation, on the real
        #: audio thread -- see `_try_realtime_scheduling()`. `None` means
        #: "not attempted yet" (no block has rendered), distinct from
        #: `False` ("attempted, denied or unsupported").
        self.realtime_priority_active = None
        self.realtime_priority_message = ""
        #: Set from `ensure_started()`, before the stream opens -- a plain
        #: sysfs read, no audio thread involved. See `_detect_cpu_governor()`.
        self.cpu_governor = None
        self.cpu_governor_message = ""

    # -- lifecycle ---------------------------------------------------------

    def _polyphony(self):
        active = self.detection_active() if callable(self.detection_active) else bool(self.detection_active)
        return polyphony_for(active)

    def ensure_started(self):
        """Opens the output stream if it isn't open yet. Idempotent, so
        every entry point can call it unconditionally (the shape
        `SessionState.ensure_started()` already established)."""
        if self._stream is not None:
            return
        import sounddevice as sd

        self._check_cpu_governor()
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate, blocksize=self.block_size, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()

    @property
    def started(self):
        return self._stream is not None

    def stop(self):
        """Closes the stream and drops every voice. Idempotent, and safe
        before any `ensure_started()` -- same convention as
        `SessionRecorder.close()`."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # A restart opens a new stream, which PortAudio backs with a new OS
        # thread -- the old thread's SCHED_FIFO grant does not carry over,
        # so the next `ensure_started()` must ask again and re-report.
        self.realtime_priority_active = None
        self.realtime_priority_message = ""
        self.voices.clear()
        self.effects.reset()
        if self.graph is not None:
            self.graph.all_notes_off()
        with self._pending_lock:
            self._pending_offs = {}

    def set_polyphony_override(self, value):
        """Point the voice manager's budget at something other than the
        two `polyphony_for()` context settings -- or back at them, with
        `None`. The one caller today is the synth tool, whose dual layout
        is a third context (see `synth_tool.polyphony_for_layout()`);
        keeping it an override rather than a third branch inside
        `polyphony_for()` means `sound_engine` stays ignorant of what a
        layout is."""
        return self.voices.set_polyphony(self._polyphony if value is None else value)

    # -- the effects bus (ticket #114) ------------------------------------

    def set_effects(self, chain):
        """Installs `chain` (an `effects.Effect`, normally an
        `EffectsChain`; `None` means an empty chain, i.e. no effects) as
        the one shared bus applied to the summed voice mix, after voice
        mixing and before the `np.tanh` soft-clip. #104 settled shared-bus
        over per-voice by arithmetic: both shipped effects are linear, so
        per-voice routing gives the identical signal at N times the cost
        and loses a delay's tail the moment its voice is released.

        `prepare()`d here against this engine's own sample rate and block
        size, then swapped in by a single attribute assignment -- the audio
        callback reads `self.effects` once per block, so a swap takes
        effect at the next block boundary with no lock and no torn state
        (the old chain, if mid-`process()`, finishes on its own object).
        Returns the installed chain."""
        if chain is None:
            chain = EffectsChain()
        chain.prepare(self.sample_rate, self.block_size)
        self.effects = chain
        return chain

    # -- the patch graph (ticket #207, decision 56 §7) ---------------------

    def set_graph(self, graph, activate=True):
        """Installs a `graph.poly.PolyGraph` whose output is added into the
        same mix the voices render into, before the effects bus and the
        soft-clip. `None` uninstalls it.

        **Additive, never replacing** (decision 56 §7). `VoiceManager` keeps
        its own voices, its own polyphony budget and its own note
        vocabulary; nothing about score-editor audition, frozen-buffer
        playback or QWERTY note entry changes because a graph is present.
        The graph owns its own sixteen voices and does its own mixing, so a
        graph note never consumes a `VoiceManager` slot and the two never
        argue about the budget. What they share is the output stream, the
        effects bus and the clip -- which is the point: one device, one
        master path.

        The install follows `set_effects()`'s idiom exactly, and for the
        same reason: `activate()` -- which allocates sixteen copies of the
        per-note subgraph and compiles the lot -- happens here, on the
        caller's thread, and the result is handed to the audio thread by a
        **single attribute store**. The callback reads `self.graph` once per
        block, so it sees the whole old graph or the whole new one and never
        a half-built anything. There is no lock and nothing to tear.

        `activate=False` is for a caller that has already activated the
        graph against this engine's sample rate and block size (the Synth
        View's bridge does, because it wants an activation failure reported
        in its own status bar rather than raised out of here).
        """
        if graph is not None and activate:
            from notecolor.audio.graph.contract import Activation

            graph.activate(Activation(self.sample_rate, self.block_size))
        self.graph = graph
        return graph

    # -- the note vocabulary ----------------------------------------------

    def note_on(self, event, velocity=1.0, channel=0, patch=None):
        """Starts a note. `event` is either a `NoteOn` or a bare MIDI
        pitch (in which case the remaining arguments fill in the rest) --
        the bare form exists so simple call sites don't have to import
        the dataclass to play one note. Returns the voice id, which
        `release_voice()`/`schedule_note_off()` take."""
        if not isinstance(event, NoteOn):
            event = NoteOn(int(event), velocity, channel, patch)
        voice = self.engine.note_on(event, self.sample_rate)
        return self.voices.allocate(voice, event.pitch, event.channel)

    def note_off(self, pitch, channel=0):
        return self.voices.note_off(pitch, channel)

    def release_voice(self, voice_id):
        with self._pending_lock:
            self._pending_offs.pop(voice_id, None)
        return self.voices.release_voice(voice_id)

    def all_notes_off(self):
        """Panic. Covers the graph's own voices too, which `VoiceManager`
        knows nothing about -- a panic that silenced only half of what is
        sounding would be the worst possible bug in a panic button."""
        with self._pending_lock:
            self._pending_offs = {}
        if self.graph is not None:
            self.graph.all_notes_off()
        return self.voices.all_notes_off()

    def schedule_note_off(self, voice_id, delay_seconds):
        """Caller-side sugar for 'this note lasts N seconds': records a
        deadline the audio callback releases against. Not a
        duration-carrying primitive in the voice model (#105 decision 1)
        -- the voice itself still only ever learns note-on and note-off;
        this just spares every duration-knowing caller from writing its
        own timer thread."""
        deadline = self._frame_clock + max(0.0, delay_seconds) * self.sample_rate
        with self._pending_lock:
            self._pending_offs[voice_id] = deadline
        return deadline

    def _resolve_due_offs(self, frame_clock):
        """Releases every voice whose scheduled note-off deadline has
        passed. Pure enough to call directly in a test -- the callback's
        only other job is mixing."""
        with self._pending_lock:
            due = [voice_id for voice_id, deadline in self._pending_offs.items() if deadline <= frame_clock]
            for voice_id in due:
                del self._pending_offs[voice_id]
        for voice_id in due:
            self.voices.release_voice(voice_id)
        return due

    # -- realtime scheduling and the CPU governor (issue #232, decision 70) --

    def _check_cpu_governor(self):
        """Read-only, called once from `ensure_started()` before the stream
        opens -- see `_detect_cpu_governor()`. Never touches the setting."""
        self.cpu_governor, self.cpu_governor_message = _detect_cpu_governor()
        if self.cpu_governor_message:
            print(f"[audio] {self.cpu_governor_message}")
        return self.cpu_governor

    def _ensure_realtime_priority(self):
        """Requests `SCHED_FIFO` for whichever thread calls this, the first
        time it's called -- see `_try_realtime_scheduling()`. Called from
        `_callback()` itself, since that is the only thread this can ever
        mean. Idempotent: `realtime_priority_active` starts `None`
        ("not attempted") and is only ever set once, to `True` or `False`."""
        if self.realtime_priority_active is not None:
            return
        self.realtime_priority_active, self.realtime_priority_message = _try_realtime_scheduling()
        if self.realtime_priority_message:
            print(f"[audio] {self.realtime_priority_message}")

    # -- the audio callback ------------------------------------------------

    def set_block_listener(self, listener):
        """Call `listener(frames)` once per audio block, before rendering.

        This is how the transport gets driven by the clock that actually
        produces sound (map #145, ticket #151): the callback owns time, so
        anything that needs to advance with it -- the playhead, a project's
        note schedule -- hangs off here rather than off a timer thread that
        would drift against the audio.

        The listener runs inside the callback, so it must not block, allocate
        heavily, or raise. It is called before `render_block()` specifically so
        a scheduler's `note_on()` for this block is audible in *this* block
        rather than the next one. An exception is swallowed and counted: a bug
        in a scheduler must not take the audio device down mid-performance.
        """
        self._block_listener = listener

    def _callback(self, outdata, frames, time_info, status):
        self._ensure_realtime_priority()
        if status:
            self.callback_status_count += 1
        listener = self._block_listener
        if listener is not None:
            try:
                listener(frames)
            except Exception:
                self.block_listener_error_count += 1
        self._frame_clock += frames
        self._resolve_due_offs(self._frame_clock)
        mix = np.zeros(frames, dtype=np.float32)
        self.voices.render_block(mix, frames)
        # Read once, into a local: a swap from `set_graph()` lands between
        # blocks, never inside one (#207).
        graph = self.graph
        if graph is not None:
            block = graph.output_block(frames)
            # float64 -> the mix's float32, through a buffer that already
            # exists. `np.add` straight from the float64 array would make
            # NumPy build a casting buffer inside the callback.
            scratch = self._graph_scratch[:frames]
            np.copyto(scratch, block[:frames], casting="same_kind")
            np.add(mix, scratch, out=mix)
        mix = self.effects.process(mix)   # the shared bus (#114), before the clip
        outdata[:, 0] = np.tanh(mix)


def _default_engine():
    """The engine a `SoundEngine` uses when its caller names none.
    Imported lazily so this module stays importable (and unit-testable)
    without pulling in a concrete engine's own dependencies -- the same
    reason `detection_backends.default_pitch_backend()` exists as a
    function rather than a module-level constant. Ticket #113's
    subtractive synth (`synth_engine.SynthEngine`) is the default; it
    needs SciPy (the `[synth]` extra, #111) and raises
    `synth_engine.SynthUnavailable` with the install line when that is
    missing, rather than falling back to anything filterless.
    `tone_engine.ToneEngine` (#112's interim engine) remains available
    to any caller that names it explicitly."""
    from notecolor.audio.synth_engine import SynthEngine

    return SynthEngine()
