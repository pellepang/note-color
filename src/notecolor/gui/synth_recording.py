"""GUI-side plumbing for bouncing a Synth-view take to a new Audio track
(map #145, ticket #159).

The take itself is captured and rendered in `audio/synth_bounce.py`, which
stays free of Qt so it is directly unit-testable with no event loop; this
module supplies only the background-thread-to-GUI-thread hop, since
nothing elsewhere in `gui/` already establishes that pattern to follow
instead. Qt's own queued signal delivery is the mechanism: connecting
`BounceWorker.finished`/`.failed` from the GUI thread means a slot
connected there runs there, even though `run()` itself executes on the
worker thread -- no manual `QMetaObject.invokeMethod` needed, and no
polling.
"""

from PySide6 import QtCore

from notecolor.audio import synth_bounce


class BounceWorker(QtCore.QObject):
    """Runs `synth_bounce.bounce_take_to_track()` off the GUI thread.

    `finished` carries the built-but-undispatched `edit.AddTrack` command;
    dispatching it (through the project's undo history) is left to the
    slot the caller connects, back on the GUI thread -- this class never
    touches the live `Project` object itself, matching
    `bounce_take_to_track()`'s own contract."""

    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, bundle_path, project, events, patch, sample_rate, start_beat,
                 track_name=None):
        super().__init__()
        self.bundle_path = bundle_path
        self.project = project
        self.events = events
        self.patch = patch
        self.sample_rate = sample_rate
        self.start_beat = start_beat
        self.track_name = track_name

    def run(self):
        try:
            command = synth_bounce.bounce_take_to_track(
                self.bundle_path, self.project, self.events, self.patch,
                self.sample_rate, self.start_beat, track_name=self.track_name)
        except Exception as exc:   # noqa: BLE001 -- surfaced to the user, never crashes the app
            self.failed.emit(str(exc))
            return
        self.finished.emit(command)


def start_bounce(owner, bundle_path, project, events, patch, sample_rate, start_beat,
                  on_done, on_error=None, track_name=None):
    """Starts a `BounceWorker` on a fresh `QThread` parented to `owner`.

    Returns `(thread, worker)` -- the caller must hold onto both (e.g. as
    `StudioWindow._bounce_thread`/`._bounce_worker`) until the
    corresponding callback fires: Qt does not keep a `QThread` alive on its
    own, and one dropped by the caller while still running is silent data
    loss, not a clean cancel.

    `on_done(command)` runs on the GUI thread once the render/import/track
    build finishes; `on_error(message)` runs there instead if it raised.
    `on_error` defaults to a no-op so a caller that only cares about
    success need not supply one."""
    thread = QtCore.QThread(owner)
    worker = BounceWorker(bundle_path, project, events, patch, sample_rate, start_beat,
                          track_name=track_name)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    def _finish(command):
        on_done(command)
        thread.quit()

    def _fail(message):
        (on_error or (lambda _message: None))(message)
        thread.quit()

    worker.finished.connect(_finish)
    worker.failed.connect(_fail)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread, worker
