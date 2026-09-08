"""Playing a Project through the sound engine.

The join between three things that already exist: a `Project` (musical
content), a `Transport` (position, owned by the audio callback), and a
`SoundEngine` (voices and an output device). Nothing here owns a clock or a
thread -- it is called from the audio callback via
`SoundEngine.set_block_listener()`, once per block.

The shape follows from #151's seam: `Transport.process_block()` returns the
half-open beat window the block covers, and this module's whole job is to turn
that window into note-ons. Because it is a *window* and not a point, a note
cannot fall between two blocks -- which is the failure a per-frame or
per-timer scheduler makes and then has to paper over with lookahead.

Durations go out as `schedule_note_off()` deadlines resolved against the
callback's own frame clock (decision #105), so a note's length is as accurate
as the block size and needs no timer thread.
"""

from notecolor.project.model import AUDIO_TRACK, NoteClip


class ScheduledNote:
    """One note, flattened out of its clip onto the project timeline."""

    __slots__ = ("start_beat", "end_beat", "pitch", "velocity", "track_index")

    def __init__(self, start_beat, end_beat, pitch, velocity, track_index):
        self.start_beat = start_beat
        self.end_beat = end_beat
        self.pitch = pitch
        self.velocity = velocity
        self.track_index = track_index

    def __repr__(self):
        return (f"ScheduledNote(beat={self.start_beat:.3f}..{self.end_beat:.3f}, "
                f"pitch={self.pitch}, track={self.track_index})")


def flatten(project):
    """Every note in the project, in absolute beats, time-ordered.

    Clip-relative positions become project-relative here and nowhere else, so
    the rest of playback never has to remember which clip a note came from.
    Audio tracks are skipped: milestone 1 plays notes only, and an Audio clip
    with no rendering path would otherwise fail silently at playback time
    rather than visibly here.
    """
    notes = []
    for index, track in enumerate(project.tracks):
        if track.kind == AUDIO_TRACK:
            continue
        for clip in track.clips:
            if not isinstance(clip, NoteClip):
                continue
            for note in clip.notes:
                start = clip.start_beat + note.start_beat
                notes.append(ScheduledNote(
                    start, start + note.duration_beats,
                    note.pitch, note.velocity, index))
    notes.sort(key=lambda n: (n.start_beat, n.pitch))
    return notes


def audible_tracks(project):
    """Indices of tracks that should sound, honouring solo then mute.

    Solo wins: if anything is soloed, only soloed tracks sound, and a track
    that is both soloed and muted stays silent -- which is what every DAW
    does and what a player expects when they hit both by accident.
    """
    soloed = {i for i, t in enumerate(project.tracks) if t.soloed}
    candidates = soloed or set(range(len(project.tracks)))
    return {i for i in candidates if not project.tracks[i].muted}


class ProjectPlayer:
    """Drives a Project through a SoundEngine, one audio block at a time."""

    def __init__(self, project, engine, transport):
        self.project = project
        self.engine = engine
        self.transport = transport
        self.notes = flatten(project)
        self.audible = audible_tracks(project)
        self.notes_started = 0

    def refresh(self):
        """Re-read the project after an edit. Cheap enough to call on save or
        on a mute change; not called per block."""
        self.notes = flatten(self.project)
        self.audible = audible_tracks(self.project)

    def on_block(self, frames):
        """The audio callback's per-block entry point."""
        start_beat, end_beat = self.transport.process_block(frames)
        if end_beat == start_beat:
            return                              # stopped
        if end_beat > start_beat:
            self._fire(start_beat, end_beat)
        else:
            # The loop wrapped inside this block: the window is two pieces,
            # tail then head. Firing only one of them would drop notes at the
            # loop seam, which is exactly where a listener notices.
            self._fire(start_beat, self._loop_end())
            self._fire(self._loop_start(), end_beat)

    def _loop_start(self):
        return self.transport.snapshot().loop_start_beat

    def _loop_end(self):
        return self.transport.snapshot().loop_end_beat

    def _fire(self, start_beat, end_beat):
        tempo = self.project.tempo_map
        for note in self.notes:
            if note.start_beat >= end_beat:
                break                           # sorted, so nothing later is due
            if note.start_beat < start_beat or note.track_index not in self.audible:
                continue
            voice = self.engine.note_on(
                _note_on_event(note), velocity=note.velocity,
                channel=note.track_index % 16)
            self.notes_started += 1
            if voice is None:
                continue
            seconds = (tempo.beats_to_seconds(note.end_beat)
                       - tempo.beats_to_seconds(note.start_beat))
            self.engine.schedule_note_off(voice, max(0.01, seconds))


def _note_on_event(note):
    """A `sound_engine.NoteOn` for a scheduled note, built lazily so this
    module imports without the audio stack present."""
    from notecolor.audio.sound_engine import NoteOn

    return NoteOn(pitch=note.pitch, velocity=note.velocity)
