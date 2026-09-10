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


@pytest.fixture(autouse=True)
def _close_synth_view_windows():
    """Ticket #184 follow-up: `_make_view()` never closed the `SynthView`
    it created, so every test in this module left its `QMainWindow` (and
    its 200ms status-refresh `QTimer`) alive for the rest of the run.
    That pollution was harmless for the assertions this file used to
    make, but the new splitter-collapse tests below hit real,
    order-dependent Qt layout flakiness once enough leaked windows piled
    up in the same offscreen `QApplication` -- closing each `SynthView`
    (and letting Qt process the resulting deferred-delete events) after
    every test removes that pollution instead of working around it."""
    yield
    for widget in QtWidgets.QApplication.topLevelWidgets():
        if isinstance(widget, QtWidgets.QMainWindow):
            widget.close()
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

    target = view.splitter.playable_min + 60
    view.splitter.setSizes([total - target, target])
    QtWidgets.QApplication.processEvents()

    # The resize must not raise and must actually move the handle: the
    # footer's height should now roughly track the requested size rather
    # than staying pinned at its old value.
    assert footer.height() != before
    assert abs(footer.height() - target) <= 15  # nested-layout rounding slack


def test_footer_playable_minimum_is_smaller_than_its_full_size_hint(app):
    # Ticket #184: the floor used to equal the footer's full natural
    # height with no give at all (see the test this replaces, from
    # ticket #157). The floor is now the footer's own real, Qt-computed
    # `minimumSizeHint()` instead of a hand-picked number: `KeyBoxRow`'s
    # `sizeHint()` deliberately equals its `minimumSizeHint()` (it has no
    # "preferred" size distinct from its floor -- filling extra room is
    # `Expanding` sizePolicy's job, not a bigger sizeHint), so this floor
    # equals the footer's `sizeHint()` too. What actually gives the
    # splitter handle room to move is growth *above* that floor, up to
    # `_FOOTER_MAX_HEIGHT` -- checked by the "tracks" test below.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    assert 0 < view.splitter.playable_min <= footer.sizeHint().height()
    assert footer.maximumHeight() < 16777215  # not Qt's unbounded default
    assert view.splitter.playable_min < _FOOTER_MAX_HEIGHT


def test_footer_collapses_fully_below_its_playable_minimum(app):
    # Dragging (or, here, programmatically requesting) the footer down to
    # less than its playable minimum must snap it fully shut rather than
    # clamp it at the minimum -- ticket #184's "shrinks to a sensible
    # playable minimum, then collapses entirely" requirement. A laid-out
    # widget can never actually be *sized* to 0 (its children's own
    # `minimumSizeHint()` floors it), so the collapse is done by hiding
    # it outright -- `QSplitter` gives a hidden pane zero space on its
    # own, which is what makes the "0" here real rather than clamped.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    total = sum(view.splitter.sizes())

    view.splitter.setSizes([total - 1, 1])  # far below the playable minimum
    QtWidgets.QApplication.processEvents()

    assert footer.height() == 0
    assert footer.isVisible() is False


def test_footer_tracks_a_size_at_or_above_its_playable_minimum(app):
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    total = sum(view.splitter.sizes())
    requested = view.splitter.playable_min + 20

    view.splitter.setSizes([total - requested, requested])
    QtWidgets.QApplication.processEvents()

    assert footer.height() >= view.splitter.playable_min - 15  # nested-layout rounding slack
    assert footer.isVisible() is True


def test_footer_expands_back_out_of_a_collapsed_state(app):
    # The hide/show mechanics behind the collapse must also work in
    # reverse: dragging back out past the playable minimum after having
    # been fully collapsed must re-show the footer and size it, not
    # leave it stuck hidden.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    total = sum(view.splitter.sizes())

    view.splitter.setSizes([total - 1, 1])
    QtWidgets.QApplication.processEvents()
    assert footer.height() == 0

    requested = view.splitter.playable_min + 40
    view.splitter.setSizes([total - requested, requested])
    QtWidgets.QApplication.processEvents()

    assert footer.isVisible() is True
    assert footer.height() >= view.splitter.playable_min - 15  # nested-layout rounding slack


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


def test_real_interactive_drag_collapses_and_reopens_the_footer(app):
    # Ticket #185: a genuine mouse drag through the handle's own event
    # handlers (not `setSizes()`) must be able to shrink the footer,
    # collapse it fully, and then reopen it again -- all in one
    # exercise, since #184's fix only had `setSizes()`-driven coverage
    # and turned out not to hold up under a real drag (see
    # `_CollapsingSplitter`'s docstring for what a direct
    # `QSplitterPrivate.moveSplitter()` probe found).
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    handle = view.splitter.handle(1)

    # Drag far down: shrinks the footer, then collapses it fully once
    # past the playable minimum.
    _drag_handle(handle, 400, [100] * 8)
    assert footer.height() == 0
    assert footer.isVisible() is False

    # A fresh drag session back up must reopen it and track the new
    # size, not stay stuck collapsed (the native-Qt failure mode this
    # class exists to avoid).
    _drag_handle(handle, 900, [-100] * 8)
    assert footer.isVisible() is True
    assert footer.height() >= view.splitter.playable_min - 15


def test_real_interactive_drag_tracks_smoothly_above_the_minimum(app):
    # A drag that never crosses the playable minimum should track the
    # mouse roughly 1:1 rather than jumping or refusing to move. The
    # footer starts at exactly its playable minimum on a fresh view
    # (ticket #184's floor == its own sizeHint), so grow it first with
    # one drag before shrinking it partway back with a second -- neither
    # step should cross the collapse threshold.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    view.resize(900, 900)
    handle = view.splitter.handle(1)

    _drag_handle(handle, 400, [-30] * 5)  # -150px: grow footer by ~150
    grown = footer.height()
    assert footer.isVisible() is True
    assert grown > view.splitter.playable_min + 100

    _drag_handle(handle, 250, [20] * 3)  # +60px: shrink footer by ~60, still above the floor

    assert footer.isVisible() is True
    assert footer.height() < grown
    assert abs(footer.height() - (grown - 60)) <= 15


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
