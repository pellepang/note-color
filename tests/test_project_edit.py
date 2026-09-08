"""Undoable Project edits (map #145).

Command objects rather than snapshots: `score_editor_state.EditHistory`'s
`deepcopy` works only because an `EditorScore` is small, and a Project
references audio files. These tests pin the behaviour that makes commands
worth the extra shape -- exact reversal, a name, and a redo that cannot pick
up state that moved underneath it.
"""

import pytest

from notecolor.project import edit
from notecolor.project.model import NoteClip, Project, Track


def _project():
    return Project(tracks=[
        Track(name="a", clips=[NoteClip(name="one", start_beat=0.0)]),
        Track(name="b", clips=[NoteClip(name="two", start_beat=4.0)]),
    ])


def test_mute_round_trips_and_carries_a_name():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetTrackMute(project.tracks[0], True))
    assert project.tracks[0].muted is True
    assert stack.undo_name() == "Mute a"
    stack.undo()
    assert project.tracks[0].muted is False


def test_redo_reapplies():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetTrackSolo(project.tracks[1], True))
    stack.undo()
    stack.redo()
    assert project.tracks[1].soloed is True


def test_a_new_edit_clears_the_redo_branch():
    """A branch nobody can reach is memory pretending to be history."""
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetTrackMute(project.tracks[0], True))
    stack.undo()
    assert stack.can_redo
    stack.run(edit.SetTrackSolo(project.tracks[0], True))
    assert not stack.can_redo


def test_removing_a_track_puts_it_back_where_it_was():
    """A track that reappears in a different lane is a worse surprise than no
    undo at all."""
    project = _project()
    project.tracks.append(Track(name="c"))
    stack = edit.EditStack()
    stack.run(edit.RemoveTrack(project, 1))
    assert [t.name for t in project.tracks] == ["a", "c"]
    stack.undo()
    assert [t.name for t in project.tracks] == ["a", "b", "c"]


def test_moving_a_clip_between_tracks_reverses_completely():
    project = _project()
    clip = project.tracks[0].clips[0]
    stack = edit.EditStack()
    stack.run(edit.MoveClip(project, 0, clip, 8.0, new_track_index=1))
    assert clip.start_beat == 8.0
    assert clip in project.tracks[1].clips and clip not in project.tracks[0].clips
    stack.undo()
    assert clip.start_beat == 0.0
    assert clip in project.tracks[0].clips and clip not in project.tracks[1].clips


def test_a_clip_cannot_be_moved_before_the_start():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.MoveClip(project, 1, project.tracks[1].clips[0], -12.0))
    assert project.tracks[1].clips[0].start_beat == 0.0


def test_tempo_edits_replace_the_whole_map_reversibly():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetTempo(project, 150.0))
    assert project.tempo_map.bpm_at(0) == 150.0
    stack.undo()
    assert project.tempo_map.bpm_at(0) == 120.0


def test_the_stack_is_bounded():
    project = _project()
    stack = edit.EditStack(depth=3)
    for i in range(10):
        stack.run(edit.RenameTrack(project.tracks[0], f"n{i}"))
    for _ in range(10):
        stack.undo()
    assert not stack.can_undo
    # Only the last three are reversible; the name is whatever the fourth-from-
    # last edit left, not the original.
    assert project.tracks[0].name == "n6"


def test_revision_moves_on_every_change_so_dirty_needs_no_diff():
    project = _project()
    stack = edit.EditStack()
    start = stack.revision
    stack.run(edit.SetTrackMute(project.tracks[0], True))
    assert stack.revision != start
    stack.undo()
    assert stack.revision != start          # undoing is also a change


def test_undo_on_an_empty_stack_is_a_no_op_not_an_error():
    stack = edit.EditStack()
    assert stack.undo() is None and stack.redo() is None
