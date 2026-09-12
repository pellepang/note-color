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

from notecolor.settings import patch_format  # noqa: E402
from notecolor.tui import synth_params  # noqa: E402
from notecolor.tui.synth_layout import NOTE_CHANNEL, PAD_CHANNEL  # noqa: E402
from notecolor.notation.score_audition import PIANO_LOWER_ROW, pitch_for_key  # noqa: E402
from notecolor.audio.sound_engine import midi_pitch  # noqa: E402
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

    def note_on(self, event):
        self.note_on_calls.append(event)
        return f"synth-voice-{len(self.note_on_calls)}"

    def release_voice(self, voice_id):
        self.released.append(voice_id)

    def all_notes_off(self):
        self.all_notes_off_called = True


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
