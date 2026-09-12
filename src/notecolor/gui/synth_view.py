"""The Synth View (map #145, ticket #157, stage 3 of 3): assembles
`synth_workspace.py`'s window manager (`Drawer`/`Canvas`/`ModuleWindow`)
and `synth_keyboard.py`'s key-box band into one window, and wires both to
a real `Patch` -- knob edits through `tui/synth_params.py`'s
`ParamSpec`/`step_value()`, key presses through a `sound_engine_provider`
supplied by a small controller interface (`StudioWindow`, see
`studio.py`).

`SynthView` never imports `studio.StudioWindow` -- only the controller
surface documented on `_ControllerLike` below (informal; Python has no
Protocol enforcement worth adding here). That keeps this module testable
with a bare stub and keeps `studio.py` free to change its own internals
without this file noticing.

**Pad playback is deliberately not routed through `SoundEngine.note_on()`
using the shared `.engine` slot.** `studio.py`'s own recording path
(`StudioWindow._start_synth_recording()`) reads `self.player.engine.engine
.patch_for(...)` to find the synth's current patch; swapping that `.engine`
for a `tui.synth_tool.ChannelRouter` here (the obvious way to reuse
`sound_engine.VoiceManager`'s polyphony budget for both synth and pad
notes) would silently break that lookup for the rest of the app. Instead
a pad note-on calls this view's own `SamplerEngine.note_on()` directly to
get a `Voice`, then hands that `Voice` to the *same* `SoundEngine`'s
`voices.allocate()` -- one shared polyphony budget and output stream,
with `StudioWindow` none the wiser about a second engine existing.
"""

from __future__ import annotations

import functools
import html
import math
import os

from PySide6 import QtCore, QtGui, QtWidgets

from notecolor.gui import theme
from notecolor.gui.synth_workspace import (
    Canvas, Drawer, ModuleWindow, SYNTH_CORE_MODULES, CLAP_TYPE_KEY,
)
from notecolor.gui.synth_keyboard import SynthKeyboardBand, LAYOUT_ORDER, LAYOUT_DUAL
from notecolor.settings import config, patch_format
from notecolor.tui import synth_params
from notecolor.tui.synth_layout import slugify, PAD_CHANNEL
from notecolor.audio import effects as effects_audio
from notecolor.audio.sound_engine import NoteOn
from notecolor.audio.sampler import SamplerEngine

#: Always-open on first show of a never-before-seen patch, per the accepted
#: prototype (`makeWindow('osc1'|'filter'|'ampenv', ...)`).
DEFAULT_MODULES = ("osc1", "filter", "amp_env")

#: type key -> the exact section title `synth_params.sections_for()` uses,
#: so a module window's knobs are bound to the real spec list rather than a
#: hand-copied one.
SECTION_TITLE_FOR_TYPE = {
    "osc1": "OSC 1",
    "osc2": "OSC 2",
    "noise": "NOISE",
    "filter": "FILTER",
    "amp_env": "AMP ENV",
    "filter_env": "FILTER ENV",
    "lfo": "LFO",
    "voice": "VOICE",
}

#: Tab labels for the layout-switcher bar (`.layout-tabs` in the accepted
#: prototype), keyed by `synth_keyboard.LAYOUT_ORDER` entry.
LAYOUT_TAB_LABELS = {
    "dual": "1 DUAL SYNTH",
    "hybrid": "2 SYNTH+PADS",
    "allpads": "3 ALL PADS",
    "custom": "4 CUSTOM",
}

#: Display titles for the three always-open modules (drawer rows already
#: carry their own titles for the draggable ones -- see
#: `synth_workspace.SYNTH_CORE_MODULES`).
_ALWAYS_OPEN_TITLES = {"osc1": "OSC 1", "filter": "FILTER", "amp_env": "AMP ENV"}
_DRAWER_TITLES = dict(SYNTH_CORE_MODULES)

#: Effect module param specs -- `audio/effects.py`'s dataclasses take plain
#: keyword params, not `Patch` attributes, so these describe
#: `EffectSpec.params` dict keys rather than a `Patch` section. Only
#: `step_value()`/`format_value()` are pure functions of a spec + value
#: (no `Patch` access), so those two are all that get reused here.
EFFECT_PARAM_SPECS = {
    "delay": (
        synth_params.ParamSpec("params", "time", "Time", synth_params.KIND_FLOAT,
                                0.001, 2.0, 1.3, synth_params.SCALE_LOG, unit="s", digits=3),
        synth_params.ParamSpec("params", "feedback", "Fdbk", synth_params.KIND_FLOAT,
                                0.0, 0.95, 0.05),
        synth_params.ParamSpec("params", "mix", "Mix", synth_params.KIND_FLOAT,
                                0.0, 1.0, 0.05),
        synth_params.ParamSpec("params", "damping", "Damp", synth_params.KIND_FLOAT,
                                0.0, 1.0, 0.05),
    ),
    "chorus": (
        synth_params.ParamSpec("params", "rate_hz", "Rate", synth_params.KIND_FLOAT,
                                0.0, 100.0, 1.2, synth_params.SCALE_LOG, unit="Hz", digits=2),
        synth_params.ParamSpec("params", "depth_ms", "Depth", synth_params.KIND_FLOAT,
                                0.0, 100.0, 1.0, unit="ms", digits=1),
        synth_params.ParamSpec("params", "centre_delay_ms", "Delay", synth_params.KIND_FLOAT,
                                1.0, 100.0, 1.0, unit="ms", digits=1),
        synth_params.ParamSpec("params", "mix", "Mix", synth_params.KIND_FLOAT,
                                0.0, 1.0, 0.05),
        synth_params.ParamSpec("params", "feedback", "Fdbk", synth_params.KIND_FLOAT,
                                -0.95, 0.95, 0.05),
        synth_params.ParamSpec("params", "voices", "Voices", synth_params.KIND_INT,
                                1, 8, 1),
    ),
}

#: Defaults for an effect's params dict when a module is freshly dropped in
#: (mirrors `audio/effects.py`'s own constructor defaults, sourced from
#: `settings/config.py`).
EFFECT_DEFAULTS = {
    "delay": {
        "time": config.EFFECT_DELAY_TIME_SECONDS,
        "feedback": config.EFFECT_DELAY_FEEDBACK,
        "mix": config.EFFECT_DELAY_MIX,
        "damping": config.EFFECT_DELAY_DAMPING,
    },
    "chorus": {
        "rate_hz": config.EFFECT_CHORUS_RATE_HZ,
        "depth_ms": config.EFFECT_CHORUS_DEPTH_MS,
        "centre_delay_ms": config.EFFECT_CHORUS_CENTRE_DELAY_MS,
        "mix": config.EFFECT_CHORUS_MIX,
        "feedback": config.EFFECT_CHORUS_FEEDBACK,
        "voices": config.EFFECT_CHORUS_VOICES,
    },
}

#: Display labels for the drawer's effect rows -- kept in step with
#: `synth_workspace._effect_drawer_entries()`'s own labels dict (module-
#: private there, so duplicated rather than reached into).
EFFECT_TITLES = {"delay": "Delay", "chorus": "Chorus"}

#: Short descriptive tag shown small/dim next to a module window's title,
#: per the accepted prototype's mock data -- covers every synth-core type
#: key plus every key actually present in `effects_audio.EFFECT_TYPES`.
TAG_FOR_TYPE = {
    "osc1": "saw",
    "osc2": "off",
    "noise": "white",
    "filter": "lp",
    "amp_env": "ADSR",
    "filter_env": "ADSR",
    "lfo": "sine→pitch",
    "voice": "poly 16",
    "delay": "1/8 dot",
    "chorus": "detune",
}

#: Title-bar dot accent color per module type -- per the accepted
#: prototype's mock data (`--amber`/`--teal`/etc), mapped onto this
#: codebase's `theme` constants. `chorus` has no prototype entry (the
#: prototype only mocked eq/reverb/compressor/saturator, none of which
#: exist here); TEAL_PALE is reused for it as a similarly "spacious/
#: modulation" accent, matching the prototype's own reverb choice.
DOT_COLOR_FOR_TYPE = {
    "osc1": theme.AMBER,
    "osc2": theme.TEAL_PALE,
    "noise": theme.EMBER,
    "filter": theme.TEAL,
    "amp_env": theme.CORAL,
    "filter_env": theme.CLAY_RED,
    "lfo": theme.AMBER,
    "voice": theme.LINEN_DIM,
    "delay": theme.AMBER,
    "chorus": theme.TEAL_PALE,
}

ENGINE_PILLS = ("synth", "sampler", "sf2")

STATUS_TIMER_MS = 200

#: Generous but not literally unbounded ceiling for the footer pane
#: (ticket #184) -- big enough that dragging for more room never feels
#: capped in ordinary use, without handing the whole window to the
#: keyboard band.
#: Ceiling for the footer. Deliberately far larger than any real window:
#: the user asked for the footer to go "so far up that it fills the module
#: canvas", so the binding limit should be `_CANVAS_MIN_HEIGHT` below --
#: an actual decision about how much canvas to leave -- and not a
#: leftover constant. `_FooterSplitter.effective_ceiling()` takes the
#: smaller of the two, so on every real window this one never binds.
_FOOTER_MAX_HEIGHT = 1 << 16

#: How little vertical room the canvas row (drawer + module canvas) may be
#: squeezed to. This is what actually stops the footer, and it is a
#: deliberately small sliver: "fills the module canvas" means the canvas
#: keeps just enough to stay grabbable, not a comfortable working height.
#: (Its natural `minimumSizeHint()` is the drawer's full 356px, which left
#: the footer no travel at all -- the original #196 bug.)
_CANVAS_MIN_HEIGHT = 24


#: Same dot spacing `synth_workspace.Canvas.paintEvent` uses for its own
#: background grid (`CANVAS_GRID_SPACING`) -- reused rather than picked
#: independently, so the footer's texture reads as the same visual
#: system as the canvas's, just recoloured for a lighter surface.
_FOOTER_GRID_SPACING = 22


class _FooterSurface(QtWidgets.QWidget):
    """The footer pane's own background: `theme.CHROME` with a dot grid,
    the same convention `synth_workspace.Canvas` uses for the module
    canvas (dots at `_FOOTER_GRID_SPACING` spacing) -- ticket #184's bug
    report #6. The canvas draws lighter dots (`theme.RULE`) on its darker
    `theme.CANVAS`; the footer's `theme.CHROME` is lighter than that, so
    matching dots there means a *darker* token instead (`theme.INK_0`),
    not literally reusing `RULE`, which is lighter than `CHROME`."""

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), theme.CHROME)
        painter.setPen(QtGui.QPen(theme.ink(theme.INK_0), 1))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        spacing = _FOOTER_GRID_SPACING
        y = spacing / 2
        while y < self.height():
            x = spacing / 2
            while x < self.width():
                painter.drawPoint(QtCore.QPointF(x, y))
                x += spacing
            y += spacing


class _DragHandle(QtWidgets.QSplitterHandle):
    """The one handle in `_FooterSplitter`: computes the footer's new
    size itself, straight from the mouse's own on-screen movement, and
    hands it to `_FooterSplitter.setSizes()` -- it never calls
    `super().mousePressEvent()`/`mouseMoveEvent()`, so Qt's own native
    splitter-drag math (`QSplitterPrivate::moveSplitter()`) never runs at
    all for this handle (see the class docstring below for why that
    native path, alone or paired with `childrenCollapsible`, can't do
    this job)."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._drag_start_y = None
        self._drag_start_sizes = None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_y = event.globalPosition().y()
            self._drag_start_sizes = list(self.splitter().sizes())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start_y is None or not (event.buttons() & QtCore.Qt.LeftButton):
            return
        dy = event.globalPosition().y() - self._drag_start_y
        top, bottom = self._drag_start_sizes
        # Dragging down grows the top pane (canvas) and shrinks the
        # bottom one (footer); dragging up does the reverse.
        self.splitter().setSizes([top + dy, bottom - dy])
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start_y = None
        self._drag_start_sizes = None
        event.accept()


def _effective_minimum_height(widget):
    """The height `QSplitter` will actually refuse to shrink `widget` past.

    An explicit `setMinimumHeight()` *overrides* `minimumSizeHint()` -- so
    reading the hint alone reports a floor Qt is not enforcing. That
    distinction is not academic here: `main_row`'s natural hint is the
    drawer's full 356px while its set minimum is `_CANVAS_MIN_HEIGHT`, and
    using the hint left the footer with a computed ceiling *equal to its
    floor* on any window shorter than ~840px -- i.e. no travel at all on
    the 768px-tall screen this is actually used on, which is the very bug
    #196 is about.
    """
    if widget is None:
        return 0
    explicit = widget.minimumHeight()
    return explicit if explicit > 0 else widget.minimumSizeHint().height()


class _FooterSplitter(QtWidgets.QSplitter):
    """A splitter that resizes the footer between a hard floor and a hard
    ceiling. It does not collapse, and that is the whole point.

    This replaces `_CollapsingSplitter` (tickets #162, #184, #185-#190,
    #196). That class snapped the footer shut once dragged below
    `playable_min`, and its docstring was a long account of trying to make
    Qt's native collapse logic and a hand-rolled one agree -- the two
    could never both collapse *and* reopen, because Qt excludes an
    already-hidden pane from its position math and there is no way back.
    Three rounds of tuning later the user's report was unchanged: "it
    doesn't change size and suddenly just disappears."

    The fix was not a fourth round. Collapse is gone. The user asked for
    the footer to "go down and up", not to vanish -- and a footer that can
    hide itself on a stray drag takes the key band, the assignment pill
    and the recents rail (and every click target on them) with it, which
    is what generated the "rail/pill/keys are invisible" reports that were
    never about those widgets at all. If the footer should ever be
    hideable, that is a toggle with a way back, not a drag gesture.

    What is left is deliberately boring: clamp the footer to
    `[floor, ceiling]`, give the remainder to the pane above, never hide
    anything. `_DragHandle` still computes sizes straight from the mouse
    delta and calls `setSizes()`, so interactive dragging and programmatic
    sizing run through this one clamp -- the "no fight" #185 asked for,
    now with nothing left to fight about.

    The floor has to be Qt's own `minimumSizeHint()` rather than a
    hand-picked smaller number: `QLayout` never lets a laid-out widget
    size below its children's own minimum, so a smaller floor is silently
    clamped back up and only produces a dead zone at the bottom of the
    drag.
    """

    def __init__(self, orientation, footer_index, floor, ceiling, parent=None):
        super().__init__(orientation, parent)
        self._footer_index = footer_index
        #: Public: the smallest the footer is ever allowed to be. Read by
        #: `SynthView`'s tests rather than recomputed.
        self.floor = floor
        #: Public: the largest, before the pane above claims its own
        #: minimum -- see `_clamp`, which lowers it when there isn't room.
        self.ceiling = ceiling
        self.setChildrenCollapsible(False)

    def createHandle(self):
        return _DragHandle(self.orientation(), self)

    def setSizes(self, sizes):
        super().setSizes(self._clamp(list(sizes)))

    def effective_ceiling(self, total=None):
        """The real upper bound right now.

        `ceiling` is the design limit; the pane above also has its own
        `minimumSizeHint()`, and on a short window that is the binding
        constraint. Returned as one number so the drag, the tests and any
        caller asking "how far can this go" all read the same value
        instead of each rediscovering the second limit.
        """
        if total is None:
            total = sum(self.sizes()) or self.height()
        other = self.widget(1 - self._footer_index)
        headroom = total - _effective_minimum_height(other)
        return max(self.floor, min(self.ceiling, headroom))

    def _clamp(self, sizes):
        idx = self._footer_index
        if not (0 <= idx < len(sizes)) or len(sizes) != 2:
            return sizes
        total = sum(sizes)
        other = 1 - idx
        sizes[idx] = max(self.floor, min(self.effective_ceiling(total), sizes[idx]))
        sizes[other] = max(0, total - sizes[idx])
        # Never hidden -- the previous class's collapse left these widgets
        # invisible with no reliable way back.
        widget = self.widget(idx)
        if widget is not None and not widget.isVisible():
            widget.setVisible(True)
        return sizes


def _rotation_for(spec, value):
    """Knob-hand angle (-135..+135 degrees) for `value` on `spec`'s range
    -- log-scaled specs get a log-fraction, choice specs get their index
    fraction, everything else a plain linear fraction."""
    if spec.kind == synth_params.KIND_CHOICE:
        options = spec.options
        if not options:
            return 0.0
        try:
            index = options.index(value)
        except ValueError:
            index = 0
        fraction = index / max(1, len(options) - 1)
    elif spec.scale == synth_params.SCALE_LOG:
        low = max(float(spec.low), config.SYNTH_PARAM_LOG_FLOOR)
        high = float(spec.high)
        current = max(low, min(high, float(value)))
        fraction = 0.0 if high <= low else (
            (math.log(current) - math.log(low)) / (math.log(high) - math.log(low)))
    else:
        low, high = float(spec.low), float(spec.high)
        fraction = 0.0 if high <= low else (float(value) - low) / (high - low)
    fraction = max(0.0, min(1.0, fraction))
    return -135.0 + fraction * 270.0


class SynthView(QtWidgets.QMainWindow):
    """The whole Synth View window: patchbar, `Drawer` + `Canvas` side by
    side, `SynthKeyboardBand` below, status bar. Owns the in-memory
    `Patch` currently open, the per-patch workspace-state dict, and the
    lazily-created `SamplerEngine` pad playback uses.

    `controller` is the small surface `studio.StudioWindow` implements --
    see the module docstring."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Synth View")
        self.setStyleSheet(theme.main_stylesheet())

        self.current_patch = None
        #: patch name -> {"modules": [(type_key, x, y), ...], "keyboard": {...}}
        #: In-memory only this pass -- not persisted to disk (map #145's own
        #: note: the prototype itself calls this "in this sketch").
        self._workspace = {}
        #: physical key letter -> (voice_id, midi pitch), so `noteReleased`
        #: (which only carries the letter) can release the right voice and
        #: report the right pitch to `controller.record_note_off()`.
        self._voice_by_key = {}
        #: Built lazily: bare sample name -> the kit `Patch` that maps it,
        #: scanned once from every kit on disk. Refreshed on next Load in
        #: case a patch was saved meanwhile.
        self._sample_to_kit = {}
        self._sampler_engine = SamplerEngine()
        self._shown_once = False

        self._build()

        initial = None
        if hasattr(controller, "initial_patch"):
            initial = controller.initial_patch()
        #: Deferred to the first `showEvent()` -- the map's own behaviour
        #: spec calls out "on first show", not on construction, and a
        #: window nobody has shown yet should not need a live sound engine
        #: reachable just to be built.
        self._initial_patch = initial or patch_format.new_patch()

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(STATUS_TIMER_MS)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self._refresh_status()

    # -- construction -------------------------------------------------------

    def _build(self):
        central = QtWidgets.QWidget(self)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_patchbar())

        main_row = QtWidgets.QWidget(central)
        row = QtWidgets.QHBoxLayout(main_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.drawer = Drawer(main_row)
        self.canvas = Canvas(self._module_factory, main_row)
        row.addWidget(self.drawer)
        row.addWidget(self.canvas, 1)

        footer = _FooterSurface(central)
        footer_layout = QtWidgets.QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(0)
        footer_layout.addWidget(self._build_layout_tabs())

        self.keyboard_band = SynthKeyboardBand(parent=footer)
        self.keyboard_band.notePreviewRequested.connect(self._on_note_preview)
        self.keyboard_band.noteReleased.connect(self._on_note_released)
        self.keyboard_band.layoutChanged.connect(self._on_layout_changed)
        self.keyboard_band.panicRequested.connect(self._on_panic_clicked)
        # Stretch 1: the layout-tabs bar above it is fixed-height, so the
        # band is the only thing that should grow when the footer pane
        # itself grows -- otherwise extra room from dragging the splitter
        # open just sits as dead space below a top-pinned band instead of
        # letting the key rows fill/recenter in it (ticket #184 bug #1).
        footer_layout.addWidget(self.keyboard_band, 1)

        #: A drag handle between the canvas and the footer (user feedback
        #: on #157; #162, #184, #185-#190, and finally #196).
        #:
        #: The floor is the footer's own Qt-computed `minimumSizeHint()`.
        #: It has to be Qt's number, not a hand-picked smaller one:
        #: `QLayout` never lets a laid-out widget size below its
        #: children's own minimum, so a smaller floor is silently clamped
        #: back up and only buys a dead zone at the bottom of the drag.
        #:
        #: The footer no longer collapses at that floor -- see
        #: `_FooterSplitter`. It stops there.
        floor = footer.minimumSizeHint().height()
        footer.setMinimumHeight(floor)
        footer.setMaximumHeight(_FOOTER_MAX_HEIGHT)

        # The pane above must be allowed to give room back, or the footer
        # cannot grow at all: `main_row`'s own `minimumSizeHint()` is the
        # drawer's full natural height (356px), and `QSplitter` honours
        # it, so on any normal window height it claimed everything the
        # footer wanted. That -- not the collapse logic -- is the "it
        # doesn't change size" half of #196; a 150px drag moved the
        # footer exactly 0px. The canvas scrolls and the drawer clips
        # gracefully, so a smaller explicit minimum costs nothing and is
        # what gives the handle real travel.
        main_row.setMinimumHeight(_CANVAS_MIN_HEIGHT)

        self.splitter = _FooterSplitter(QtCore.Qt.Vertical, 1, floor,
                                        _FOOTER_MAX_HEIGHT, central)
        self.splitter.addWidget(main_row)
        self.splitter.addWidget(footer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        outer.addWidget(self.splitter, 1)

        outer.addWidget(self._build_status_bar())

        self.setCentralWidget(central)
        self.resize(900, 620)

    def _build_patchbar(self):
        bar = QtWidgets.QWidget(self)
        bar.setStyleSheet(f"background: {theme.rgba(theme.CHROME)};")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._patch_name_label = QtWidgets.QLabel("", bar)
        self._patch_name_label.setFont(theme.font(9, bold=True))
        self._patch_name_label.setTextFormat(QtCore.Qt.RichText)
        self._patch_name_label.setStyleSheet("background: transparent;")
        layout.addWidget(self._patch_name_label)

        self._engine_pills = {}
        for engine in ENGINE_PILLS:
            pill = QtWidgets.QLabel(engine.upper(), bar)
            pill.setFont(theme.font(7, bold=True))
            pill.setContentsMargins(6, 2, 6, 2)
            self._engine_pills[engine] = pill
            layout.addWidget(pill)
        clap_pill = QtWidgets.QLabel("…", bar)
        clap_pill.setFont(theme.font(7))
        clap_pill.setEnabled(False)
        clap_pill.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)}; background: {theme.rgba(theme.PANEL)};")
        layout.addWidget(clap_pill)

        load_button = QtWidgets.QPushButton("⇩ Load", bar)
        load_button.clicked.connect(self._open_load_dialog)
        layout.addWidget(load_button)

        save_button = QtWidgets.QPushButton("Save As", bar)
        save_button.clicked.connect(self._open_save_dialog)
        layout.addWidget(save_button)

        tidy_button = QtWidgets.QPushButton("⊞ Tidy", bar)
        tidy_button.setStyleSheet(
            f"border: 1px solid {theme.rgba(theme.ink(theme.COPPER))}; "
            f"color: {theme.rgba(theme.ink(theme.COPPER_LIGHT))};")
        tidy_button.clicked.connect(self._tidy)
        layout.addWidget(tidy_button)

        layout.addStretch(1)

        self._rec_button = QtWidgets.QPushButton("● Rec ⇧S → new track", bar)
        self._rec_button.setCheckable(True)
        self._rec_button.clicked.connect(self._on_rec_clicked)
        layout.addWidget(self._rec_button)

        self._play_button = QtWidgets.QPushButton("Play", bar)
        self._play_button.clicked.connect(self._on_play_clicked)
        layout.addWidget(self._play_button)

        panic_button = QtWidgets.QPushButton("Panic ⇧M", bar)
        panic_button.clicked.connect(self._on_panic_clicked)
        layout.addWidget(panic_button)

        return bar

    def _build_layout_tabs(self):
        """The layout-switcher row (`.layout-tabs` in the accepted
        prototype) that sits between the module canvas and the key-box
        band: one clickable tab per `LAYOUT_ORDER` entry, the active one
        highlighted amber, plus a right-aligned hint about `Tab`. Clicking
        a tab jumps `self.keyboard_band` straight to that layout via
        `set_layout()`; `_on_layout_changed()` keeps the highlight in sync
        whenever the band's layout changes some other way (Tab, or a
        workspace restore)."""
        bar = QtWidgets.QWidget(self)
        bar.setStyleSheet(
            f"background: {theme.rgba(theme.ink(theme.INK_0))}; "
            f"border-top: 1px solid {theme.rgba(theme.RULE)};")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._layout_tab_buttons = {}
        for layout_name in LAYOUT_ORDER:
            button = QtWidgets.QToolButton(bar)
            button.setAutoRaise(True)
            button.setText(LAYOUT_TAB_LABELS[layout_name])
            button.setFont(theme.font(8, bold=True))
            button.clicked.connect(
                functools.partial(self._on_layout_tab_clicked, layout_name))
            layout.addWidget(button)
            self._layout_tab_buttons[layout_name] = button

        layout.addStretch(1)

        hint = QtWidgets.QLabel("Tab cycles, as in the terminal synth", bar)
        hint.setFont(theme.font(7))
        hint.setContentsMargins(0, 5, 12, 5)
        hint.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        layout.addWidget(hint)

        self._refresh_layout_tabs(LAYOUT_DUAL)
        return bar

    def _on_layout_tab_clicked(self, layout_name):
        self.keyboard_band.set_layout(layout_name)

    def _on_layout_changed(self, layout_name):
        self._refresh_layout_tabs(layout_name)

    def _refresh_layout_tabs(self, current_layout):
        for layout_name, button in self._layout_tab_buttons.items():
            if layout_name == current_layout:
                button.setStyleSheet(
                    f"padding: 5px 12px; color: {theme.rgba(theme.ink(theme.INK_0))}; "
                    f"background: {theme.rgba(theme.ink(theme.AMBER))}; font-weight: 700; "
                    f"border-right: 1px solid {theme.rgba(theme.RULE)};")
            else:
                button.setStyleSheet(
                    f"padding: 5px 12px; color: {theme.rgba(theme.TEXT_FAINT)}; "
                    f"background: transparent; "
                    f"border-right: 1px solid {theme.rgba(theme.RULE)};")

    def _build_status_bar(self):
        bar = QtWidgets.QWidget(self)
        bar.setStyleSheet(f"background: {theme.rgba(theme.CHROME_DEEP)};")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(12)

        self._status_oct = QtWidgets.QLabel("", bar)
        self._status_rec = QtWidgets.QLabel("", bar)
        self._status_windows = QtWidgets.QLabel("", bar)
        for label in (self._status_oct, self._status_rec, self._status_windows):
            label.setFont(theme.font(7))
            label.setStyleSheet("background: transparent;")
            layout.addWidget(label)

        layout.addStretch(1)
        hint = QtWidgets.QLabel("drag a title bar to move · × to close · ⊞ Tidy to snap to a grid", bar)
        hint.setFont(theme.font(7))
        hint.setStyleSheet(f"background: transparent; color: {theme.rgba(theme.TEXT_FAINT)};")
        layout.addWidget(hint)
        return bar

    # -- first-show default modules -----------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._apply_patch(self._initial_patch)
        # Nothing else in this window competes for keyboard focus (module
        # windows/knobs/drawer rows are all NoFocus), but nothing hands the
        # keyboard band focus either -- without this, every key press this
        # widget's `keyPressEvent` exists to handle (note preview, Tab,
        # Shift+M, octave) never reaches it at all.
        self.keyboard_band.setFocus()

    # -- module factory (Canvas's drop callback) ----------------------------

    def _is_open(self, type_key):
        return any(w.type_key == type_key for w in self.canvas.windows())

    def _module_factory(self, type_key, _pos):
        if type_key == CLAP_TYPE_KEY:
            return None
        if self._is_open(type_key):
            return None
        if type_key in effects_audio.EFFECT_TYPES:
            return self._build_effect_module(type_key)
        return self._build_synth_module(type_key)

    def _specs_for_type(self, type_key):
        title = SECTION_TITLE_FOR_TYPE.get(type_key)
        if title is None:
            return None
        for section_title, specs in synth_params.sections_for(self.current_patch):
            if section_title == title:
                return specs
        return None

    def _build_synth_module(self, type_key):
        specs = self._specs_for_type(type_key)
        if specs is None:
            return None
        title = _ALWAYS_OPEN_TITLES.get(type_key) or _DRAWER_TITLES.get(type_key, type_key.title())
        knob_specs = [
            (spec.label, synth_params.format_value(spec, synth_params.read(self.current_patch, spec)), None)
            for spec in specs
        ]
        window = ModuleWindow(type_key, title, tag=TAG_FOR_TYPE.get(type_key, ""),
                               dot_color=DOT_COLOR_FOR_TYPE.get(type_key, theme.COPPER), knob_specs=knob_specs)
        for knob, spec in zip(window.knobs(), specs):
            knob.wheelStepped.connect(functools.partial(self._on_synth_knob_wheel, spec, knob))
        return window

    def _build_effect_module(self, type_key):
        patch = self.current_patch
        effect_spec = next((e for e in patch.effects if e.type == type_key), None)
        if effect_spec is None:
            effect_spec = patch_format.EffectSpec(type=type_key, params=dict(EFFECT_DEFAULTS.get(type_key, {})))
            patch.effects.append(effect_spec)
        specs = EFFECT_PARAM_SPECS.get(type_key, ())
        defaults = EFFECT_DEFAULTS.get(type_key, {})
        knob_specs = [
            (spec.label,
             synth_params.format_value(spec, effect_spec.params.get(spec.attr, defaults.get(spec.attr, 0.0))),
             None)
            for spec in specs
        ]
        title = EFFECT_TITLES.get(type_key, type_key.title())
        window = ModuleWindow(type_key, title, tag=TAG_FOR_TYPE.get(type_key, "FX"),
                               dot_color=DOT_COLOR_FOR_TYPE.get(type_key, theme.TEAL), knob_specs=knob_specs)
        for knob, spec in zip(window.knobs(), specs):
            knob.wheelStepped.connect(functools.partial(self._on_effect_knob_wheel, effect_spec, spec, knob))
        window.closed.connect(functools.partial(self._on_effect_module_closed, effect_spec))
        return window

    def _on_effect_module_closed(self, effect_spec, _type_key):
        if effect_spec in self.current_patch.effects:
            self.current_patch.effects.remove(effect_spec)

    def _open_default_modules(self):
        for type_key in DEFAULT_MODULES:
            if self._is_open(type_key):
                continue
            window = self._module_factory(type_key, QtCore.QPoint(0, 0))
            if window is not None:
                self.canvas.add_window(window)
        self.canvas.tidy()

    # -- knob wheel handlers --------------------------------------------------

    def _on_synth_knob_wheel(self, spec, knob, direction):
        value = synth_params.adjust(self.current_patch, spec, direction, coarse=knob.last_shift)
        knob.set_display(synth_params.format_value(spec, value), _rotation_for(spec, value))

    def _on_effect_knob_wheel(self, effect_spec, spec, knob, direction):
        defaults = EFFECT_DEFAULTS.get(effect_spec.type, {})
        current = effect_spec.params.get(spec.attr, defaults.get(spec.attr, 0.0))
        value = synth_params.step_value(spec, current, direction, coarse=knob.last_shift)
        effect_spec.params[spec.attr] = value
        knob.set_display(synth_params.format_value(spec, value), _rotation_for(spec, value))

    # -- Tidy / patch load / save --------------------------------------------

    def _tidy(self):
        self.canvas.tidy()

    def _open_load_dialog(self):
        paths = patch_format.patch_paths()
        names = [patch_format.patch_name_for_path(p) for p in paths]
        if not names:
            return
        name, ok = QtWidgets.QInputDialog.getItem(
            self, "Load Patch", "Patch:", names, 0, False)
        if not ok or not name:
            return
        for path in paths:
            if patch_format.patch_name_for_path(path) == name:
                self._apply_patch(patch_format.load_patch(path))
                self._sample_to_kit = {}  # re-scan kits lazily next pad hit
                return

    def _open_save_dialog(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save Patch As", "Name:", text=self.current_patch.name)
        if not ok or not name:
            return
        path = os.path.join(patch_format.patches_dir(), f"{slugify(name)}.toml")
        self.current_patch.name = name
        patch_format.save_patch(self.current_patch, path)
        self._register_patch_live(self.current_patch)
        self._refresh_patchbar()

    # -- patch switching / per-patch workspace state -------------------------

    def _register_patch_live(self, patch):
        """Writes `patch` into the live synth engine's name -> `Patch` map
        (`synth_engine.SynthEngine.patches`), so a preview note picks up an
        edit immediately -- no save required. A missing engine (no audio
        device) or an engine with no such map (a bare stub in tests, say)
        is a silent no-op."""
        provider = getattr(self.controller, "sound_engine_provider", None)
        if provider is None:
            return
        sound_engine = provider()
        if sound_engine is None:
            return
        inner = getattr(sound_engine, "engine", None)
        if inner is None:
            return
        patches = getattr(inner, "patches", None)
        if patches is None:
            return
        patches[patch.name] = patch

    def _snapshot_workspace(self):
        modules = [(w.type_key, w.x(), w.y()) for w in self.canvas.windows()]
        kb = self.keyboard_band
        keyboard = {
            "layout": kb.layout_state.layout,
            "split": dict(kb.layout_state.split),
            "assignments": kb.snapshot_assignments(),
            "base_octave": kb.base_octave,
        }
        return {"modules": modules, "keyboard": keyboard}

    def _restore_workspace(self, snapshot):
        for window in list(self.canvas.windows()):
            window.request_close()
        for type_key, x, y in snapshot["modules"]:
            window = self._module_factory(type_key, QtCore.QPoint(x, y))
            if window is None:
                continue
            self.canvas.add_window(window)
            window.move(x, y)
        kb = self.keyboard_band
        keyboard = snapshot["keyboard"]
        kb.layout_state.layout = keyboard["layout"]
        kb.layout_state.split = dict(keyboard["split"])
        kb.restore_assignments(keyboard.get("assignments", {}))
        kb.base_octave = keyboard["base_octave"]
        kb._rebuild_structure()
        self._refresh_layout_tabs(kb.layout_state.layout)

    def _apply_patch(self, patch):
        if self.current_patch is not None and self.current_patch is not patch:
            self._workspace[self.current_patch.name] = self._snapshot_workspace()
        self.current_patch = patch
        self._register_patch_live(patch)
        snapshot = self._workspace.get(patch.name)
        if snapshot is not None:
            self._restore_workspace(snapshot)
        else:
            for window in list(self.canvas.windows()):
                window.request_close()
            self._open_default_modules()
        self._refresh_patchbar()

    def _refresh_patchbar(self):
        name = html.escape(self.current_patch.name)
        self._patch_name_label.setText(
            f'<span style="color:{theme.rgba(theme.ink(theme.COPPER))};">♪ </span>{name}')
        self.setWindowTitle(f"VisualNote Studio — Synth View — {self.current_patch.name}")
        for engine, pill in self._engine_pills.items():
            active = engine == self.current_patch.engine
            if active:
                pill.setStyleSheet(
                    f"color: {theme.rgba(theme.ink(theme.INK_0))}; "
                    f"background: {theme.rgba(theme.ink(theme.TEAL_PALE))}; "
                    f"border: 1px solid {theme.rgba(theme.ink(theme.TEAL_PALE))}; "
                    f"font-weight: 600;")
            else:
                pill.setStyleSheet(
                    f"color: {theme.rgba(theme.TEXT_FAINT)}; "
                    f"background: transparent; "
                    f"border: 1px solid {theme.rgba(theme.RULE_STRONG)};")

    # -- keyboard band wiring -------------------------------------------------

    def _prepare_pad_patch(self, sample_name):
        if not self._sample_to_kit:
            self._rescan_kits()
        kit_patch = self._sample_to_kit.get(sample_name)
        if kit_patch is not None:
            self._sampler_engine.set_patch(kit_patch)

    def _rescan_kits(self):
        mapping = {}
        for path in patch_format.patch_paths():
            try:
                patch = patch_format.load_patch(path)
            except (OSError, ValueError):
                continue
            if not patch.is_kit():
                continue
            for zone in patch.zones:
                mapping.setdefault(zone.sample, patch)
        self._sample_to_kit = mapping

    def _on_note_preview(self, preview):
        letter = self._infer_new_letter()
        if preview.pitch is not None:
            self.controller.record_note_on(preview.pitch, preview.velocity)

        provider = getattr(self.controller, "sound_engine_provider", None)
        sound_engine = provider() if provider is not None else None
        if sound_engine is None or preview.pitch is None:
            if letter is not None:
                self._voice_by_key[letter] = (None, preview.pitch)
            return

        pitch = int(preview.pitch)
        if preview.channel == PAD_CHANNEL:
            self._prepare_pad_patch(preview.name)
            voice = self._sampler_engine.note_on(
                NoteOn(pitch, preview.velocity, preview.channel, None),
                getattr(sound_engine, "sample_rate", config.PLAYBACK_SAMPLE_RATE))
            voice_id = sound_engine.voices.allocate(voice, pitch, preview.channel)
        else:
            voice_id = sound_engine.note_on(
                NoteOn(pitch, preview.velocity, preview.channel, preview.name))

        if letter is not None:
            self._voice_by_key[letter] = (voice_id, pitch)

    def _infer_new_letter(self):
        """Which physical key letter this note-preview came from --
        `notePreviewRequested`'s payload carries no correlation id (see
        `synth_keyboard.NotePreview`), but `SynthKeyboardBand` has already
        added the letter to its own `_held` map by the time this signal
        reaches us, synchronously, one call stack deeper. The one letter
        present there that we have not yet assigned a voice to is the one
        that just fired."""
        held = getattr(self.keyboard_band, "_held", {})
        for letter in held:
            if letter not in self._voice_by_key:
                return letter
        return None

    def _on_note_released(self, letter):
        entry = self._voice_by_key.pop(letter, None)
        if entry is None:
            return
        voice_id, pitch = entry
        if voice_id is not None:
            provider = getattr(self.controller, "sound_engine_provider", None)
            sound_engine = provider() if provider is not None else None
            if sound_engine is not None:
                sound_engine.release_voice(voice_id)
        if pitch is not None:
            self.controller.record_note_off(pitch)

    # -- patchbar buttons -------------------------------------------------

    def _on_rec_clicked(self, _checked=False):
        self.controller.toggle_recording()

    def _on_play_clicked(self, _checked=False):
        self.controller.toggle_play()

    def _on_panic_clicked(self, _checked=False):
        # Shared by both entry points: the Panic button connects here
        # directly, and the keyboard band's Shift+M shortcut connects its
        # panicRequested signal here too -- so releasing every held key
        # before silencing audio covers both without duplicating the fix.
        self.keyboard_band._release_all_held()
        self.controller.panic()

    # -- status bar ----------------------------------------------------------

    def _refresh_status(self):
        recording = bool(self.controller.is_recording())
        self._rec_button.setChecked(recording)
        self._status_rec.setText(f"rec={'on' if recording else 'off'}")
        self._status_oct.setText(f"oct={self.keyboard_band.base_octave}")
        self._status_windows.setText(f"windows={self.canvas.window_count()}")
