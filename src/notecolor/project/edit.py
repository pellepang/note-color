"""Editing a Project, undoably (map #145).

Every mutation of a Project goes through a **command object** -- a small thing
that knows how to do itself and how to undo itself. That was decided rather
than assumed (#145): snapshotting, which `score_editor_state.EditHistory`
does, works there only because an `EditorScore` is small and has no music21
graph attached. A Project references audio files and will grow clips by the
hundred, so `copy.deepcopy` per keystroke stops being viable.

Commands buy two other things beyond undo, and both matter more than they
look:

- **A name.** "Undo Move Clip" is only possible if the edit knew what it was.
- **A count.** #132's evaluation metric is "editor operations to correct",
  which `evaluate.py` currently *infers* from a diff. With commands it can be
  observed instead.

This lives in `project/` rather than `gui/` on purpose: it is core, so the
terminal front-end can drive the same edits, and `tests/test_package_boundary`
keeps Qt out of it.
"""

from notecolor.project.model import NoteClip, Track


def remove_exact(items, target):
    """Remove `target` from `items` **by identity**, and return its index.

    `list.remove()` matches by `==`, and `Track`/`NoteClip`/`AudioClip` are
    plain dataclasses with generated value equality -- so two clips that merely
    look alike are interchangeable to it. That is not theoretical: `AddTrack`
    creates every new track with an identical `NoteClip(name="empty",
    length_beats=4.0)`, so two fresh tracks already hold value-equal clips.
    Moving one of them then removed the *other* (whichever came first in the
    list) and left the moved clip present in both tracks -- one clip silently
    deleted, another aliased across two tracks, so editing it in one place
    edited it in the other.

    Identity is the only correct answer here: these objects are things, not
    values.
    """
    for index, item in enumerate(items):
        if item is target:
            del items[index]
            return index
    raise ValueError("object is not in the list")


class Command:
    """One undoable edit.

    `do()` must be safe to call again after `undo()` -- redo is just `do()`.
    Commands capture whatever they need to reverse themselves *at construction
    time*, not at `do()` time, so a redo cannot pick up state that changed
    underneath it.
    """

    name = "Edit"

    def do(self):
        raise NotImplementedError

    def undo(self):
        raise NotImplementedError


class _SetAttribute(Command):
    """Shared shape for 'change one field on one object'."""

    def __init__(self, target, attribute, value, name=None):
        self.target = target
        self.attribute = attribute
        self.value = value
        self.previous = getattr(target, attribute)
        if name:
            self.name = name

    def do(self):
        setattr(self.target, self.attribute, self.value)

    def undo(self):
        setattr(self.target, self.attribute, self.previous)


class SetTrackMute(_SetAttribute):
    def __init__(self, track, value):
        super().__init__(track, "muted", value,
                         f"{'Mute' if value else 'Unmute'} {track.name}")


class SetTrackSolo(_SetAttribute):
    def __init__(self, track, value):
        super().__init__(track, "soloed", value,
                         f"{'Solo' if value else 'Unsolo'} {track.name}")


class RenameTrack(_SetAttribute):
    def __init__(self, track, value):
        super().__init__(track, "name", value, "Rename Track")


class SetTrackPatch(_SetAttribute):
    """Assigns (or clears, with `value=None`) the bare patch name a track
    plays through (#156). `ProjectPlayer` resolves the name to a loaded
    `Patch` lazily at playback time, so this command only ever touches the
    plain string on the model."""

    def __init__(self, track, value):
        super().__init__(track, "patch_name", value, f"Set Patch: {track.name}")


class SetTempo(Command):
    name = "Set Tempo"

    def __init__(self, project, bpm):
        from notecolor.project.model import TempoAnchor, TempoMap

        self.project = project
        self.previous = project.tempo_map
        self.tempo_map = TempoMap([TempoAnchor(0.0, float(bpm))])

    def do(self):
        self.project.tempo_map = self.tempo_map

    def undo(self):
        self.project.tempo_map = self.previous


class SetTimeSignature(Command):
    name = "Set Time Signature"

    def __init__(self, project, numerator, denominator):
        from notecolor.project.model import TimeSignature

        self.project = project
        self.previous = project.time_signature
        self.time_signature = TimeSignature(int(numerator), int(denominator))

    def do(self):
        self.project.time_signature = self.time_signature

    def undo(self):
        self.project.time_signature = self.previous


class SetKey(Command):
    """Sets `key_fifths` and `key_mode` together as one undo step -- a key
    change is one musical fact, not two independent field edits, so undo
    should not be able to leave fifths and mode from different keys."""

    name = "Set Key"

    def __init__(self, project, key_fifths, key_mode):
        self.project = project
        self.previous_fifths = project.key_fifths
        self.previous_mode = project.key_mode
        self.key_fifths = int(key_fifths)
        self.key_mode = key_mode

    def do(self):
        self.project.key_fifths = self.key_fifths
        self.project.key_mode = self.key_mode

    def undo(self):
        self.project.key_fifths = self.previous_fifths
        self.project.key_mode = self.previous_mode


class MoveClip(Command):
    name = "Move Clip"

    def __init__(self, project, track_index, clip, beat, new_track_index=None):
        self.project = project
        self.clip = clip
        self.from_track = track_index
        self.to_track = track_index if new_track_index is None else new_track_index
        self.from_beat = clip.start_beat
        self.to_beat = max(0.0, float(beat))

    def _move(self, source, target, beat):
        # Remove *before* mutating the clip: `remove_exact` is identity-based
        # so order no longer matters for correctness, but a half-moved clip is
        # never observable this way either.
        if source != target:
            remove_exact(self.project.tracks[source].clips, self.clip)
            self.project.tracks[target].clips.append(self.clip)
        self.clip.start_beat = beat

    def do(self):
        self._move(self.from_track, self.to_track, self.to_beat)

    def undo(self):
        self._move(self.to_track, self.from_track, self.from_beat)


class AddTrack(Command):
    name = "Add Track"

    def __init__(self, project, track=None):
        self.project = project
        self.track = track or Track(name=project.unique_track_name("Track"),
                                    clips=[NoteClip(name="empty", length_beats=4.0)])

    def do(self):
        self.project.tracks.append(self.track)

    def undo(self):
        remove_exact(self.project.tracks, self.track)


class RemoveTrack(Command):
    name = "Remove Track"

    def __init__(self, project, index):
        self.project = project
        self.index = index
        self.track = project.tracks[index]

    def do(self):
        remove_exact(self.project.tracks, self.track)

    def undo(self):
        # Back where it was, not on the end -- a track that reappears in a
        # different lane is a worse surprise than no undo at all.
        self.project.tracks.insert(self.index, self.track)


class AddNote(Command):
    name = "Add Note"

    def __init__(self, clip, note):
        self.clip = clip
        self.note = note

    def do(self):
        self.clip.notes.append(self.note)

    def undo(self):
        remove_exact(self.clip.notes, self.note)


class DeleteNote(Command):
    name = "Delete Note"

    def __init__(self, clip, note):
        self.clip = clip
        self.note = note
        # Identity-based, like `remove_exact` -- `list.index()` matches by
        # `==` and would find the wrong note among value-equal ones.
        self.index = next((i for i, n in enumerate(clip.notes) if n is note),
                          len(clip.notes))

    def do(self):
        remove_exact(self.clip.notes, self.note)

    def undo(self):
        # Clamp rather than trust the captured index: EditStack usage never
        # interleaves other edits between an undo and its matching redo, but
        # a stale index should degrade to "append" instead of crashing.
        index = min(self.index, len(self.clip.notes))
        self.clip.notes.insert(index, self.note)


class MoveNote(Command):
    """Move a note in time and, optionally, in pitch -- one drag, one command.

    `pitch=None` means the gesture was time-only (e.g. a keyboard nudge);
    leaving `self.pitch` `None` too means `do`/`undo` never touch
    `note.pitch`, rather than round-tripping it through its own unchanged
    value.
    """

    name = "Move Note"

    def __init__(self, clip, note, start_beat, pitch=None):
        self.clip = clip
        self.note = note
        self.from_beat = note.start_beat
        self.to_beat = max(0.0, float(start_beat))
        self.from_pitch = note.pitch
        self.to_pitch = pitch

    def do(self):
        self.note.start_beat = self.to_beat
        if self.to_pitch is not None:
            self.note.pitch = self.to_pitch

    def undo(self):
        self.note.start_beat = self.from_beat
        if self.to_pitch is not None:
            self.note.pitch = self.from_pitch


class ResizeNote(_SetAttribute):
    name = "Resize Note"

    #: Short enough to be "as short as it gets" but never zero -- a
    #: zero-length note is invisible on the piano roll and meaningless to
    #: play back.
    MIN_DURATION_BEATS = 0.05

    def __init__(self, clip, note, duration_beats):
        # `clip` isn't needed to mutate `note`, but every note command takes
        # it -- the GUI always has a clip in hand and a consistent shape
        # means it doesn't need to special-case this one.
        self.clip = clip
        super().__init__(note, "duration_beats",
                         max(self.MIN_DURATION_BEATS, float(duration_beats)),
                         "Resize Note")


class EditStack:
    """Undo/redo over commands.

    Bounded like `config.EDITOR_UNDO_MAX_DEPTH`, and the same convention: a new
    edit clears the redo stack, because a branch nobody can reach is just
    memory pretending to be history.
    """

    def __init__(self, depth=50):
        self.depth = depth
        self._done = []
        self._undone = []
        #: Bumped on every change. A window can compare it against the value at
        #: last save to answer "is this dirty" without diffing the project.
        self.revision = 0

    def run(self, command):
        command.do()
        self._done.append(command)
        if len(self._done) > self.depth:
            del self._done[:-self.depth]
        self._undone.clear()
        self.revision += 1
        return command

    def undo(self):
        if not self._done:
            return None
        command = self._done.pop()
        command.undo()
        self._undone.append(command)
        self.revision += 1
        return command

    def redo(self):
        if not self._undone:
            return None
        command = self._undone.pop()
        command.do()
        self._done.append(command)
        self.revision += 1
        return command

    @property
    def can_undo(self):
        return bool(self._done)

    @property
    def can_redo(self):
        return bool(self._undone)

    def undo_name(self):
        return self._done[-1].name if self._done else None

    def redo_name(self):
        return self._undone[-1].name if self._undone else None
