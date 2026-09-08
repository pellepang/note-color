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


class _Schedule:
    """The flattened notes and the audible-track set, as one immutable pair.

    One object so a refresh is a single atomic rebind rather than two, which
    is what makes the audio callback's read of it coherent without a lock.
    """

    __slots__ = ("notes", "audible")

    def __init__(self, notes, audible):
        self.notes = notes
        self.audible = audible


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
        self.schedule = _Schedule(flatten(project), audible_tracks(project))
        self.notes_started = 0

    @property
    def notes(self):
        return self.schedule.notes

    @property
    def audible(self):
        return self.schedule.audible

    def refresh(self, project=None):
        """Re-read the project after an edit.

        Builds the new notes and audible-set locally and swaps them in with a
        **single** attribute assignment. Two assignments -- which this used to
        do -- leave a window in which the audio callback can see new notes
        against an old mute state; harmless (one block, ~10ms) but the whole
        seam is built on atomic snapshots, and one rebind costs nothing.

        `project=` swaps the project itself in the same single assignment,
        which is what File->Open needs: setting `.project` and then calling
        `refresh()` left the callback able to see a new tempo map against the
        old project's notes.
        """
        if project is not None:
            self.project = project
        self.schedule = _Schedule(flatten(self.project),
                                  audible_tracks(self.project))

    def on_block(self, frames):
        """The audio callback's per-block entry point.

        The transport hands back every beat window the block covered -- more
        than one when it crossed the loop point, however many times. Firing
        them all is what keeps a short loop from dropping whole passes.
        """
        for start_beat, end_beat in self.transport.process_block(frames):
            self._fire(start_beat, end_beat)

    def _fire(self, start_beat, end_beat):
        # One read of one attribute: `refresh()` swaps notes and the audible
        # set together as a single object, so a block can never see new notes
        # against an old mute state.
        schedule = self.schedule
        notes, audible = schedule.notes, schedule.audible
        tempo = self.project.tempo_map
        for note in notes:
            if note.start_beat >= end_beat:
                break                           # sorted, so nothing later is due
            if note.start_beat < start_beat or note.track_index not in audible:
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
