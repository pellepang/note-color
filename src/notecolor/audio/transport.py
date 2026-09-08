"""The seam between the core and a front-end (map #145, ticket #151).

One rule, and everything here follows from it: **the audio callback owns
time.** The sample counter it advances is the single truth about where
playback is. A UI never sets the position and never reads engine state
directly -- it *sends commands* down a queue and *reads a snapshot* the audio
thread published. This formalises decision #105, which already resolves
`SoundEngine.schedule_note_off()` against the callback's own frame clock
rather than a timer thread.

Why not the obvious alternative: if a GUI timer drove the transport, the
playhead would be pointing at a position derived from a clock that is not the
one producing the sound. The two drift, and they drift *visibly* -- a playhead
that lags or leads the audio is the thing every DAW is judged on first.

The traffic across the seam is deliberately one-way in each direction:

    UI  --- Command ---->  [queue]  ---> audio callback
    UI  <-- Snapshot ----  [slot]   <--- audio callback

Both directions are lock-free, and neither blocks the other. A snapshot is
immutable and published by a single attribute assignment, which is atomic
under CPython -- so a reader gets one coherent snapshot or the previous one,
never a half-updated mixture. The queue is a `deque`, whose `append` and
`popleft` are individually atomic for the same reason. This is the same
GIL-based argument `main.ReanalysisBuffer` already relies on (issue #77), and
deliberately *not* the case `VoiceManager` needed a real lock for -- that one
does a read-modify-write across a whole list every block, which is not a
single atomic operation.

**One honest caveat.** The stated ideal is "no locks, no allocation, no Python
object churn in the callback". The first is achieved. The second is not, and
cannot be: publishing an immutable snapshot allocates it, and in CPython there
is no way around that. It is small, short-lived, and happens once per block
rather than per sample -- but it is real, and it is one of the specific reasons
map #145 keeps the audio engine behind a seam that a C implementation can take
over. Better to write that down than to claim a guarantee this cannot make.
"""

import math
from collections import deque
from dataclasses import dataclass, replace
from typing import Optional

from notecolor.project.model import TempoMap

STOPPED = "stopped"
PLAYING = "playing"


# --------------------------------------------------------------------------
# UI -> engine: commands
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """Base class. Commands are immutable, and are applied at a block
    boundary rather than the instant they are sent -- which is what makes a
    locate mid-playback well-defined instead of a race."""


@dataclass(frozen=True)
class Play(Command):
    pass


@dataclass(frozen=True)
class Stop(Command):
    #: Whether to return to where playback began, the way a DAW's stop button
    #: does on a second press. The transport stores that origin so the UI does
    #: not have to track it.
    to_start: bool = False


@dataclass(frozen=True)
class Locate(Command):
    """Move the playhead. Expressed in *beats*, because musical position is
    what a UI has -- the conversion to samples belongs on this side of the
    seam, against the tempo map the transport already holds."""

    beat: float


@dataclass(frozen=True)
class SetLoop(Command):
    start_beat: float = 0.0
    end_beat: float = 0.0
    enabled: bool = False


@dataclass(frozen=True)
class SetTempoMap(Command):
    tempo_map: Optional[TempoMap] = None


class CommandQueue:
    """Single-producer, single-consumer, lock-free, and **never drops**.

    Unbounded on purpose. `VoiceManager` never refuses a note because dropping
    the note someone just played is the most audible possible failure; the same
    logic applies harder here, since the dropped item might be the Stop that
    was supposed to end playback. Commands arrive at UI rates -- a keypress, a
    click -- so unbounded growth is not a real risk, and a bounded queue would
    trade a theoretical memory bound for a stuck transport.
    """

    def __init__(self):
        self._items = deque()

    def send(self, command):
        self._items.append(command)

    def drain(self):
        """Every command sent since the last call, oldest first."""
        drained = []
        while True:
            try:
                drained.append(self._items.popleft())
            except IndexError:
                return drained

    def __len__(self):
        return len(self._items)


# --------------------------------------------------------------------------
# engine -> UI: the snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportSnapshot:
    """What the UI is allowed to know about playback.

    Immutable, and carries *both* sample position and beat position: the UI
    needs beats to draw a ruler and samples to be honest about what the engine
    actually did. Deriving one from the other in the UI would mean the UI
    holding a second copy of the tempo map, which is exactly the kind of
    duplicated state that drifts.
    """

    state: str = STOPPED
    frame: int = 0                  # samples since playback began at frame 0
    beat: float = 0.0
    seconds: float = 0.0
    bpm: float = 120.0
    sample_rate: int = 48000
    block_count: int = 0
    #: Blocks the callback failed to service in time, as reported by the
    #: driver. Surfaced because #100 measured that the ring buffer hides
    #: overruns until the engine has *already* xrun -- so this is the only
    #: honest signal a UI can show about whether audio is keeping up.
    xruns: int = 0
    loop_enabled: bool = False
    loop_start_beat: float = 0.0
    loop_end_beat: float = 0.0

    @property
    def playing(self):
        return self.state == PLAYING


class SnapshotSlot:
    """A single-slot mailbox: the audio thread writes, any number of readers
    read, nobody waits.

    Publishing is one attribute assignment, which is atomic under CPython, so
    a reader sees either the new snapshot whole or the previous one whole.
    There is no tearing to guard against and therefore no lock to take -- and
    no lock is what matters, because a lock in an audio callback is a lock the
    UI thread can make the audio thread wait on.
    """

    def __init__(self, initial=None):
        self._value = initial or TransportSnapshot()

    def publish(self, snapshot):
        self._value = snapshot

    def read(self):
        return self._value


# --------------------------------------------------------------------------
# The transport itself
# --------------------------------------------------------------------------


class Transport:
    """Owns the playback position. Driven from the audio callback.

    `process_block(frames)` is the only method the audio thread calls. It
    drains pending commands, advances the position by exactly the number of
    frames the driver asked for, publishes a snapshot, and returns the
    **half-open beat window** `[start, end)` that the block covers -- which is
    what a scheduler needs to decide which notes fall due, and is expressed as
    a window rather than a point precisely so nothing between two blocks can be
    missed.
    """

    def __init__(self, tempo_map=None, sample_rate=48000, snapshot_slot=None,
                 command_queue=None):
        self.tempo_map = tempo_map or TempoMap()
        self.sample_rate = int(sample_rate)
        self.commands = command_queue or CommandQueue()
        self.snapshots = snapshot_slot or SnapshotSlot()
        self._frame = 0
        self._state = STOPPED
        self._play_origin_frame = 0
        self._loop = (0.0, 0.0, False)
        self._block_count = 0
        self._xruns = 0
        self._publish()

    # -- position ----------------------------------------------------------

    @property
    def frame(self):
        return self._frame

    @property
    def beat(self):
        return self.tempo_map.seconds_to_beats(self._frame / self.sample_rate)

    def _frame_for_beat(self, beat):
        """The frame a musical position starts on.

        **Floor, not round**, and this is load-bearing rather than a detail.
        `Locate` stores a frame, and `beat` converts that frame back through
        the tempo map -- a round trip that is not idempotent. With `round()`
        the result lands *above* the requested beat about half the time (a
        measured 1502 of 3000 random tempo/rate/beat triples), by 1e-8 to 1e-4
        of a beat. That is invisible on a ruler and fatal to playback:
        `ProjectPlayer` fires a note when `note.start_beat >= window_start`, so
        a note sitting exactly on the beat you located to falls outside the
        first window and never sounds. Locating onto a note and hearing it
        became a coin flip -- on the one operation a DAW is judged on.

        Flooring makes the round trip land at or just below the target, so a
        note on that beat is always inside the window. The cost is at most one
        sample of position error, which is 20 microseconds.
        """
        return int(math.floor(self.tempo_map.beats_to_seconds(beat) * self.sample_rate))

    # -- the audio thread --------------------------------------------------

    def process_block(self, frames, xruns=None):
        """Advance one block. Called from the audio callback, and only there."""
        self._apply(self.commands.drain())
        start_beat = self.beat
        if self._state == PLAYING:
            self._frame += int(frames)
            start, end, enabled = self._loop
            if enabled and end > start:
                # `while`, not `if`: one block can span a loop shorter than
                # itself (a one-beat loop at a fast tempo is under 512 frames),
                # and a single wrap would leave the position past the loop end
                # for good -- the loop would simply stop looping. The overshoot
                # is carried rather than discarded each time round, so a loop
                # does not gain a fraction of a block on every repeat.
                loop_frames = self._frame_for_beat(end) - self._frame_for_beat(start)
                guard = 0
                while loop_frames > 0 and self.beat >= end and guard < 1024:
                    self._frame -= loop_frames
                    guard += 1
        if xruns is not None:
            self._xruns = int(xruns)
        self._block_count += 1
        self._publish()
        return (start_beat, self.beat)

    def _apply(self, commands):
        for command in commands:
            if isinstance(command, Play):
                if self._state != PLAYING:
                    self._play_origin_frame = self._frame
                self._state = PLAYING
            elif isinstance(command, Stop):
                self._state = STOPPED
                if command.to_start:
                    self._frame = self._play_origin_frame
            elif isinstance(command, Locate):
                self._frame = max(0, self._frame_for_beat(command.beat))
                self._play_origin_frame = self._frame
            elif isinstance(command, SetLoop):
                self._loop = (command.start_beat, command.end_beat, command.enabled)
            elif isinstance(command, SetTempoMap) and command.tempo_map is not None:
                # Locate to the same *musical* position under the new map, not
                # the same sample -- changing the tempo moves the music, it
                # does not teleport the playhead into a different bar.
                beat = self.beat
                self.tempo_map = command.tempo_map
                self._frame = max(0, self._frame_for_beat(beat))

    def _publish(self):
        start, end, enabled = self._loop
        self.snapshots.publish(TransportSnapshot(
            state=self._state,
            frame=self._frame,
            beat=self.beat,
            seconds=self._frame / self.sample_rate,
            bpm=self.tempo_map.bpm_at(self.beat),
            sample_rate=self.sample_rate,
            block_count=self._block_count,
            xruns=self._xruns,
            loop_enabled=enabled,
            loop_start_beat=start,
            loop_end_beat=end,
        ))

    # -- the UI thread -----------------------------------------------------

    def play(self):
        self.commands.send(Play())

    def stop(self, to_start=False):
        self.commands.send(Stop(to_start=to_start))

    def locate(self, beat):
        self.commands.send(Locate(float(beat)))

    def set_loop(self, start_beat, end_beat, enabled=True):
        self.commands.send(SetLoop(float(start_beat), float(end_beat), bool(enabled)))

    def set_tempo_map(self, tempo_map):
        self.commands.send(SetTempoMap(tempo_map))

    def snapshot(self):
        """What the UI reads, once per frame. Never blocks."""
        return self.snapshots.read()
