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

import threading

import numpy as np

import config
from config_store import store
from effects import EffectsChain


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
        self._stream = None
        self._frame_clock = 0
        self._pending_offs = {}
        self._pending_lock = threading.Lock()
        self.callback_status_count = 0

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
        self.voices.clear()
        self.effects.reset()
        with self._pending_lock:
            self._pending_offs = {}

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
        with self._pending_lock:
            self._pending_offs = {}
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

    # -- the audio callback ------------------------------------------------

    def _callback(self, outdata, frames, time_info, status):
        if status:
            self.callback_status_count += 1
        self._frame_clock += frames
        self._resolve_due_offs(self._frame_clock)
        mix = np.zeros(frames, dtype=np.float32)
        self.voices.render_block(mix, frames)
        mix = self.effects.process(mix)   # the shared bus (#114), before the clip
        outdata[:, 0] = np.tanh(mix)


def _default_engine():
    """The engine a `SoundEngine` uses when its caller names none.
    Imported lazily so this module stays importable (and unit-testable)
    without pulling in a concrete engine's own dependencies -- the same
    reason `detection_backends.default_pitch_backend()` exists as a
    function rather than a module-level constant. Ticket #113's
    subtractive synth becomes the default here once it lands; until
    then it is `tone_engine.ToneEngine`, which is map #24's existing
    harmonic-stack+ADSR voice made note-on/note-off-shaped."""
    from tone_engine import ToneEngine

    return ToneEngine()
