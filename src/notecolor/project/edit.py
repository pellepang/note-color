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
        self.clip.start_beat = beat
        if source != target:
            self.project.tracks[source].clips.remove(self.clip)
            self.project.tracks[target].clips.append(self.clip)

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
        self.project.tracks.remove(self.track)


class RemoveTrack(Command):
    name = "Remove Track"

    def __init__(self, project, index):
        self.project = project
        self.index = index
        self.track = project.tracks[index]

    def do(self):
        self.project.tracks.remove(self.track)

    def undo(self):
        # Back where it was, not on the end -- a track that reappears in a
        # different lane is a worse surprise than no undo at all.
        self.project.tracks.insert(self.index, self.track)


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
