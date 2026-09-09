"""The piano-roll panel -- a dedicated, resizable bottom pane for editing one
track's notes (map #145 milestone 2, reversing #155 after hands-on use --
see `docs/decisions/52-the-piano-roll-editing-surface-reversed-into-its-own-panel-issue-155-reopened-by-hands-on-use.md`).

Deliberately its own `QGraphicsView`/`QGraphicsScene`, not a zoomed-in row of
`gui/studio.py`'s `ArrangeScene`: the previous inline approach grew a track's
row height in place and routed every edit through `StudioWindow.keyPressEvent()`
globally, so the piano roll's own arrow-key/Space/Enter shortcuts collided
with the main window's always-live transport/track-select bindings (Up/Down
track select, Left/Right seek, Space play/stop). Giving this panel its own
widget with its own keyboard focus fixes that at the root: Qt delivers a key
event to whichever widget currently holds focus, and an event a widget does
not accept propagates to its parent -- so this view's `keyPressEvent()` only
has to accept the keys it actually uses (arrows, Space, Enter, Delete/
Backspace) while a track is open, and let everything else (Ctrl+S, Ctrl+Z, M,
S, L, ...) fall through to `StudioWindow`'s own handler unchanged.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.gui import theme
from notecolor.notation.score_audition import (PIANO_LOWER_ROW, PIANO_UPPER_ROW,
                                                clamp_base_octave, pitch_for_key)
from notecolor.project import edit
from notecolor.project.model import (Note, NoteClip, chromatic_note_names,
                                     diatonic_pitch_classes)
from notecolor.settings import config

#: Default pixel-per-semitone scale, same value the inline piano roll used --
#: now a `PianoRollScene` instance attribute (`px_per_semitone`) rather than
#: a constant, so zooming can change it; this is only the starting value.
PIANO_ROLL_PX_PER_SEMITONE = 8
PIANO_ROLL_PAD = 12
#: Semitones of headroom above/below the open track's own note range, so a
#: note can be dragged or nudged past what is already there without the panel
#: feeling clipped.
PIANO_ROLL_MARGIN_SEMITONES = 3
#: Window used when a track has no notes yet: two and a half octaves centred
#: on middle C, wide enough to place a first note without hitting an edge.
PIANO_ROLL_DEFAULT_LOW, PIANO_ROLL_DEFAULT_HIGH = 48, 78
#: Hit-target width, in scene pixels, for "grabbed the right edge to resize"
#: rather than "grabbed the body to move".
RESIZE_HANDLE_PX = 6
DEFAULT_PX_PER_BEAT = 34
#: Minimum bars of empty grid shown past the last clip, so there is always
#: room to add a note past the end of what exists.
MIN_BARS = 4

#: Zoom bounds and step for both axes together (toolbar +/- buttons,
#: Ctrl+wheel) -- horizontal and vertical always move together, not
#: independent knobs, per the feature's own design.
MIN_PX_PER_BEAT, MAX_PX_PER_BEAT = 10.0, 200.0
MIN_PX_PER_SEMITONE, MAX_PX_PER_SEMITONE = 3.0, 24.0
ZOOM_FACTOR = 1.25

#: Left-side note-name column width, in widget pixels.
PIANO_ROLL_KEYS_WIDTH = 32

#: Keys the panel's view claims for itself while a track is open. Anything
#: else is left for `StudioWindow`'s global handler.
_ARROWS = (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down,
           QtCore.Qt.Key_Left, QtCore.Qt.Key_Right)
_CLAIMED_KEYS = _ARROWS + (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return,
                           QtCore.Qt.Key_Enter, QtCore.Qt.Key_Delete,
                           QtCore.Qt.Key_Backspace)

#: Note-entry mode (issue #158, decision below): `I` (Insert) is always
#: claimed while a track is open, so it can enter the mode from cursor mode
#: no matter what else is going on. `Escape` is claimed only while note-entry
#: is actually active (`PianoRollScene.note_entry`) -- outside it, Esc must
#: keep falling through to `StudioWindow`'s own Q/Esc-closes-the-window
#: binding unchanged, exactly as before this ticket.
_ENTER_NOTE_ENTRY_KEY = QtCore.Qt.Key_I

#: Qt key code -> layout char, built from `score_audition`'s own two-octave
#: keyboard map (`PIANO_LOWER_ROW`/`PIANO_UPPER_ROW`) rather than a second,
#: hand-copied table -- see the module docstring on why those stay the one
#: source of truth. Qt's letter/digit key codes equal the ASCII code of the
#: *uppercase* character (`Qt.Key_A == ord('A')`, `Qt.Key_2 == ord('2')`),
#: which is what makes this derivable instead of another literal mapping.
_PIANO_ENTRY_KEYS = {QtCore.Qt.Key(ord(_c.upper())): _c
                     for _c in PIANO_LOWER_ROW + PIANO_UPPER_ROW}


class PianoRollScene(QtWidgets.QGraphicsScene):
    """One track's notes, at a fixed pitch scale, over the panel's own
    vertical space -- no row geometry shared with the main arrange canvas."""

    Z_CLIP, Z_NOTE = 0, 1

    def __init__(self, pitch_colour):
        super().__init__()
        self.pitch_colour = pitch_colour
        self.px_per_beat = DEFAULT_PX_PER_BEAT
        #: Pixel-per-semitone scale -- instance state (not the module
        #: constant it started from) so zooming can change it per panel.
        self.px_per_semitone = PIANO_ROLL_PX_PER_SEMITONE
        #: The open `Track`, or `None` when the panel is closed.
        self.track = None
        #: `(low, high)` MIDI pitch bounds, frozen at open time -- recomputing
        #: it live would mean a note dragged near the current edge widens the
        #: window and shifts every other note's y under the pointer mid-drag.
        self.bounds = None
        #: Last-clicked/selected note, `(clip, note)` or `None`.
        self.selected_note = None
        #: Keyboard-only ghost cursor, `(beat, pitch)` in absolute beats --
        #: live only while a track is open and no note is selected, mutually
        #: exclusive with `selected_note` so arrows never mean two things.
        self.cursor = None
        #: Note-entry mode (issue #158): a third mode, toggled explicitly
        #: (`I` in / Esc always out), disjoint from both cursor mode and
        #: note-select mode -- entering it does not disturb `selected_note`
        #: (entry is only reachable from cursor mode, where it is already
        #: `None`) or the ghost `cursor` (kept around so leaving restores it).
        self.note_entry = False
        #: The column (absolute beat) note-entry places its next chord on.
        #: Arrows move it; a commit auto-advances it by one beat.
        self.entry_beat = 0.0
        #: The lower row's leftmost key's octave -- Up/Down inside note-entry
        #: shift this, exactly the same knob `score_audition.PianoEntry`'s
        #: terminal counterpart calls `base_octave`. Not reset by
        #: open_track()/close() -- it is a keyboard-feel preference, not
        #: per-track state.
        self.entry_base_octave = clamp_base_octave(config.EDITOR_PIANO_BASE_OCTAVE)
        #: `{layout_char: midi_pitch}` for every piano key currently held
        #: down in note-entry mode -- rendering-only state (the ghost
        #: outlines); `PianoRollView` owns the matching sound-engine voice
        #: ids, since this scene knows nothing about audio.
        self.entry_ghosts = {}

    # -- open / close --------------------------------------------------

    def open_track(self, track):
        self.track = track
        pitches = [note.pitch for clip in track.clips
                  if isinstance(clip, NoteClip) for note in clip.notes]
        if pitches:
            low = min(pitches) - PIANO_ROLL_MARGIN_SEMITONES
            high = max(pitches) + PIANO_ROLL_MARGIN_SEMITONES
        else:
            low, high = PIANO_ROLL_DEFAULT_LOW, PIANO_ROLL_DEFAULT_HIGH
        self.bounds = (low, high)
        self.selected_note = None
        self.cursor = (0.0, pitches[0] if pitches else (low + high) // 2)
        self.note_entry = False
        self.entry_beat = 0.0
        self.entry_ghosts = {}
        self.rebuild()

    def close(self):
        self.track = None
        self.bounds = None
        self.selected_note = None
        self.cursor = None
        self.note_entry = False
        self.entry_beat = 0.0
        self.entry_ghosts = {}
        self.clear()

    @property
    def total_beats(self):
        if not self.track or not self.track.clips:
            return MIN_BARS * 4.0
        return max(MIN_BARS * 4.0,
                  max(c.start_beat + c.length_beats for c in self.track.clips))

    # -- pitch <-> y ------------------------------------------------------

    def pitch_to_y(self, pitch):
        low, high = self.bounds
        return PIANO_ROLL_PAD + (high - pitch) * self.px_per_semitone

    def y_to_pitch(self, y):
        low, high = self.bounds
        semitones = (y - PIANO_ROLL_PAD) / self.px_per_semitone
        return max(low, min(high, round(high - semitones)))

    # -- zoom ---------------------------------------------------------------

    def zoom(self, factor):
        """Scale both axes together by `factor` (>1 in, <1 out), clamped so
        the grid never gets illegibly small or absurdly large. Horizontal and
        vertical always move together -- not independent knobs -- per this
        feature's own design."""
        self.px_per_beat = max(MIN_PX_PER_BEAT,
                               min(MAX_PX_PER_BEAT, self.px_per_beat * factor))
        self.px_per_semitone = max(MIN_PX_PER_SEMITONE,
                                   min(MAX_PX_PER_SEMITONE,
                                       self.px_per_semitone * factor))

    # -- model-coordinate lookups (no mouse position needed) ---------------

    def clip_at_beat(self, beat):
        for clip in self.track.clips:
            if (isinstance(clip, NoteClip)
                    and clip.start_beat <= beat < clip.start_beat + clip.length_beats):
                return clip
        return None

    def note_at_cursor(self, beat, pitch):
        """`(clip, note)` at an absolute `(beat, pitch)`, or `(None, None)`."""
        clip = self.clip_at_beat(beat)
        if clip is None:
            return None, None
        local_beat = beat - clip.start_beat
        for note in clip.notes:
            if (note.pitch == pitch
                    and note.start_beat <= local_beat < note.start_beat + note.duration_beats):
                return clip, note
        return None, None

    def all_notes(self):
        """Every `(clip, note)` in the open track, across all clips -- used
        for note-to-note keyboard navigation in note-select mode, which
        steps by time or pitch order regardless of which clip a note is in."""
        return [(clip, note) for clip in self.track.clips
               if isinstance(clip, NoteClip) for note in clip.notes]

    # -- scene-position lookups (mouse hit-testing) ------------------------

    def note_at(self, scene_point):
        """`(note, clip, is_resize_handle)` under a scene position, or `None`."""
        for item in self.items(scene_point):
            note = item.data(2)
            if note is not None:
                is_resize = item.rect().right() - scene_point.x() <= RESIZE_HANDLE_PX
                return note, item.data(3), is_resize
        return None

    # -- rendering ----------------------------------------------------------

    def _pen(self, colour, width=1):
        pen = QtGui.QPen(colour)
        pen.setWidth(width)
        pen.setCosmetic(True)
        return pen

    def rebuild(self):
        self.clear()
        if self.track is None:
            self.setSceneRect(0, 0, 0, 0)
            return
        low, high = self.bounds
        height = (high - low + 1) * self.px_per_semitone + 2 * PIANO_ROLL_PAD
        width = self.total_beats * self.px_per_beat
        self.setSceneRect(0, 0, width, height)

        self.addRect(0, 0, width, height, QtGui.QPen(QtCore.Qt.NoPen),
                     QtGui.QBrush(theme.LANE))
        beat = 0.0
        while beat <= self.total_beats + 0.001:
            on_bar = abs(beat % 4.0) < 1e-6
            x = beat * self.px_per_beat
            self.addLine(x, 0, x, height,
                         self._pen(theme.RULE_STRONG if on_bar else theme.RULE))
            beat += 1.0

        for clip in self.track.clips:
            if isinstance(clip, NoteClip):
                self._clip_band(clip, height)
                self._notes(clip)

        if self.note_entry:
            self._draw_entry_column()
        elif self.cursor is not None and self.selected_note is None:
            self._draw_cursor()

    def _clip_band(self, clip, height):
        """A faint background band marking the clip's own beat range --
        visual orientation only, not a drag target: moving a whole clip in
        time is the main arrange canvas's job, not this panel's."""
        x = clip.start_beat * self.px_per_beat
        w = max(6.0, clip.length_beats * self.px_per_beat - 1)
        band = QtWidgets.QGraphicsRectItem(x, 0, w, height)
        band.setBrush(QtGui.QBrush(theme.CLIP_BODY))
        band.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        band.setZValue(self.Z_CLIP)
        self.addItem(band)

    def _notes(self, clip):
        note_h = max(2.0, self.px_per_semitone - 1)
        for note in clip.notes:
            nx = (clip.start_beat + note.start_beat) * self.px_per_beat
            nw = max(RESIZE_HANDLE_PX * 1.5, note.duration_beats * self.px_per_beat - 1)
            ny = self.pitch_to_y(note.pitch)
            # Identity, not `==`: `Note` is a plain dataclass with generated
            # value equality, so two notes that merely look alike (same
            # pitch/beat) would otherwise both light up as "selected".
            selected = (self.selected_note is not None
                       and self.selected_note[0] is clip
                       and self.selected_note[1] is note)
            item = QtWidgets.QGraphicsRectItem(nx, ny, nw, note_h)
            item.setBrush(QtGui.QBrush(self.pitch_colour(note.pitch_class)))
            item.setPen(QtGui.QPen(theme.SELECTION if selected else theme.CLIP_EDGE,
                                   2 if selected else 1))
            item.setData(2, note)
            item.setData(3, clip)
            item.setZValue(self.Z_NOTE)
            self.addItem(item)

    def _draw_cursor(self):
        """The keyboard-only ghost square: a dashed outline, no fill --
        distinct from a selected note's solid highlight so the two arrow-key
        modes stay visually unmistakable, not just behaviourally different."""
        beat, pitch = self.cursor
        nx = beat * self.px_per_beat
        nw = max(RESIZE_HANDLE_PX * 1.5, 1.0 * self.px_per_beat - 1)
        ny = self.pitch_to_y(pitch)
        note_h = max(2.0, self.px_per_semitone - 1)
        item = QtWidgets.QGraphicsRectItem(nx, ny, nw, note_h)
        pen = QtGui.QPen(theme.SELECTION, 2, QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        item.setZValue(self.Z_NOTE)
        self.addItem(item)

    def _draw_entry_column(self):
        """Note-entry mode's own column marker (issue #158): a translucent
        full-height band at `entry_beat`, plus one dashed ghost outline per
        piano key still physically held down. There is no single pitch to
        highlight the way `_draw_cursor()` does -- pitch comes from whichever
        keys are down, not from a cursor -- so the band marks *where* a
        chord will land and the per-key ghosts show *what* it will be."""
        low, high = self.bounds
        height = (high - low + 1) * self.px_per_semitone + 2 * PIANO_ROLL_PAD
        x = self.entry_beat * self.px_per_beat
        w = max(RESIZE_HANDLE_PX * 1.5, 1.0 * self.px_per_beat - 1)
        band = QtWidgets.QGraphicsRectItem(x, 0, w, height)
        band.setBrush(QtGui.QBrush(theme.SELECTION))
        band.setOpacity(0.15)
        band.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        band.setZValue(self.Z_NOTE)
        self.addItem(band)

        note_h = max(2.0, self.px_per_semitone - 1)
        for pitch in self.entry_ghosts.values():
            ny = self.pitch_to_y(pitch)
            item = QtWidgets.QGraphicsRectItem(x, ny, w, note_h)
            pen = QtGui.QPen(theme.SELECTION, 2, QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            item.setZValue(self.Z_NOTE)
            self.addItem(item)


class PianoRollView(QtWidgets.QGraphicsView):
    """Mouse and keyboard editing over a `PianoRollScene`.

    `StrongFocus` so a click claims keyboard focus from the main window, and
    `keyPressEvent()` only ever `accept()`s the keys this panel actually
    means something by -- every other key (`Ctrl+S`, `Ctrl+Z`, `M`, `S`,
    `L`, ...) is left alone via `super().keyPressEvent()`, which lets Qt's
    normal "unaccepted event climbs to the parent" propagation carry it up to
    `StudioWindow`'s own handler unchanged.
    """

    #: Emitted after any user action that changes mode, selection, or the
    #: model -- the host refreshes its toolbar labels off this rather than
    #: every call site remembering to ask for it.
    edited = QtCore.Signal()
    #: Emitted after a zoom step -- separate from `edited` since zoom is not
    #: a mode/selection/model change, but the host's note-name column (which
    #: shares this view's `px_per_semitone`/row geometry) still has to redraw.
    zoomed = QtCore.Signal()

    def __init__(self, run_command, say, pitch_colour, sound_engine_provider=None):
        # Held in `self._piano_scene` too: passing the scene straight through
        # to `QGraphicsView.__init__()` with no other Python reference lets
        # PySide/Shiboken garbage-collect it immediately after construction
        # (`QGraphicsView` does not itself keep the wrapper alive on the
        # Python side), which silently leaves `self.scene()` returning
        # `None` forever after.
        self._piano_scene = PianoRollScene(pitch_colour)
        super().__init__(self._piano_scene)
        self._run = run_command
        self._say = say
        self._drag = None
        # Issue #158's live preview: a zero-argument callable returning the
        # process's `sound_engine.SoundEngine`, or `None` when there is no
        # audio device -- the same lazy-provider shape as
        # `SessionState.ensure_sound_engine()` (decision #105), passed in by
        # the host rather than looked up here, since VisualNote Studio (this
        # widget's only host) has no `SessionState` at all: it owns one
        # `SoundEngine` directly (`gui/app.py`'s `start_audio()`) and hands
        # it to `ProjectPlayer`, so the provider this ticket wires up is
        # `lambda: self.player.engine if self.player else None`.
        self._sound_engine_provider = sound_engine_provider
        #: `{layout_char: {"pitch": midi_pitch, "voice_id": ...}}` for every
        #: piano key currently held down in note-entry mode -- the sounding
        #: side of `PianoRollScene.entry_ghosts` (the rendering side).
        self._entry_held = {}
        #: Every distinct MIDI pitch that has been part of the chord
        #: currently being built, accumulated across the whole held-group's
        #: lifetime (not just whatever is still down at the moment the last
        #: key comes up) -- release order must not drop an early note from
        #: the eventual chord. Already deduplicated by construction (a set).
        self._entry_group_pitches = set()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setBackgroundBrush(QtGui.QBrush(theme.CANVAS))
        self.setStyleSheet(theme.CANVAS_STYLESHEET)
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    # -- open/close, driven by the panel host ------------------------------

    def open_track(self, track):
        self.scene().open_track(track)

    def close_track(self):
        self._discard_entry_chord()
        self.scene().close()

    def rebuild(self):
        self.scene().rebuild()

    # -- zoom: toolbar +/- buttons and Ctrl+wheel, both axes together -------

    def zoom_in(self):
        self._zoom(ZOOM_FACTOR)

    def zoom_out(self):
        self._zoom(1.0 / ZOOM_FACTOR)

    def _zoom(self, factor, viewport_pos=None):
        scene = self.scene()
        if scene.track is None:
            return
        if viewport_pos is None:
            viewport_pos = self.viewport().rect().center()
        # Standard "zoom to a point" trick: remember where the anchor point
        # sits in the viewport, rescale, then shift the scrollbars back by
        # however far that point drifted -- keeps whatever is under the
        # mouse (or the viewport centre, for the toolbar buttons) visually
        # still instead of the view jumping to the scene origin.
        anchor_scene = self.mapToScene(viewport_pos)
        old_px_per_beat, old_px_per_semitone = scene.px_per_beat, scene.px_per_semitone
        scene.zoom(factor)
        scene.rebuild()
        x_scale = scene.px_per_beat / old_px_per_beat
        y_scale = scene.px_per_semitone / old_px_per_semitone
        target = QtCore.QPointF(
            anchor_scene.x() * x_scale,
            PIANO_ROLL_PAD + (anchor_scene.y() - PIANO_ROLL_PAD) * y_scale)
        self.centerOn(target)
        drift = self.viewport().rect().center() - viewport_pos
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - drift.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - drift.y())
        self.zoomed.emit()

    def wheelEvent(self, event):
        scene = self.scene()
        if scene.track is not None and event.modifiers() & QtCore.Qt.ControlModifier:
            factor = ZOOM_FACTOR if event.angleDelta().y() > 0 else 1.0 / ZOOM_FACTOR
            self._zoom(factor, event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)

    # -- one action = one command, run through the host's undo stack -------

    def _apply(self, command):
        # `self._run()` is the host's `StudioWindow.run()` -- it already
        # drives the undo stack, the status line, and the main arrange
        # canvas's own rebuild (whose clip thumbnails reflect this edit too).
        self._run(command)
        self.scene().rebuild()
        self.edited.emit()

    def _snap(self, beat):
        return round(beat)

    def _add_note(self, clip, beat, pitch, select=True):
        note = Note(max(0.0, self._snap(beat) - clip.start_beat), 1.0, pitch)
        self._apply(edit.AddNote(clip, note))
        if select:
            self.scene().selected_note = (clip, note)
            self.scene().rebuild()

    def toggle_note_at_cursor(self):
        """Space: add a note at the ghost cursor, or delete the one already
        there -- the keyboard equivalent of Ctrl+click-to-add and
        click-then-Delete, in one key, since the cursor (unlike a mouse
        click) is always already positioned somewhere sensible. Only meant
        for cursor mode: a no-op while a note is selected, since Space has
        no cursor position to add at there. Deliberately does not select the
        note it adds (unlike a mouse Ctrl+click) -- staying in cursor mode
        with the cursor untouched is what lets Space be pressed repeatedly
        to lay down a run of notes, per hands-on feedback on the original
        63b0c04 scheme, which switched into note-select mode on every add."""
        scene = self.scene()
        if scene.track is None or scene.selected_note is not None or scene.note_entry:
            return
        beat, pitch = scene.cursor
        clip, note = scene.note_at_cursor(beat, pitch)
        if note is not None:
            self._apply(edit.DeleteNote(clip, note))
            return
        clip = scene.clip_at_beat(beat)
        if clip is None:
            return self._say("no clip here")
        self._add_note(clip, beat, pitch, select=False)

    def delete_selected(self):
        scene = self.scene()
        if scene.note_entry or scene.selected_note is None:
            return self._say("no note selected")
        clip, note = scene.selected_note
        scene.selected_note = None
        self._apply(edit.DeleteNote(clip, note))

    def toggle_mode(self):
        """Enter, or the mode button: switch between free ghost-cursor
        movement and note-select mode (note-to-note navigation, or
        Shift+arrow to move the selected note) -- explicitly, rather than
        inferring which is meant from the cursor's position on every arrow
        press (a UX call made with the user, not guessed)."""
        scene = self.scene()
        if scene.track is None or scene.note_entry:
            return
        if scene.selected_note is not None:
            clip, note = scene.selected_note
            scene.cursor = (clip.start_beat + note.start_beat, note.pitch)
            scene.selected_note = None
        else:
            beat, pitch = scene.cursor
            clip, note = scene.note_at_cursor(beat, pitch)
            if note is None:
                return self._say("no note under cursor")
            scene.selected_note = (clip, note)
        scene.rebuild()
        self.edited.emit()

    def in_note_mode(self):
        return self.scene().selected_note is not None

    def in_note_entry_mode(self):
        return self.scene().note_entry

    def _move_cursor(self, key, shift):
        scene = self.scene()
        beat, pitch = scene.cursor
        low, high = scene.bounds
        if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            delta = (12 if shift else 1) * (1 if key == QtCore.Qt.Key_Up else -1)
            pitch = max(low, min(high, pitch + delta))
        else:
            step = 4.0 if shift else 1.0     # one bar (4 beats) or one beat
            delta = step if key == QtCore.Qt.Key_Right else -step
            beat = max(0.0, beat + delta)
        scene.cursor = (beat, pitch)
        scene.rebuild()
        self.ensureVisible(beat * scene.px_per_beat, scene.pitch_to_y(pitch),
                          1.0, 1.0, 40, 40)

    def _nudge_selected_note(self, key):
        """Shift+arrow in note-select mode: move the selected note by one
        grid step -- one key press, one undo entry, an exact semitone/beat
        step where a drag only gets you close. Pitch is clamped to the
        panel's own bounds, the same range a mouse drag's `y_to_pitch()`
        already clamps to. Plain (un-shifted) arrows instead navigate
        between existing notes -- see `_select_adjacent_note` -- so Shift is
        now the "move it" modifier rather than a big-step modifier."""
        scene = self.scene()
        clip, note = scene.selected_note
        low, high = scene.bounds
        if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            delta = 1 if key == QtCore.Qt.Key_Up else -1
            pitch = max(low, min(high, note.pitch + delta))
            if pitch != note.pitch:
                self._apply(edit.MoveNote(clip, note, note.start_beat, pitch=pitch))
        else:
            delta = 1.0 if key == QtCore.Qt.Key_Right else -1.0
            beat = max(0.0, note.start_beat + delta)
            if beat != note.start_beat:
                self._apply(edit.MoveNote(clip, note, beat))
        scene.selected_note = (clip, note)

    def _select_adjacent_note(self, key):
        """Plain arrow in note-select mode: step the *selection* to another
        existing note rather than moving anything -- Left/Right to the
        nearest note later/earlier in time (any pitch, any clip in the open
        track), Up/Down to the nearest note higher/lower in pitch (any
        time). Arrows never land on empty space here; Shift+arrow is what
        moves the note itself (`_nudge_selected_note`). Replaces free
        nudging as the plain-arrow behaviour per hands-on feedback on the
        original 63b0c04 scheme."""
        scene = self.scene()
        clip, note = scene.selected_note
        abs_beat = clip.start_beat + note.start_beat
        others = [(c, n) for c, n in scene.all_notes() if n is not note]

        if key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            if key == QtCore.Qt.Key_Right:
                candidates = [(c, n) for c, n in others
                             if c.start_beat + n.start_beat > abs_beat]
            else:
                candidates = [(c, n) for c, n in others
                             if c.start_beat + n.start_beat < abs_beat]
            if not candidates:
                return self._say("no more notes")
            candidates.sort(key=lambda cn: (
                abs(cn[0].start_beat + cn[1].start_beat - abs_beat),
                abs(cn[1].pitch - note.pitch), cn[1].pitch))
        else:
            if key == QtCore.Qt.Key_Up:
                candidates = [(c, n) for c, n in others if n.pitch > note.pitch]
            else:
                candidates = [(c, n) for c, n in others if n.pitch < note.pitch]
            if not candidates:
                return self._say("no note there")
            candidates.sort(key=lambda cn: (
                abs(cn[1].pitch - note.pitch),
                abs(cn[0].start_beat + cn[1].start_beat - abs_beat)))

        scene.selected_note = candidates[0]
        scene.rebuild()
        self.edited.emit()

    # -- note-entry mode: held piano keys -> a chord (issue #158) ----------
    #
    # A third mode, entered from cursor mode via `I` and always left via
    # Esc (`keyPressEvent()` below wires both, plus the note-select-mode
    # fallthrough). While active, `PIANO_LOWER_ROW`/`PIANO_UPPER_ROW` keys
    # (imported unchanged from `notation/score_audition.py` -- see the
    # module docstring) are pitches rather than editor commands: each
    # key-down sounds a live preview note and stages a ghost outline in the
    # same column (`scene.entry_beat`); the chord commits -- one
    # `edit.AddNote` per unique pitch -- only once every held key has come
    # back up, and the column then auto-advances by one beat. Left/Right and
    # Up/Down are repurposed (column and base-octave, respectively) since
    # pitch no longer comes from a cursor; Space/Delete/Backspace/Enter stay
    # unclaimed no-ops, per the ticket's "nothing overloaded" rule.

    def enter_note_entry(self):
        """`I`, only from cursor mode -- mirrors the terminal score editor's
        own "a key enters piano mode" rule (#108), scoped here to cursor
        mode specifically since note-select mode has its own fallthrough
        (`_enter_note_entry_from_note_select()`, called by `keyPressEvent()`
        instead of this whenever a piano key -- not `I` -- arrives with a
        note selected)."""
        scene = self.scene()
        if scene.track is None or scene.note_entry or scene.selected_note is not None:
            return
        scene.note_entry = True
        scene.entry_beat = self._snap(scene.cursor[0]) if scene.cursor else 0.0
        scene.entry_ghosts = {}
        self._entry_held = {}
        self._entry_group_pitches = set()
        scene.rebuild()
        self.edited.emit()

    def leave_note_entry(self):
        """Esc: always leaves, discarding any chord still mid-hold -- an
        escape hatch, not an alternate commit path (per the ticket's own
        wording). Cuts every currently-sounding preview voice and restores
        the ghost cursor at the column note-entry was sitting on."""
        scene = self.scene()
        if not scene.note_entry:
            return
        self._discard_entry_chord()
        scene.note_entry = False
        pitch = scene.cursor[1] if scene.cursor else (scene.bounds[0] + scene.bounds[1]) // 2
        scene.cursor = (scene.entry_beat, pitch)
        scene.rebuild()
        self.edited.emit()

    def _discard_entry_chord(self):
        """Releases every sounding preview voice and forgets the pending
        (uncommitted) chord -- called by Esc and by `close_track()`, so a
        chord can never keep ringing or land as notes after either."""
        for info in self._entry_held.values():
            self._release_preview(info.get("voice_id"))
        self._entry_held = {}
        self._entry_group_pitches = set()
        self.scene().entry_ghosts = {}

    def _entry_key_down(self, char):
        """One piano key going down inside note-entry mode: sounds it (live
        preview) and stages its ghost. OS key-repeat sends this repeatedly
        for one physical hold -- `keyPressEvent()` filters that with
        `event.isAutoRepeat()` before ever calling this, and the `char in
        self._entry_held` guard here is the second line of defence."""
        if char in self._entry_held:
            return
        scene = self.scene()
        pitch_class, octave = pitch_for_key(char, scene.entry_base_octave)
        from notecolor.audio.sound_engine import midi_pitch

        pitch = midi_pitch(pitch_class, octave)
        voice_id = self._sound_preview_on(pitch)
        self._entry_held[char] = {"pitch": pitch, "voice_id": voice_id}
        self._entry_group_pitches.add(pitch)
        scene.entry_ghosts[char] = pitch
        scene.rebuild()

    def _entry_key_up(self, char):
        """One piano key coming back up. A release for a key that was never
        (or no longer) held is a no-op -- in particular "releasing with zero
        keys ever held" never advances the column, exactly as the ticket
        specifies. Once this empties `_entry_held`, the whole group commits."""
        if char not in self._entry_held:
            return
        info = self._entry_held.pop(char)
        self._release_preview(info.get("voice_id"))
        self.scene().entry_ghosts.pop(char, None)
        if not self._entry_held:
            self._commit_entry_chord()
        else:
            self.scene().rebuild()

    def _commit_entry_chord(self):
        """Every held key has come up: turn the accumulated, deduplicated
        pitch set into one `edit.AddNote` each, then auto-advance the
        column. "Commit" strictly means a chord happened, so this is only
        ever called from `_entry_key_up()` at the moment the last key
        releases -- never on a release that found nothing held."""
        scene = self.scene()
        pitches = sorted(self._entry_group_pitches)
        self._entry_group_pitches = set()
        clip = scene.clip_at_beat(scene.entry_beat)
        if clip is None:
            self._say("no clip here")
        else:
            for pitch in pitches:
                note = Note(max(0.0, scene.entry_beat - clip.start_beat), 1.0, pitch)
                self._apply(edit.AddNote(clip, note))
            scene.entry_beat += 1.0
        scene.entry_ghosts = {}
        scene.rebuild()

    def _move_entry_column(self, key, shift):
        """Left/Right inside note-entry mode: manually move the column --
        the only way to advance without playing a chord, including
        deliberately skipping a rest column (the ticket's own example)."""
        scene = self.scene()
        step = 4.0 if shift else 1.0
        delta = step if key == QtCore.Qt.Key_Right else -step
        scene.entry_beat = max(0.0, scene.entry_beat + delta)
        scene.rebuild()
        self.ensureVisible(scene.entry_beat * scene.px_per_beat, PIANO_ROLL_PAD,
                          1.0, 1.0, 40, 40)

    def _shift_entry_octave(self, key):
        """Up/Down inside note-entry mode: shifts the QWERTY rows' base
        octave rather than a pitch cursor, since pitch now comes from
        whichever keys are held. Clamped the same way the terminal score
        editor's own two-octave layout is (`clamp_base_octave()`)."""
        scene = self.scene()
        delta = 1 if key == QtCore.Qt.Key_Up else -1
        scene.entry_base_octave = clamp_base_octave(scene.entry_base_octave + delta)

    def _enter_note_entry_from_note_select(self, char):
        """Note-select mode, typing a piano key (never a silent no-op, per
        the ticket): switches to cursor mode at the selected note's own
        position first, then immediately enters note-entry there and treats
        this same keystroke as the chord's first key-down -- "applies the
        normal insert behaviour" once cursor mode is reached."""
        scene = self.scene()
        clip, note = scene.selected_note
        scene.selected_note = None
        scene.cursor = (clip.start_beat + note.start_beat, note.pitch)
        scene.note_entry = True
        scene.entry_beat = self._snap(scene.cursor[0])
        scene.entry_ghosts = {}
        self._entry_held = {}
        self._entry_group_pitches = set()
        self._entry_key_down(char)
        self.edited.emit()

    # -- the sound-engine edge: live preview only, never scheduled --------

    def _sound_preview_on(self, pitch):
        """Sounds `pitch` immediately with no scheduled note-off -- the
        release comes from the key coming back up (`_release_preview()`),
        unlike every other audition path in this codebase, which knows its
        note's length up front and uses `schedule_note_off()`. Returns the
        voice id, or `None` when there is no sound engine (no audio device,
        or `[synth]` not installed) -- the same silent-but-usable
        degradation every other audio call site in this app already takes."""
        engine = self._sound_engine_provider() if self._sound_engine_provider else None
        if engine is None:
            return None
        from notecolor.audio.sound_engine import NoteOn

        patch_name = self.scene().track.patch_name
        return engine.note_on(NoteOn(pitch, patch=patch_name))

    def _release_preview(self, voice_id):
        if voice_id is None:
            return
        engine = self._sound_engine_provider() if self._sound_engine_provider else None
        if engine is not None:
            engine.release_voice(voice_id)

    # -- mouse: click-to-select, Ctrl+click-to-add, drag-to-move/resize -----

    def mousePressEvent(self, event):
        scene = self.scene()
        if scene.track is None or event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint())
        note_hit = scene.note_at(point)
        if note_hit is not None:
            note, clip, is_resize = note_hit
            scene.selected_note = (clip, note)
            scene.rebuild()
            self.edited.emit()
            if is_resize:
                self._drag = {"kind": "resize", "clip": clip, "note": note,
                              "grab": point.x() / scene.px_per_beat,
                              "from_duration": note.duration_beats, "moved": False}
            else:
                self._drag = {"kind": "move", "clip": clip, "note": note,
                              "grab": point.x() / scene.px_per_beat,
                              "from_beat": note.start_beat,
                              "from_pitch": note.pitch, "moved": False}
            return
        if event.modifiers() & QtCore.Qt.ControlModifier:
            clip = scene.clip_at_beat(point.x() / scene.px_per_beat)
            if clip is not None:
                self._add_note(clip, point.x() / scene.px_per_beat,
                              scene.y_to_pitch(point.y()))
                return
        # Empty space: park the ghost cursor there rather than doing nothing --
        # a click is otherwise a dead gesture on a panel whose whole point is
        # positioning the cursor.
        scene.selected_note = None
        scene.cursor = (max(0.0, round(point.x() / scene.px_per_beat)),
                       scene.y_to_pitch(point.y()))
        scene.rebuild()
        self.edited.emit()

    def mouseMoveEvent(self, event):
        if not self._drag:
            return super().mouseMoveEvent(event)
        scene = self.scene()
        point = self.mapToScene(event.position().toPoint())
        beat = point.x() / scene.px_per_beat
        drag = self._drag
        if drag["kind"] == "move":
            note = drag["note"]
            target_beat = max(0.0, round(drag["from_beat"] + beat - drag["grab"]))
            target_pitch = scene.y_to_pitch(point.y())
            if target_beat != note.start_beat or target_pitch != note.pitch:
                note.start_beat, note.pitch = target_beat, target_pitch
                drag["moved"] = True
        elif drag["kind"] == "resize":
            note = drag["note"]
            target = max(edit.ResizeNote.MIN_DURATION_BEATS,
                        drag["from_duration"] + beat - drag["grab"])
            if target != note.duration_beats:
                note.duration_beats = target
                drag["moved"] = True
        if drag["moved"]:
            scene.rebuild()

    def mouseReleaseEvent(self, event):
        if not self._drag:
            return super().mouseReleaseEvent(event)
        drag, self._drag = self._drag, None
        if not drag["moved"]:
            return
        note = drag["note"]
        if drag["kind"] == "move":
            landed_beat, landed_pitch = note.start_beat, note.pitch
            note.start_beat, note.pitch = drag["from_beat"], drag["from_pitch"]
            self._apply(edit.MoveNote(drag["clip"], note, landed_beat, pitch=landed_pitch))
        elif drag["kind"] == "resize":
            landed = note.duration_beats
            note.duration_beats = drag["from_duration"]
            self._apply(edit.ResizeNote(drag["clip"], note, landed))
        self.scene().selected_note = (drag["clip"], note)
        self.scene().rebuild()

    # -- keyboard: only claims what it uses, everything else bubbles up ----

    def keyPressEvent(self, event):
        scene = self.scene()
        if scene.track is None:
            return super().keyPressEvent(event)
        key = event.key()
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)

        if scene.note_entry:
            if key == QtCore.Qt.Key_Escape:
                self.leave_note_entry()
            elif key in _ARROWS:
                if key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
                    self._move_entry_column(key, shift)
                else:
                    self._shift_entry_octave(key)
            elif key in _PIANO_ENTRY_KEYS:
                if not event.isAutoRepeat():
                    self._entry_key_down(_PIANO_ENTRY_KEYS[key])
                # else: OS key-repeat for an already-held key -- swallowed,
                # not retriggered (`_entry_key_down()` itself also guards
                # this, so this branch is belt-and-braces).
            elif key in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Delete,
                        QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Return,
                        QtCore.Qt.Key_Enter):
                pass    # explicit no-ops: not claimed by note-entry's own
                        # vocabulary (piano keys + arrows + Esc), per the
                        # ticket -- but still swallowed here, not bubbled,
                        # so e.g. Enter can't leak out to `toggle_mode()`.
            else:
                return super().keyPressEvent(event)
            event.accept()
            return

        if key == _ENTER_NOTE_ENTRY_KEY:
            self.enter_note_entry()
            event.accept()
            return

        if (key in _PIANO_ENTRY_KEYS and scene.selected_note is not None
                and not event.isAutoRepeat()):
            self._enter_note_entry_from_note_select(_PIANO_ENTRY_KEYS[key])
            event.accept()
            return

        if key in _ARROWS:
            if scene.selected_note is not None:
                if shift:
                    self._nudge_selected_note(key)
                else:
                    self._select_adjacent_note(key)
            else:
                self._move_cursor(key, shift)
        elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.toggle_mode()
        elif key == QtCore.Qt.Key_Space:
            self.toggle_note_at_cursor()
        elif key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_selected()
        else:
            return super().keyPressEvent(event)
        event.accept()

    def keyReleaseEvent(self, event):
        scene = self.scene()
        if scene.track is None or not scene.note_entry:
            return super().keyReleaseEvent(event)
        key = event.key()
        if key in _PIANO_ENTRY_KEYS and not event.isAutoRepeat():
            self._entry_key_up(_PIANO_ENTRY_KEYS[key])
            event.accept()
            return
        super().keyReleaseEvent(event)

    def event(self, event):
        """Claim the panel's keys *before* Qt's shortcut map gets to them.

        `StudioWindow`'s menu registers plain-key `QAction` shortcuts (Space
        for play/stop, among them) with the default `Qt.WindowShortcut`
        context, which fire on a `ShortcutOverride` event that Qt dispatches
        to the focused widget *ahead of* its normal `keyPressEvent()` -- a
        widget that does not explicitly accept that event loses the key to
        the action outright, no matter what its own `keyPressEvent()` would
        have done with it. This is the actual mechanism behind the
        collisions the inline approach had: the fix is not "handle the key",
        it's "tell Qt not to treat it as a shortcut" first.

        Note-entry's own keys (issue #158) are claimed conditionally rather
        than added to the static `_CLAIMED_KEYS` tuple: `I` only while a
        track is open at all (so it can always enter the mode from cursor
        mode); the piano-key letters/digits only while note-entry is
        actually active, or a note is selected (the note-select-mode
        fallthrough) -- otherwise e.g. `S` must keep reaching
        `StudioWindow`'s solo shortcut, and `Escape` only while note-entry
        is active -- otherwise it must keep reaching `StudioWindow`'s own
        Q/Esc-closes-the-window binding, unchanged from before this ticket.
        """
        if event.type() == QtCore.QEvent.ShortcutOverride and self.scene().track is not None:
            scene = self.scene()
            key = event.key()
            claimed = (key in _CLAIMED_KEYS or key == _ENTER_NOTE_ENTRY_KEY
                      or (scene.note_entry and key == QtCore.Qt.Key_Escape)
                      or (key in _PIANO_ENTRY_KEYS
                          and (scene.note_entry or scene.selected_note is not None)))
            if claimed:
                event.accept()
                return True
        return super().event(event)


class PianoRollKeys(QtWidgets.QWidget):
    """The left-side note-name column, one label per semitone row.

    A separate sibling widget kept in vertical scroll-sync with
    `PianoRollView` -- the same precedent `gui/studio.py`'s `TrackHeaders`
    uses for `ArrangeScene` (a `scroll` offset the host updates from the
    view's `verticalScrollBar().valueChanged`, rather than drawing labels
    inside the scrollable `QGraphicsScene` itself). Reads `scene.bounds` and
    `scene.px_per_semitone` directly so a row's label always lines up with
    the matching note row, including after a zoom.

    `key_signature` is a callable returning `(key_fifths, key_mode)` (not a
    plain value) so the label spelling and in-scale tint follow the
    project's *current* key live -- re-read on every `paintEvent()` rather
    than cached at construction or `open_track()` time, since the key can
    change from the header while this panel is open.
    """

    def __init__(self, scene, key_signature):
        super().__init__()
        self._scene = scene
        self._key_signature = key_signature
        self.scroll = 0
        self.setFixedWidth(PIANO_ROLL_KEYS_WIDTH)

    def set_scroll(self, value):
        self.scroll = value
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), theme.CHROME)
        scene = self._scene
        if scene.track is None:
            return
        low, high = scene.bounds
        key_fifths, key_mode = self._key_signature()
        names = chromatic_note_names(key_fifths, key_mode)
        scale = diatonic_pitch_classes(key_fifths, key_mode)
        row_h = scene.px_per_semitone
        font_size = max(5, min(9, int(row_h) - 3))
        for pitch in range(int(low), int(high) + 1):
            y = scene.pitch_to_y(pitch) - self.scroll
            if y + row_h < 0 or y > self.height():
                continue
            name = names[pitch % 12]
            in_scale = pitch % 12 in scale
            if not in_scale:
                p.fillRect(QtCore.QRectF(0, y, PIANO_ROLL_KEYS_WIDTH, row_h),
                          theme.CHROME_DEEP)
            p.setPen(theme.RULE)
            p.drawLine(QtCore.QPointF(0, y + row_h), QtCore.QPointF(PIANO_ROLL_KEYS_WIDTH, y + row_h))
            p.setPen(theme.TEXT if in_scale else theme.TEXT_FAINT)
            p.setFont(theme.font(font_size, bold=in_scale))
            p.drawText(QtCore.QRectF(2, y, PIANO_ROLL_KEYS_WIDTH - 4, row_h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)


class PianoRollPanel(QtWidgets.QWidget):
    """The bottom pane: a mode/add/delete toolbar over the `PianoRollView`.

    A plain, always-anchored widget rather than a `QDockWidget` -- the user
    asked for a panel that stretches taller/shorter in place, not one that
    floats or detaches, so the host puts this inside a vertical `QSplitter`
    instead of a dock area.
    """

    #: Emitted whenever an edit or mode change happens, so the host can
    #: refresh headers/title/status the same way `StudioWindow.run()` does
    #: for the main canvas.
    changed = QtCore.Signal()
    #: Emitted only when the panel closes itself (its own Close button) --
    #: distinct from `changed` because the host needs to react specifically
    #: here (clear the header's "piano roll open" marker), not just refresh.
    closed = QtCore.Signal()

    def __init__(self, run_command, say, pitch_colour, key_signature,
                sound_engine_provider=None):
        super().__init__()
        self._say_host = say
        self.track_index = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QtWidgets.QWidget()
        toolbar.setFixedHeight(28)
        bar = QtWidgets.QHBoxLayout(toolbar)
        bar.setContentsMargins(8, 2, 8, 2)
        bar.setSpacing(6)

        self.title_label = QtWidgets.QLabel("piano roll")
        self.title_label.setFont(theme.font(8, bold=True))
        bar.addWidget(self.title_label)
        bar.addStretch(1)

        self.mode_button = QtWidgets.QPushButton("cursor mode (enter)")
        self.mode_button.clicked.connect(self._on_mode_clicked)
        bar.addWidget(self.mode_button)

        self.add_delete_button = QtWidgets.QPushButton("add / delete (space)")
        self.add_delete_button.clicked.connect(self._on_add_delete_clicked)
        bar.addWidget(self.add_delete_button)

        self.delete_button = QtWidgets.QPushButton("delete (del)")
        self.delete_button.clicked.connect(self._on_delete_clicked)
        bar.addWidget(self.delete_button)

        self.zoom_out_button = QtWidgets.QPushButton("−")
        self.zoom_out_button.setFixedWidth(24)
        self.zoom_out_button.setToolTip("zoom out (ctrl+scroll)")
        self.zoom_out_button.clicked.connect(self._on_zoom_out_clicked)
        bar.addWidget(self.zoom_out_button)

        self.zoom_in_button = QtWidgets.QPushButton("+")
        self.zoom_in_button.setFixedWidth(24)
        self.zoom_in_button.setToolTip("zoom in (ctrl+scroll)")
        self.zoom_in_button.clicked.connect(self._on_zoom_in_clicked)
        bar.addWidget(self.zoom_in_button)

        close_button = QtWidgets.QPushButton("close")
        close_button.clicked.connect(self._on_close_clicked)
        bar.addWidget(close_button)

        layout.addWidget(toolbar)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.view = PianoRollView(run_command, self._say, pitch_colour,
                                  sound_engine_provider)
        self.view.edited.connect(self._on_edited)
        self.view.zoomed.connect(self._on_zoomed)

        # The note-name column is a sibling widget kept in vertical
        # scroll-sync with `self.view`, not drawn inside its scene -- see
        # `PianoRollKeys`'s own docstring for why.
        self.keys_column = PianoRollKeys(self.view.scene(), key_signature)
        self.view.verticalScrollBar().valueChanged.connect(self.keys_column.set_scroll)

        body_layout.addWidget(self.keys_column)
        body_layout.addWidget(self.view, 1)
        layout.addWidget(body, 1)

        self.setMinimumHeight(60)
        self.hide()

    def _say(self, message):
        self._say_host(message)

    def _on_edited(self):
        self._refresh_toolbar()
        self.changed.emit()

    # -- host-driven open/close ---------------------------------------------

    def open_track(self, track_index, track):
        self.track_index = track_index
        self.title_label.setText(f"piano roll -- {track.name}")
        self.view.open_track(track)
        self.keys_column.update()
        self.show()
        self.view.setFocus()
        self._refresh_toolbar()

    def close_track(self):
        self.track_index = None
        self.view.close_track()
        self.keys_column.update()
        self.hide()

    @property
    def is_open(self):
        return self.track_index is not None

    # -- toolbar --------------------------------------------------------

    def _refresh_toolbar(self):
        note_mode = self.view.in_note_mode()
        if self.view.in_note_entry_mode():
            text = "note-entry mode (esc)"
        elif note_mode:
            text = "note mode (enter)"
        else:
            text = "cursor mode (enter/i)"
        self.mode_button.setText(text)
        self.delete_button.setEnabled(note_mode)

    def _on_mode_clicked(self):
        self.view.toggle_mode()
        self.view.setFocus()
        self._refresh_toolbar()
        self.changed.emit()

    def _on_add_delete_clicked(self):
        self.view.toggle_note_at_cursor()
        self.view.setFocus()
        self._refresh_toolbar()
        self.changed.emit()

    def _on_delete_clicked(self):
        self.view.delete_selected()
        self.view.setFocus()
        self._refresh_toolbar()
        self.changed.emit()

    def _on_close_clicked(self):
        self.close_track()
        self.closed.emit()

    def _on_zoomed(self):
        self.keys_column.update()

    def _on_zoom_in_clicked(self):
        self.view.zoom_in()
        self.view.setFocus()

    def _on_zoom_out_clicked(self):
        self.view.zoom_out()
        self.view.setFocus()
