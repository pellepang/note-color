"""The Synth View's key-box band (map #145, ticket #157, stage 2 of 3):
layout state (dual/hybrid/allpads/custom), per-row synth-patch/pad-kit
assignment, flat key-box rendering, and the live keyboard-to-preview
wiring.

Deliberately not built on `tui/synth_layout.py`'s `Layout`/`KeySlot` --
that module's geometry is a piano-shaped 4-row grid (black keys drawn
between white ones); the accepted prototype's key boxes are flat and
uniform, so this module only borrows that module's *concepts* (layout
names, `is_dual`-style duality, `sample_hue_step()` for pad tint) rather
than its classes. Pitch itself always comes from
`notation/score_audition.py`'s `PIANO_KEY_SEMITONES` table (via
`pitch_for_key()`), the one place this repo's QWERTY-to-pitch mapping is
allowed to live.

No mode toggle, unlike the piano roll's Insert/Esc gate (#158): every key
on an active row always plays, matching the terminal synth tool's
"always plays" invariant (decision #107). `SynthKeyboardBand` never calls
a sound engine itself -- it emits `notePreviewRequested`/`noteReleased`
and leaves the engine call, the recorder hookup and the Panic/Record
wiring to `synth_view.py` (stage 3), per the plan's controller-interface
split.
"""

from collections import namedtuple

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.gui import theme
from notecolor.settings import config
from notecolor.notation.score_audition import (
    PIANO_LOWER_ROW, PIANO_UPPER_ROW, pitch_for_key, clamp_base_octave,
)
from notecolor.tui.synth_layout import NOTE_CHANNEL, PAD_CHANNEL, sample_hue_step
from notecolor.analysis.color_map import NOTE_NAMES_FIFTHS
from notecolor.audio.sound_engine import midi_pitch

#: A note preview/pad hit, carried by `notePreviewRequested`. `pitch` is a
#: MIDI note number (synth rows) or the kit zone's own key (pad rows, so a
#: sampler note-on lands on the right zone); `name` is the patch name
#: (synth) or the sample's bare filename (pad) -- either can be `None`
#: when the row has nothing assigned yet (no patches/kits on disk).
NotePreview = namedtuple("NotePreview", "pitch name channel velocity")

#: QWERTY has no velocity sensing -- every key plays at the same, full
#: velocity, same posture `score_audition.EDITOR_AUDITION_VELOCITY`-style
#: constants elsewhere in this repo take for a keyboard with no analog input.
DEFAULT_VELOCITY = 1.0

#: How many names the recents rail/popup "Recent" section remembers, per
#: layout tab (ticket #184) -- an assignment (row pill pick, per-key
#: override, or a rail drag-drop), not a mere note preview, is what counts
#: as "used": previewing a note by playing is noisy signal, deliberately
#: reassigning a sound is not.
RECENTS_LIMIT = 8

# --------------------------------------------------------------------------
# Layout state
# --------------------------------------------------------------------------

LAYOUT_DUAL = "dual"
LAYOUT_HYBRID = "hybrid"
LAYOUT_ALLPADS = "allpads"
LAYOUT_CUSTOM = "custom"

#: Tab-cycle order, matching decision #107's convention on the terminal
#: synth tool: dual -> hybrid -> allpads -> custom -> back to dual.
LAYOUT_ORDER = (LAYOUT_DUAL, LAYOUT_HYBRID, LAYOUT_ALLPADS, LAYOUT_CUSTOM)

#: A custom row split appends this suffix to its base row key ("upper" ->
#: "upper-r") for the right half -- the left half keeps the base row key.
HALF_SUFFIX = "-r"

BASE_ROWS = ("upper", "lower")


class KeyboardLayoutState:
    """Which of the four layouts is active, plus which custom rows are
    currently split. Kind resolution (which row plays synth vs pad) is a
    pure function of this state (`kind_for_row_key()`), not stored here --
    only the two things the prototype's own state actually holds: the
    layout name and the split flags.
    """

    def __init__(self):
        self.layout = LAYOUT_DUAL
        #: Only consulted while `layout == LAYOUT_CUSTOM`; harmless to
        #: leave set while cycled away from custom, since nothing reads it
        #: in another layout.
        self.split = {"upper": False, "lower": False}

    def cycle(self):
        index = LAYOUT_ORDER.index(self.layout)
        self.layout = LAYOUT_ORDER[(index + 1) % len(LAYOUT_ORDER)]
        return self.layout

    def toggle_split(self, base_row):
        """No-op outside custom -- split is a custom-only concept, and a
        stray toggle from another layout must not leave the split flag set
        for when the user cycles back into custom later expecting a fresh
        (unsplit) row."""
        if self.layout != LAYOUT_CUSTOM:
            return
        self.split[base_row] = not self.split[base_row]

    def active_row_keys(self):
        """Every row key on screen right now, in display order: both base
        rows always, plus a right half for each split custom row."""
        keys = []
        for base_row in BASE_ROWS:
            keys.append(base_row)
            if self.layout == LAYOUT_CUSTOM and self.split.get(base_row):
                keys.append(base_row + HALF_SUFFIX)
        return keys


def kind_for_row_key(layout, row_key, custom_kinds):
    """"synth" or "pad" for a row key, given the layout and (for custom
    only) each base row's user-chosen kind. A split row's right half
    defaults to the *other* kind from its left half (`customRow()` in the
    prototype) -- a split row that was both halves the same kind would be
    indistinguishable from not splitting it at all."""
    is_right_half = row_key.endswith(HALF_SUFFIX)
    base_row = row_key[: -len(HALF_SUFFIX)] if is_right_half else row_key
    if layout == LAYOUT_DUAL:
        base_kind = "synth"
    elif layout == LAYOUT_HYBRID:
        base_kind = "synth" if base_row == "upper" else "pad"
    elif layout == LAYOUT_ALLPADS:
        base_kind = "pad"
    else:
        base_kind = custom_kinds.get(base_row, "synth")
    if not is_right_half:
        return base_kind
    return "pad" if base_kind == "synth" else "synth"


# --------------------------------------------------------------------------
# Row assignment: which patch/kit each row currently plays
# --------------------------------------------------------------------------

class RowAssignments:
    """Which synth patch or pad kit each row key currently plays, plus (for
    custom layouts) each base row's own synth/pad kind. `synth_names` and
    `kit_names` are the two lists an index can select into -- sorted,
    supplied by the caller (disk-scanned in the real app, hand-built in
    tests) rather than read from disk here, so this class has no I/O of
    its own.

    Indices are kept independently per row key (including the two split
    halves), so cycling "upper-r" never disturbs "upper"'s own position in
    whichever list it's on.
    """

    ALL_ROW_KEYS = ("upper", "lower", "upper-r", "lower-r")

    def __init__(self, synth_names, kit_names):
        self.synth_names = list(synth_names)
        self.kit_names = list(kit_names)
        self._index = {key: 0 for key in self.ALL_ROW_KEYS}
        #: Custom layout only: each base row's own kind. Defaults mirror
        #: hybrid's split (upper synth, lower pad) -- a reasonable "already
        #: playable" starting point rather than both rows on the same kind.
        self.custom_kinds = {"upper": "synth", "lower": "pad"}
        #: Per-key override, ticket #184: `(row_key, letter) -> name`,
        #: consulted ahead of the row default by `name_for_key()`. A key
        #: with no entry here just plays whatever its row plays.
        self.key_override = {}
        #: Most-recently-assigned names, most recent first -- capped at
        #: `RECENTS_LIMIT`. Own state per `RowAssignments` instance, which
        #: (per `SynthKeyboardBand`) means one recents list per layout tab.
        self.recents = []

    def names_for_kind(self, kind):
        return self.synth_names if kind == "synth" else self.kit_names

    def name_for(self, row_key, layout_state):
        kind = kind_for_row_key(layout_state.layout, row_key, self.custom_kinds)
        names = self.names_for_kind(kind)
        if not names:
            return None
        return names[self._index[row_key] % len(names)]

    def name_for_key(self, row_key, letter, layout_state):
        """The name `letter` on `row_key` actually plays: its own
        per-key override if one was set, else the row's default."""
        override = self.key_override.get((row_key, letter))
        if override is not None:
            return override
        return self.name_for(row_key, layout_state)

    def cycle_row(self, row_key, layout_state, direction=1):
        """Steps `row_key`'s index within whichever list its current kind
        selects into, wrapping at both ends. Returns the new name, or None
        when that list is empty (no patches/kits on disk at all)."""
        kind = kind_for_row_key(layout_state.layout, row_key, self.custom_kinds)
        names = self.names_for_kind(kind)
        if not names:
            return None
        self._index[row_key] = (self._index[row_key] + direction) % len(names)
        name = names[self._index[row_key]]
        self.record_recent(name)
        return name

    def assign_row(self, row_key, layout_state, name):
        """Jumps `row_key` straight to `name` -- what a popup pick makes,
        as opposed to `cycle_row()`'s relative step. A no-op if `name`
        isn't in the list its current kind selects into."""
        kind = kind_for_row_key(layout_state.layout, row_key, self.custom_kinds)
        names = self.names_for_kind(kind)
        if name not in names:
            return
        self._index[row_key] = names.index(name)
        self.record_recent(name)

    def set_key_override(self, row_key, letter, name):
        self.key_override[(row_key, letter)] = name
        self.record_recent(name)

    def clear_key_override(self, row_key, letter):
        self.key_override.pop((row_key, letter), None)

    def record_recent(self, name):
        if not name:
            return
        if name in self.recents:
            self.recents.remove(name)
        self.recents.insert(0, name)
        del self.recents[RECENTS_LIMIT:]

    def toggle_custom_kind(self, base_row):
        self.custom_kinds[base_row] = (
            "pad" if self.custom_kinds.get(base_row) == "synth" else "synth"
        )

    # -- snapshot/restore: per-layout-tab persistence (ticket #184) --------

    def snapshot(self):
        return {
            "index": dict(self._index),
            "custom_kinds": dict(self.custom_kinds),
            "key_override": dict(self.key_override),
            "recents": list(self.recents),
        }

    def restore(self, data):
        self._index = {**self._index, **data.get("index", {})}
        self.custom_kinds = dict(data.get("custom_kinds", self.custom_kinds))
        self.key_override = dict(data.get("key_override", {}))
        self.recents = list(data.get("recents", []))


# --------------------------------------------------------------------------
# Patch/kit discovery -- no ready-made "list kits" helper exists, so this
# builds one locally, scanning once and caching rather than on every render.
# --------------------------------------------------------------------------

_patch_cache = None


#: The popup picker's "folders" (ticket #184) group patches by data the
#: patch already carries, never an invented tag -- a kit is grouped under
#: one bucket (a kit has no field to split further on cheaply), a synth
#: patch under its own `osc1.waveform`, which is real, already-saved data
#: that meaningfully clusters "what does this sound roughly like".
KIT_FOLDER = "Kits"
OTHER_FOLDER = "Other"


def discover_patches():
    """(sorted synth-patch names, {kit name: [Zone, ...] sorted by
    low_key}, {patch name: folder label}) -- scans `patch_paths()` and
    loads each patch once. A patch that fails to load is skipped, same
    degrade-don't-crash posture `patch_format.load_patch()` itself
    already takes for a malformed file."""
    from notecolor.settings import patch_format

    synth_names = []
    kit_zones = {}
    folders = {}
    for path in patch_format.patch_paths():
        try:
            patch = patch_format.load_patch(path)
        except (OSError, ValueError):
            continue
        name = patch_format.patch_name_for_path(path)
        if patch.is_kit():
            kit_zones[name] = sorted(patch.zones, key=lambda z: z.low_key)
            folders[name] = KIT_FOLDER
        else:
            synth_names.append(name)
            folders[name] = patch.osc1.waveform.title() if patch.osc1.waveform else OTHER_FOLDER
    return sorted(synth_names), kit_zones, folders


def cached_discover_patches(force=False):
    """The lazy-once cache `SynthKeyboardBand` uses when it isn't handed
    explicit names/kits (real app usage) -- `force=True` re-scans, for
    whatever calls this after the user saves a new patch."""
    global _patch_cache
    if force or _patch_cache is None:
        _patch_cache = discover_patches()
    return _patch_cache


# --------------------------------------------------------------------------
# Colour: saturation means pitch (or, for pads, means sample)
# --------------------------------------------------------------------------

def _synth_lit_colour(pitch_class):
    # Imported lazily: `studio.py` is the app's main window and does not
    # need to import this module at class-definition time just so this
    # function can borrow one colour helper from it.
    from notecolor.gui.studio import pitch_colour

    return pitch_colour(pitch_class)


def _pad_lit_colour(sample_name):
    from notecolor.analysis.color_map import hue_for_step, hsl_to_rgb255

    step = sample_hue_step(sample_name)
    if step is None:
        return None
    r, g, b = hsl_to_rgb255(hue_for_step(step), 0.62, 0.66)
    return QtGui.QColor(r, g, b, 255)


# --------------------------------------------------------------------------
# Rendering widgets
# --------------------------------------------------------------------------

class KeyBoxRow(QtWidgets.QWidget):
    """A row of flat key boxes: physical key letter on top, note/sample
    name below, coloured only while the key is held (idle boxes carry no
    colour -- the app's "saturation means pitch" rule).

    Also the click/drop surface for per-key assignment (ticket #184): a
    left click on a box asks to open the popup picker scoped to that one
    key (`boxClicked`); a drag-and-drop from the recents rail asks to
    assign that key directly (`boxDropped`), reusing `synth_workspace.
    Canvas`'s plain-text-`QMimeData` drag pattern."""

    #: Reference side length for a box at its unscaled (minimum) size --
    #: boxes are square (ticket #186: "the buttons should be square", not
    #: just resized to an arbitrary aspect), so there is one size constant,
    #: not an independent width/height pair.
    BOX_SIZE = 36
    GAP = 3
    #: Vertical raise for a black-key box, in pixels -- enough to read as a
    #: deliberate piano-style stagger at `BOX_SIZE`'s scale without looking
    #: broken. Black keys sit above center; white/pad keys sit staggered
    #: down by this many pixels below them.
    STAGGER = 10
    #: Equal top/bottom breathing room so the black+white contour sits
    #: centered in the row instead of flush top (black) / flush bottom
    #: (white) the way an unmargined stagger reads (ticket #184).
    MARGIN = STAGGER // 2

    #: Small marker for a per-key override, drawn top-right of the box --
    #: the "amber dot" idea from the accepted prototype.
    OVERRIDE_DOT = 4

    boxClicked = QtCore.Signal(str)
    boxDropped = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._boxes = []
        #: Ticket #184 bug report: keys must resize to fill the available
        #: view rather than staying pinned to `BOX_W`/`BOX_H` -- so this
        #: widget now takes whatever room its layout gives it (down to
        #: `minimumSizeHint()`) and `_geometry()` derives the actual
        #: on-screen box size from `self.width()`/`self.height()` at
        #: paint time, instead of `set_boxes()` dictating a fixed size.
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def set_boxes(self, boxes):
        """`boxes`: list of {"letter", "label", "color" (QColor or None),
        "is_black" (bool, synth rows only -- pad rows omit/default False),
        "overridden" (bool, optional -- draws the per-key marker dot)}."""
        self._boxes = boxes
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        count = max(1, len(self._boxes))
        width = count * (self.BOX_SIZE + self.GAP) - self.GAP
        height = self.BOX_SIZE + self.STAGGER + 2 * self.MARGIN
        return QtCore.QSize(width, height)

    def _geometry(self):
        """(box_side, gap, stagger, margin, x_offset, y_offset) actually
        drawn this paint. Ticket #186: boxes are square and scale together
        (never independently in width vs. height, unlike the #184-era
        version this replaced) to fill the available *height*, then are
        centered both horizontally (in case the row is wider than the
        squares need, e.g. a short/narrow-count row) and vertically (in
        case the available width is the tighter constraint, e.g. many
        boxes in a narrow footer) -- never scaled down below the reference
        size (the widget's own `minimumSizeHint()` is what keeps a layout
        from ever handing it less room than that in the first place)."""
        count = max(1, len(self._boxes))
        gap = float(self.GAP)

        ref_height = self.BOX_SIZE + self.STAGGER + 2 * self.MARGIN
        scale_h = max(1.0, self.height() / ref_height) if ref_height else 1.0
        scale_w = max(1.0, (self.width() - (count - 1) * gap) / (count * self.BOX_SIZE))
        scale = min(scale_h, scale_w)

        box_side = self.BOX_SIZE * scale
        stagger = self.STAGGER * scale
        margin = self.MARGIN * scale

        content_width = count * (box_side + gap) - gap
        content_height = box_side + stagger + 2 * margin
        x_offset = max(0.0, (self.width() - content_width) / 2)
        y_offset = max(0.0, (self.height() - content_height) / 2)

        return box_side, gap, stagger, margin, x_offset, y_offset

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        box_side, gap, stagger, margin, x_offset, y_offset = self._geometry()
        x = x_offset
        for box in self._boxes:
            y = y_offset + (margin if box.get("is_black") else margin + stagger)
            rect = QtCore.QRectF(x, y, box_side, box_side)
            colour = box.get("color")
            if colour is not None:
                painter.fillRect(rect, colour)
                painter.setPen(QtGui.QPen(theme.RULE_STRONG, 1))
            else:
                painter.fillRect(rect, theme.CHROME_DEEP)
                painter.setPen(QtGui.QPen(theme.RULE, 1))
            painter.drawRect(rect)

            painter.setFont(theme.font(7, bold=True))
            painter.setPen(theme.TEXT if colour is not None else theme.TEXT_FAINT)
            painter.drawText(QtCore.QRectF(x, y + 2, box_side, 13),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             box.get("letter", "").upper())

            painter.setFont(theme.font(6))
            painter.setPen(theme.TEXT if colour is not None else theme.TEXT_DIM)
            painter.drawText(QtCore.QRectF(x, y + 17, box_side, box_side - 17),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             box.get("label", ""))

            if box.get("overridden"):
                dot = QtCore.QRectF(x + box_side - self.OVERRIDE_DOT - 2, y + 2,
                                    self.OVERRIDE_DOT, self.OVERRIDE_DOT)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(theme.AMBER)
                painter.drawEllipse(dot)
                painter.setBrush(QtCore.Qt.NoBrush)

            x += box_side + gap

    # -- per-key click / drag-drop -----------------------------------------

    def _box_index_at(self, x):
        if not self._boxes:
            return None
        box_side, gap, _stagger, _margin, x_offset, _y_offset = self._geometry()
        index = int((x - x_offset) // (box_side + gap))
        if 0 <= index < len(self._boxes):
            return index
        return None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            index = self._box_index_at(event.position().x())
            if index is not None:
                self.boxClicked.emit(self._boxes[index]["letter"])
                event.accept()
                return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        index = self._box_index_at(event.position().toPoint().x())
        if index is not None:
            self.boxDropped.emit(self._boxes[index]["letter"], event.mimeData().text())
            event.acceptProposedAction()


class AssignmentPill(QtWidgets.QWidget):
    """The row's current patch/kit name, click-to-open. Wheel-to-step
    remains the fast path (matches the terminal synth's "swept by ear"
    convention); a left click opens the popup picker instead of the old
    chevron-click-to-step -- ticket #184 traded the chevrons for that
    popup, since the popup now also groups patches into folders and
    surfaces recents, which a plain step can't."""

    stepRequested = QtCore.Signal(int)
    clicked = QtCore.Signal()
    dropped = QtCore.Signal(str)

    #: Matches the accepted prototype's `.pill{width:66px}` -- fixed rather
    #: than text-driven, so every row's pill lines up regardless of how long
    #: the assigned patch/kit name is (long names elide in `set_text()`
    #: instead of stretching the pill).
    WIDTH = 66

    def __init__(self, parent=None):
        super().__init__(parent)
        # Required for a plain QWidget subclass to actually paint the
        # background/border set via setStyleSheet() below -- without this,
        # Qt never applies a bare QWidget's stylesheet background at all,
        # which is why this pill rendered as invisible (just floating label
        # text over the footer's own chrome) in both #184 passes. Same fix
        # `synth_workspace.py`'s `_ModuleWindow` already applies for the
        # identical reason (see its own comment there).
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"AssignmentPill {{ background: {theme.rgba(theme.PANEL)}; "
            f"border: 1px solid {theme.rgba(theme.RULE)}; }}"
            f"AssignmentPill:hover {{ border: 1px solid {theme.rgba(theme.FOCUS)}; }}")
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedWidth(self.WIDTH)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)

        self._label = QtWidgets.QLabel("--", self)
        self._label.setFont(theme.font(8, bold=True))
        # `padding: 0` must be explicit here, not just omitted: an ordinary
        # child QLabel (unlike a QMenu) does inherit `theme.main_stylesheet()`
        # `QLabel { padding: 8px; }` rule from `SynthView`'s own stylesheet,
        # and this instance's own stylesheet only overrides the properties it
        # names -- leaving `padding` cascaded in from there widened the label
        # past the pill's fixed width and got centre-clipped (an "Fat Bass"
        # rendered as "at Bas"), independent of the WA_StyledBackground fix.
        self._label.setStyleSheet(
            f"background: transparent; color: {theme.rgba(theme.TEXT)}; padding: 0;")
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self._label, 1)

    def set_text(self, text):
        text = text or "--"
        available = self.WIDTH - 12  # minus the layout's 6px left/right margins
        metrics = QtGui.QFontMetrics(self._label.font())
        elided = metrics.elidedText(text, QtCore.Qt.ElideRight, available)
        self._label.setText(elided)
        self._label.setToolTip(text if elided != text else "")

    def wheelEvent(self, event):
        direction = 1 if event.angleDelta().y() > 0 else -1
        self.stepRequested.emit(direction)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.dropped.emit(event.mimeData().text())
        event.acceptProposedAction()


class _RecentChip(QtWidgets.QLabel):
    """One draggable recents-rail chip -- the same `QDrag`/plain-text-
    `QMimeData` pattern `synth_workspace._DrawerRow` established for
    drawer-to-canvas dragging (issue #167/#175), reused here for
    rail-to-key/row dragging rather than inventing a second one."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.name = name
        self.setFont(theme.font(7))
        self.setContentsMargins(7, 3, 7, 3)
        self.setStyleSheet(
            f"background: {theme.rgba(theme.ink(theme.AMBER, 30))}; "
            f"color: {theme.rgba(theme.ink(theme.AMBER))}; "
            f"border: 1px solid {theme.rgba(theme.RULE_STRONG)};")
        self.setCursor(QtCore.Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setText(self.name)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        drag.exec(QtCore.Qt.CopyAction)


class RecentsRail(QtWidgets.QWidget):
    """The full-footer-width strip of recently-assigned patches/kits above
    the keyboard rows -- prototype round 2's "variant 2" (the pinned
    rail), the direction the user signed off on. Pure drag source: a
    chip dragged onto an `AssignmentPill` or a `KeyBoxRow` box assigns it
    there via those widgets' own drop handling, same division of labour
    `synth_workspace.Drawer`/`Canvas` already use."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.rgba(theme.CHROME)};")
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(6)
        self._label = QtWidgets.QLabel("RECENT", self)
        self._label.setFont(theme.font(7))
        self._label.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        self._layout.addWidget(self._label)
        #: Shown only while `set_names([])` -- an explicit "nothing yet"
        #: placeholder, so the rail (and its "RECENT" label) still occupy
        #: their full-width strip on a fresh session instead of the whole
        #: rail disappearing (ticket #184 bug report: hiding this widget
        #: whenever there were no recents yet made it read as "not
        #: visible at all", and its `sizeHint()` -- used by `synth_view.
        #: SynthView` to size the splitter's playable minimum -- silently
        #: excluded a hidden widget's height, throwing off that whole
        #: calculation too).
        self._empty_hint = QtWidgets.QLabel("none yet", self)
        self._empty_hint.setFont(theme.font(7))
        self._empty_hint.setStyleSheet(
            f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)}; font-style: italic;")
        self._layout.addWidget(self._empty_hint)
        self._layout.addStretch(1)
        self._chips = []

    def set_names(self, names):
        for chip in self._chips:
            self._layout.removeWidget(chip)
            chip.deleteLater()
        self._chips = []
        self._empty_hint.setVisible(not names)
        insert_at = self._layout.count() - 1  # just before the trailing stretch
        for name in names:
            chip = _RecentChip(name, self)
            self._layout.insertWidget(insert_at, chip)
            insert_at += 1
            self._chips.append(chip)


# --------------------------------------------------------------------------
# The live-play widget
# --------------------------------------------------------------------------

#: Reverse `Qt.Key` -> physical key-token map, built the exact same way
#: `piano_roll_panel._PIANO_ENTRY_KEYS` builds its own -- `ord(c.upper())`
#: happens to equal the matching `Qt.Key_<letter/digit>` constant for every
#: character on this keyboard (letters and the digits `PIANO_UPPER_ROW`
#: uses alike), which is why that module doesn't hand-list `Qt.Key_Z`,
#: `Qt.Key_2`, etc. one by one and neither does this one.
_PIANO_KEYS = {QtCore.Qt.Key(ord(c.upper())): c for c in PIANO_LOWER_ROW + PIANO_UPPER_ROW}


def _delete_layout_item(item):
    """`deleteLater()`s every widget under a `takeAt()`-taken `QLayoutItem`,
    including ones nested inside a sub-layout. A plain `item.widget()`
    check misses everything inside a row built with `addLayout()` (as
    `_build_base_row`'s rows are) -- that check alone silently leaked every
    row's chip label/pill/key-box-row widgets on every layout switch, since
    the taken item's `.widget()` is always `None` for a nested layout."""
    widget = item.widget()
    if widget is not None:
        widget.deleteLater()
        return
    layout = item.layout()
    if layout is not None:
        while layout.count():
            _delete_layout_item(layout.takeAt(0))


class SynthKeyboardBand(QtWidgets.QWidget):
    """The key-box band stage 3 drops into the Synth View window: owns the
    layout state, per-row assignment, `base_octave`, and turns physical
    key presses into preview/pad signals -- no sound engine call and no
    mode gate, per decision #107.

    `synth_names`/`kit_zone_names` let a caller (tests, or a future
    "no patches yet" empty state) supply the assignable lists directly;
    left `None`, the widget scans the real patch directory once via
    `cached_discover_patches()`.
    """

    #: Fires on every key-down for an active row -- see `NotePreview`.
    notePreviewRequested = QtCore.Signal(object)
    #: Fires on key-up. Payload is just the physical key letter: enough of
    #: a correlation id for a caller that tracks voice ids by key, per the
    #: plan (it also avoids this widget needing to remember what it sent
    #: on the matching key-down after the assignment has since changed).
    noteReleased = QtCore.Signal(object)
    #: Fires whenever `layout_state.layout` actually changes -- via `Tab`
    #: (`keyPressEvent`) or a direct jump (`set_layout()`) -- so external UI
    #: (the layout-tabs bar in `synth_view.py`) can keep its highlighted tab
    #: in sync. Not fired by `_toggle_kind()`/`_toggle_split()`, which also
    #: call `_rebuild_structure()` but never change `.layout` itself.
    layoutChanged = QtCore.Signal(str)
    #: Fires on Shift+M -- the "Panic" shortcut. Kept distinct from plain
    #: `m`, which is a live note-preview key (LOWER row) via `_PIANO_KEYS`.
    panicRequested = QtCore.Signal()

    def __init__(self, synth_names=None, kit_zone_names=None, base_octave=None, parent=None,
                 patch_folders=None):
        super().__init__(parent)
        if synth_names is None and kit_zone_names is None:
            synth_names, kit_zone_names, discovered_folders = cached_discover_patches()
            if patch_folders is None:
                patch_folders = discovered_folders
        self._kit_zones = dict(kit_zone_names or {})
        #: name -> folder label for the popup picker (ticket #184). Left
        #: unmapped names fall back to `OTHER_FOLDER`/`KIT_FOLDER` in
        #: `_folder_for()` rather than raising, so a name that's real but
        #: wasn't handed a folder (tests, mostly) still groups sanely.
        self._patch_folders = dict(patch_folders or {})
        self.layout_state = KeyboardLayoutState()
        #: One independent `RowAssignments` per layout tab (ticket #184):
        #: Dual/Hybrid/AllPads/Custom each keep their own row/key
        #: assignments and recents, exactly like separate workspaces.
        #: `self.assignments` always points at the active tab's instance.
        self._assignments_by_layout = {
            layout: RowAssignments(synth_names or [], sorted(self._kit_zones.keys()))
            for layout in LAYOUT_ORDER
        }
        self.assignments = self._assignments_by_layout[self.layout_state.layout]
        self.base_octave = clamp_base_octave(
            config.SYNTH_BASE_OCTAVE if base_octave is None else base_octave)

        #: `{physical key letter: {"row_key", "pitch"}}` -- the dedupe set
        #: for OS key-repeat (a repeat re-lands here, already present) and
        #: what key-up looks up to un-light the right box.
        self._held = {}
        self._row_widgets = {}   # row_key -> (AssignmentPill, KeyBoxRow)
        self._kind_buttons = {}  # base_row -> QToolButton, custom-only
        self._split_buttons = {}
        #: Keeps whichever popup `_show_popup()` most recently opened alive
        #: (see its own docstring for why) and gives tests/verification
        #: scripts a handle to inspect the open popup.
        self._active_popup = None

        self._body = QtWidgets.QVBoxLayout(self)
        self._body.setContentsMargins(4, 4, 4, 4)
        self._body.setSpacing(6)

        hint = QtWidgets.QLabel("↑/↓ octave  ·  Tab layout", self)
        hint.setFont(theme.font(7))
        hint.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        self._body.addWidget(hint)

        self.recents_rail = RecentsRail(self)
        self._body.addWidget(self.recents_rail)

        #: Stretch 1 (rather than the layout default of 0), so this row of
        #: key-box rows -- not the fixed-height hint label/recents rail
        #: above it -- is what actually grows to fill any extra vertical
        #: room the footer gets handed (ticket #184 bug report: keys were
        #: staying pinned to the top with dead space below as the footer
        #: grew, instead of the whole band filling/recentering).
        self._rows_layout = QtWidgets.QVBoxLayout()
        self._rows_layout.setSpacing(4)
        self._body.addLayout(self._rows_layout, 1)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._rebuild_structure()

    # -- structure: which pill/box-row widgets exist right now -----------

    def _rebuild_structure(self):
        while self._rows_layout.count():
            _delete_layout_item(self._rows_layout.takeAt(0))
        self._row_widgets = {}
        self._kind_buttons = {}
        self._split_buttons = {}

        # Upper row drawn above lower, matching the prototype and a real
        # keyboard's own up/down sense. Stretch 1 each, so the two rows
        # share any extra vertical room equally.
        for base_row in ("upper", "lower"):
            self._rows_layout.addLayout(self._build_base_row(base_row), 1)

        self._refresh_boxes()

    def _build_base_row(self, base_row):
        row_box = QtWidgets.QHBoxLayout()
        row_box.setSpacing(6)

        if self.layout_state.layout == LAYOUT_CUSTOM:
            kind_button = QtWidgets.QToolButton(self)
            kind_button.setAutoRaise(True)
            kind_button.setText(self.assignments.custom_kinds.get(base_row, "synth").title())
            kind_button.clicked.connect(lambda _c=False, r=base_row: self._toggle_kind(r))
            row_box.addWidget(kind_button)
            self._kind_buttons[base_row] = kind_button

            split_button = QtWidgets.QToolButton(self)
            split_button.setAutoRaise(True)
            split_button.setCheckable(True)
            split_button.setChecked(self.layout_state.split.get(base_row, False))
            split_button.setText("+ split")
            split_button.clicked.connect(lambda _c=False, r=base_row: self._toggle_split(r))
            row_box.addWidget(split_button)
            self._split_buttons[base_row] = split_button

        active = self.layout_state.active_row_keys()
        row_keys = [base_row]
        if base_row + HALF_SUFFIX in active:
            row_keys.append(base_row + HALF_SUFFIX)

        for row_key in row_keys:
            if len(row_keys) == 2 and row_key == row_keys[1]:
                row_box.addSpacing(2)
                divider = QtWidgets.QFrame(self)
                divider.setFrameShape(QtWidgets.QFrame.VLine)
                divider.setFixedWidth(1)
                divider.setStyleSheet(f"background: {theme.rgba(theme.RULE)}; border: none;")
                row_box.addWidget(divider)
                row_box.addSpacing(2)

            half_box = QtWidgets.QVBoxLayout()
            half_box.setSpacing(2)
            pill = AssignmentPill(self)
            pill.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            pill.stepRequested.connect(lambda direction, rk=row_key: self._cycle_row(rk, direction))
            pill.clicked.connect(lambda rk=row_key: self._open_row_popup(rk))
            pill.dropped.connect(lambda name, rk=row_key: self._assign_row(rk, name))
            key_box_row = KeyBoxRow(self)
            key_box_row.boxClicked.connect(lambda letter, rk=row_key: self._open_key_popup(rk, letter))
            key_box_row.boxDropped.connect(
                lambda letter, name, rk=row_key: self._assign_key(rk, letter, name))
            half_box.addWidget(pill)
            half_box.setAlignment(pill, QtCore.Qt.AlignLeft)
            half_box.addWidget(key_box_row)
            # Stretch 1: this column (pill + its keys) is what fills the
            # row's available width -- ticket #184 bug report wanted keys
            # to grow to fill the view rather than sit fixed-size with
            # dead space held open by flanking stretches (the previous,
            # now-removed `insertStretch`/`addStretch` centering here).
            row_box.addLayout(half_box, 1)
            self._row_widgets[row_key] = (pill, key_box_row)

        return row_box

    # -- content refresh: box specs + pill labels, no structural change ---

    def _refresh_boxes(self):
        for row_key, (pill, key_box_row) in self._row_widgets.items():
            pill.set_text(self.assignments.name_for(row_key, self.layout_state))
            key_box_row.set_boxes(self._boxes_for_row(row_key))
        self.recents_rail.set_names(self.assignments.recents)

    def _boxes_for_row(self, row_key):
        base_row = row_key[: -len(HALF_SUFFIX)] if row_key.endswith(HALF_SUFFIX) else row_key
        full_letters = PIANO_UPPER_ROW if base_row == "upper" else PIANO_LOWER_ROW
        active = self.layout_state.active_row_keys()
        if base_row + HALF_SUFFIX in active:
            mid = len(full_letters) // 2
            letters = full_letters[mid:] if row_key.endswith(HALF_SUFFIX) else full_letters[:mid]
        else:
            letters = full_letters

        kind = kind_for_row_key(self.layout_state.layout, row_key, self.assignments.custom_kinds)

        boxes = []
        for position, letter in enumerate(letters):
            lit = letter in self._held
            overridden = (row_key, letter) in self.assignments.key_override
            if kind == "synth":
                pitch_class, octave = pitch_for_key(letter, self.base_octave)
                label = f"{NOTE_NAMES_FIFTHS[pitch_class]}{octave}"
                colour = _synth_lit_colour(pitch_class) if lit else None
                is_black = pitch_class in {1, 3, 6, 8, 10}
                boxes.append({"letter": letter, "label": label, "color": colour,
                              "is_black": is_black, "overridden": overridden})
            else:
                name = self.assignments.name_for_key(row_key, letter, self.layout_state)
                zones = self._kit_zones.get(name)
                if zones:
                    sample = zones[position % len(zones)].sample or "--"
                else:
                    sample = "--"
                label = sample
                colour = _pad_lit_colour(sample) if (lit and zones) else None
                boxes.append({"letter": letter, "label": label, "color": colour,
                              "overridden": overridden})
        return boxes

    # -- layout switching ---------------------------------------------------

    def set_layout(self, layout):
        """Jump straight to `layout` (one of `LAYOUT_ORDER`), as opposed to
        `Tab`'s relative `cycle()` -- what a clicked layout-tab calls."""
        if layout not in LAYOUT_ORDER:
            raise ValueError(f"unknown layout: {layout!r}")
        if layout == self.layout_state.layout:
            return
        self.layout_state.layout = layout
        self.assignments = self._assignments_by_layout[layout]
        self._rebuild_structure()
        self.layoutChanged.emit(self.layout_state.layout)

    def snapshot_assignments(self):
        """Every layout tab's own `RowAssignments` state, for
        `synth_view.py`'s per-patch workspace snapshot (ticket #184)."""
        return {layout: ra.snapshot() for layout, ra in self._assignments_by_layout.items()}

    def restore_assignments(self, data):
        for layout, ra in self._assignments_by_layout.items():
            if layout in data:
                ra.restore(data[layout])
        self.assignments = self._assignments_by_layout[self.layout_state.layout]

    # -- row assignment / custom controls ---------------------------------

    def _cycle_row(self, row_key, direction):
        self.assignments.cycle_row(row_key, self.layout_state, direction)
        self._refresh_boxes()

    def _folder_for(self, name, kind):
        return self._patch_folders.get(name) or (KIT_FOLDER if kind == "pad" else OTHER_FOLDER)

    def _folders_for_kind(self, kind):
        """`{folder label: [name, ...]}`, in first-seen order, over
        whichever list `kind` selects into -- the popup picker's
        "folders" grouping (ticket #184)."""
        folders = {}
        for name in self.assignments.names_for_kind(kind):
            folders.setdefault(self._folder_for(name, kind), []).append(name)
        return folders

    def _add_popup_header(self, menu, title, is_first):
        # Deliberately *not* `menu.addSection(title)`: Qt's titled-separator
        # rendering is style-dependent, and once a custom QSS is applied (as
        # `theme.main_stylesheet()`'s `QMenu::separator { height: 1px; ...}`
        # rule is, just below) a section's title text stopped painting at
        # all -- verified by grabbing a real popup screenshot and finding
        # the folder names silently missing, exactly the failure mode
        # ticket #187 asked to be caught here rather than left for the
        # user's live pass. A `QWidgetAction` wrapping a plain, explicitly
        # styled `QLabel` renders identically regardless of the menu's QSS.
        if not is_first:
            menu.addSeparator()
        header = QtWidgets.QLabel(title.upper(), menu)
        header.setFont(theme.font(7))
        header.setStyleSheet(
            f"background: transparent; color: {theme.rgba(theme.ink(theme.COPPER_LIGHT))}; "
            "padding: 4px 10px 2px;")
        action = QtWidgets.QWidgetAction(menu)
        action.setDefaultWidget(header)
        action.setEnabled(False)
        menu.addAction(action)

    def _build_popup(self, kind, current_name):
        menu = QtWidgets.QMenu(self)
        # A QMenu is its own top-level window (`isWindow()` is true even
        # though it's parented), and Qt stylesheet cascades stop at window
        # boundaries -- so it does *not* pick up `SynthView`'s
        # `theme.main_stylesheet()` automatically the way an ordinary child
        # widget would. Applying that same stylesheet directly is what
        # actually gets the popup its Copper-token look (border/background/
        # selected-item colour) rather than the plain-Qt fallback that made
        # earlier attempts read as "not really a popup" -- reuses the one
        # `QMenu {...}` rule set already defined in `theme.py` instead of
        # duplicating those tokens here.
        menu.setStyleSheet(theme.main_stylesheet())
        is_first = True
        recents = [n for n in self.assignments.recents if n in self.assignments.names_for_kind(kind)]
        if recents:
            self._add_popup_header(menu, "Recent", is_first)
            is_first = False
            for name in recents:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == current_name)
        for folder, names in self._folders_for_kind(kind).items():
            self._add_popup_header(menu, folder, is_first)
            is_first = False
            for name in names:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == current_name)
        return menu

    def _show_popup(self, menu, pos, on_pick):
        # `.popup()` rather than `.exec()`: `.exec()` runs its own nested
        # event loop and blocks the caller until the menu closes, which
        # made this whole picker un-drivable from an automated/headless
        # test (see the stale comment `.exec()` left on the old click
        # test) -- a real click could never be verified to actually open
        # anything, exactly the gap ticket #187 was opened to close.
        # `.popup()` shows the menu and returns immediately; picking an
        # item is handled via `triggered` instead of an `exec()` return
        # value. Keeping a reference on `self` is required -- without it
        # the menu (and its popup) would be garbage-collected the instant
        # this method returns, since nothing else would hold it alive.
        self._active_popup = menu
        menu.triggered.connect(lambda action: on_pick(action.text()))
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(pos)

    def _open_row_popup(self, row_key):
        kind = kind_for_row_key(self.layout_state.layout, row_key, self.assignments.custom_kinds)
        if not self.assignments.names_for_kind(kind):
            return
        pill, _ = self._row_widgets[row_key]
        current = self.assignments.name_for(row_key, self.layout_state)
        menu = self._build_popup(kind, current)
        self._show_popup(
            menu, pill.mapToGlobal(QtCore.QPoint(0, pill.height())),
            lambda name: self._assign_row(row_key, name))

    def _open_key_popup(self, row_key, letter):
        kind = kind_for_row_key(self.layout_state.layout, row_key, self.assignments.custom_kinds)
        if not self.assignments.names_for_kind(kind):
            return
        current = self.assignments.name_for_key(row_key, letter, self.layout_state)
        menu = self._build_popup(kind, current)
        self._show_popup(
            menu, QtGui.QCursor.pos(),
            lambda name: self._assign_key(row_key, letter, name))

    def _assign_row(self, row_key, name):
        self.assignments.assign_row(row_key, self.layout_state, name)
        self._refresh_boxes()

    def _assign_key(self, row_key, letter, name):
        kind = kind_for_row_key(self.layout_state.layout, row_key, self.assignments.custom_kinds)
        if name not in self.assignments.names_for_kind(kind):
            return
        self.assignments.set_key_override(row_key, letter, name)
        self._refresh_boxes()

    def _toggle_kind(self, base_row):
        self.assignments.toggle_custom_kind(base_row)
        self._rebuild_structure()

    def _toggle_split(self, base_row):
        self.layout_state.toggle_split(base_row)
        self._rebuild_structure()

    # -- key letter -> (row_key, position) ---------------------------------

    def _locate(self, letter):
        """(row_key, position-within-that-row-key) for a lowercase letter
        on the tracker keyboard, or None if it isn't one at all. Both rows
        are always active in every layout (per the prototype) -- the only
        way this returns None is a key that was never on the keyboard."""
        if letter in PIANO_LOWER_ROW:
            base_row, full = "lower", PIANO_LOWER_ROW
        elif letter in PIANO_UPPER_ROW:
            base_row, full = "upper", PIANO_UPPER_ROW
        else:
            return None
        index = full.index(letter)
        if base_row + HALF_SUFFIX in self.layout_state.active_row_keys():
            mid = len(full) // 2
            if index < mid:
                return base_row, index
            return base_row + HALF_SUFFIX, index - mid
        return base_row, index

    # -- live key handling --------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()

        if key == QtCore.Qt.Key_Tab:
            if not event.isAutoRepeat():
                self.layout_state.cycle()
                self.assignments = self._assignments_by_layout[self.layout_state.layout]
                self._rebuild_structure()
                self.layoutChanged.emit(self.layout_state.layout)
            event.accept()
            return

        if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            delta = 1 if key == QtCore.Qt.Key_Up else -1
            self.base_octave = clamp_base_octave(self.base_octave + delta)
            self._refresh_boxes()
            event.accept()
            return

        if key == QtCore.Qt.Key_M and event.modifiers() & QtCore.Qt.ShiftModifier:
            if not event.isAutoRepeat():
                self.panicRequested.emit()
            event.accept()
            return

        letter = _PIANO_KEYS.get(key)
        if letter is None:
            return super().keyPressEvent(event)

        # OS key-repeat for an already-held key: swallowed, not
        # retriggered -- same posture piano_roll_panel's note entry takes,
        # and there is no chord-commit step here for a repeat to matter to.
        if event.isAutoRepeat() or letter in self._held:
            event.accept()
            return

        located = self._locate(letter)
        if located is None:
            return super().keyPressEvent(event)
        row_key, position = located
        kind = kind_for_row_key(self.layout_state.layout, row_key, self.assignments.custom_kinds)
        name = self.assignments.name_for_key(row_key, letter, self.layout_state)

        if kind == "synth":
            pitch_class, octave = pitch_for_key(letter, self.base_octave)
            pitch = midi_pitch(pitch_class, octave)
            self._held[letter] = {"row_key": row_key, "pitch": pitch}
            self.notePreviewRequested.emit(
                NotePreview(pitch, name, NOTE_CHANNEL, DEFAULT_VELOCITY))
        else:
            zones = self._kit_zones.get(name)
            if zones:
                zone = zones[position % len(zones)]
                pitch, sample = zone.low_key, zone.sample
            else:
                pitch, sample = None, None
            self._held[letter] = {"row_key": row_key, "pitch": pitch}
            self.notePreviewRequested.emit(
                NotePreview(pitch, sample, PAD_CHANNEL, DEFAULT_VELOCITY))

        self._refresh_boxes()
        event.accept()

    def _release_all_held(self):
        """Release every currently-held key as if each had been physically
        released -- used by panic (both the Shift+M shortcut and the Panic
        button, see `synth_view.SynthView._on_panic_clicked`) so silencing
        audio also clears the visual "lit" state and, for a note in
        progress, lets `noteReleased` reach `record_note_off` the same way
        a real release does. Mirrors `keyReleaseEvent`'s per-key logic;
        iterates over a copy of the keys since `noteReleased` handlers may
        read `_held` (see `SynthView._infer_new_letter`)."""
        for letter in list(self._held.keys()):
            del self._held[letter]
            self.noteReleased.emit(letter)
        self._refresh_boxes()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return super().keyReleaseEvent(event)
        letter = _PIANO_KEYS.get(event.key())
        if letter is None or letter not in self._held:
            return super().keyReleaseEvent(event)
        del self._held[letter]
        self.noteReleased.emit(letter)
        self._refresh_boxes()
        event.accept()

    def event(self, event):
        """Claim this band's own keys ahead of Qt's `ShortcutOverride` pass,
        mirroring `piano_roll_panel.PianoRollView.event()`'s technique --
        see that method's docstring for the actual mechanism (a widget that
        does not explicitly accept a `ShortcutOverride` loses the key to a
        `QAction` shortcut outright, regardless of its own `keyPressEvent`).

        The `ShortcutOverride` branch is belt-and-braces rather than
        load-bearing: as of this writing `studio.py` registers no plain-
        `Tab` (or plain-letter) `QAction` shortcut, so nothing actually
        collides yet. Implemented anyway per the plan, since a future menu
        action easily could.

        The `KeyPress` branch below IS load-bearing: this widget has
        `QtCore.Qt.StrongFocus` and contains focusable child `QToolButton`s
        (the chevrons in `AssignmentPill`, plus the kind/split buttons), so
        Qt's default `QWidget.event()` intercepts a plain `Key_Tab` and
        calls `focusNextPrevChild(true)` *before* `keyPressEvent()` is ever
        reached -- since a focusable child exists, that call succeeds and
        `keyPressEvent()`'s Tab-cycles-layout logic never fires. Routing
        plain Tab straight to `keyPressEvent()` here is what makes Tab
        cycle the keyboard layout instead of moving focus to a chevron
        button. Shift+Tab is left alone: it isn't given any meaning by
        `keyPressEvent()`, so ordinary backwards focus traversal is fine.
        """
        if event.type() == QtCore.QEvent.ShortcutOverride:
            key = event.key()
            if key == QtCore.Qt.Key_Tab or key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down) \
                    or key in _PIANO_KEYS:
                event.accept()
                return True
        if event.type() == QtCore.QEvent.KeyPress:
            key_event = event
            if key_event.key() == QtCore.Qt.Key_Tab and key_event.modifiers() == QtCore.Qt.NoModifier:
                self.keyPressEvent(key_event)
                return True
        return super().event(event)
