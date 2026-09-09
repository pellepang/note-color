"""Bouncing a Synth-view take to a new Audio track (map #145, ticket #159).

**Capture** reuses `session_recorder.SessionRecorder.note_on()`/`.note_off()`
(decision #110) as-is -- it already fires on every note at zero extra
realtime cost, so `SynthTakeRecorder` below is a thin wrapper around one
private `SessionRecorder` instance and adds no new realtime-thread path at
all.

**Render happens after the take ends, off the realtime thread.**
`playback.render_offline()` is not reused here -- it is a toy synth that
wants a pre-quantized flat note list. Instead, `render_take()` replays the
captured event log through a *fresh, non-live* `sound_engine.VoiceManager`
driven by the take's actual `synth_engine` patch (real filter/envelope
chain), producing a plain NumPy buffer with nothing touching the audio
callback. Timing is intentionally unquantized: each event's raw `t` /
`duration_seconds` from the session log is preserved exactly, true to the
performance -- quantization (decision #110's own pipeline) only applies to
symbolic/editable import, and this produces an opaque audio clip, not
editable notes.

Block-granularity note-on/off scheduling (notes start/stop on a
`config.PLAYBACK_BLOCK_SIZE` boundary, never mid-block) is a deliberate
simplification, not an oversight: it is the same "a block, not a sample, is
the scheduling grain" precedent `audio/player.ProjectPlayer._fire()`
already sets for live playback, and it is inaudible next to the amp
envelope's own attack time.

**Storage** goes through `project.bundle.import_audio()` (#149/#154)
unmodified -- `bounce_take_to_track()` writes a temporary `.wav` (stdlib
`wave`, no new dependency), hands it to `import_audio()`, and builds one
`project.edit.AddTrack` around the resulting `AudioClip`. It does **not**
call `.do()` -- applying the command through the project's undo history is
the caller's job, back on the GUI thread, once a background render
completes (see `gui/synth_recording.py`).
"""

import json
import math
import os
import tempfile
import wave

import numpy as np

from notecolor.project import bundle
from notecolor.project import edit
from notecolor.project.model import AUDIO_TRACK, AudioClip, Track
from notecolor.settings import config

#: A bounced clip is never shorter than this -- an exact zero-length clip
#: (an empty or instantaneous take) would be invisible on the arrange
#: canvas and meaningless to play back. Same figure `edit.ResizeNote`
#: already uses for the same reason on the note side.
MIN_CLIP_LENGTH_BEATS = 0.05


# --------------------------------------------------------------------------
# Capture: SessionRecorder, reused as-is (decision #110)
# --------------------------------------------------------------------------

class SynthTakeRecorder:
    """Captures one Synth-view take via `session_recorder.SessionRecorder`,
    unmodified. Owns a private, temp-file-backed `SessionRecorder` instance
    per take (never the shared session log), so a bounced take never mixes
    into -- or depends on -- whatever `session_log_*.jsonl` a live view may
    also be writing.

    `note_on()`/`note_off()` are meant to be called from whatever thread
    plays the notes (today, the GUI thread) -- nothing here is realtime-safe
    machinery, and nothing here needs to be: capture is exactly as cheap as
    `SessionRecorder` already is, and the expensive part (`render_take()`)
    only happens once the take is over.
    """

    def __init__(self, path=None):
        if path is None:
            fd, path = tempfile.mkstemp(prefix="synth_take_", suffix=".jsonl")
            os.close(fd)
        from notecolor.notation.session_recorder import SessionRecorder

        self._recorder = SessionRecorder(path=path)
        self.patch = None
        self.start_beat = 0.0
        self.recording = False

    def start(self, patch, start_beat=0.0, now=None):
        """Arms a fresh take. `patch` is the take's own patch object (real
        filter/envelope chain) that `render_take()` will drive the offline
        re-render with; `start_beat` is the transport's current beat
        position at this exact moment (#151's snapshot) -- stamped onto the
        eventual `AudioClip.start_beat` uniformly whether the transport is
        playing or stopped, per #159."""
        self.patch = patch
        self.start_beat = float(start_beat)
        self.recording = True
        self._recorder.toggle()   # armed False -> True: opens this take's log

    def note_on(self, pitch, velocity=1.0, patch_name=None, now=None):
        """`pitch` is a MIDI note number, doubling as the pending-note key --
        adequate for one voice per pitch, which is what a QWERTY-style
        keyboard input can produce anyway. A no-op while not recording."""
        if not self.recording:
            return
        from notecolor.audio.sound_engine import pitch_class_octave

        pitch_class, octave = pitch_class_octave(pitch)
        self._recorder.note_on(pitch, pitch_class, octave, velocity=velocity,
                               patch=patch_name, now=now)

    def note_off(self, pitch, now=None):
        if not self.recording:
            return
        self._recorder.note_off(pitch, now=now)

    def stop(self, now=None):
        """Ends the take: finalizes any still-held note (a smaller lie than
        dropping it, per `SessionRecorder.close()`'s own contract), reads
        back every captured event, deletes the take's own temp log, and
        returns the events -- oldest first. Idempotent-ish: calling this
        with nothing armed returns an empty list rather than raising."""
        self.recording = False
        self._recorder.close(now=now)
        events = _read_events(self._recorder.path)
        try:
            if self._recorder.path and os.path.exists(self._recorder.path):
                os.remove(self._recorder.path)
        except OSError:
            pass
        return events


def _read_events(path):
    """Every JSON line in `path`, oldest (`t`) first. Missing file (never
    armed) or a stray unreadable line degrades to "skip it", matching this
    module's own read tolerance elsewhere in the app."""
    if not path or not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    events.sort(key=lambda event: event.get("t", 0.0))
    return events


# --------------------------------------------------------------------------
# Offline re-render (off the realtime thread entirely)
# --------------------------------------------------------------------------

def render_take(events, patch, sample_rate, block_size=None):
    """Replays a captured take's `events` (session-log dicts, as
    `SynthTakeRecorder.stop()` returns) through a fresh, non-live
    `sound_engine.VoiceManager`, driven by `patch`. Returns a mono
    `float32` buffer -- empty for an empty take.

    Not realtime-safe and not meant to be: this runs after the take ends,
    on whatever thread the caller chooses (a background thread from the
    GUI, per #159 -- see `gui/synth_recording.py`), never from the audio
    callback."""
    from notecolor.audio.sound_engine import NoteOn, VoiceManager, midi_pitch
    from notecolor.audio.synth_engine import SynthEngine

    if not events:
        return np.zeros(0, dtype=np.float32)

    block_size = block_size or config.PLAYBACK_BLOCK_SIZE
    total_seconds = max(
        float(event.get("t", 0.0)) + float(event.get("duration_seconds") or 0.0)
        for event in events
    )
    # One extra block's worth of tail so a note's release stage (which
    # outlives its own note-off) is not chopped off mid-fade.
    total_frames = int(math.ceil(total_seconds * sample_rate)) + block_size

    engine = SynthEngine(patch=patch)
    voices = VoiceManager(polyphony=config.POLYPHONY_STANDALONE)
    buffer = np.zeros(total_frames, dtype=np.float32)

    scheduled = []
    for event in events:
        pitch = midi_pitch(int(event["pc"]), int(event["octave"]))
        velocity = float(event.get("velocity") if event.get("velocity") is not None else 127) / 127.0
        onset_frame = max(0, int(round(float(event.get("t", 0.0)) * sample_rate)))
        duration_seconds = float(event.get("duration_seconds") or 0.0)
        off_frame = max(onset_frame + 1, int(round(
            (float(event.get("t", 0.0)) + duration_seconds) * sample_rate)))
        scheduled.append((onset_frame, 0, pitch, velocity))   # 0 sorts before 1: "on" before "off"
        scheduled.append((off_frame, 1, pitch, None))
    scheduled.sort(key=lambda item: (item[0], item[1]))

    voice_id_by_pitch = {}
    index, count = 0, len(scheduled)
    position = 0
    while position < total_frames:
        block_end = min(position + block_size, total_frames)
        while index < count and scheduled[index][0] < block_end:
            frame, kind, pitch, velocity = scheduled[index]
            if kind == 0:
                voice = engine.note_on(NoteOn(pitch=pitch, velocity=velocity), sample_rate)
                voice_id_by_pitch[pitch] = voices.allocate(voice, pitch=pitch)
            else:
                voice_id = voice_id_by_pitch.pop(pitch, None)
                if voice_id is not None:
                    voices.release_voice(voice_id)
            index += 1
        voices.render_block(buffer[position:block_end], block_end - position)
        position = block_end

    return buffer


def write_wav(buffer, sample_rate, path):
    """Writes a mono buffer (float, -1..1) to a 16-bit PCM `.wav` at `path`.
    Stdlib `wave` is enough for a bounce -- no new dependency for this."""
    clipped = np.clip(np.asarray(buffer, dtype=np.float64), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------
# Storage + track creation (safe to call off the GUI thread)
# --------------------------------------------------------------------------

def bounce_take_to_track(bundle_path, project, events, patch, sample_rate, start_beat,
                          track_name=None, block_size=None):
    """The full offline path (#159): render `events` through `patch`, copy
    the result into the project bundle via the existing `import_audio()`
    (#149/#154), and build one `edit.AddTrack(project, track=Track(kind=
    AUDIO_TRACK, clips=[audio_clip]))` around it.

    Returns the command **undispatched** -- `.do()` is never called here.
    This function only touches the filesystem and `import_audio()`'s own
    copy-into-bundle step, never the live `project` object, which is what
    makes it safe to run on a background thread: the caller applies the
    returned command through the project's undo history back on the GUI
    thread once this returns (see `gui/synth_recording.py`)."""
    buffer = render_take(events, patch, sample_rate, block_size=block_size)
    duration_seconds = buffer.shape[0] / float(sample_rate) if sample_rate else 0.0

    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_wav(buffer, sample_rate, temp_path)
        source_name = bundle.import_audio(bundle_path, temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    tempo = project.tempo_map
    start_seconds = tempo.beats_to_seconds(start_beat)
    length_beats = max(MIN_CLIP_LENGTH_BEATS,
                       tempo.seconds_to_beats(start_seconds + duration_seconds) - start_beat)

    name = track_name or "Synth Take"
    clip = AudioClip(
        name=name,
        start_beat=float(start_beat),
        length_beats=length_beats,
        source=source_name,
        source_offset_samples=0,
        source_length_samples=int(buffer.shape[0]),
    )
    track = Track(name=project.unique_track_name(name), kind=AUDIO_TRACK, clips=[clip])
    return edit.AddTrack(project, track=track)
