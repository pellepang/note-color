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

from PySide6 import QtCore, QtWidgets  # noqa: E402

from notecolor.settings import patch_format  # noqa: E402
from notecolor.tui import synth_params  # noqa: E402
from notecolor.tui.synth_layout import NOTE_CHANNEL, PAD_CHANNEL  # noqa: E402
from notecolor.notation.score_audition import PIANO_LOWER_ROW, pitch_for_key  # noqa: E402
from notecolor.audio.sound_engine import midi_pitch  # noqa: E402
from notecolor.gui import synth_keyboard as sk  # noqa: E402
from notecolor.gui.synth_view import SynthView  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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

    view.resize(900, 700)
    total = sum(view.splitter.sizes())
    assert total > 0
    before = view.keyboard_band.height()

    view.splitter.setSizes([total - 250, 250])
    QtWidgets.QApplication.processEvents()

    # The resize must not raise and must actually move the handle: the
    # footer's height should now roughly track the requested size rather
    # than staying pinned at its old value.
    assert footer.height() != before
    assert abs(footer.height() - 250) <= 40


def test_footer_minimum_height_matches_its_own_size_hint(app):
    # Regression: a hardcoded 140px floor predated the piano-key stagger
    # increasing `KeyBoxRow`'s height, and went stale -- dragging the
    # splitter to that old minimum clipped the lower row's boxes against
    # the status bar. The floor must track the footer's actual required
    # height instead of a guessed constant.
    view, _controller, _patch = _make_view()
    footer = view.keyboard_band.parentWidget()
    assert footer.minimumHeight() == footer.sizeHint().height()

    view.resize(900, 700)
    total = sum(view.splitter.sizes())
    view.splitter.setSizes([total, 1])  # try to starve the footer pane
    QtWidgets.QApplication.processEvents()
    assert footer.height() >= footer.minimumHeight()


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
