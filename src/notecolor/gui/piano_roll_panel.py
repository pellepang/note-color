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
from notecolor.project import edit
from notecolor.project.model import Note, NoteClip

#: Same fixed pixel-per-semitone scale the inline piano roll used -- pitch has
#: to mean the same y everywhere so a dragged note lands where the eye expects
#: it, not wherever the open track's own range happened to put it.
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

#: Keys the panel's view claims for itself while a track is open. Anything
#: else is left for `StudioWindow`'s global handler.
_ARROWS = (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down,
           QtCore.Qt.Key_Left, QtCore.Qt.Key_Right)
_CLAIMED_KEYS = _ARROWS + (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return,
                           QtCore.Qt.Key_Enter, QtCore.Qt.Key_Delete,
                           QtCore.Qt.Key_Backspace)


class PianoRollScene(QtWidgets.QGraphicsScene):
    """One track's notes, at a fixed pitch scale, over the panel's own
    vertical space -- no row geometry shared with the main arrange canvas."""

    Z_CLIP, Z_NOTE = 0, 1

    def __init__(self, pitch_colour):
        super().__init__()
        self.pitch_colour = pitch_colour
        self.px_per_beat = DEFAULT_PX_PER_BEAT
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
        self.rebuild()

    def close(self):
        self.track = None
        self.bounds = None
        self.selected_note = None
        self.cursor = None
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
        return PIANO_ROLL_PAD + (high - pitch) * PIANO_ROLL_PX_PER_SEMITONE

    def y_to_pitch(self, y):
        low, high = self.bounds
        semitones = (y - PIANO_ROLL_PAD) / PIANO_ROLL_PX_PER_SEMITONE
        return max(low, min(high, round(high - semitones)))

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
        height = (high - low + 1) * PIANO_ROLL_PX_PER_SEMITONE + 2 * PIANO_ROLL_PAD
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

        if self.cursor is not None and self.selected_note is None:
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
        note_h = max(2.0, PIANO_ROLL_PX_PER_SEMITONE - 1)
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
        note_h = max(2.0, PIANO_ROLL_PX_PER_SEMITONE - 1)
        item = QtWidgets.QGraphicsRectItem(nx, ny, nw, note_h)
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

    def __init__(self, run_command, say, pitch_colour):
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
        self.scene().close()

    def rebuild(self):
        self.scene().rebuild()

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
        if scene.track is None or scene.selected_note is not None:
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
        if scene.selected_note is None:
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
        if scene.track is None:
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
        """
        if (event.type() == QtCore.QEvent.ShortcutOverride
                and self.scene().track is not None
                and event.key() in _CLAIMED_KEYS):
            event.accept()
            return True
        return super().event(event)


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

    def __init__(self, run_command, say, pitch_colour):
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

        close_button = QtWidgets.QPushButton("close")
        close_button.clicked.connect(self._on_close_clicked)
        bar.addWidget(close_button)

        layout.addWidget(toolbar)

        self.view = PianoRollView(run_command, self._say, pitch_colour)
        self.view.edited.connect(self._on_edited)
        layout.addWidget(self.view, 1)

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
        self.show()
        self.view.setFocus()
        self._refresh_toolbar()

    def close_track(self):
        self.track_index = None
        self.view.close_track()
        self.hide()

    @property
    def is_open(self):
        return self.track_index is not None

    # -- toolbar --------------------------------------------------------

    def _refresh_toolbar(self):
        note_mode = self.view.in_note_mode()
        self.mode_button.setText(
            "note mode (enter)" if note_mode else "cursor mode (enter)")
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
