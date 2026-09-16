"""Stage 3 of the Synth View (map #145, ticket #157): `gui/synth_view.py`
-- assembling `synth_workspace.py`'s window manager and
`synth_keyboard.py`'s key-box band with a real `Patch`.

Same offscreen-Qt, no-audio-device discipline as the other two stages'
test files. A stub controller stands in for `studio.StudioWindow`; a stub
sound engine stands in for `sound_engine.SoundEngine` (its `.engine` is a
bare object with a `.patches` dict, mirroring `synth_engine.SynthEngine`;
its `.voices` is a bare object with `.allocate()`, mirroring
`sound_engine.VoiceManager` -- pad note-ons go through that, not
`.note_on()`, per `synth_view.py`'s own module docstring on why).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
scipy_signal = pytest.importorskip("scipy.signal")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from notecolor.settings import config, patch_format  # noqa: E402
from notecolor.tui import synth_params  # noqa: E402
from notecolor.tui.synth_layout import NOTE_CHANNEL, PAD_CHANNEL  # noqa: E402
from notecolor.notation.score_audition import PIANO_LOWER_ROW, pitch_for_key  # noqa: E402
from notecolor.audio.sound_engine import midi_pitch  # noqa: E402
from notecolor.gui import graph_format  # noqa: E402
from notecolor.gui import patch_graph  # noqa: E402
from notecolor.gui import synth_keyboard as sk  # noqa: E402
from notecolor.gui.synth_view import SynthView, _FOOTER_MAX_HEIGHT  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


#: Every `SynthView` `_make_view()` has built in the current test, so
#: teardown can close exactly those.
_MADE_VIEWS = []


@pytest.fixture(autouse=True)
def _close_synth_view_windows():
    """Ticket #184 follow-up: `_make_view()` never closed the `SynthView`
    it created, so every test in this module left its `QMainWindow` (and
    its 200ms status-refresh `QTimer`) alive for the rest of the run.
    That pollution was harmless for the assertions this file used to
    make, but the splitter tests below hit real, order-dependent Qt
    layout flakiness once enough leaked windows piled up in the same
    offscreen `QApplication`.

    Closes only the views *this module* made. It used to close every
    `QMainWindow` in the application, which reached into other modules'
    leftovers -- and a `StudioWindow` left dirty by
    `test_studio_window.py` answers `close()` with a modal
    `QMessageBox.question()` that nothing in a test run will ever
    dismiss. The whole suite hung there, forever, with no output: the
    exact "modal dialog that never returned" failure `conftest.py`
    warns about, reintroduced by an over-broad teardown.
    """
    _MADE_VIEWS.clear()
    yield
    for view in _MADE_VIEWS:
        view.close()
    _MADE_VIEWS.clear()
    QtWidgets.QApplication.processEvents()


class _FakeKeyEvent:
    def __init__(self, key, auto_repeat=False):
        self._key = key
        self._auto_repeat = auto_repeat

    def key(self):
        return self._key

    def isAutoRepeat(self):
        return self._auto_repeat

    def accept(self):
        pass


class StubVoices:
    def __init__(self):
        self.allocated = []

    def allocate(self, voice, pitch, channel):
        self.allocated.append((voice, pitch, channel))
        return f"pad-voice-{len(self.allocated)}"


class StubInnerEngine:
    def __init__(self):
        self.patches = {}


class StubSoundEngine:
    def __init__(self):
        self.engine = StubInnerEngine()
        self.voices = StubVoices()
        self.sample_rate = 44100
        self.note_on_calls = []
        self.released = []
        self.all_notes_off_called = False
        self.polyphony_overrides = []

    def note_on(self, event):
        self.note_on_calls.append(event)
        return f"synth-voice-{len(self.note_on_calls)}"

    def release_voice(self, voice_id):
        self.released.append(voice_id)

    def all_notes_off(self):
        self.all_notes_off_called = True

    def set_polyphony_override(self, value):
        self.polyphony_overrides.append(value)


class StubController:
    def __init__(self, sound_engine=None, patch=None):
        self._sound_engine = sound_engine
        self._patch = patch
        self.recorded_on = []
        self.recorded_off = []
        self._recording = False
        self.panicked = False
        self.played = False

    def sound_engine_provider(self):
        return self._sound_engine

    def initial_patch(self):
        return self._patch

    def record_note_on(self, pitch, velocity=1.0):
        self.recorded_on.append((pitch, velocity))

    def record_note_off(self, pitch):
        self.recorded_off.append(pitch)

    def toggle_recording(self):
        self._recording = not self._recording

    def is_recording(self):
        return self._recording

    def toggle_play(self):
        self.played = True

    def panic(self):
        self.panicked = True
        if self._sound_engine is not None:
            self._sound_engine.all_notes_off()


def _make_view(patch=None, sound_engine=None):
    patch = patch or patch_format.new_patch(name="Init")
    controller = StubController(sound_engine=sound_engine, patch=patch)
    view = SynthView(controller)
    view.show()
    _MADE_VIEWS.append(view)
    return view, controller, patch


# --- default modules ---------------------------------------------------


def test_the_view_claims_a_smaller_voice_budget_and_hands_it_back(app):
    """#180: 16 voices while this view is open, so the effects bus has a
    budget to spend. The engine outlives the window, so the release half
    matters as much as the claim -- without it every other tool in the
    session inherits the smaller cap."""
    engine = StubSoundEngine()
    view, _controller, _patch = _make_view(sound_engine=engine)
    assert engine.polyphony_overrides == [config.POLYPHONY_SYNTH_VIEW]
    view.close()
    assert engine.polyphony_overrides == [config.POLYPHONY_SYNTH_VIEW, None]


def test_default_modules_open_on_first_show(app):
    view, _controller, _patch = _make_view()
    open_types = {w.type_key for w in view.canvas.windows()}
    assert open_types == {"osc1", "filter", "amp_env"}


# --- knob wheel -> Patch field + display -------------------------------


def test_knob_wheel_mutates_patch_field_and_updates_display(app):
    view, _controller, patch = _make_view()
    window = next(w for w in view.canvas.windows() if w.type_key == "filter")
    specs = view._specs_for_type("filter")
    index = next(i for i, s in enumerate(specs) if s.attr == "cutoff")
    spec = specs[index]
    knob = window.knobs()[index]

    before = patch.filter.cutoff
    expected = synth_params.step_value(spec, before, 1, coarse=False)
    knob.last_shift = False
    knob.wheelStepped.emit(1)

    assert patch.filter.cutoff == expected
    assert patch.filter.cutoff != before


# --- live registration into the engine's patches map --------------------


def test_current_patch_is_registered_into_the_engines_patches_map(app):
    sound_engine = StubSoundEngine()
    view, _controller, patch = _make_view(sound_engine=sound_engine)
    assert sound_engine.engine.patches[patch.name] is patch


def test_knob_wheel_edit_is_reflected_in_the_engines_live_patch(app):
    sound_engine = StubSoundEngine()
    view, _controller, patch = _make_view(sound_engine=sound_engine)
    window = next(w for w in view.canvas.windows() if w.type_key == "filter")
    specs = view._specs_for_type("filter")
    index = next(i for i, s in enumerate(specs) if s.attr == "cutoff")
    spec = specs[index]
    knob = window.knobs()[index]

    before = patch.filter.cutoff
    expected = synth_params.step_value(spec, before, 1, coarse=False)
    knob.last_shift = False
    knob.wheelStepped.emit(1)

    assert sound_engine.engine.patches[patch.name].filter.cutoff == expected
    assert sound_engine.engine.patches[patch.name] is patch


# --- the Level utility module (#218) -------------------------------------


def test_level_is_reachable_from_the_drawer_and_starts_at_unity(app):
    """It is not in `MODULE_FACTORIES`' Patch-backed half or on the fixed
    effects bus (`UTILITY_TYPES`'s own docstring on why), so this exercises
    the third path -- `_build_utility_module()` -- end to end."""
    view, _controller, _patch = _make_view()
    window = view._module_factory("level", QtCore.QPoint(0, 0))
    assert window is not None
    assert window.type_key == "level"
    knobs = window.knobs()
    assert len(knobs) == 1
    view.canvas.add_window(window)
    view._on_window_added(window)
    assert view._utility_params["level"]["level"] == pytest.approx(1.0)


def test_level_knob_wheel_updates_its_own_store_and_reaches_the_bridge(app):
    view, _controller, _patch = _make_view()
    window = view._module_factory("level", QtCore.QPoint(0, 0))
    view.canvas.add_window(window)
    view._on_window_added(window)
    knob = window.knobs()[0]

    knob.last_shift = False
    knob.wheelStepped.emit(-1)

    value = view._utility_params["level"]["level"]
    assert value < 1.0
    # Reached the bridge's pending-parameter store, the same path every
    # other knob uses (`_knob_reached_engine` -> `PatchBridge.set_parameter`).
    assert view.bridge._parameters["level"]["level"] == value


def test_closing_the_level_window_resets_its_stored_gain(app):
    """Symmetry with an effect module: reopening should start from the
    module's own default, not from wherever the knob was left."""
    view, _controller, _patch = _make_view()
    window = view._module_factory("level", QtCore.QPoint(0, 0))
    view.canvas.add_window(window)
    view._on_window_added(window)
    knob = window.knobs()[0]
    knob.last_shift = False
    knob.wheelStepped.emit(-5)
    assert view._utility_params["level"]["level"] != pytest.approx(1.0)

    window.closed.emit("level")
    assert "level" not in view._utility_params


# --- #223: a real drag between two modules, one of them drawer-added ----
#
# A read-only diagnosis (see the issue) rebuilt the default chain's
# `NodeSpec`/`Cable` shapes by hand and found the engine side blameless --
# cables reach `graph.connect()` and the compiled graph runs the cabled
# path. It explicitly could not clear two things: Qt's own drag-and-drop
# gesture (`patch_canvas.py`'s `SocketWidget` -> `PatchLayer.drag_release`)
# and a module added from the drawer rather than opened by default. Both
# are exercised here, together, because the diagnosis's live lead --
# "a drag that leaves a module unpatched produces exactly this symptom" --
# is specifically about a drawer-added module cabled in by hand.


class _GraphCapableStubEngine:
    """Just enough of `SoundEngine` for `PatchBridge._engine()` to accept
    it (`set_graph` and `block_size`) -- `StubSoundEngine` above does not
    carry either, which is why no existing test here ever drove
    `view.bridge` past "pending parameters"."""

    def __init__(self):
        self.sample_rate = 44100
        self.block_size = 512
        self.graph = None

    def set_graph(self, graph, activate=True):
        self.graph = graph

    def all_notes_off(self):
        """`_on_panic_clicked()` calls `controller.panic()`, which (in
        `StubController` below) reaches for the sound engine's own
        `all_notes_off()` too -- present here only so issue #173's panic
        tests can use this engine without tripping over an unrelated
        missing stub method."""


def test_a_real_drag_wires_a_drawer_added_module_into_the_engine(app):
    """Reproduces neither hypothesis #223 raised, on purpose: this drives
    the actual Qt widgets rather than the graph underneath them. Osc 2 is
    added the same way the drawer adds it (`Canvas.spawn_module`, the
    method `dropEvent()` itself calls once past the DnD transport), and
    the cable from its spare Out jack to Filter's spare In jack is made by
    sending real `QMouseEvent`s to the `SocketWidget`s -- press, move,
    release -- exactly as `PatchLayer` receives them from a live drag."""
    view, _controller, _patch = _make_view()
    view.bridge.sound_engine_provider = _GraphCapableStubEngine
    view.resize(1000, 700)
    QtWidgets.QApplication.processEvents()

    view.canvas.spawn_module("osc2", QtCore.QPoint(20, 400))
    QtWidgets.QApplication.processEvents()

    layer = view.patch_layer

    def spare(node_id, io):
        sockets = [s for (n, i, _slot), s in layer._sockets.items()
                   if n == node_id and i == io]
        return max(sockets, key=lambda s: s.slot)

    source = spare("osc2", "out")
    dest = spare("filter", "in")
    dest_global = dest.mapToGlobal(dest.rect().center())

    QtWidgets.QApplication.sendEvent(source, QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress, QtCore.QPointF(source.rect().center()),
        QtCore.QPointF(source.mapToGlobal(source.rect().center())),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    QtWidgets.QApplication.sendEvent(source, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, QtCore.QPointF(source.rect().center()),
        QtCore.QPointF(dest_global),
        QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    QtWidgets.QApplication.sendEvent(source, QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(source.rect().center()),
        QtCore.QPointF(dest_global),
        QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    QtWidgets.QApplication.processEvents()

    assert ("osc2", "filter", None) in [
        (c.source, c.dest, c.knob) for c in layer.graph.cables]

    assert view.bridge.active
    assert view.bridge.error == ""
    connections = view.bridge.playing.graph.connections
    assert any(c.source == "osc2" and c.dest == "filter" for c in connections)


# --- keyboard preview / release -----------------------------------------


def test_synth_row_key_press_calls_engine_note_on_and_records(app):
    sound_engine = StubSoundEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.assignments = sk.RowAssignments([patch.name], [])
    view.keyboard_band._refresh_boxes()

    letter = PIANO_LOWER_ROW[0]
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))

    assert len(sound_engine.note_on_calls) == 1
    event = sound_engine.note_on_calls[0]
    expected_pitch = midi_pitch(*pitch_for_key(letter, view.keyboard_band.base_octave))
    assert event.pitch == expected_pitch
    assert event.channel == NOTE_CHANNEL
    assert event.patch == patch.name
    assert controller.recorded_on == [(expected_pitch, sk.DEFAULT_VELOCITY)]

    view.keyboard_band.keyReleaseEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    assert sound_engine.released == ["synth-voice-1"]
    assert controller.recorded_off == [expected_pitch]


def test_pad_row_key_press_uses_the_sampler_engine_not_the_synth_path(app):
    sound_engine = StubSoundEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)

    zones = [patch_format.Zone(sample="kick.wav", low_key=36, high_key=36, root_key=36)]
    kit_patch = patch_format.Patch(name="Kit", engine="sampler", zones=zones)
    view.keyboard_band._kit_zones = {"Kit": zones}
    view.keyboard_band.assignments = sk.RowAssignments([], ["Kit"])
    view.keyboard_band.layout_state.layout = sk.LAYOUT_ALLPADS
    view.keyboard_band._rebuild_structure()
    view._sample_to_kit = {"kick.wav": kit_patch}

    letter = PIANO_LOWER_ROW[0]
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))

    assert sound_engine.note_on_calls == []  # never forced through the synth path
    assert len(sound_engine.voices.allocated) == 1
    _voice, pitch, channel = sound_engine.voices.allocated[0]
    assert pitch == 36
    assert channel == PAD_CHANNEL
    assert view._sampler_engine.patch is kit_patch


# --- Panic ---------------------------------------------------------------


def test_panic_button_calls_controllers_panic_which_calls_all_notes_off(app):
    sound_engine = StubSoundEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view._on_panic_clicked()
    assert controller.panicked is True
    assert sound_engine.all_notes_off_called is True


def test_keyboard_band_panic_shortcut_calls_controllers_panic(app):
    # Shift+M on the keyboard band (synth_keyboard.py) must reach the same
    # controller.panic() path as clicking the Panic button (ticket #157
    # round 2).
    sound_engine = StubSoundEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.panicRequested.emit()
    assert controller.panicked is True
    assert sound_engine.all_notes_off_called is True


def test_panic_button_releases_held_keys_and_records_note_off():
    # Regression: holding a note key when Panic fires used to silence
    # audio (controller.panic()) without clearing SynthKeyboardBand._held,
    # leaving the key box "lit" and skipping record_note_off for any note
    # in progress. Panic must release every held key the same way a real
    # keyReleaseEvent would, for both the button and the Shift+M shortcut.
    sound_engine = StubSoundEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.assignments = sk.RowAssignments([patch.name], [])
    view.keyboard_band._refresh_boxes()

    letter = PIANO_LOWER_ROW[0]
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    assert letter in view.keyboard_band._held

    view._on_panic_clicked()

    assert view.keyboard_band._held == {}
    assert controller.panicked is True
    assert sound_engine.all_notes_off_called is True
    expected_pitch = midi_pitch(*pitch_for_key(letter, view.keyboard_band.base_octave))
    assert controller.recorded_off == [expected_pitch]


def test_shift_m_shortcut_releases_held_keys_and_records_note_off():
    # Same regression as above, via the Shift+M path (panicRequested ->
    # SynthView._on_panic_clicked, which both entry points share).
    sound_engine = StubSoundEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.assignments = sk.RowAssignments([patch.name], [])
    view.keyboard_band._refresh_boxes()

    letter = PIANO_LOWER_ROW[0]
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    assert letter in view.keyboard_band._held

    view.keyboard_band.panicRequested.emit()

    assert view.keyboard_band._held == {}
    assert controller.panicked is True
    expected_pitch = midi_pitch(*pitch_for_key(letter, view.keyboard_band.base_octave))
    assert controller.recorded_off == [expected_pitch]


# --- per-patch workspace state -------------------------------------------


# --- footer splitter (user feedback round on ticket #157) ---------------


def test_splitter_between_canvas_and_keyboard_band_is_resizable(app):
    view, _controller, _patch = _make_view()
    assert isinstance(view.splitter, QtWidgets.QSplitter)
    assert view.splitter.orientation() == QtCore.Qt.Vertical
    assert view.splitter.count() == 2
    footer = view.keyboard_band.parentWidget()
    assert view.splitter.widget(1) is footer

    # Tall enough that main_row's own minimum height doesn't compete with
    # the footer for the requested split below.
    view.resize(900, 900)
    total = sum(view.splitter.sizes())
    assert total > 0
    before = view.keyboard_band.height()

    target = view.splitter.floor + 60
    view.splitter.setSizes([total - target, target])
    QtWidgets.QApplication.processEvents()

    # The resize must not raise and must actually move the handle: the
    # footer's height should now roughly track the requested size rather
    # than staying pinned at its old value.
    assert footer.height() != before
    assert abs(footer.height() - target) <= 15  # nested-layout rounding slack


def test_footer_floor_is_qt_s_own_minimum_and_below_the_ceiling(app):
    # The floor is the footer's own real, Qt-computed `minimumSizeHint()`
    # rather than a hand-picked number: `KeyBoxRow`'s `sizeHint()`
    # deliberately equals its `minimumSizeHint()` (it has no "preferred"
    # size distinct from its floor -- filling extra room is `Expanding`
    # sizePolicy's job, not a bigger sizeHint), so this floor equals the
    # footer's `sizeHint()` too. What gives the handle room to move is
    # growth *above* that floor, up to `_FOOTER_MAX_HEIGHT`.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    assert 0 < view.splitter.floor <= footer.sizeHint().height()
    assert footer.maximumHeight() < 16777215  # not Qt's unbounded default
    assert view.splitter.floor < _FOOTER_MAX_HEIGHT


def test_footer_never_collapses_below_its_floor(app):
    # Ticket #196, the behaviour that replaced collapse-to-zero: asking
    # for less than the floor clamps there. It must never hide the
    # footer -- a hidden footer takes the key band, the assignment pill
    # and the recents rail (and every click target on them) with it, and
    # the old collapse had no reliable way back.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    total = sum(view.splitter.sizes())

    view.splitter.setSizes([total - 1, 1])  # far below the floor
    QtWidgets.QApplication.processEvents()

    assert footer.isVisible() is True
    assert footer.height() >= view.splitter.floor - 15


def test_footer_tracks_a_size_between_floor_and_ceiling(app):
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    total = sum(view.splitter.sizes())
    requested = view.splitter.floor + 20

    view.splitter.setSizes([total - requested, requested])
    QtWidgets.QApplication.processEvents()

    assert footer.height() >= view.splitter.floor - 15  # nested-layout rounding slack
    assert footer.isVisible() is True


def test_footer_has_real_travel_between_floor_and_ceiling(app):
    # The other half of #196: the footer "doesn't change size". The pane
    # above used to claim everything via its own `minimumSizeHint()` (the
    # drawer's full 356px height), so there was nothing for the footer to
    # grow into and a drag moved it 0px. There must be real room.
    view, _controller, _patch = _make_view()
    view.resize(900, 900)
    QtWidgets.QApplication.processEvents()

    assert view.splitter.effective_ceiling() > view.splitter.floor + 100


def _drag_handle(handle, start_global_y, dy_steps):
    """Drives `_DragHandle`'s own mouse*Event() methods directly -- ticket
    #185's follow-up to #184: the earlier `_CollapsingSplitter` only ever
    got exercised through `setSizes()` in this file (every test above this
    point), which passed even though a *real* interactive drag through
    Qt's native `QSplitterHandle` machinery could never actually reach the
    collapse branch at all (see `_CollapsingSplitter`'s docstring). Calling
    the handle's own event handlers with real `QMouseEvent`s -- rather
    than `setSizes()` -- is what actually exercises the interactive path
    `_DragHandle` now owns."""
    def mk(etype, y, button, buttons):
        local = QtCore.QPointF(5, 5)
        return QtGui.QMouseEvent(etype, local, local, QtCore.QPointF(0, y),
                                  button, buttons, QtCore.Qt.NoModifier)

    handle.mousePressEvent(
        mk(QtCore.QEvent.MouseButtonPress, start_global_y, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton))
    QtWidgets.QApplication.processEvents()
    y = start_global_y
    for dy in dy_steps:
        y += dy
        handle.mouseMoveEvent(mk(QtCore.QEvent.MouseMove, y, QtCore.Qt.NoButton, QtCore.Qt.LeftButton))
        QtWidgets.QApplication.processEvents()
    handle.mouseReleaseEvent(
        mk(QtCore.QEvent.MouseButtonRelease, y, QtCore.Qt.LeftButton, QtCore.Qt.NoButton))
    QtWidgets.QApplication.processEvents()


def test_real_interactive_drag_never_makes_the_footer_disappear(app):
    # The user's report, verbatim: "it doesn't change size and suddenly
    # just disappears". Driven through `_DragHandle`'s own mouse events
    # rather than `setSizes()`, because the bug was specific to the drag
    # path -- the old collapse branch was reachable by mouse and by
    # nothing else, which is how three rounds of fixes passed their tests
    # and shipped the bug anyway.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    QtWidgets.QApplication.processEvents()
    handle = view.splitter.handle(1)

    travel = view.splitter.effective_ceiling() - view.splitter.floor
    assert travel >= 90, f"no room to test a drag in: {travel}px"

    # Drag far down -- well past where the old code snapped it shut.
    _drag_handle(handle, 400, [100] * 8)

    assert footer.isVisible() is True
    assert footer.height() >= view.splitter.floor - 15

    # And it comes straight back up in one drag -- no second drag needed
    # to recover, which was the native-Qt footgun the old class fought.
    _drag_handle(handle, 900, [-(travel // 2)])
    assert footer.isVisible() is True
    assert footer.height() > view.splitter.floor


def test_real_interactive_drag_tracks_smoothly_above_the_minimum(app):
    # A drag that stays inside floor..ceiling tracks the mouse 1:1 rather
    # than jumping or refusing to move. The footer starts at exactly its
    # floor on a fresh view, so grow it first with one drag before
    # shrinking it partway back with a second -- neither step leaves the
    # range.
    #
    # The distances are derived from the splitter's own range rather than
    # hardcoded: offscreen, `resize()` is a request, and how much height
    # the window really ends up with varies with what ran before it. A
    # hardcoded 150px drag silently became a clamp-to-floor in a full-suite
    # run while passing on its own -- the test was measuring the window
    # manager, not the drag.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    QtWidgets.QApplication.processEvents()
    handle = view.splitter.handle(1)

    # Measured from where the footer actually starts, which is its
    # natural `sizeHint()` -- above the floor since #196 gave the key
    # rows a vertical minimum well below their reference size.
    start = footer.height()
    headroom = view.splitter.effective_ceiling() - start
    assert headroom >= 90, f"no room to grow into: {headroom}px"
    grow = int(headroom * 0.6)
    shrink = grow // 3

    _drag_handle(handle, 400, [-grow])
    grown = footer.height()
    assert footer.isVisible() is True
    assert abs(grown - (start + grow)) <= 15

    _drag_handle(handle, 250, [shrink])

    assert footer.isVisible() is True
    assert footer.height() < grown
    assert abs(footer.height() - (grown - shrink)) <= 15


def test_per_patch_workspace_is_restored_on_switching_back(app):
    patch_a = patch_format.new_patch(name="A")
    patch_b = patch_format.new_patch(name="B")
    view, _controller, _patch = _make_view(patch=patch_a)

    window = view.canvas.spawn_module("osc2", QtCore.QPoint(5, 5))
    assert window is not None
    view.canvas.tidy()
    positions_a = {w.type_key: (w.x(), w.y()) for w in view.canvas.windows()}

    view._apply_patch(patch_b)
    assert {w.type_key for w in view.canvas.windows()} == {"osc1", "filter", "amp_env"}

    view._apply_patch(patch_a)
    positions_restored = {w.type_key: (w.x(), w.y()) for w in view.canvas.windows()}
    assert positions_restored == positions_a


# --- per-layout-tab independence through the real tab bar (ticket #190) ---


def _stub_assignable_names(view, synth_names, kit_names):
    """Point every one of the band's four per-tab `RowAssignments` at the
    same hand-built name lists. `SynthView` builds its band with no
    explicit names, so it scans the real patch directory -- which on a
    test machine may be empty, making every `_assign_row()` a silent
    no-op. Overriding the lists (rather than the band) keeps this test on
    the real, fully-wired `SynthView` while staying off disk."""
    for assignments in view.keyboard_band._assignments_by_layout.values():
        assignments.synth_names = list(synth_names)
        assignments.kit_names = list(kit_names)


def test_clicking_layout_tabs_restores_each_tabs_assignments(app):
    """Ticket #190 end-to-end: the per-tab state is reached here through
    the actual `.layout-tabs` buttons `_build_layout_tabs()` creates, not
    by calling `SynthKeyboardBand.set_layout()` directly -- so a tab
    button wired to the wrong thing (or a highlight that desyncs from the
    band's real layout) would show up too."""
    view, _controller, _patch = _make_view()
    band = view.keyboard_band
    _stub_assignable_names(view, ["Alpha", "Beta", "Gamma"], ["KitA", "KitB"])

    def click(layout_name):
        view._layout_tab_buttons[layout_name].click()

    click(sk.LAYOUT_DUAL)
    band._assign_row("upper", "Beta")
    band._assign_key("upper", "q", "Gamma")

    click(sk.LAYOUT_ALLPADS)
    assert band.layout_state.layout == sk.LAYOUT_ALLPADS
    assert band.assignments.key_override == {}
    band._assign_row("upper", "KitB")
    band._assign_key("upper", "q", "KitA")

    click(sk.LAYOUT_DUAL)
    assert band.layout_state.layout == sk.LAYOUT_DUAL
    assert band.assignments.name_for("upper", band.layout_state) == "Beta"
    assert band.assignments.name_for_key("upper", "q", band.layout_state) == "Gamma"
    assert band.assignments.recents == ["Gamma", "Beta"]

    click(sk.LAYOUT_ALLPADS)
    assert band.assignments.name_for("upper", band.layout_state) == "KitB"
    assert band.assignments.name_for_key("upper", "q", band.layout_state) == "KitA"
    assert band.assignments.recents == ["KitA", "KitB"]


def test_per_patch_workspace_restores_every_tabs_assignments(app):
    """Per-tab state has to survive a patch switch as well as a tab
    switch: `_snapshot_workspace()` stores all four tabs' `RowAssignments`
    and `_restore_workspace()` puts them all back."""
    patch_a = patch_format.new_patch(name="A")
    patch_b = patch_format.new_patch(name="B")
    view, _controller, _patch = _make_view(patch=patch_a)
    band = view.keyboard_band
    _stub_assignable_names(view, ["Alpha", "Beta", "Gamma"], ["KitA", "KitB"])

    band.set_layout(sk.LAYOUT_DUAL)
    band._assign_row("upper", "Beta")
    band._assign_key("upper", "q", "Gamma")
    band.set_layout(sk.LAYOUT_ALLPADS)
    band._assign_row("upper", "KitB")
    expected = band.snapshot_assignments()

    # On patch B, move every tab's state somewhere else, so a restore
    # that quietly did nothing would leave B's values behind and fail.
    view._apply_patch(patch_b)
    band.set_layout(sk.LAYOUT_ALLPADS)
    band._assign_row("upper", "KitA")
    band._assign_key("upper", "w", "KitA")
    band.set_layout(sk.LAYOUT_DUAL)
    band._assign_row("upper", "Alpha")
    band.assignments.clear_key_override("upper", "q")
    assert band.snapshot_assignments() != expected

    view._apply_patch(patch_a)
    assert band.snapshot_assignments() == expected


def test_footer_keeps_its_height_when_the_window_is_resized(app):
    # On a tiling WM the window is resized constantly and not by the
    # user's choosing, so a footer that forgot its height on every resize
    # would read as the same "it doesn't change size / it disappears"
    # bug from the other direction. The canvas absorbs the change.
    #
    # Only grows the window: shrinking it far enough legitimately forces
    # the footer down off its chosen height, which is not what this is
    # about.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    # `setFixedHeight`, not `resize`: offscreen there is no window
    # manager holding the window at a requested size, and a layout
    # invalidation part-way through a drag snapped it back to its own
    # minimum -- the drag then had nothing left to move and the test was
    # measuring that, not the splitter.
    view.resize(900, 900)
    QtWidgets.QApplication.processEvents()

    travel = view.splitter.effective_ceiling() - view.splitter.floor
    assert travel >= 60, f"no room to test in: {travel}px"
    target = view.splitter.floor + 60
    total = sum(view.splitter.sizes())
    view.splitter.setSizes([total - target, target])
    QtWidgets.QApplication.processEvents()
    assert footer.height() == target

    for height in (950, 1000, 1100, 900):
        view.resize(900, height)
        QtWidgets.QApplication.processEvents()
        assert footer.isVisible() is True
        assert footer.height() == target, f"lost its height at window height {height}"


# --- #230: Save/Load wired to graph_format (#209, decision 69) -----------


def test_saving_a_graph_with_a_modulation_cable_and_reloading_restores_it(app, tmp_path):
    """Save must write the canvas's *real* graph (`save_graph_patch()`),
    not the old fixed-topology `Patch` -- a modulation cable is exactly
    the thing the old format could never carry."""
    view, _controller, patch = _make_view()
    view.canvas.spawn_module("lfo", QtCore.QPoint(20, 20))
    QtWidgets.QApplication.processEvents()
    assert "lfo" in {w.type_key for w in view.canvas.windows()}

    view._utility_params["lfo"]["rate"] = 6.0
    patch.filter.cutoff = 5000.0
    patch.filter.type = "hp"

    cable = view.patch_layer.graph.connect(
        "lfo", patch_graph.Target("knob", "filter", "Cutoff"))
    view.patch_layer.graph.set_depth(cable, 0.42)

    path = tmp_path / "mod_test.toml"
    view._save_patch_file(str(path), "Mod Test")

    text = path.read_text()
    assert text.startswith("version = 2")

    other, _controller2, _patch2 = _make_view()
    other._load_patch_file(str(path))

    assert other.current_patch.name == "Mod Test"
    assert other.current_patch.engine == "synth"
    open_types = {w.type_key for w in other.canvas.windows()}
    assert {"osc1", "filter", "amp_env", "lfo"} <= open_types

    cables = [(c.source, c.knob, c.depth) for c in other.patch_layer.graph.cables]
    assert ("lfo", ("filter", "Cutoff"), pytest.approx(0.42)) in cables
    # The default chain (osc1->filter->amp_env->mix) survives too.
    dests = {(c.source, c.dest) for c in other.patch_layer.graph.cables if c.dest}
    assert {("osc1", "filter"), ("filter", "amp_env"), ("amp_env", "mix")} <= dests

    assert other.current_patch.filter.cutoff == pytest.approx(5000.0)
    assert other.current_patch.filter.type == "hp"
    assert other._utility_params["lfo"]["rate"] == pytest.approx(6.0)


def test_loading_never_rewrites_a_version_2_file_on_disk(app, tmp_path):
    view, _controller, _patch = _make_view()
    path = tmp_path / "roundtrip.toml"
    view._save_patch_file(str(path), "Roundtrip")
    before = path.read_text()

    other, _controller2, _patch2 = _make_view()
    other._load_patch_file(str(path))

    assert path.read_text() == before


def _old_format_synth_patch(tmp_path, name="Old Fixed"):
    patch = patch_format.new_patch(name=name, engine="synth")
    patch.osc1.waveform = "square"
    patch.filter.cutoff = 2000.0
    patch.filter.type = "hp"
    patch.osc2.level = 0.4
    patch.lfo.depth = 0.5
    patch.lfo.rate = 3.0
    patch.lfo.destination = "filter"
    patch.effects.append(patch_format.EffectSpec(
        type="delay", params={"time": 0.2, "feedback": 0.3, "mix": 0.4, "damping": 0.1}))
    path = tmp_path / "old_fixed.toml"
    patch_format.save_patch(patch, str(path))
    return path


def test_loading_a_real_old_format_patch_file_migrates_it_onto_the_canvas(app, tmp_path):
    """A version 1 file has none of `graph_format`'s `version` key at
    all (decision 69 §1) -- `load_graph_patch()` migrates it in memory
    via `migrate_fixed_patch()`, and Load must reach that path rather
    than the old flat `_apply_patch()` alone, or the LFO's modulation
    cable and the effects chain would never make it onto the canvas."""
    path = _old_format_synth_patch(tmp_path)
    text_before = path.read_text()
    assert "version" not in text_before.split("\n")[0]

    view, _controller, _patch = _make_view()
    view._load_patch_file(str(path))

    assert view.current_patch.name == "Old Fixed"
    assert view.current_patch.engine == "synth"
    open_types = {w.type_key for w in view.canvas.windows()}
    assert {"osc1", "filter", "amp_env", "osc2", "lfo", "delay"} <= open_types

    assert view.current_patch.filter.cutoff == pytest.approx(2000.0)
    assert view.current_patch.filter.type == "hp"  # a choice value, round-tripped

    cables = [(c.source, c.knob) for c in view.patch_layer.graph.cables]
    assert ("lfo", ("filter", "Cutoff")) in cables

    # Loading never rewrites the file (decision 69, #230's own requirement).
    assert path.read_text() == text_before


def test_loading_a_non_synth_old_patch_falls_back_to_the_old_apply_and_notices(app, tmp_path):
    """The graph format is synth-only (decision 69 §2): a sampler/SF2
    patch has nothing for `migrate_fixed_patch()` to build, so Load must
    keep going through the old fixed-topology path for it rather than
    leaving the canvas empty."""
    zones = [patch_format.Zone(sample="kick.wav", low_key=36, high_key=36, root_key=36)]
    patch = patch_format.Patch(name="A Kit", engine="sampler", zones=zones)
    path = tmp_path / "kit.toml"
    patch_format.save_patch(patch, str(path))

    view, _controller, _patch = _make_view()
    notices = []
    view.patch_layer.statusChanged.connect(lambda text, _refusal: notices.append(text))
    view._load_patch_file(str(path))

    assert view.current_patch.name == "A Kit"
    assert view.current_patch.engine == "sampler"
    assert view.current_patch.zones == zones
    assert any("sampler" in text for text in notices)


def test_loading_a_file_naming_a_missing_module_skips_it_and_announces_a_notice(app, tmp_path):
    """`graph_format.py` already turns an unknown `module` id into a
    nameable notice instead of a `KeyError` (decision 69 §4); Load must
    let that notice reach the user (#230, #227) and must not crash trying
    to open a window for a module type this build has never heard of."""
    graph = patch_graph.PatchGraph()
    graph.add_node(patch_graph.NodeSpec("mix", "MIX", side=patch_graph.SIDE_BOUNDARY, is_mix=True))
    graph.add_node(patch_graph.NodeSpec("osc1", "OSC 1", can_in=False))
    graph.add_node(patch_graph.NodeSpec("filter", "FILTER"))
    graph.add_node(patch_graph.NodeSpec("amp_env", "AMP ENV"))
    graph.add_node(patch_graph.NodeSpec("reverb1", "Reverb", side=patch_graph.SIDE_MONO))
    graph.cables.append(patch_graph.Cable(source="osc1", dest="filter"))
    graph.cables.append(patch_graph.Cable(source="filter", dest="amp_env"))
    graph.cables.append(patch_graph.Cable(source="amp_env", dest="reverb1"))
    graph.cables.append(patch_graph.Cable(source="reverb1", dest="mix"))
    path = tmp_path / "missing_module.toml"
    graph_format.save_graph_patch(str(path), "Missing Module", graph)

    view, _controller, _patch = _make_view()
    notices = []
    view.patch_layer.statusChanged.connect(lambda text, _refusal: notices.append(text))
    view._load_patch_file(str(path))

    assert view.current_patch.name == "Missing Module"
    open_types = {w.type_key for w in view.canvas.windows()}
    assert open_types == {"osc1", "filter", "amp_env"}
    assert any("reverb1" in text for text in notices)


def test_saving_a_sampler_patch_keeps_the_old_fixed_format(app, tmp_path):
    """The graph format has no field for a zone list at all -- Save must
    not attempt it for anything but a synth patch."""
    zones = [patch_format.Zone(sample="kick.wav", low_key=36, high_key=36, root_key=36)]
    patch = patch_format.Patch(name="A Kit", engine="sampler", zones=zones)
    view, _controller, _patch = _make_view(patch=patch)
    path = tmp_path / "kit_save.toml"
    view._save_patch_file(str(path), "A Kit")

    text = path.read_text()
    assert "version" not in text.split("\n")[0]
    reloaded = patch_format.load_patch(str(path))
    assert reloaded.zones[0].sample == "kick.wav"


# --- MIDI hardware input (issue #173, decision 72) --------------------------


def test_midi_note_on_off_plays_through_the_graph_with_real_velocity(app):
    """`_on_midi_note_on`/`_on_midi_note_off` are what `_MidiSink` reaches
    (via `MidiDispatcher`, via the queued `midiMessageReceived` signal) --
    exercised directly here, the same way the QWERTY tests above drive
    `keyPressEvent` rather than a raw Qt key event through the OS."""
    view, controller, _patch = _make_view(sound_engine=_GraphCapableStubEngine())
    view.bridge.sound_engine_provider = view.controller.sound_engine_provider
    assert view.bridge.active

    view._on_midi_note_on(60, 0.42)
    voice = next(v for v in view.bridge.playing.voices if v.active and v.note.pitch == 60)
    assert voice.note.velocity == pytest.approx(0.42)
    assert controller.recorded_on == [(60, 0.42)]

    view._on_midi_note_off(60)
    assert not voice.note.gate
    assert controller.recorded_off == [60]


def test_a_keyboard_hold_survives_a_midi_release_of_the_same_pitch(app):
    """The scenario issue #173 names explicitly: both input sources play
    the same pitch through the graph; releasing MIDI's copy must not cut
    off the computer keyboard's still-held one."""
    sound_engine = _GraphCapableStubEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.assignments = sk.RowAssignments([patch.name], [])
    view.keyboard_band._refresh_boxes()
    assert view.bridge.active

    letter = PIANO_LOWER_ROW[0]
    pitch = midi_pitch(*pitch_for_key(letter, view.keyboard_band.base_octave))
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))

    view._on_midi_note_on(pitch, 0.8)
    view._on_midi_note_off(pitch)

    held = [v for v in view.bridge.playing.voices if v.active and v.note.pitch == pitch]
    assert held and all(v.note.gate for v in held)   # still sounding -- the key is still down

    view.keyboard_band.keyReleaseEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    assert not any(v.note.gate for v in view.bridge.playing.voices if v.active and v.note.pitch == pitch)


def test_a_midi_hold_survives_release_of_the_keyboards_copy(app):
    """The mirror image of the test above -- order of release reversed."""
    sound_engine = _GraphCapableStubEngine()
    view, controller, patch = _make_view(sound_engine=sound_engine)
    view.keyboard_band.assignments = sk.RowAssignments([patch.name], [])
    view.keyboard_band._refresh_boxes()

    letter = PIANO_LOWER_ROW[0]
    pitch = midi_pitch(*pitch_for_key(letter, view.keyboard_band.base_octave))
    view.keyboard_band.keyPressEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    view._on_midi_note_on(pitch, 0.8)

    view.keyboard_band.keyReleaseEvent(_FakeKeyEvent(QtCore.Qt.Key(ord(letter.upper()))))
    held = [v for v in view.bridge.playing.voices if v.active and v.note.pitch == pitch]
    assert held and all(v.note.gate for v in held)   # MIDI's copy is still down

    view._on_midi_note_off(pitch)
    assert not any(v.note.gate for v in view.bridge.playing.voices if v.active and v.note.pitch == pitch)


def test_panic_releases_midi_held_notes_too(app):
    sound_engine = _GraphCapableStubEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view._midi_dispatcher.handle_message([0x90, 60, 115])   # real note-on, through the dispatcher
    assert view._midi_voices  # bookkeeping present before panic

    view._on_panic_clicked()

    assert view._midi_voices == {}
    assert not any(v.active and v.note.gate for v in view.bridge.playing.voices)


def test_closing_the_window_releases_midi_held_notes(app):
    sound_engine = _GraphCapableStubEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view._midi_dispatcher.handle_message([0x90, 60, 115])   # real note-on, through the dispatcher
    view.close()
    assert view._midi_voices == {}


def test_midi_falls_back_to_the_legacy_engine_when_no_graph_is_playing(app):
    """`bridge.active` is False whenever `StubSoundEngine` (no `set_graph`/
    `block_size`) is the provider -- MIDI must take the same legacy-engine
    fallback the computer keyboard already does."""
    sound_engine = StubSoundEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    assert not view.bridge.active

    view._on_midi_note_on(60, 0.5)
    assert len(sound_engine.note_on_calls) == 1
    event = sound_engine.note_on_calls[0]
    assert event.pitch == 60
    assert event.velocity == pytest.approx(0.5)
    assert event.channel == NOTE_CHANNEL

    view._on_midi_note_off(60)
    assert sound_engine.released == ["synth-voice-1"]


def test_midi_pitch_bend_reaches_the_bridge(app):
    sound_engine = _GraphCapableStubEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view.bridge.apply_pitch_bend(1.0)
    for voice in view.bridge.playing.voices:
        assert voice.graph.node("osc1").module.params.get("fine") == pytest.approx(100.0)


def test_midi_input_status_reports_unavailable_with_no_hardware(app, monkeypatch):
    """No `python-rtmidi` installed on this machine (checked, not
    assumed) -- the one thing the issue requires above all else: a user
    with no MIDI hardware sees an honest status and nothing else changes."""
    import sys

    monkeypatch.setitem(sys.modules, "rtmidi", None)
    view, _controller, _patch = _make_view()
    assert view._midi_input.available is False
    assert view._midi_status_text() == "none"


# --- #235: a drawer entry and node type for the mod wheel (ExternalCc) ------


def test_mod_wheel_is_a_drawer_row_named_for_what_it_actually_does(app):
    """`ExternalCc` is the engine's name; the drawer's is a user-facing
    choice (issue #235). Only CC1 is ever forwarded to it today
    (`audio/midi_input.py`'s `_control_change()`, no CC-picker UI exists
    yet), so "Mod Wheel" -- not the more general "External CC" -- is what
    the row is named."""
    from notecolor.gui import synth_workspace

    assert ("midi_cc", "Mod Wheel") in synth_workspace.SYNTH_CORE_MODULES


def test_mod_wheel_node_is_a_once_only_mod_source_with_no_sound_jacks(app):
    """Decision 68's `KIND_BOTH` softening is the LFO's own special case
    (it also carries sound); `ExternalCc` is the simpler single-kind
    source and must not inherit it. Decision 72: `POLY_ONCE`, so it lands
    on the mono side of Mix, matching the engine's own fixed
    `descriptor().poly`."""
    view, _controller, _patch = _make_view()
    window = view._module_factory("midi_cc", QtCore.QPoint(0, 0))
    assert window is not None
    assert window.type_key == "midi_cc"
    assert len(window.knobs()) == 0   # nothing on the module itself to dial in

    view.canvas.add_window(window)
    view._on_window_added(window)

    spec = view.patch_layer.graph.node("midi_cc")
    assert spec.side == patch_graph.SIDE_MONO
    assert spec.out_kind == patch_graph.KIND_MOD
    assert spec.can_in is False
    assert spec.can_out is True


def test_mod_wheel_cabled_to_a_per_note_knob_modulates_the_engine_end_to_end(app):
    """The whole path #235 asked to be verified, since `ExternalCc` was
    proven only at the engine layer (`tests/test_patch_bridge.py`'s
    `test_external_cc_modulates_a_patched_destination`): place the node
    from the drawer, cable it to a knob, set a depth, feed a live CC1
    value through the real dispatcher (`tests/test_midi_input.py`'s own
    fake-source convention -- raw bytes, no hardware), and read back a
    changed sound. Also exercises decision 66 §3's crossing rule for real:
    a global (`POLY_ONCE`) source reaching a per-note (`osc1`) knob."""
    sound_engine = _GraphCapableStubEngine()
    view, controller, _patch = _make_view(sound_engine=sound_engine)
    view.bridge.sound_engine_provider = view.controller.sound_engine_provider
    assert view.bridge.active

    view.canvas.spawn_module("midi_cc", QtCore.QPoint(20, 20))
    QtWidgets.QApplication.processEvents()
    assert "midi_cc" in {w.type_key for w in view.canvas.windows()}

    cable = view.patch_layer.graph.connect(
        "midi_cc", patch_graph.Target("knob", "osc1", "Fine"))
    assert cable is not None
    view.patch_layer.graph.set_depth(cable, 1.0)
    view._rebuild_graph()
    mod_connections = view.bridge.playing.graph.mod_connections
    assert len(mod_connections) == 1
    assert (mod_connections[0].source, mod_connections[0].dest, mod_connections[0].param_id) == (
        "midi_cc", "osc1", "fine")

    # No CC has arrived yet -- the honest "nothing plugged in" default is
    # a flat 0.0 (`ExternalCc.__init__`), the same "silent until a device
    # says otherwise" default proven at the engine layer
    # (`test_patch_bridge.test_external_cc_feeds_a_midi_cc_module_when_one_
    # is_patched`).
    external_cc_module = view.bridge.playing.graph.node("midi_cc").module
    assert external_cc_module._value == pytest.approx(0.0)
    view.bridge.note_on(60)
    view.bridge.playing.process(512)
    voice = view.bridge.playing.voices[0]
    fine_index = voice.graph.node("osc1").module.params.index("fine")
    assert voice.graph.node("osc1").module.params.mod_active[fine_index]

    # A real CC1 (mod wheel) message, through the same dispatcher a
    # physical controller's callback thread would call into -- this is the
    # one inch #235 had to prove that #173 could not: the GUI's drawer,
    # its cable, and its depth all really reach the live value the
    # dispatcher writes.
    view._midi_dispatcher.handle_message([0xB0, 1, 127])
    assert external_cc_module._value == pytest.approx(1.0)
