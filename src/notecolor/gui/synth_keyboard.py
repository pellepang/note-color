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

    def names_for_kind(self, kind):
        return self.synth_names if kind == "synth" else self.kit_names

    def name_for(self, row_key, layout_state):
        kind = kind_for_row_key(layout_state.layout, row_key, self.custom_kinds)
        names = self.names_for_kind(kind)
        if not names:
            return None
        return names[self._index[row_key] % len(names)]

    def cycle_row(self, row_key, layout_state, direction=1):
        """Steps `row_key`'s index within whichever list its current kind
        selects into, wrapping at both ends. Returns the new name, or None
        when that list is empty (no patches/kits on disk at all)."""
        kind = kind_for_row_key(layout_state.layout, row_key, self.custom_kinds)
        names = self.names_for_kind(kind)
        if not names:
            return None
        self._index[row_key] = (self._index[row_key] + direction) % len(names)
        return names[self._index[row_key]]

    def toggle_custom_kind(self, base_row):
        self.custom_kinds[base_row] = (
            "pad" if self.custom_kinds.get(base_row) == "synth" else "synth"
        )


# --------------------------------------------------------------------------
# Patch/kit discovery -- no ready-made "list kits" helper exists, so this
# builds one locally, scanning once and caching rather than on every render.
# --------------------------------------------------------------------------

_patch_cache = None


def discover_patches():
    """(sorted synth-patch names, {kit name: [Zone, ...] sorted by
    low_key}) -- scans `patch_paths()` and loads each patch once. A patch
    that fails to load is skipped, same degrade-don't-crash posture
    `patch_format.load_patch()` itself already takes for a malformed
    file."""
    from notecolor.settings import patch_format

    synth_names = []
    kit_zones = {}
    for path in patch_format.patch_paths():
        try:
            patch = patch_format.load_patch(path)
        except (OSError, ValueError):
            continue
        name = patch_format.patch_name_for_path(path)
        if patch.is_kit():
            kit_zones[name] = sorted(patch.zones, key=lambda z: z.low_key)
        else:
            synth_names.append(name)
    return sorted(synth_names), kit_zones


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
    colour -- the app's "saturation means pitch" rule)."""

    BOX_W = 34
    BOX_H = 38
    GAP = 3
    #: Vertical raise for a black-key box, in pixels -- enough to read as a
    #: deliberate piano-style stagger at `BOX_H`'s scale without looking
    #: broken. Black keys sit at `y = 0` (top of the taller row); white/pad
    #: keys sit staggered down by this many pixels.
    STAGGER = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._boxes = []
        self.setFixedHeight(self.BOX_H + self.STAGGER)

    def set_boxes(self, boxes):
        """`boxes`: list of {"letter", "label", "color" (QColor or None),
        "is_black" (bool, synth rows only -- pad rows omit/default False)}."""
        self._boxes = boxes
        width = len(boxes) * (self.BOX_W + self.GAP) - self.GAP if boxes else 0
        self.setFixedWidth(max(0, width))
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        x = 0.0
        for box in self._boxes:
            y = 0.0 if box.get("is_black") else float(self.STAGGER)
            rect = QtCore.QRectF(x, y, self.BOX_W, self.BOX_H)
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
            painter.drawText(QtCore.QRectF(x, y + 2, self.BOX_W, 13),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             box.get("letter", "").upper())

            painter.setFont(theme.font(6))
            painter.setPen(theme.TEXT if colour is not None else theme.TEXT_DIM)
            painter.drawText(QtCore.QRectF(x, y + 17, self.BOX_W, self.BOX_H - 17),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             box.get("label", ""))
            x += self.BOX_W + self.GAP


class AssignmentPill(QtWidgets.QWidget):
    """`‹ Name ›`: two chevron buttons plus a name label. Wheel-to-step is
    the primary interaction (matches the terminal synth's "swept by ear"
    convention); the chevrons are the click-target parity the prototype's
    own arrows offered."""

    stepRequested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.rgba(theme.PANEL)};")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(2)

        self._left = QtWidgets.QToolButton(self)
        self._left.setText("‹")
        self._left.setAutoRaise(True)
        self._left.clicked.connect(lambda: self.stepRequested.emit(-1))
        layout.addWidget(self._left)

        self._label = QtWidgets.QLabel("--", self)
        self._label.setFont(theme.font(8, bold=True))
        self._label.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT)};")
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self._label, 1)

        self._right = QtWidgets.QToolButton(self)
        self._right.setText("›")
        self._right.setAutoRaise(True)
        self._right.clicked.connect(lambda: self.stepRequested.emit(1))
        layout.addWidget(self._right)

    def set_text(self, text):
        self._label.setText(text or "--")

    def wheelEvent(self, event):
        direction = 1 if event.angleDelta().y() > 0 else -1
        self.stepRequested.emit(direction)
        event.accept()


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

    def __init__(self, synth_names=None, kit_zone_names=None, base_octave=None, parent=None):
        super().__init__(parent)
        if synth_names is None and kit_zone_names is None:
            synth_names, kit_zone_names = cached_discover_patches()
        self._kit_zones = dict(kit_zone_names or {})
        self.layout_state = KeyboardLayoutState()
        self.assignments = RowAssignments(synth_names or [], sorted(self._kit_zones.keys()))
        self.base_octave = clamp_base_octave(
            config.SYNTH_BASE_OCTAVE if base_octave is None else base_octave)

        #: `{physical key letter: {"row_key", "pitch"}}` -- the dedupe set
        #: for OS key-repeat (a repeat re-lands here, already present) and
        #: what key-up looks up to un-light the right box.
        self._held = {}
        self._row_widgets = {}   # row_key -> (AssignmentPill, KeyBoxRow)
        self._kind_buttons = {}  # base_row -> QToolButton, custom-only
        self._split_buttons = {}

        self._body = QtWidgets.QVBoxLayout(self)
        self._body.setContentsMargins(4, 4, 4, 4)
        self._body.setSpacing(6)

        hint = QtWidgets.QLabel("↑/↓ octave  ·  Tab layout", self)
        hint.setFont(theme.font(7))
        hint.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        self._body.addWidget(hint)

        self._rows_layout = QtWidgets.QVBoxLayout()
        self._rows_layout.setSpacing(4)
        self._body.addLayout(self._rows_layout)

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
        # keyboard's own up/down sense.
        for base_row in ("upper", "lower"):
            self._rows_layout.addLayout(self._build_base_row(base_row))

        self._refresh_boxes()

    def _row_chip_label(self, base_row):
        layout = self.layout_state.layout
        if layout == LAYOUT_DUAL:
            return "Synth B" if base_row == "upper" else "Synth A"
        if layout == LAYOUT_HYBRID:
            return "Synth" if base_row == "upper" else "Pads"
        if layout == LAYOUT_ALLPADS:
            return "Pads Hi" if base_row == "upper" else "Pads Lo"
        # LAYOUT_CUSTOM
        base_label = "Upper" if base_row == "upper" else "Lower"
        if self.layout_state.split.get(base_row, False):
            return f"{base_label} lo"
        return base_label

    def _build_base_row(self, base_row):
        row_box = QtWidgets.QHBoxLayout()
        row_box.setSpacing(6)
        row_box.insertStretch(0, 1)

        chip_label = QtWidgets.QLabel(self._row_chip_label(base_row).upper(), self)
        chip_label.setFont(theme.font(7))
        chip_label.setStyleSheet(
            f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};"
        )
        chip_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        chip_label.setMinimumWidth(52)
        row_box.addWidget(chip_label)

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
            key_box_row = KeyBoxRow(self)
            half_box.addWidget(pill)
            half_box.setAlignment(pill, QtCore.Qt.AlignLeft)
            half_box.addWidget(key_box_row)
            row_box.addLayout(half_box)
            self._row_widgets[row_key] = (pill, key_box_row)

        row_box.addStretch(1)
        return row_box

    # -- content refresh: box specs + pill labels, no structural change ---

    def _refresh_boxes(self):
        for row_key, (pill, key_box_row) in self._row_widgets.items():
            pill.set_text(self.assignments.name_for(row_key, self.layout_state))
            key_box_row.set_boxes(self._boxes_for_row(row_key))

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
        name = self.assignments.name_for(row_key, self.layout_state)
        zones = self._kit_zones.get(name) if kind == "pad" else None

        boxes = []
        for position, letter in enumerate(letters):
            lit = letter in self._held
            if kind == "synth":
                pitch_class, octave = pitch_for_key(letter, self.base_octave)
                label = f"{NOTE_NAMES_FIFTHS[pitch_class]}{octave}"
                colour = _synth_lit_colour(pitch_class) if lit else None
                is_black = pitch_class in {1, 3, 6, 8, 10}
                boxes.append({"letter": letter, "label": label, "color": colour,
                              "is_black": is_black})
            else:
                if zones:
                    sample = zones[position % len(zones)].sample or "--"
                else:
                    sample = "--"
                label = sample
                colour = _pad_lit_colour(sample) if (lit and zones) else None
                boxes.append({"letter": letter, "label": label, "color": colour})
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
        self._rebuild_structure()
        self.layoutChanged.emit(self.layout_state.layout)

    # -- row assignment / custom controls ---------------------------------

    def _cycle_row(self, row_key, direction):
        self.assignments.cycle_row(row_key, self.layout_state, direction)
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
        name = self.assignments.name_for(row_key, self.layout_state)

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
