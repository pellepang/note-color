"""Undoable Project edits (map #145).

Command objects rather than snapshots: `score_editor_state.EditHistory`'s
`deepcopy` works only because an `EditorScore` is small, and a Project
references audio files. These tests pin the behaviour that makes commands
worth the extra shape -- exact reversal, a name, and a redo that cannot pick
up state that moved underneath it.
"""

import pytest

from notecolor.project import edit
from notecolor.project.model import Note, NoteClip, Project, Track


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


def test_time_signature_edits_round_trip():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetTimeSignature(project, 3, 8))
    assert (project.time_signature.numerator, project.time_signature.denominator) == (3, 8)
    stack.undo()
    assert (project.time_signature.numerator, project.time_signature.denominator) == (4, 4)


def test_key_edits_change_fifths_and_mode_together_and_undo_together():
    project = _project()
    stack = edit.EditStack()
    stack.run(edit.SetKey(project, -3, "minor"))
    assert (project.key_fifths, project.key_mode) == (-3, "minor")
    stack.undo()
    assert (project.key_fifths, project.key_mode) == (0, "major")


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


def _clip():
    return NoteClip(name="melody", notes=[Note(start_beat=0.0, duration_beats=1.0, pitch=60)])


def test_add_note_round_trips():
    clip = _clip()
    note = Note(start_beat=2.0, duration_beats=0.5, pitch=64)
    stack = edit.EditStack()
    stack.run(edit.AddNote(clip, note))
    assert note in clip.notes and len(clip.notes) == 2
    stack.undo()
    assert note not in clip.notes and len(clip.notes) == 1
    stack.redo()
    assert note in clip.notes and len(clip.notes) == 2


def test_add_note_undo_removes_the_right_instance_among_value_equal_notes():
    """Two freshly-added default notes are value-equal; undo must remove the
    one that was actually added, by identity, not whichever `==` matches
    first (the bug `remove_exact` exists to prevent -- see its docstring)."""
    clip = NoteClip(name="melody")
    first = Note(start_beat=0.0, duration_beats=1.0, pitch=60)
    second = Note(start_beat=0.0, duration_beats=1.0, pitch=60)
    assert first == second and first is not second
    stack = edit.EditStack()
    stack.run(edit.AddNote(clip, first))
    stack.run(edit.AddNote(clip, second))
    stack.undo()
    assert clip.notes == [first]
    assert clip.notes[0] is first


def test_delete_note_round_trips_back_to_its_index():
    clip = _clip()
    extra = Note(start_beat=1.0, duration_beats=1.0, pitch=62)
    clip.notes.append(extra)
    target = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.DeleteNote(clip, target))
    assert target not in clip.notes and clip.notes == [extra]
    stack.undo()
    assert clip.notes[0] is target and clip.notes[1] is extra
    stack.redo()
    assert clip.notes == [extra]


def test_delete_note_identity_safety_among_value_equal_notes():
    clip = NoteClip(name="melody")
    first = Note(start_beat=0.0, duration_beats=1.0, pitch=60)
    second = Note(start_beat=0.0, duration_beats=1.0, pitch=60)
    clip.notes = [first, second]
    stack = edit.EditStack()
    stack.run(edit.DeleteNote(clip, first))
    assert clip.notes == [second] and clip.notes[0] is second
    stack.undo()
    assert clip.notes[0] is first and clip.notes[1] is second


def test_move_note_moves_time_only_when_no_pitch_given():
    clip = _clip()
    note = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.MoveNote(clip, note, 4.0))
    assert note.start_beat == 4.0 and note.pitch == 60
    stack.undo()
    assert note.start_beat == 0.0 and note.pitch == 60


def test_move_note_moves_time_and_pitch_together():
    clip = _clip()
    note = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.MoveNote(clip, note, 4.0, pitch=67))
    assert note.start_beat == 4.0 and note.pitch == 67
    stack.undo()
    assert note.start_beat == 0.0 and note.pitch == 60
    stack.redo()
    assert note.start_beat == 4.0 and note.pitch == 67


def test_move_note_cannot_go_before_the_start():
    clip = _clip()
    note = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.MoveNote(clip, note, -3.0))
    assert note.start_beat == 0.0


def test_resize_note_round_trips():
    clip = _clip()
    note = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.ResizeNote(clip, note, 2.0))
    assert note.duration_beats == 2.0
    stack.undo()
    assert note.duration_beats == 1.0
    stack.redo()
    assert note.duration_beats == 2.0


def test_resize_note_clamps_to_a_minimum_duration():
    clip = _clip()
    note = clip.notes[0]
    stack = edit.EditStack()
    stack.run(edit.ResizeNote(clip, note, 0.0))
    assert note.duration_beats == edit.ResizeNote.MIN_DURATION_BEATS
    stack.run(edit.ResizeNote(clip, note, -5.0))
    assert note.duration_beats == edit.ResizeNote.MIN_DURATION_BEATS
